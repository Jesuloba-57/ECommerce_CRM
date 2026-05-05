# Canes Market

Canes Market is a Flask marketplace app for browsing campus-friendly listings, creating seller accounts, publishing items, tracking price history, sending or responding to offers, and settling completed purchases with an in-app wallet.

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
|-- app.py               # WSGI entry point for Render/Gunicorn
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

New accounts start with `$100.00` in app-only wallet currency. When a seller accepts a funded offer, the buyer's wallet is debited and the seller's wallet is credited.

## Environment Variables

These are optional for local development:

```bash
export SECRET_KEY="replace-with-a-local-secret"
export DATABASE_URL="sqlite:///database.db"
```

If `DATABASE_URL` is not set, the app defaults to a local SQLite database named `database.db`. With Flask-SQLAlchemy, the active local database is stored under the app instance folder, usually `instance/database.db`.

## Render Deployment

The project includes `.python-version` to pin Render to Python 3.13.

Render is configured to watch the `develop` branch for the live testing deployment.

Use these Render settings:

```text
Build Command: pip install -r requirements.txt
Start Command: gunicorn main:app
```

`gunicorn app:app` also works because `app.py` exposes the same Flask app for Render's default Flask quickstart command.

Production-style environment variables:

```text
DATABASE_URL=<Neon or Render Postgres connection string>
SECRET_KEY=<strong generated secret>
```

The deployed app exposes `/healthz` for a quick app/database readiness check.

Free-tier note: Render web services may sleep after inactivity, so open the site a few minutes before a live demo.

## CI/CD Workflow

The project uses GitHub Actions for CI and Render for deployment:

```text
feature/* branch -> PR into develop -> CI test job -> review approval -> auto-merge -> Render deploy
```

The `CI` workflow runs on pushes and pull requests to `develop` and `main`.

The `Auto Merge Approved Feature PRs` workflow enables GitHub auto-merge after a pull request is approved when all of these are true:

- The pull request targets `develop`.
- The source branch starts with `feature/`.
- The pull request is not a draft.
- GitHub branch protection still requires the `test` status check and review approval before merging.

Repository settings required:

```text
Settings > General > Pull Requests > Allow auto-merge
Settings > Branches > develop protection:
- Require a pull request before merging
- Require at least one approval
- Require status checks before merging
- Select the required status check named test
```

Do not use force-push or admin bypass for normal development. If CI or review requirements are not met, auto-merge should wait instead of overriding them.

## Live Smoke Test

After each deployment, verify:

- `/healthz` returns `{"status": "ok", "database": "ok"}`.
- A new user receives `$100.00` in app wallet balance.
- A buyer can make an offer within their wallet balance.
- The demo seller can accept the offer.
- The buyer wallet is debited and the seller wallet is credited.

## Useful Routes

- `/` - Browse marketplace listings
- `/healthz` - App and database health check
- `/about` - Project and marketplace feature overview
- `/signup` - Create an account
- `/login` - Log in
- `/seller` - Seller dashboard for creating listings, updating prices, and responding to offers
- `/activity` - Buyer and seller offer activity with wallet balance and completed transfers
- `/cart` - Alias for the activity page
- `/conversations` - JSON list of the signed-in user's buyer/seller chats
- `/conversations/<id>` - JSON detail for one chat, including messages
- `/conversations/<id>/messages` - POST a new chat message
- `/conversations/<id>/read` - POST to mark received messages as read

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

The tests use a temporary SQLite database and cover the main marketplace paths: browsing seeded listings, CSRF protection, signup wallet credit, listing creation, price history, funded offer submission, seller acceptance with wallet transfer, conversation creation, message sending, read receipts, and chat access control.
