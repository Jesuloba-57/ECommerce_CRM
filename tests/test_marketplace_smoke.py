import os
import re
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from __init__ import create_app, db
from db_model import Conversation, Listing, Message, Offer, PriceHistory, User


CSRF_RE = re.compile(
    r'(?:name="_csrf_token" value="([^"]+)"|name="csrf-token" content="([^"]+)")'
)


class MarketplaceSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_database_url = os.environ.get("DATABASE_URL")
        self.previous_secret_key = os.environ.get("SECRET_KEY")

        os.environ["DATABASE_URL"] = f"sqlite:///{self.temp_dir.name}/test.db"
        os.environ["SECRET_KEY"] = "test-secret-key"

        self.app = create_app()
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()

        self._restore_env("DATABASE_URL", self.previous_database_url)
        self._restore_env("SECRET_KEY", self.previous_secret_key)
        self.temp_dir.cleanup()

    def _restore_env(self, key, previous_value):
        if previous_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous_value

    def _csrf_token(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        match = CSRF_RE.search(response.get_data(as_text=True))
        self.assertIsNotNone(match, f"No CSRF token found on {path}")
        return match.group(1) or match.group(2)

    def _login(self, email, password):
        token = self._csrf_token("/login")
        return self.client.post(
            "/login",
            data={
                "_csrf_token": token,
                "email": email,
                "password": password,
            },
            follow_redirects=True,
        )

    def _create_buyer(self, email="buyer@example.com"):
        with self.app.app_context():
            buyer = User(
                id=email.split("@")[0].replace(".", "-")[:15],
                email=email,
                password=generate_password_hash("password123"),
                status=True,
                first_name="Test",
                last_name="Buyer",
            )
            db.session.add(buyer)
            db.session.commit()
            return buyer.id

    def test_home_and_listing_detail_render_seeded_marketplace(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Used MacBook Air M1", response.data)

        response = self.client.get("/listings/1")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Price history", response.data)

    def test_missing_csrf_token_is_rejected_for_post_requests(self):
        response = self.client.post(
            "/login",
            data={"email": "demo-seller@canesmarket.local", "password": "marketplace123"},
        )
        self.assertEqual(response.status_code, 400)

    def test_signup_then_create_listing_records_initial_price_history(self):
        token = self._csrf_token("/signup")
        response = self.client.post(
            "/signup",
            data={
                "_csrf_token": token,
                "first_name": "Alex",
                "last_name": "Buyer",
                "email": "alex@example.com",
                "password1": "password123",
                "password2": "password123",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Seller dashboard", response.data)

        token = self._csrf_token("/seller")
        response = self.client.post(
            "/seller/listings",
            data={
                "_csrf_token": token,
                "title": "Graphing Calculator",
                "price": "84.50",
                "category": "Electronics",
                "condition": "Good",
                "location": "Library pickup",
                "image_url": "/static/images/webImage.jpeg",
                "description": "TI calculator with fresh batteries.",
                "note": "Initial test listing",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            listing = Listing.query.filter_by(title="Graphing Calculator").one()
            self.assertEqual(listing.price_cents, 8450)
            history = PriceHistory.query.filter_by(listing_id=listing.id).one()
            self.assertIsNone(history.old_price_cents)
            self.assertEqual(history.new_price_cents, 8450)

    def test_seller_can_update_price_and_history_is_saved(self):
        response = self._login("demo-seller@canesmarket.local", "marketplace123")
        self.assertEqual(response.status_code, 200)

        token = self._csrf_token("/seller")
        response = self.client.post(
            "/seller/listings/1/price",
            data={
                "_csrf_token": token,
                "price": "650.00",
                "note": "Spring semester discount",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            listing = db.session.get(Listing, 1)
            self.assertEqual(listing.price_cents, 65000)
            price_change = PriceHistory.query.filter_by(
                listing_id=1,
                old_price_cents=68000,
                new_price_cents=65000,
            ).one()
            self.assertEqual(price_change.note, "Spring semester discount")

    def test_buyer_offer_can_be_accepted_by_seller(self):
        self._create_buyer()

        response = self._login("buyer@example.com", "password123")
        self.assertEqual(response.status_code, 200)

        token = self._csrf_token("/listings/1")
        response = self.client.post(
            "/listings/1/offers",
            data={
                "_csrf_token": token,
                "amount": "640.00",
                "message": "I can pick it up today.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            offer_id = Offer.query.one().id

        self.client.get("/logout", follow_redirects=True)
        response = self._login("demo-seller@canesmarket.local", "marketplace123")
        self.assertEqual(response.status_code, 200)

        token = self._csrf_token("/seller")
        response = self.client.post(
            f"/offers/{offer_id}/respond",
            data={
                "_csrf_token": token,
                "action": "accept",
                "seller_response": "Deal.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            offer = db.session.get(Offer, offer_id)
            listing = db.session.get(Listing, 1)
            conversation = Conversation.query.one()
            message_types = [message.message_type for message in conversation.messages]
            self.assertEqual(offer.status, "accepted")
            self.assertEqual(offer.seller_response, "Deal.")
            self.assertEqual(listing.status, "sold")
            self.assertEqual(conversation.deal_status, "accepted")
            self.assertIn("offer", message_types)
            self.assertIn("accepted", message_types)

    def test_offer_starts_conversation_and_participants_can_chat(self):
        buyer_id = self._create_buyer("chat-buyer@example.com")

        response = self._login("chat-buyer@example.com", "password123")
        self.assertEqual(response.status_code, 200)

        token = self._csrf_token("/listings/1")
        response = self.client.post(
            "/listings/1/offers",
            data={
                "_csrf_token": token,
                "amount": "640.00",
                "message": "Could you meet near the library?",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            conversation = Conversation.query.one()
            conversation_id = conversation.id
            self.assertEqual(conversation.buyer_id, buyer_id)
            self.assertEqual(conversation.deal_status, "negotiating")
            self.assertEqual(len(conversation.messages), 1)
            self.assertEqual(conversation.messages[0].message_type, "offer")

        response = self.client.get("/conversations")
        self.assertEqual(response.status_code, 200)
        conversations = response.get_json()["conversations"]
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["id"], conversation_id)

        response = self.client.post(
            f"/conversations/{conversation_id}/messages",
            headers={"X-CSRFToken": token},
            json={"body": "I can pick it up this afternoon."},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["message"]["message_type"], "text")

        self.client.get("/logout", follow_redirects=True)
        response = self._login("demo-seller@canesmarket.local", "marketplace123")
        self.assertEqual(response.status_code, 200)

        response = self.client.get(f"/conversations/{conversation_id}")
        self.assertEqual(response.status_code, 200)
        conversation_payload = response.get_json()["conversation"]
        self.assertEqual(conversation_payload["unread_count"], 2)
        self.assertEqual(len(conversation_payload["messages"]), 2)

        token = self._csrf_token("/seller")
        response = self.client.post(
            f"/conversations/{conversation_id}/read",
            headers={"X-CSRFToken": token},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["marked_read"], 2)

        response = self.client.post(
            f"/conversations/{conversation_id}/messages",
            headers={"X-CSRFToken": token},
            json={"body": "That works. I will bring the charger too."},
        )
        self.assertEqual(response.status_code, 201)

        with self.app.app_context():
            conversation = db.session.get(Conversation, conversation_id)
            self.assertEqual(len(conversation.messages), 3)
            self.assertEqual(Message.query.filter_by(message_type="text").count(), 2)

    def test_conversations_are_private_to_the_buyer_and_seller(self):
        self._create_buyer("private-buyer@example.com")
        self._create_buyer("outsider@example.com")

        response = self._login("private-buyer@example.com", "password123")
        self.assertEqual(response.status_code, 200)

        token = self._csrf_token("/listings/1")
        response = self.client.post(
            "/listings/1/offers",
            data={
                "_csrf_token": token,
                "amount": "600.00",
                "message": "Starting the private chat.",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)

        with self.app.app_context():
            conversation_id = Conversation.query.one().id

        self.client.get("/logout", follow_redirects=True)
        response = self._login("outsider@example.com", "password123")
        self.assertEqual(response.status_code, 200)

        response = self.client.get(f"/conversations/{conversation_id}")
        self.assertEqual(response.status_code, 403)

        token = self._csrf_token("/")
        response = self.client.post(
            f"/conversations/{conversation_id}/messages",
            headers={"X-CSRFToken": token},
            json={"body": "I should not be able to join this."},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
