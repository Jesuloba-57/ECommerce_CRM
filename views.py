from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_
from sqlalchemy.orm import joinedload

from __init__ import db
from db_model import Listing, Offer, PriceHistory, User

views = Blueprint("views", __name__)

LISTING_CATEGORIES = [
    "Electronics",
    "Fashion",
    "Home",
    "Sports",
    "Collectibles",
    "Textbooks",
]
LISTING_CONDITIONS = ["New", "Like new", "Good", "Fair", "For parts"]
LISTING_STATUSES = ["active", "paused", "sold", "archived"]
DEFAULT_IMAGE_URL = "/static/images/webImage.jpeg"


def parse_price_to_cents(raw_price):
    cleaned = (raw_price or "").replace("$", "").replace(",", "").strip()
    if not cleaned:
        raise ValueError("Price is required.")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError("Enter a valid price.") from exc

    if amount <= 0:
        raise ValueError("Price must be greater than zero.")

    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def get_owned_listing(listing_id):
    listing = Listing.query.get_or_404(listing_id)
    if listing.seller_id != current_user.id:
        abort(403)
    return listing


@views.route("/")
def home():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()

    listings_query = Listing.query.options(joinedload(Listing.seller)).filter(Listing.status == "active")

    if search:
        search_term = f"%{search}%"
        listings_query = listings_query.join(User).filter(
            or_(
                Listing.title.ilike(search_term),
                Listing.description.ilike(search_term),
                Listing.location.ilike(search_term),
                User.email.ilike(search_term),
            )
        )

    if category:
        listings_query = listings_query.filter(Listing.category == category)

    listings = listings_query.order_by(Listing.updated_at.desc()).all()

    recent_price_drops = (
        PriceHistory.query.options(joinedload(PriceHistory.listing))
        .join(Listing)
        .filter(
            Listing.status == "active",
            PriceHistory.old_price_cents.isnot(None),
            PriceHistory.new_price_cents < PriceHistory.old_price_cents,
        )
        .order_by(PriceHistory.changed_at.desc())
        .limit(4)
        .all()
    )

    featured_sellers = (
        db.session.query(User, func.count(Listing.id).label("listing_count"))
        .join(Listing, Listing.seller_id == User.id)
        .filter(Listing.status == "active")
        .group_by(User.id)
        .order_by(func.count(Listing.id).desc(), User.email.asc())
        .limit(3)
        .all()
    )

    stats = {
        "active_listings": Listing.query.filter_by(status="active").count(),
        "sellers": db.session.query(User.id).join(Listing).distinct().count(),
        "offers": Offer.query.count(),
    }

    return render_template(
        "index.html",
        listings=listings,
        categories=LISTING_CATEGORIES,
        selected_category=category,
        search=search,
        recent_price_drops=recent_price_drops,
        featured_sellers=featured_sellers,
        stats=stats,
    )


@views.route("/about")
def about():
    stats = {
        "listings": Listing.query.count(),
        "offers": Offer.query.count(),
        "price_updates": PriceHistory.query.filter(PriceHistory.old_price_cents.isnot(None)).count(),
    }
    return render_template("about.html", stats=stats)


@views.route("/seller")
@login_required
def seller_dashboard():
    listings = (
        Listing.query.options(
            joinedload(Listing.price_history),
            joinedload(Listing.offers).joinedload(Offer.buyer),
        )
        .filter_by(seller_id=current_user.id)
        .order_by(Listing.updated_at.desc())
        .all()
    )

    incoming_offers = (
        Offer.query.options(
            joinedload(Offer.buyer),
            joinedload(Offer.listing),
        )
        .filter_by(seller_id=current_user.id)
        .order_by(Offer.created_at.desc())
        .all()
    )

    dashboard_stats = {
        "active": sum(1 for listing in listings if listing.status == "active"),
        "sold": sum(1 for listing in listings if listing.status == "sold"),
        "offers": len(incoming_offers),
    }

    return render_template(
        "seller.html",
        listings=listings,
        incoming_offers=incoming_offers,
        categories=LISTING_CATEGORIES,
        conditions=LISTING_CONDITIONS,
        statuses=LISTING_STATUSES,
        dashboard_stats=dashboard_stats,
    )


