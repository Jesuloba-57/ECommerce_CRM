from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import case, func, or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload, selectinload
from werkzeug.utils import secure_filename

from __init__ import db
from db_model import (
    Conversation,
    Listing,
    ListingImage,
    Message,
    Notification,
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
MAX_LISTING_IMAGE_BYTES = 3 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


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


def next_url_or(default_endpoint):
    next_url = request.form.get("next", "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for(default_endpoint)


def wants_json_response():
    return request.accept_mimetypes.best_match(["application/json", "text/html"]) == "application/json"


def action_error(message, redirect_url, status_code=400, category="error"):
    if wants_json_response():
        return jsonify({"error": message}), status_code
    flash(message, category)
    return redirect(redirect_url)


def action_success(message, redirect_url, conversation=None, category="success", extra=None):
    if wants_json_response():
        payload = {"message": message}
        if conversation is not None:
            payload["conversation"] = conversation_payload(conversation, include_messages=True)
        if extra:
            payload.update(extra)
        return jsonify(payload)
    flash(message, category)
    return redirect(redirect_url)


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


def get_accessible_wallet_transaction(transaction_id):
    transaction = db.get_or_404(
        WalletTransaction,
        transaction_id,
        options=[
            joinedload(WalletTransaction.offer),
            joinedload(WalletTransaction.listing),
            joinedload(WalletTransaction.buyer),
            joinedload(WalletTransaction.seller),
        ],
    )
    if current_user.id not in {transaction.buyer_id, transaction.seller_id}:
        abort(403)
    return transaction


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


def notification_payload(notification):
    return {
        "id": notification.id,
        "kind": notification.kind,
        "title": notification.title,
        "body": notification.body,
        "target_url": notification.target_url,
        "read_at": datetime_payload(notification.read_at),
        "created_at": datetime_payload(notification.created_at),
        "actor": {
            "id": notification.actor.id,
            "display_name": notification.actor.display_name,
        }
        if notification.actor
        else None,
    }


def create_notification(
    user_id,
    kind,
    title,
    body,
    target_url,
    actor_id=None,
    listing_id=None,
    offer_id=None,
    conversation_id=None,
    message_id=None,
):
    if not user_id or user_id == actor_id:
        return None

    notification = Notification(
        user_id=user_id,
        actor_id=actor_id,
        listing_id=listing_id,
        offer_id=offer_id,
        conversation_id=conversation_id,
        message_id=message_id,
        kind=kind,
        title=title,
        body=body,
        target_url=target_url,
    )
    db.session.add(notification)
    return notification


def message_notification_details(conversation, message):
    actor_name = message.sender.display_name if message.sender else "A marketplace user"
    listing_title = conversation.listing.title

    if message.message_type == "offer":
        amount = money_label(message.offer.amount_cents) if message.offer else "an offer"
        if message.sender_id == conversation.buyer_id:
            return (
                "new_offer",
                "New offer received",
                f"{actor_name} offered {amount} for {listing_title}.",
            )
        return (
            "offer_update",
            "Offer update",
            f"{actor_name} updated an offer on {listing_title}.",
        )

    if message.message_type == "counter":
        amount = (
            money_label(message.offer.counter_amount_cents)
            if message.offer and message.offer.counter_amount_cents
            else "a counter offer"
        )
        return (
            "counter_offer",
            "Counter offer received",
            f"{actor_name} sent a counter offer of {amount} for {listing_title}.",
        )

    if message.message_type == "accepted":
        return (
            "offer_accepted",
            "Offer accepted",
            f"{actor_name} accepted the offer for {listing_title}.",
        )

    if message.message_type == "declined":
        return (
            "offer_declined",
            "Offer declined",
            f"{actor_name} declined the offer for {listing_title}.",
        )

    snippet = " ".join((message.body or "").split())
    if len(snippet) > 120:
        snippet = f"{snippet[:117]}..."
    return (
        "message",
        "New message",
        f"{actor_name}: {snippet}",
    )


def notify_conversation_recipient(conversation, message):
    if message.sender_id == conversation.buyer_id:
        recipient_id = conversation.seller_id
    else:
        recipient_id = conversation.buyer_id

    kind, title, body = message_notification_details(conversation, message)
    return create_notification(
        user_id=recipient_id,
        actor_id=message.sender_id,
        kind=kind,
        title=title,
        body=body,
        target_url=url_for("views.conversation_detail", conversation_id=conversation.id),
        listing_id=conversation.listing_id,
        offer_id=message.offer_id,
        conversation_id=conversation.id,
        message_id=message.id,
    )


def notify_item_sold(conversation, offer, transaction):
    return create_notification(
        user_id=offer.seller_id,
        actor_id=None,
        kind="item_sold",
        title="Item sold",
        body=f"{offer.listing.title} sold for {money_label(transaction.amount_cents)}.",
        target_url=url_for("views.receipt_detail", transaction_id=transaction.id),
        listing_id=offer.listing_id,
        offer_id=offer.id,
        conversation_id=conversation.id,
    )


def notify_buyer_deal_confirmed(conversation, offer, transaction):
    return create_notification(
        user_id=offer.buyer_id,
        actor_id=offer.seller_id,
        kind="offer_accepted",
        title="Offer accepted",
        body=(
            f"{offer.seller.display_name} accepted the offer for "
            f"{offer.listing.title}. Final price: {money_label(transaction.amount_cents)}."
        ),
        target_url=url_for("views.receipt_detail", transaction_id=transaction.id),
        listing_id=offer.listing_id,
        offer_id=offer.id,
        conversation_id=conversation.id,
    )


def receipt_text(transaction):
    lines = [
        "Canes Market Receipt",
        f"Receipt #: {transaction.id}",
        f"Date: {transaction.created_at.strftime('%B %d, %Y %I:%M %p')}",
        "",
        f"Item: {transaction.listing.title}",
        f"Seller: {transaction.seller.display_name}",
        f"Buyer: {transaction.buyer.display_name}",
        f"Original Price: {money_label(transaction.listing.price_cents)}",
        f"Final Price: {money_label(transaction.amount_cents)}",
        "Shipping Details: PENDING",
    ]
    return "\n".join(lines) + "\n"


def mark_notifications_read(user_id, conversation_id=None, notification_id=None):
    query = Notification.query.filter_by(user_id=user_id, read_at=None)
    if conversation_id is not None:
        query = query.filter_by(conversation_id=conversation_id)
    if notification_id is not None:
        query = query.filter_by(id=notification_id)

    notifications = query.all()
    if not notifications:
        return 0

    now = utc_now()
    for notification in notifications:
        notification.read_at = now
    return len(notifications)


def add_conversation_message(
    conversation,
    sender_id,
    body,
    message_type="text",
    offer_id=None,
    notify=True,
):
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
    db.session.flush()
    if notify:
        notify_conversation_recipient(conversation, message)
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


def request_listing_image_upload():
    image_file = request.files.get("image_file")
    if image_file is None or not image_file.filename:
        return None

    if image_file.mimetype not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Upload a JPG, PNG, GIF, or WebP image.")

    image_data = image_file.read()
    if not image_data:
        raise ValueError("Choose an image before publishing the listing.")
    if len(image_data) > MAX_LISTING_IMAGE_BYTES:
        raise ValueError("Listing image must be 3 MB or smaller.")

    return {
        "filename": secure_filename(image_file.filename) or "listing-image",
        "content_type": image_file.mimetype,
        "data": image_data,
    }


def decline_competing_offers(accepted_offer, now):
    competing_offers = Offer.query.filter(
        Offer.listing_id == accepted_offer.listing_id,
        Offer.id != accepted_offer.id,
        Offer.status.in_(("pending", "countered")),
    ).all()

    for competing_offer in competing_offers:
        competing_offer.status = "declined"
        competing_offer.seller_response = "Another offer was accepted for this listing."
        competing_offer.responded_at = now
        competing_conversation = find_or_create_conversation(
            listing=accepted_offer.listing,
            buyer_id=competing_offer.buyer_id,
            seller_id=competing_offer.seller_id,
        )
        competing_conversation.status = "closed"
        competing_conversation.deal_status = "declined"
        add_conversation_message(
            conversation=competing_conversation,
            sender_id=accepted_offer.seller_id,
            offer_id=competing_offer.id,
            body="Another offer was accepted for this listing.",
            message_type="declined",
        )


def finalize_accepted_offer(
    offer,
    conversation,
    accepted_amount_cents,
    sender_id,
    body,
    now,
    seller_response=None,
):
    buyer = offer.buyer
    seller = offer.seller
    if wallet_balance_cents(buyer) < accepted_amount_cents:
        raise ValueError(
            f"{buyer.display_name}'s wallet only has "
            f"{money_label(wallet_balance_cents(buyer))}, so this offer cannot be accepted."
        )

    buyer.wallet_balance_cents = wallet_balance_cents(buyer) - accepted_amount_cents
    seller.wallet_balance_cents = wallet_balance_cents(seller) + accepted_amount_cents

    offer.status = "accepted"
    if seller_response is not None:
        offer.seller_response = seller_response
    offer.responded_at = now
    offer.listing.status = "sold"
    offer.listing.updated_at = now
    conversation.deal_status = "accepted"
    add_conversation_message(
        conversation=conversation,
        sender_id=sender_id,
        offer_id=offer.id,
        body=body,
        message_type="accepted",
        notify=False,
    )
    wallet_transaction = WalletTransaction(
        offer_id=offer.id,
        listing_id=offer.listing_id,
        buyer_id=buyer.id,
        seller_id=seller.id,
        amount_cents=accepted_amount_cents,
        buyer_balance_after_cents=buyer.wallet_balance_cents,
        seller_balance_after_cents=seller.wallet_balance_cents,
    )
    db.session.add(wallet_transaction)
    db.session.flush()
    notify_item_sold(conversation, offer, wallet_transaction)
    if sender_id == offer.seller_id:
        notify_buyer_deal_confirmed(conversation, offer, wallet_transaction)
    decline_competing_offers(offer, now)
    return wallet_transaction


@views.route("/healthz")
def healthz():
    try:
        db.session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({"status": "error", "database": "unavailable"}), 503

    return jsonify({"status": "ok", "database": "ok"})


@views.route("/listings/<int:listing_id>/image")
def listing_image(listing_id):
    image = db.session.get(ListingImage, listing_id)
    if image is None:
        abort(404)

    response = Response(image.data, mimetype=image.content_type)
    response.cache_control.public = True
    response.cache_control.max_age = 3600
    return response


@views.route("/notifications")
@login_required
def notifications():
    user_notifications = (
        Notification.query.options(joinedload(Notification.actor))
        .filter_by(user_id=current_user.id)
        .order_by(
            case((Notification.read_at.is_(None), 0), else_=1),
            Notification.created_at.desc(),
        )
        .limit(50)
        .all()
    )

    unread_count = Notification.query.filter_by(
        user_id=current_user.id,
        read_at=None,
    ).count()

    if wants_json_response():
        return jsonify(
            {
                "unread_count": unread_count,
                "notifications": [
                    notification_payload(notification) for notification in user_notifications
                ],
            }
        )

    return render_template(
        "notifications.html",
        notifications=user_notifications,
        unread_count=unread_count,
    )


@views.route("/notifications/read", methods=["POST"])
@login_required
def mark_all_notifications_read():
    marked_read = mark_notifications_read(current_user.id)
    db.session.commit()

    if wants_json_response():
        return jsonify({"marked_read": marked_read})

    return redirect(url_for("views.notifications"))


@views.route("/notifications/<int:notification_id>/read", methods=["POST"])
@login_required
def mark_notification_read(notification_id):
    notification = db.get_or_404(Notification, notification_id)
    if notification.user_id != current_user.id:
        abort(403)

    if notification.read_at is None:
        notification.read_at = utc_now()
        marked_read = 1
    else:
        marked_read = 0
    db.session.commit()

    if wants_json_response():
        return jsonify({"marked_read": marked_read, "notification": notification_payload(notification)})

    return redirect(url_for("views.notifications"))


@views.route("/notifications/<int:notification_id>/open", methods=["POST"])
@login_required
def open_notification(notification_id):
    notification = db.get_or_404(Notification, notification_id)
    if notification.user_id != current_user.id:
        abort(403)

    if notification.read_at is None:
        notification.read_at = utc_now()
    target_url = notification.target_url or url_for("views.notifications")
    db.session.commit()
    return redirect(target_url)


@views.route("/conversations")
@login_required
def conversations():
    if request.accept_mimetypes.best_match(["application/json", "text/html"]) == "text/html":
        return render_template("conversations.html")

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
    if request.accept_mimetypes.best_match(["application/json", "text/html"]) == "text/html":
        get_accessible_conversation(conversation_id)
        return render_template("conversation_detail.html", conversation_id=conversation_id)

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

    mark_notifications_read(current_user.id, conversation_id=conversation.id)
    db.session.commit()

    return jsonify(
        {
            "marked_read": marked_read,
            "conversation": conversation_payload(conversation, include_messages=True),
        }
    )


@views.route("/receipts/<int:transaction_id>")
@login_required
def receipt_detail(transaction_id):
    transaction = get_accessible_wallet_transaction(transaction_id)
    return render_template(
        "receipt.html",
        transaction=transaction,
        is_buyer=transaction.buyer_id == current_user.id,
        shipping_status="PENDING",
    )


@views.route("/receipts/<int:transaction_id>/download")
@login_required
def download_receipt(transaction_id):
    transaction = get_accessible_wallet_transaction(transaction_id)
    filename = f"canes-market-receipt-{transaction.id}.txt"
    return Response(
        receipt_text(transaction),
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@views.route("/")
def home():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    sort = request.args.get("sort", "newest").strip()

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

    _sort_map = {
        "newest":     Listing.created_at.desc(),
        "oldest":     Listing.created_at.asc(),
        "price_asc":  Listing.price_cents.asc(),
        "price_desc": Listing.price_cents.desc(),
    }
    listings = listings_query.order_by(_sort_map.get(sort, Listing.created_at.desc())).all()

    new_listings = (
        Listing.query.options(joinedload(Listing.seller))
        .filter(Listing.status == "active")
        .order_by(Listing.created_at.desc())
        .limit(4)
        .all()
    )

    cheap_listings = (
        Listing.query.options(joinedload(Listing.seller))
        .filter(Listing.status == "active")
        .order_by(Listing.price_cents.asc())
        .limit(4)
        .all()
    )

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
        new_listings=new_listings,
        cheap_listings=cheap_listings,
        categories=LISTING_CATEGORIES,
        selected_category=category,
        search=search,
        sort=sort,
        recent_price_drops=recent_price_drops,
        featured_sellers=featured_sellers,
        stats=stats,
        now=utc_now(),
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

    try:
        uploaded_image = request_listing_image_upload()
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

    if uploaded_image:
        db.session.add(ListingImage(listing_id=listing.id, **uploaded_image))
        listing.image_url = url_for("views.listing_image", listing_id=listing.id)

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
        now=utc_now(),
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
    redirect_url = url_for("views.seller_dashboard")
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
        return action_error(
            "That offer has already been finalized.",
            redirect_url,
            status_code=409,
            category="info",
        )

    action = request.form.get("action", "").strip().lower()
    response_text = request.form.get("seller_response", "").strip()
    now = utc_now()
    response_extra = None
    conversation = find_or_create_conversation(
        listing=offer.listing,
        buyer_id=offer.buyer_id,
        seller_id=offer.seller_id,
    )

    if action == "accept":
        try:
            wallet_transaction = finalize_accepted_offer(
                offer=offer,
                conversation=conversation,
                accepted_amount_cents=offer.amount_cents,
                sender_id=current_user.id,
                body=response_text or f"Offer accepted at {money_label(offer.amount_cents)}.",
                now=now,
                seller_response=response_text or "Offer accepted.",
            )
        except ValueError as exc:
            return action_error(str(exc), redirect_url)

        success_message = "SOLD! Buyer wallet debited and seller wallet credited."
        success_category = "success"
        response_extra = {
            "celebration_message": "SOLD!",
            "receipt_url": url_for("views.receipt_detail", transaction_id=wallet_transaction.id),
        }

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
        success_message = "Offer declined."
        success_category = "info"

    elif action == "counter":
        try:
            counter_amount_cents = parse_price_to_cents(request.form.get("counter_amount"))
        except ValueError as exc:
            return action_error(str(exc), redirect_url)

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
        success_message = "Counter offer sent."
        success_category = "success"

    else:
        return action_error("Choose a valid offer action.", redirect_url)

    db.session.commit()
    return action_success(
        success_message,
        redirect_url,
        conversation=conversation,
        category=success_category,
        extra=response_extra,
    )


@views.route("/offers/<int:offer_id>/buyer-respond", methods=["POST"])
@login_required
def buyer_respond_to_offer(offer_id):
    offer = db.get_or_404(
        Offer,
        offer_id,
        options=[
            joinedload(Offer.listing),
            joinedload(Offer.buyer),
            joinedload(Offer.seller),
        ],
    )
    if offer.buyer_id != current_user.id:
        abort(403)

    redirect_url = next_url_or("views.activity")

    if offer.status != "countered":
        return action_error(
            "That counter offer is no longer active.",
            redirect_url,
            status_code=409,
            category="info",
        )

    if offer.listing.status != "active":
        return action_error(
            "This listing is no longer accepting negotiations.",
            redirect_url,
            status_code=409,
        )

    action = request.form.get("action", "").strip().lower()
    response_text = request.form.get("buyer_response", "").strip()
    now = utc_now()
    response_extra = None
    conversation = find_or_create_conversation(
        listing=offer.listing,
        buyer_id=offer.buyer_id,
        seller_id=offer.seller_id,
    )

    if action == "accept":
        if offer.counter_amount_cents is None:
            return action_error("There is no seller counter offer to accept.", redirect_url)

        accepted_amount_cents = offer.counter_amount_cents
        body = response_text or f"Counter accepted at {money_label(accepted_amount_cents)}."
        try:
            wallet_transaction = finalize_accepted_offer(
                offer=offer,
                conversation=conversation,
                accepted_amount_cents=accepted_amount_cents,
                sender_id=current_user.id,
                body=body,
                now=now,
            )
        except ValueError as exc:
            return action_error(str(exc), redirect_url)

        success_message = "Counter accepted. Your wallet was debited and the seller was credited."
        success_category = "success"
        receipt_url = url_for("views.receipt_detail", transaction_id=wallet_transaction.id)
        redirect_url = receipt_url
        response_extra = {
            "celebration_message": "CONGRATULATIONS WE HAVE A DEAL",
            "receipt_url": receipt_url,
            "redirect_url": receipt_url,
            "redirect_after_ms": 1800,
        }

    elif action == "counter":
        try:
            revised_amount_cents = parse_price_to_cents(request.form.get("amount"))
        except ValueError as exc:
            return action_error(str(exc), redirect_url)

        if revised_amount_cents > wallet_balance_cents(current_user):
            return action_error(
                "Your wallet balance is "
                f"{money_label(wallet_balance_cents(current_user))}; "
                "send a counter within your available app currency.",
                redirect_url,
            )

        offer.amount_cents = revised_amount_cents
        offer.counter_amount_cents = None
        offer.status = "pending"
        offer.seller_response = None
        offer.responded_at = None
        conversation.deal_status = "negotiating"

        counter_message = f"Revised offer: {money_label(revised_amount_cents)}"
        if response_text:
            counter_message = f"{counter_message}\n{response_text}"
        add_conversation_message(
            conversation=conversation,
            sender_id=current_user.id,
            offer_id=offer.id,
            body=counter_message,
            message_type="offer",
        )
        success_message = "Revised offer sent back to the seller."
        success_category = "success"

    elif action == "decline":
        offer.status = "declined"
        offer.seller_response = "Buyer declined the counter offer."
        offer.responded_at = now
        conversation.deal_status = "declined"
        add_conversation_message(
            conversation=conversation,
            sender_id=current_user.id,
            offer_id=offer.id,
            body=response_text or "Counter offer declined.",
            message_type="declined",
        )
        success_message = "Counter offer declined."
        success_category = "info"

    else:
        return action_error("Choose a valid counter response.", redirect_url)

    db.session.commit()
    return action_success(
        success_message,
        redirect_url,
        conversation=conversation,
        category=success_category,
        extra=response_extra,
    )


@views.route("/activity")
@views.route("/cart")
@login_required
def activity():
    offers_made = (
        Offer.query.options(
            joinedload(Offer.listing),
            joinedload(Offer.seller),
            selectinload(Offer.messages).joinedload(Message.sender),
        )
        .filter_by(buyer_id=current_user.id)
        .order_by(Offer.created_at.desc())
        .all()
    )

    offers_received = (
        Offer.query.options(
            joinedload(Offer.listing),
            joinedload(Offer.buyer),
            selectinload(Offer.messages).joinedload(Message.sender),
        )
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
