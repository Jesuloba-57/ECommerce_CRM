# Chat Frontend Integration Guide

## Feature Goal

When a buyer makes an offer on a listing, the backend automatically creates or reuses a conversation between that buyer and the seller. The frontend should display a chatbox where both users can exchange messages tied to that listing and offer.

## Auth

All chat routes require the user to be logged in.

Only the buyer or seller attached to a conversation can access that conversation. Other users receive a `403` response.

## CSRF

Every `POST` request must include the CSRF token.

The token is available in the base page:

```html
<meta name="csrf-token" content="...">
```

For JSON requests, send it as:

```http
X-CSRFToken: <token>
```

## Endpoints

### List Conversations

```http
GET /conversations
```

Returns all conversations for the signed-in user.

Example response:

```json
{
  "conversations": [
    {
      "id": 1,
      "status": "active",
      "deal_status": "negotiating",
      "listing": {
        "id": 1,
        "title": "Used MacBook Air M1",
        "status": "active",
        "price": "$680.00",
        "image_url": "/static/images/laptop2.webp"
      },
      "buyer": {
        "id": "buyer-id",
        "display_name": "Test Buyer",
        "email": "buyer@example.com"
      },
      "seller": {
        "id": "seller-id",
        "display_name": "Campus Seller",
        "email": "demo-seller@canesmarket.local"
      },
      "unread_count": 2,
      "latest_message": {
        "id": 3,
        "body": "I can pick it up this afternoon.",
        "message_type": "text"
      },
      "last_message_at": "2026-05-01T12:00:00"
    }
  ]
}
```

### Get Conversation Detail

```http
GET /conversations/<conversation_id>
```

Returns one conversation, including all messages.

Example response:

```json
{
  "conversation": {
    "id": 1,
    "status": "active",
    "deal_status": "negotiating",
    "messages": [
      {
        "id": 1,
        "conversation_id": 1,
        "sender_id": "buyer-id",
        "sender": {
          "id": "buyer-id",
          "display_name": "Test Buyer"
        },
        "offer_id": 3,
        "body": "Offer sent: $640.00\nCan you meet near the library?",
        "message_type": "offer",
        "created_at": "2026-05-01T12:00:00",
        "read_at": null,
        "offer": {
          "id": 3,
          "amount_cents": 64000,
          "amount": "$640.00",
          "status": "pending",
          "counter_amount": null
        }
      }
    ]
  }
}
```

### Send Message

```http
POST /conversations/<conversation_id>/messages
```

Sends a chat message.

Headers:

```http
Content-Type: application/json
X-CSRFToken: <token>
```

Request body:

```json
{
  "body": "I can pick it up this afternoon."
}
```

Response status: `201`

Example response:

```json
{
  "message": {
    "id": 2,
    "body": "I can pick it up this afternoon.",
    "message_type": "text"
  },
  "conversation": {
    "id": 1,
    "latest_message": {
      "id": 2,
      "body": "I can pick it up this afternoon.",
      "message_type": "text"
    }
  }
}
```

### Mark Conversation Read

```http
POST /conversations/<conversation_id>/read
```

Marks all messages from the other participant as read.

Headers:

```http
X-CSRFToken: <token>
```

Example response:

```json
{
  "marked_read": 2,
  "conversation": {
    "id": 1,
    "unread_count": 0,
    "messages": []
  }
}
```

## Message Types

The UI should handle these `message_type` values:

```text
text
offer
counter
accepted
declined
```

Suggested rendering:

- `text`: normal chat bubble
- `offer`: buyer offer card/message
- `counter`: seller counteroffer card
- `accepted`: success/deal message
- `declined`: muted or closed negotiation message

## Conversation State

Use `conversation.status` to decide whether users can keep messaging:

```text
active
closed
```

Use `conversation.deal_status` to show negotiation state:

```text
negotiating
accepted
declined
```

The frontend should disable message sending if:

```js
conversation.status !== "active"
```

## Recommended UI Behavior

- After a buyer submits an offer, refresh or open conversations and show the created chat.
- Poll `GET /conversations/<conversation_id>` every few seconds for now.
- Call `POST /conversations/<conversation_id>/read` when a user opens or views a conversation.
- Use `unread_count` in the conversation list.
- Render offer/counter/accepted/declined messages differently from normal chat text.
- Keep the chat tied to the listing, using the included `listing` payload for title, image, price, and status.

## Current Limitation

The existing offer form route still redirects to the listing page after submission. It does not yet return the `conversation_id` directly.

For the first frontend pass, after offer submission:

1. Call `GET /conversations`.
2. Find the newest conversation for the listing.
3. Open that conversation in the chatbox.

If needed later, the backend can be extended with a JSON offer endpoint that returns the created `conversation_id` immediately.
