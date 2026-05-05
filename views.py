from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload

from __init__ import db
from db_model import (
    Conversation,
    Listing,
    Message,
    Offer,
    PriceHistory,
    User,
    WalletTransaction,
    utc_now,
)

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
MAX_MESSAGE_LENGTH = 2000


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
    listing = db.get_or_404(Listing, listing_id)
    if listing.seller_id != current_user.id:
        abort(403)
    return listing


def money_label(value):
    return f"${value / 100:,.2f}"


def wallet_balance_cents(user):
    return user.wallet_balance_cents or 0


def datetime_payload(value):
    if value is None:
        return None
    return value.isoformat()


def current_user_can_access_conversation(conversation):
    return current_user.id in {conversation.buyer_id, conversation.seller_id}


def get_accessible_conversation(conversation_id):
    conversation = db.get_or_404(
        Conversation,
        conversation_id,
        options=[
            joinedload(Conversation.listing),
            joinedload(Conversation.buyer),
            joinedload(Conversation.seller),
            selectinload(Conversation.messages).joinedload(Message.sender),
            selectinload(Conversation.messages).joinedload(Message.offer),
        ],
    )
    if not current_user_can_access_conversation(conversation):
        abort(403)
    return conversation


def find_or_create_conversation(listing, buyer_id, seller_id):
    conversation = Conversation.query.filter_by(
        listing_id=listing.id,
        buyer_id=buyer_id,
        seller_id=seller_id,
    ).first()

    if conversation is None:
        conversation = Conversation(
            listing_id=listing.id,
            buyer_id=buyer_id,
            seller_id=seller_id,
            status="active",
            deal_status="negotiating",
        )
        db.session.add(conversation)
        db.session.flush()
    elif conversation.status != "active":
        conversation.status = "active"
        conversation.deal_status = "negotiating"

    return conversation


def add_conversation_message(conversation, sender_id, body, message_type="text", offer_id=None):
    now = utc_now()
    message = Message(
        conversation=conversation,
        sender_id=sender_id,
        offer_id=offer_id,
        body=body.strip(),
        message_type=message_type,
        created_at=now,
    )
    conversation.last_message_at = now
    conversation.updated_at = now
    db.session.add(message)
    return message


def message_payload(message):
    payload = {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "sender_id": message.sender_id,
        "sender": {
            "id": message.sender.id,
            "display_name": message.sender.display_name,
        }
        if message.sender
        else None,
        "offer_id": message.offer_id,
        "body": message.body,
        "message_type": message.message_type,
        "created_at": datetime_payload(message.created_at),
        "read_at": datetime_payload(message.read_at),
    }

    if message.offer:
        payload["offer"] = {
            "id": message.offer.id,
            "amount_cents": message.offer.amount_cents,
            "amount": money_label(message.offer.amount_cents),
            "status": message.offer.status,
            "counter_amount_cents": message.offer.counter_amount_cents,
            "counter_amount": money_label(message.offer.counter_amount_cents)
            if message.offer.counter_amount_cents
            else None,
        }

    return payload


def conversation_payload(conversation, include_messages=False):
    latest_message = conversation.messages[-1] if conversation.messages else None
    payload = {
        "id": conversation.id,
        "status": conversation.status,
        "deal_status": conversation.deal_status,
        "listing": {
            "id": conversation.listing.id,
            "title": conversation.listing.title,
            "status": conversation.listing.status,
            "price_cents": conversation.listing.price_cents,
            "price": money_label(conversation.listing.price_cents),
            "image_url": conversation.listing.image_url,
        },
        "buyer": {
            "id": conversation.buyer.id,
            "display_name": conversation.buyer.display_name,
            "email": conversation.buyer.email,
        },
        "seller": {
            "id": conversation.seller.id,
            "display_name": conversation.seller.display_name,
            "email": conversation.seller.email,
        },
        "unread_count": sum(
            1
            for message in conversation.messages
            if message.sender_id != current_user.id and message.read_at is None
        ),
        "latest_message": message_payload(latest_message) if latest_message else None,
        "created_at": datetime_payload(conversation.created_at),
        "updated_at": datetime_payload(conversation.updated_at),
        "last_message_at": datetime_payload(conversation.last_message_at),
    }

    if include_messages:
        payload["messages"] = [message_payload(message) for message in conversation.messages]

    return payload


def request_message_body():
    payload = request.get_json(silent=True) if request.is_json else None
    body = (payload or {}).get("body") if payload is not None else request.form.get("body")
    body = (body or "").strip()

    if not body:
        abort(400, description="Message body is required.")
    if len(body) > MAX_MESSAGE_LENGTH:
        abort(400, description=f"Message body must be {MAX_MESSAGE_LENGTH} characters or fewer.")

    return body