@views.route("/seller/listings", methods=["POST"])
@login_required
def create_listing():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "").strip()
    condition = request.form.get("condition", "").strip()
    location = request.form.get("location", "").strip()
    image_url = request.form.get("image_url", "").strip() or DEFAULT_IMAGE_URL
    note = request.form.get("note", "").strip() or "Listing created"

    try:
        price_cents = parse_price_to_cents(request.form.get("price"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("views.seller_dashboard"))

    if not title or not description or not location:
        flash("Title, description, and location are required.", "error")
        return redirect(url_for("views.seller_dashboard"))

    if category not in LISTING_CATEGORIES:
        flash("Choose a valid category.", "error")
        return redirect(url_for("views.seller_dashboard"))

    if condition not in LISTING_CONDITIONS:
        flash("Choose a valid condition.", "error")
        return redirect(url_for("views.seller_dashboard"))

    listing = Listing(
        seller_id=current_user.id,
        title=title,
        description=description,
        category=category,
        condition=condition,
        location=location,
        image_url=image_url,
        price_cents=price_cents,
    )
    db.session.add(listing)
    db.session.flush()
    db.session.add(
        PriceHistory(
            listing_id=listing.id,
            changed_by_user_id=current_user.id,
            old_price_cents=None,
            new_price_cents=price_cents,
            note=note,
        )
    )
    db.session.commit()

    flash("Listing created and added to your storefront.", "success")
    return redirect(url_for("views.seller_dashboard"))


@views.route("/seller/listings/<int:listing_id>/price", methods=["POST"])
@login_required
def update_listing_price(listing_id):
    listing = get_owned_listing(listing_id)

    try:
        new_price_cents = parse_price_to_cents(request.form.get("price"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("views.seller_dashboard"))

    if new_price_cents == listing.price_cents:
        flash("That price is already live on the listing.", "info")
        return redirect(url_for("views.seller_dashboard"))

    note = request.form.get("note", "").strip() or "Seller updated price"
    previous_price = listing.price_cents

    listing.price_cents = new_price_cents
    listing.updated_at = datetime.utcnow()
    db.session.add(
        PriceHistory(
            listing_id=listing.id,
            changed_by_user_id=current_user.id,
            old_price_cents=previous_price,
            new_price_cents=new_price_cents,
            note=note,
        )
    )
    db.session.commit()

    flash("Price updated and saved to product history.", "success")
    return redirect(url_for("views.seller_dashboard"))


@views.route("/seller/listings/<int:listing_id>/status", methods=["POST"])
@login_required
def update_listing_status(listing_id):
    listing = get_owned_listing(listing_id)
    new_status = request.form.get("status", "").strip().lower()

    if new_status not in LISTING_STATUSES:
        flash("Choose a valid listing status.", "error")
        return redirect(url_for("views.seller_dashboard"))

    listing.status = new_status
    listing.updated_at = datetime.utcnow()

    if new_status == "sold":
        open_offers = Offer.query.filter(
            Offer.listing_id == listing.id,
            Offer.status.in_(("pending", "countered")),
        ).all()
        for offer in open_offers:
            offer.status = "declined"
            offer.seller_response = "This listing is no longer available."
            offer.responded_at = datetime.utcnow()

    db.session.commit()
    flash(f"Listing marked as {new_status}.", "success")
    return redirect(url_for("views.seller_dashboard"))


@views.route("/listings/<int:listing_id>")
def listing_detail(listing_id):
    listing = Listing.query.options(
        joinedload(Listing.seller),
        joinedload(Listing.price_history).joinedload(PriceHistory.changed_by),
        joinedload(Listing.offers).joinedload(Offer.buyer),
    ).get_or_404(listing_id)

    related_listings = (
        Listing.query.options(joinedload(Listing.seller))
        .filter(
            Listing.id != listing.id,
            Listing.category == listing.category,
            Listing.status == "active",
        )
        .order_by(Listing.updated_at.desc())
        .limit(3)
        .all()
    )

    viewer_offers = []
    if current_user.is_authenticated:
        viewer_offers = [offer for offer in listing.offers if offer.buyer_id == current_user.id]

    return render_template(
        "listing_detail.html",
        listing=listing,
        related_listings=related_listings,
        viewer_offers=viewer_offers,
    )


@views.route("/listings/<int:listing_id>/offers", methods=["POST"])
@login_required
def submit_offer(listing_id):
    listing = Listing.query.options(joinedload(Listing.seller)).get_or_404(listing_id)

    if listing.seller_id == current_user.id:
        flash("You cannot submit an offer on your own listing.", "error")
        return redirect(url_for("views.listing_detail", listing_id=listing.id))

    if listing.status != "active":
        flash("Offers are only open for active listings.", "error")
        return redirect(url_for("views.listing_detail", listing_id=listing.id))

    try:
        amount_cents = parse_price_to_cents(request.form.get("amount"))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("views.listing_detail", listing_id=listing.id))

    message = request.form.get("message", "").strip()

    offer = Offer(
        listing_id=listing.id,
        buyer_id=current_user.id,
        seller_id=listing.seller_id,
        amount_cents=amount_cents,
        message=message,
    )
    db.session.add(offer)
    db.session.commit()

    flash("Offer sent to the seller.", "success")
    return redirect(url_for("views.listing_detail", listing_id=listing.id))


@views.route("/offers/<int:offer_id>/respond", methods=["POST"])
@login_required
def respond_to_offer(offer_id):
    offer = Offer.query.options(joinedload(Offer.listing)).get_or_404(offer_id)
    if offer.seller_id != current_user.id:
        abort(403)

    if offer.status not in {"pending", "countered"}:
        flash("That offer has already been finalized.", "info")
        return redirect(url_for("views.seller_dashboard"))

    action = request.form.get("action", "").strip().lower()
    response_text = request.form.get("seller_response", "").strip()
    now = datetime.utcnow()

    if action == "accept":
        offer.status = "accepted"
        offer.seller_response = response_text or "Offer accepted."
        offer.responded_at = now
        offer.listing.status = "sold"
        offer.listing.updated_at = now

        competing_offers = Offer.query.filter(
            Offer.listing_id == offer.listing_id,
            Offer.id != offer.id,
            Offer.status.in_(("pending", "countered")),
        ).all()
        for competing_offer in competing_offers:
            competing_offer.status = "declined"
            competing_offer.seller_response = "Another offer was accepted for this listing."
            competing_offer.responded_at = now

        flash("Offer accepted and listing marked as sold.", "success")

    elif action == "decline":
        offer.status = "declined"
        offer.seller_response = response_text or "Offer declined."
        offer.responded_at = now
        flash("Offer declined.", "info")

    elif action == "counter":
        try:
            counter_amount_cents = parse_price_to_cents(request.form.get("counter_amount"))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("views.seller_dashboard"))

        offer.status = "countered"
        offer.counter_amount_cents = counter_amount_cents
        offer.seller_response = response_text or "Seller sent a counter offer."
        offer.responded_at = now
        flash("Counter offer sent.", "success")

    else:
        flash("Choose a valid offer action.", "error")
        return redirect(url_for("views.seller_dashboard"))

    db.session.commit()
    return redirect(url_for("views.seller_dashboard"))


@views.route("/activity")
@views.route("/cart")
@login_required
def activity():
    offers_made = (
        Offer.query.options(joinedload(Offer.listing), joinedload(Offer.seller))
        .filter_by(buyer_id=current_user.id)
        .order_by(Offer.created_at.desc())
        .all()
    )

    offers_received = (
        Offer.query.options(joinedload(Offer.listing), joinedload(Offer.buyer))
        .filter_by(seller_id=current_user.id)
        .order_by(Offer.created_at.desc())
        .all()
    )

    listings = (
        Listing.query.filter_by(seller_id=current_user.id)
        .order_by(Listing.updated_at.desc())
        .all()
    )

    return render_template(
        "activity.html",
        offers_made=offers_made,
        offers_received=offers_received,
        listings=listings,
    )
