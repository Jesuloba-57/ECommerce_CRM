import hmac
import os
from secrets import token_urlsafe

import shortuuid
from flask import Flask, abort, request, session
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash

db = SQLAlchemy()
DB_NAME = "database.db"
CSRF_SESSION_KEY = "_csrf_token"


def create_app():
    app = Flask(__name__)
    database_url = os.environ.get("DATABASE_URL", f"sqlite:///{DB_NAME}")
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-marketplace-secret")
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    db.init_app(app)

    from authorization import auth
    from views import views
    from db_model import User

    app.register_blueprint(views)
    app.register_blueprint(auth)

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"
    login_manager.login_message_category = "info"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, user_id)

    app.jinja_env.filters["money"] = format_money
    app.jinja_env.filters["datetime"] = format_datetime
    app.jinja_env.globals["csrf_token"] = generate_csrf_token
    app.before_request(validate_csrf_token)

    with app.app_context():
        create_database()
        seed_marketplace_data()

    return app


def create_database():
    db.create_all()
    ensure_user_columns()


def ensure_user_columns():
    from db_model import INITIAL_WALLET_BALANCE_CENTS

    from db_model import INITIAL_WALLET_BALANCE_CENTS

    inspector = inspect(db.engine)
    if "user" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("user")}
    preparer = db.engine.dialect.identifier_preparer
    user_table = preparer.quote("user")
    wallet_column = preparer.quote("wallet_balance_cents")

    def column_definition(name, column_type, suffix=""):
        column_name = preparer.quote(name)
        type_sql = column_type.compile(dialect=db.engine.dialect)
        return f"{column_name} {type_sql}{suffix}"

    statements = []

    if "first_name" not in existing_columns:
        statements.append(
            f"ALTER TABLE {user_table} ADD COLUMN "
            f"{column_definition('first_name', db.String(100))}"
        )
    if "last_name" not in existing_columns:
        statements.append(
            f"ALTER TABLE {user_table} ADD COLUMN "
            f"{column_definition('last_name', db.String(100))}"
        )
    if "wallet_balance_cents" not in existing_columns:
        wallet_suffix = f" NOT NULL DEFAULT {INITIAL_WALLET_BALANCE_CENTS}"
        statements.append(
            f"ALTER TABLE {user_table} ADD COLUMN "
            f"{column_definition('wallet_balance_cents', db.Integer(), wallet_suffix)}"
        )
    if "created_at" not in existing_columns:
        statements.append(
            f"ALTER TABLE {user_table} ADD COLUMN "
            f"{column_definition('created_at', db.DateTime())}"
        )

    with db.engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        if "wallet_balance_cents" in existing_columns:
            connection.execute(
                text(
                    f"UPDATE {user_table} SET {wallet_column} = :initial_balance "
                    f"WHERE {wallet_column} IS NULL"
                ),
                {"initial_balance": INITIAL_WALLET_BALANCE_CENTS},
            )


def seed_marketplace_data():
    from db_model import INITIAL_WALLET_BALANCE_CENTS, Listing, PriceHistory, User, utc_now

    if Listing.query.count() > 0:
        return

    seller = User.query.order_by(User.email.asc()).first()
    if seller is None:
        seller = User(
            id=shortuuid.ShortUUID().random(length=15),
            email="demo-seller@canesmarket.local",
            password=generate_password_hash("marketplace123"),
            status=True,
            first_name="Campus",
            last_name="Seller",
            wallet_balance_cents=INITIAL_WALLET_BALANCE_CENTS,
            created_at=utc_now(),
        )
        db.session.add(seller)
        db.session.commit()

    if not seller.first_name:
        seller.first_name = "Campus"
    if not seller.last_name:
        seller.last_name = "Seller"
    if seller.created_at is None:
        seller.created_at = utc_now()
    if seller.wallet_balance_cents is None:
        seller.wallet_balance_cents = INITIAL_WALLET_BALANCE_CENTS
    if seller.wallet_balance_cents is None:
        seller.wallet_balance_cents = INITIAL_WALLET_BALANCE_CENTS

    sample_listings = [
        {
            "title": "Used MacBook Air M1",
            "description": "Lightweight laptop with a fresh battery and charger included. Great for classes and side projects.",
            "category": "Electronics",
            "condition": "Good",
            "location": "On-campus pickup",
            "price_cents": 68000,
            "image_url": "/static/images/laptop2.webp",
        },
        {
            "title": "Adidas Running Shoes",
            "description": "Comfortable daily trainers with minimal wear. Perfect for gym sessions or walking to class.",
            "category": "Sports",
            "condition": "Like new",
            "location": "Miami Gardens",
            "price_cents": 5200,
            "image_url": "/static/images/shoe1.jpg",
        },
        {
            "title": "Classic Denim Jacket",
            "description": "A versatile jacket that layers well and still has a clean look. Seller can negotiate on price.",
            "category": "Fashion",
            "condition": "Good",
            "location": "North Miami",
            "price_cents": 3400,
            "image_url": "/static/images/jean7.jpg",
        },
        {
            "title": "Smart Band 4",
            "description": "Tracks steps, heart rate, and workouts. Comes with the original charger and an extra band.",
            "category": "Electronics",
            "condition": "New",
            "location": "Shipping available",
            "price_cents": 4900,
            "image_url": "/static/images/smartband.png",
        },
    ]

    for sample in sample_listings:
        listing = Listing(seller_id=seller.id, **sample)
        db.session.add(listing)
        db.session.flush()
        db.session.add(
            PriceHistory(
                listing_id=listing.id,
                changed_by_user_id=seller.id,
                old_price_cents=None,
                new_price_cents=listing.price_cents,
                note="Initial list price",
            )
        )

    db.session.commit()


def format_money(value):
    if value is None:
        return "N/A"
    return f"${value / 100:,.2f}"


def format_datetime(value):
    if value is None:
        return "Not yet"
    return value.strftime("%b %d, %Y %I:%M %p")


def generate_csrf_token():
    token = session.get(CSRF_SESSION_KEY)
    if token is None:
        token = token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token():
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return

    expected_token = session.get(CSRF_SESSION_KEY)
    submitted_token = (
        request.form.get("_csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
    )

    if (
        not expected_token
        or not submitted_token
        or not hmac.compare_digest(expected_token, submitted_token)
    ):
        abort(400, description="Invalid or missing CSRF token.")