@views.route("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"status": "error", "database": "unavailable"}), 503

    return jsonify({"status": "ok", "database": "ok"})


@views.route("/conversations")
@login_required
def conversations():
    user_conversations = (
        Conversation.query.options(
            joinedload(Conversation.listing),
            joinedload(Conversation.buyer),
            joinedload(Conversation.seller),
            selectinload(Conversation.messages).joinedload(Message.sender),
            selectinload(Conversation.messages).joinedload(Message.offer),
        )
        .filter(
            or_(
                Conversation.buyer_id == current_user.id,
                Conversation.seller_id == current_user.id,
            )
        )
        .order_by(Conversation.last_message_at.desc(), Conversation.updated_at.desc())
        .all()
    )

    return jsonify(
        {
            "conversations": [
                conversation_payload(conversation) for conversation in user_conversations
            ]
        }
    )


@views.route("/conversations/<int:conversation_id>")
@login_required
def conversation_detail(conversation_id):
    conversation = get_accessible_conversation(conversation_id)
    return jsonify({"conversation": conversation_payload(conversation, include_messages=True)})


@views.route("/conversations/<int:conversation_id>/messages", methods=["POST"])
@login_required
def send_conversation_message(conversation_id):
    conversation = get_accessible_conversation(conversation_id)
    if conversation.status != "active":
        abort(409, description="This conversation is closed.")

    message = add_conversation_message(
        conversation=conversation,
        sender_id=current_user.id,
        body=request_message_body(),
        message_type="text",
    )
    db.session.commit()

    return (
        jsonify(
            {
                "message": message_payload(message),
                "conversation": conversation_payload(conversation),
            }
        ),
        201,
    )


@views.route("/conversations/<int:conversation_id>/read", methods=["POST"])
@login_required
def mark_conversation_read(conversation_id):
    conversation = get_accessible_conversation(conversation_id)
    now = utc_now()
    marked_read = 0

    for message in conversation.messages:
        if message.sender_id != current_user.id and message.read_at is None:
            message.read_at = now
            marked_read += 1

    db.session.commit()

    return jsonify(
        {
            "marked_read": marked_read,
            "conversation": conversation_payload(conversation, include_messages=True),
        }
    )


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
        "wallet_balance_cents": wallet_balance_cents(current_user),
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
    listing.updated_at = utc_now()
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
    listing.updated_at = utc_now()

    if new_status == "sold":
        now = utc_now()
        open_offers = Offer.query.filter(
            Offer.listing_id == listing.id,
            Offer.status.in_(("pending", "countered")),
        ).all()
        for offer in open_offers:
            offer.status = "declined"
            offer.seller_response = "This listing is no longer available."
            offer.responded_at = now

            conversation = find_or_create_conversation(
                listing=listing,
                buyer_id=offer.buyer_id,
                seller_id=offer.seller_id,
            )
            conversation.status = "closed"
            conversation.deal_status = "declined"
            add_conversation_message(
                conversation=conversation,
                sender_id=current_user.id,
                offer_id=offer.id,
                body="This listing is no longer available.",
                message_type="declined",
            )

    db.session.commit()
    flash(f"Listing marked as {new_status}.", "success")
    return redirect(url_for("views.seller_dashboard"))


@views.route("/listings/<int:listing_id>")
def listing_detail(listing_id):
    listing = db.get_or_404(
        Listing,
        listing_id,
        options=[
            joinedload(Listing.seller),
            joinedload(Listing.price_history).joinedload(PriceHistory.changed_by),
            joinedload(Listing.offers).joinedload(Offer.buyer),
        ],
    )

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
    listing = db.get_or_404(Listing, listing_id, options=[joinedload(Listing.seller)])

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

    if amount_cents > wallet_balance_cents(current_user):
        flash(
            "Your wallet balance is "
            f"{money_label(wallet_balance_cents(current_user))}; "
            "send an offer within your available app currency.",
            "error",
        )
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
    db.session.flush()

    conversation = find_or_create_conversation(
        listing=listing,
        buyer_id=current_user.id,
        seller_id=listing.seller_id,
    )
    conversation.deal_status = "negotiating"
    offer_message = f"Offer sent: {money_label(amount_cents)}"
    if message:
        offer_message = f"{offer_message}\n{message}"

    add_conversation_message(
        conversation=conversation,
        sender_id=current_user.id,
        offer_id=offer.id,
        body=offer_message,
        message_type="offer",
    )
    db.session.commit()

    flash("Offer sent and chat started with the seller.", "success")
    return redirect(url_for("views.listing_detail", listing_id=listing.id))


@views.route("/offers/<int:offer_id>/respond", methods=["POST"])
@login_required
def respond_to_offer(offer_id):
    offer = db.get_or_404(
        Offer,
        offer_id,
        options=[
            joinedload(Offer.listing),
            joinedload(Offer.buyer),
            joinedload(Offer.seller),
        ],
    )
    if offer.seller_id != current_user.id:
        abort(403)

    if offer.status not in {"pending", "countered"}:
        flash("That offer has already been finalized.", "info")
        return redirect(url_for("views.seller_dashboard"))

    action = request.form.get("action", "").strip().lower()
    response_text = request.form.get("seller_response", "").strip()
    now = utc_now()
    conversation = find_or_create_conversation(
        listing=offer.listing,
        buyer_id=offer.buyer_id,
        seller_id=offer.seller_id,
    )

    if action == "accept":
        buyer = offer.buyer
        seller = offer.seller
        if wallet_balance_cents(buyer) < offer.amount_cents:
            flash(
                f"{buyer.display_name}'s wallet only has "
                f"{money_label(wallet_balance_cents(buyer))}, so this offer cannot be accepted.",
                "error",
            )
            return redirect(url_for("views.seller_dashboard"))

        buyer.wallet_balance_cents = wallet_balance_cents(buyer) - offer.amount_cents
        seller.wallet_balance_cents = wallet_balance_cents(seller) + offer.amount_cents

        offer.status = "accepted"
        offer.seller_response = response_text or "Offer accepted."
        offer.responded_at = now
        offer.listing.status = "sold"
        offer.listing.updated_at = now
        conversation.deal_status = "accepted"
        add_conversation_message(
            conversation=conversation,
            sender_id=current_user.id,
            offer_id=offer.id,
            body=response_text or f"Offer accepted at {money_label(offer.amount_cents)}.",
            message_type="accepted",
        )
        db.session.add(
            WalletTransaction(
                offer_id=offer.id,
                listing_id=offer.listing_id,
                buyer_id=buyer.id,
                seller_id=seller.id,
                amount_cents=offer.amount_cents,
                buyer_balance_after_cents=buyer.wallet_balance_cents,
                seller_balance_after_cents=seller.wallet_balance_cents,
            )
        )

        competing_offers = Offer.query.filter(
            Offer.listing_id == offer.listing_id,
            Offer.id != offer.id,
            Offer.status.in_(("pending", "countered")),
        ).all()
        for competing_offer in competing_offers:
            competing_offer.status = "declined"
            competing_offer.seller_response = "Another offer was accepted for this listing."
            competing_offer.responded_at = now
            competing_conversation = find_or_create_conversation(
                listing=offer.listing,
                buyer_id=competing_offer.buyer_id,
                seller_id=competing_offer.seller_id,
            )
            competing_conversation.status = "closed"
            competing_conversation.deal_status = "declined"
            add_conversation_message(
                conversation=competing_conversation,
                sender_id=current_user.id,
                offer_id=competing_offer.id,
                body="Another offer was accepted for this listing.",
                message_type="declined",
            )

        flash("Offer accepted. Buyer wallet debited and seller wallet credited.", "success")

    elif action == "decline":
        offer.status = "declined"
        offer.seller_response = response_text or "Offer declined."
        offer.responded_at = now
        conversation.deal_status = "declined"
        add_conversation_message(
            conversation=conversation,
            sender_id=current_user.id,
            offer_id=offer.id,
            body=response_text or "Offer declined.",
            message_type="declined",
        )
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
        conversation.deal_status = "negotiating"
        counter_message = f"Counter offer: {money_label(counter_amount_cents)}"
        if response_text:
            counter_message = f"{counter_message}\n{response_text}"
        add_conversation_message(
            conversation=conversation,
            sender_id=current_user.id,
            offer_id=offer.id,
            body=counter_message,
            message_type="counter",
        )
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

    wallet_transactions = (
        WalletTransaction.query.options(
            joinedload(WalletTransaction.listing),
            joinedload(WalletTransaction.buyer),
            joinedload(WalletTransaction.seller),
        )
        .filter(
            or_(
                WalletTransaction.buyer_id == current_user.id,
                WalletTransaction.seller_id == current_user.id,
            )
        )
        .order_by(WalletTransaction.created_at.desc())
        .all()
    )

    return render_template(
        "activity.html",
        offers_made=offers_made,
        offers_received=offers_received,
        listings=listings,
        wallet_transactions=wallet_transactions,
    )
