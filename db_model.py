from datetime import datetime, timezone

from flask_login import UserMixin

from __init__ import db

INITIAL_WALLET_BALANCE_CENTS = 10000


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(db.Model, UserMixin):
    id = db.Column(db.String(15), nullable=False, primary_key=True)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password = db.Column(db.String(255), nullable=False)
    status = db.Column(db.Boolean, default=True)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    wallet_balance_cents = db.Column(
        db.Integer,
        nullable=False,
        default=INITIAL_WALLET_BALANCE_CENTS,
    )
    created_at = db.Column(db.DateTime, default=utc_now)

    listings = db.relationship(
        "Listing",
        back_populates="seller",
        cascade="all, delete-orphan",
        lazy=True,
    )
    offers_made = db.relationship(
        "Offer",
        back_populates="buyer",
        foreign_keys="Offer.buyer_id",
        lazy=True,
    )
    offers_received = db.relationship(
        "Offer",
        back_populates="seller",
        foreign_keys="Offer.seller_id",
        lazy=True,
    )
    conversations_as_buyer = db.relationship(
        "Conversation",
        back_populates="buyer",
        foreign_keys="Conversation.buyer_id",
        lazy=True,
    )
    conversations_as_seller = db.relationship(
        "Conversation",
        back_populates="seller",
        foreign_keys="Conversation.seller_id",
        lazy=True,
    )
    messages_sent = db.relationship(
        "Message",
        back_populates="sender",
        foreign_keys="Message.sender_id",
        lazy=True,
    )
    wallet_purchases = db.relationship(
        "WalletTransaction",
        back_populates="buyer",
        foreign_keys="WalletTransaction.buyer_id",
        lazy=True,
    )
    wallet_sales = db.relationship(
        "WalletTransaction",
        back_populates="seller",
        foreign_keys="WalletTransaction.seller_id",
        lazy=True,
    )
    notifications = db.relationship(
        "Notification",
        back_populates="user",
        foreign_keys="Notification.user_id",
        cascade="all, delete-orphan",
        lazy=True,
    )

    @property
    def display_name(self):
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        if self.first_name:
            return self.first_name
        return self.email.split("@")[0].replace(".", " ").title()


class Listing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    seller_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(140), nullable=False)
    description = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(80), nullable=False)
    condition = db.Column(db.String(80), nullable=False)
    location = db.Column(db.String(120), nullable=False)
    image_url = db.Column(db.String(255), nullable=False, default="/static/images/webImage.jpeg")
    price_cents = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    seller = db.relationship("User", back_populates="listings")
    price_history = db.relationship(
        "PriceHistory",
        back_populates="listing",
        order_by=lambda: PriceHistory.changed_at.desc(),
        cascade="all, delete-orphan",
        lazy=True,
    )
    offers = db.relationship(
        "Offer",
        back_populates="listing",
        order_by=lambda: Offer.created_at.desc(),
        cascade="all, delete-orphan",
        lazy=True,
    )
    conversations = db.relationship(
        "Conversation",
        back_populates="listing",
        cascade="all, delete-orphan",
        lazy=True,
    )
    uploaded_image = db.relationship(
        "ListingImage",
        back_populates="listing",
        cascade="all, delete-orphan",
        uselist=False,
        lazy=True,
    )


class ListingImage(db.Model):
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    listing = db.relationship("Listing", back_populates="uploaded_image")


class PriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False, index=True)
    changed_by_user_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False)
    old_price_cents = db.Column(db.Integer)
    new_price_cents = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(255))
    changed_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    listing = db.relationship("Listing", back_populates="price_history")
    changed_by = db.relationship("User")

    @property
    def is_price_drop(self):
        return self.old_price_cents is not None and self.new_price_cents < self.old_price_cents


class Offer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False, index=True)
    buyer_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False, index=True)
    seller_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False, index=True)
    amount_cents = db.Column(db.Integer, nullable=False)
    message = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    counter_amount_cents = db.Column(db.Integer)
    seller_response = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    responded_at = db.Column(db.DateTime)

    listing = db.relationship("Listing", back_populates="offers")
    buyer = db.relationship("User", back_populates="offers_made", foreign_keys=[buyer_id])
    seller = db.relationship("User", back_populates="offers_received", foreign_keys=[seller_id])
    messages = db.relationship("Message", back_populates="offer", lazy=True)


class Conversation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False, index=True)
    buyer_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False, index=True)
    seller_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    deal_status = db.Column(db.String(20), nullable=False, default="negotiating", index=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    last_message_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)

    __table_args__ = (
        db.UniqueConstraint(
            "listing_id",
            "buyer_id",
            "seller_id",
            name="uq_conversation_listing_buyer_seller",
        ),
    )

    listing = db.relationship("Listing", back_populates="conversations")
    buyer = db.relationship(
        "User",
        back_populates="conversations_as_buyer",
        foreign_keys=[buyer_id],
    )
    seller = db.relationship(
        "User",
        back_populates="conversations_as_seller",
        foreign_keys=[seller_id],
    )
    messages = db.relationship(
        "Message",
        back_populates="conversation",
        order_by=lambda: Message.created_at.asc(),
        cascade="all, delete-orphan",
        lazy=True,
    )


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer,
        db.ForeignKey("conversation.id"),
        nullable=False,
        index=True,
    )
    sender_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False, index=True)
    offer_id = db.Column(db.Integer, db.ForeignKey("offer.id"), index=True)
    body = db.Column(db.Text, nullable=False)
    message_type = db.Column(db.String(30), nullable=False, default="text", index=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)
    read_at = db.Column(db.DateTime)

    conversation = db.relationship("Conversation", back_populates="messages")
    sender = db.relationship(
        "User",
        back_populates="messages_sent",
        foreign_keys=[sender_id],
    )
    offer = db.relationship("Offer", back_populates="messages")


class WalletTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    offer_id = db.Column(db.Integer, db.ForeignKey("offer.id"), nullable=False, unique=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False, index=True)
    buyer_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False, index=True)
    seller_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False, index=True)
    amount_cents = db.Column(db.Integer, nullable=False)
    buyer_balance_after_cents = db.Column(db.Integer, nullable=False)
    seller_balance_after_cents = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False)

    offer = db.relationship("Offer")
    listing = db.relationship("Listing")
    buyer = db.relationship(
        "User",
        back_populates="wallet_purchases",
        foreign_keys=[buyer_id],
    )
    seller = db.relationship(
        "User",
        back_populates="wallet_sales",
        foreign_keys=[seller_id],
    )


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False, index=True)
    actor_id = db.Column(db.String(15), db.ForeignKey("user.id"), index=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), index=True)
    offer_id = db.Column(db.Integer, db.ForeignKey("offer.id"), index=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversation.id"), index=True)
    message_id = db.Column(db.Integer, db.ForeignKey("message.id"), index=True)
    kind = db.Column(db.String(40), nullable=False, index=True)
    title = db.Column(db.String(140), nullable=False)
    body = db.Column(db.Text)
    target_url = db.Column(db.String(255), nullable=False)
    read_at = db.Column(db.DateTime, index=True)
    created_at = db.Column(db.DateTime, default=utc_now, nullable=False, index=True)

    user = db.relationship("User", back_populates="notifications", foreign_keys=[user_id])
    actor = db.relationship("User", foreign_keys=[actor_id])
    listing = db.relationship("Listing")
    offer = db.relationship("Offer")
    conversation = db.relationship("Conversation")
    message = db.relationship("Message")
