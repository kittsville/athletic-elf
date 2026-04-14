# Athletic Elf

[![Tests on main](https://github.com/kittsville/athletic-elf/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/kittsville/athletic-elf/actions/workflows/tests.yml?query=branch%3Amain)

A Flask app that receives [Strava webhook events](https://developers.strava.com/docs/webhooks/), fetches activity details via the Strava API, and stores them in a Postgres database. Athletes connect with [OAuth 2.0](https://developers.strava.com/docs/getting-started/#oauth); access and refresh tokens are stored per athlete.

## How it works

1. An athlete opens **`GET /oauth/start`** to authorize the app. Strava redirects back to **`GET /oauth/callback`** on your **`DOMAIN`** (must match your app’s [Authorization Callback Domain](https://www.strava.com/settings/api)). Tokens and profile are saved in the **`athlete`** table.
2. On the first successful registration for your Strava application, the app creates a [push subscription](https://developers.strava.com/docs/webhooks/) with callback URL **`{DOMAIN}/webhook`**. Strava allows **only one subscription per application**. Each event includes **`owner_id`**, which is stored on new **`activity`** rows.
3. For new activities, a row is inserted into **`activity`** with **`activity_id`** and **`owner_id`** as **`athlete_id`** (pending enrichment).
4. **`POST /cron`** (with **`Authorization: Bearer`** matching **`CRON_SECRET`**) responds immediately, then processes up to 50 rows still missing **`start_date`** in the background (session cleanup runs there too). On Heroku, use [Scheduler](https://devcenter.heroku.com/articles/scheduler) at **every 10 minutes** (its finest interval) with a **`curl`** that sends that header.
5. When an activity is deleted, the corresponding **`activity`** row is removed. Athlete deauthorization (`object_type: athlete`, `authorized: false`) removes the **`athlete`** row.

## Setup

### Prerequisites

- Python 3.10+
- Docker (for Postgres)

### Start the database

```bash
docker compose up -d
```

### Install dependencies

```bash
pip install -r requirements.txt
```

That file is **not** merged with anything else automatically. For optional dev tooling (e.g. [Ruff](https://docs.astral.sh/ruff/) to match CI formatting checks), install explicitly:

```bash
pip install -r requirements-dev.txt
```

### Environment variables

| Variable             | Required | Default                                              | Description |
|----------------------|----------|------------------------------------------------------|-------------|
| `CLIENT_ID`          | Yes*     | —                                                    | Strava application Client ID |
| `CLIENT_SECRET`      | Yes*     | —                                                    | Strava application secret |
| `DOMAIN`             | Yes*     | —                                                    | Public base URL for OAuth redirect and webhooks (e.g. `https://yourapp.example` or `http://127.0.0.1:5000`). A scheme is added if omitted (`https://`). |
| `SECRET_KEY`         | No       | `dev-change-me`                                      | Flask session secret for OAuth `state` (set in production) |
| `VERIFY_TOKEN`       | No       | `STRAVA`                                             | Must match the token used when creating the push subscription; Strava echoes it on webhook validation |
| `CRON_SECRET`        | No†      | —                                                    | If unset, **`POST /cron`** returns **503**. If set, callers must send **`Authorization: Bearer <CRON_SECRET>`**. |
| `DATABASE_URL`       | No       | `postgresql://strava:strava@localhost:5432/strava`    | Postgres connection string |
| `ACTIVITY_START_DATE` | No      | —                                                    | ISO 8601 competition start (e.g. `2025-06-01T00:00:00Z` or `2025-06-01` for midnight UTC). On **first** OAuth signup, the app backfills the athlete’s activities from Strava with `GET /athlete/activities?after=<epoch>` in a background thread. If unset or invalid, backfill is skipped. |
| `PORT`               | No       | `80`                                                 | Port the server listens on |

\*Required for OAuth and webhook registration; the app will return 500 from **`/oauth/start`** if they are missing.

†Set **`CRON_SECRET`** in production when using **`POST /cron`** (e.g. Heroku Scheduler job: `curl -fsS -X POST -H "Authorization: Bearer $CRON_SECRET" "$DOMAIN/cron"` with **`CRON_SECRET`** and **`DOMAIN`** configured for the app).

### Run the app

```bash
export CLIENT_ID=... CLIENT_SECRET=... DOMAIN=http://127.0.0.1:5000
python app.py
```

For local OAuth, use a **`DOMAIN`** Strava accepts (e.g. `localhost` or `127.0.0.1`) and register the same host in your Strava API application settings.

## Tests

Use a virtualenv and install dependencies so `flask_sqlalchemy` and the rest of **`requirements.txt`** are on the same interpreter you use to run tests:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

From the repository root, either run the helper (uses **`.venv`** automatically when it exists) or invoke unittest with the venv’s Python:

```bash
python3 run_tests.py
# equivalent when .venv is present:
.venv/bin/python -m unittest discover -s tests -v
```

This matches the [GitHub Actions workflow](.github/workflows/tests.yml) (CI installs **`requirements.txt`** then runs **`python -m unittest discover -s tests -v`**). The suite does not require Postgres or Strava credentials.

**Note:** `python3 -m unittest discover …` alone uses your system **`python3`**, which often has no project packages installed and can fail importing **`test_hub_department`** with **`ModuleNotFoundError`**. Prefer **`run_tests.py`** or **`.venv/bin/python`** after installing deps.

CI also runs **`ruff format --check .`** (see the **`format`** job in that workflow). To run the same check locally, install **`requirements-dev.txt`** and run **`ruff format --check .`**; use **`ruff format .`** to apply the project’s formatting.

## Endpoints

### `GET /oauth/start`

Starts the OAuth 2.0 flow: redirects the user to Strava to approve scopes (`read`, `activity:read`). After approval, Strava redirects to **`/oauth/callback`**.

### `GET /oauth/callback`

Exchanges the authorization code for tokens, upserts **`athlete`** ( **`athlete_id`**, **`firstname`**, **`lastname`**, tokens, **`expires_at`** ), and ensures a push subscription exists when possible.

### `GET /webhook`

[Webhook validation](https://developers.strava.com/docs/webhooks/#subscription-validation): echoes **`hub.challenge`** when **`hub.verify_token`** matches **`VERIFY_TOKEN`**.

### `POST /webhook`

Receives webhook events. **`owner_id`** in the JSON body identifies the athlete for new **`activity`** rows (activity **`create`** events without **`owner_id`** are ignored). Handles activity create/delete and athlete deauthorization.

### `POST /cron`

Requires **`Authorization: Bearer <CRON_SECRET>`** when **`CRON_SECRET`** is configured. Returns **200** with body **`Processing Started`** immediately, then enqueues maintenance (expired browser sessions removed; up to 50 pending **`activity`** rows enriched via Strava using stored tokens).
