from datetime import datetime

from flask_login import UserMixin

from __init__ import db


class User(db.Model, UserMixin):
    id = db.Column(db.String(15), nullable=False, primary_key=True)
    email = db.Column(db.String(150), nullable=False, unique=True)
    password = db.Column(db.String(255), nullable=False)
    status = db.Column(db.Boolean, default=True)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    profile_image_url = db.Column(db.String(255))
    wallet_balance_cents = db.Column(db.Integer, nullable=False, default=0)

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
    buyer_profile = db.relationship(
        "BuyerProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    seller_profile = db.relationship(
        "SellerProfile",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    @property
    def display_name(self):
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        if self.first_name:
            return self.first_name
        return self.email.split("@")[0].replace(".", " ").title()

    @property
    def is_buyer(self):
        return self.buyer_profile is not None

    @property
    def is_seller(self):
        return self.seller_profile is not None


class BuyerProfile(db.Model):
    user_id = db.Column(db.String(15), db.ForeignKey("user.id"), primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="buyer_profile")


class SellerProfile(db.Model):
    user_id = db.Column(db.String(15), db.ForeignKey("user.id"), primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", back_populates="seller_profile")


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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
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


class PriceHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id"), nullable=False, index=True)
    changed_by_user_id = db.Column(db.String(15), db.ForeignKey("user.id"), nullable=False)
    old_price_cents = db.Column(db.Integer)
    new_price_cents = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(255))
    changed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

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
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    responded_at = db.Column(db.DateTime)

    listing = db.relationship("Listing", back_populates="offers")
    buyer = db.relationship("User", back_populates="offers_made", foreign_keys=[buyer_id])
    seller = db.relationship("User", back_populates="offers_received", foreign_keys=[seller_id])
