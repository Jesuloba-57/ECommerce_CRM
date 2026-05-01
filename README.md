# Canes Market

Canes Market is a Flask marketplace app for browsing campus-friendly listings, creating seller accounts, publishing items, tracking price history, and sending or responding to offers.

## Tech Stack

- Python 3
- Flask
- Flask-SQLAlchemy
- Flask-Login
- SQLite for local development

## Project Structure

```text
.
|-- main.py              # Local app entry point
|-- __init__.py          # Flask app factory, database setup, seed data
|-- db_model.py          # SQLAlchemy models
|-- views.py             # Marketplace routes
|-- authorization.py     # Login, signup, logout routes
|-- requirements.txt     # Python dependencies
|-- tests/               # Smoke tests for core workflows
|-- templates/           # Jinja templates
|-- static/              # CSS and images
`-- instance/            # Local SQLite database location
```

## Run Locally

From the project root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

Open the app at:

```text
http://127.0.0.1:5001
```

The app runs in Flask debug mode when started through `main.py`.

## Demo Login

On first startup, the app creates the local database tables and seeds demo marketplace data if there are no listings yet.

You can log in with the seeded seller account:

```text
Email: demo-seller@canesmarket.local
Password: marketplace123
```

You can also create a new account from `/signup`.

## Environment Variables

These are optional for local development:

```bash
export SECRET_KEY="replace-with-a-local-secret"
export DATABASE_URL="sqlite:///database.db"
```

If `DATABASE_URL` is not set, the app defaults to a local SQLite database named `database.db`. With Flask-SQLAlchemy, the active local database is stored under the app instance folder, usually `instance/database.db`.

## Useful Routes

- `/` - Browse marketplace listings
- `/about` - Project and marketplace feature overview
- `/signup` - Create an account
- `/login` - Log in
- `/seller` - Seller dashboard for creating listings, updating prices, and responding to offers
- `/activity` - Buyer and seller offer activity
- `/cart` - Alias for the activity page

## Reset Local Data

To start with a fresh local database, stop the Flask server and remove the SQLite database in the `instance` folder:

```bash
rm instance/database.db
```

Then start the app again:

```bash
python main.py
```

The database tables and demo listings will be recreated automatically.

Local databases, virtual environments, Python cache files, and macOS `.DS_Store` files are ignored by Git.

## Troubleshooting

If `http://127.0.0.1:5001` does not load, confirm the server is still running and that port `5001` is not already in use.

If imports fail, make sure your virtual environment is active and dependencies were installed with:

```bash
python -m pip install -r requirements.txt
```

If database state looks stale, reset `instance/database.db` and restart the app.

## Tests

Run the smoke test suite with:

```bash
python -m unittest discover
```

The tests use a temporary SQLite database and cover the main marketplace paths: browsing seeded listings, CSRF protection, signup, listing creation, price history, offer submission, and seller acceptance.
