# Athletic Elf

[![Tests on main](https://github.com/kittsville/athletic-elf/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/kittsville/athletic-elf/actions/workflows/tests.yml?query=branch%3Amain)

A Flask app that receives [Strava webhook events](https://developers.strava.com/docs/webhooks/), fetches activity details via the Strava API, and stores them in a Postgres database. Athletes connect with [OAuth 2.0](https://developers.strava.com/docs/getting-started/#oauth); access and refresh tokens are stored per athlete.

## How it works

1. An athlete opens **`GET /oauth/start`** to authorize the app. Strava redirects back to **`GET /oauth/callback`** on your **`DOMAIN`** (must match your app’s [Authorization Callback Domain](https://www.strava.com/settings/api)). Tokens and profile are saved in the **`athelete`** table.
2. On the first successful registration for your Strava application, the app creates a [push subscription](https://developers.strava.com/docs/webhooks/) with callback URL **`{DOMAIN}/webhook`**. Strava allows **only one subscription per application**. Each event includes **`owner_id`**, which is stored on new **`activity`** rows.
3. For new activities, a row is inserted into **`activity`** with **`activity_id`** and **`owner_id`** as **`athlete_id`** (pending enrichment).
4. **`POST /cron`** processes up to 10 rows still missing **`start_date`**, looks up that athlete’s tokens in **`athelete`**, refreshes the access token if needed, then fetches and updates the activity.
5. When an activity is deleted, the corresponding **`activity`** row is removed. Athlete deauthorization (`object_type: athlete`, `authorized: false`) removes the **`athelete`** row.

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

### Environment variables

| Variable             | Required | Default                                              | Description |
|----------------------|----------|------------------------------------------------------|-------------|
| `CLIENT_ID`          | Yes*     | —                                                    | Strava application Client ID |
| `CLIENT_SECRET`      | Yes*     | —                                                    | Strava application secret |
| `DOMAIN`             | Yes*     | —                                                    | Public base URL for OAuth redirect and webhooks (e.g. `https://yourapp.example` or `http://127.0.0.1:5000`). A scheme is added if omitted (`https://`). |
| `SECRET_KEY`         | No       | `dev-change-me`                                      | Flask session secret for OAuth `state` (set in production) |
| `VERIFY_TOKEN`       | No       | `STRAVA`                                             | Must match the token used when creating the push subscription; Strava echoes it on webhook validation |
| `DATABASE_URL`       | No       | `postgresql://strava:strava@localhost:5432/strava`    | Postgres connection string |
| `PORT`               | No       | `80`                                                 | Port the server listens on |

\*Required for OAuth and webhook registration; the app will return 500 from **`/oauth/start`** if they are missing.

### Run the app

```bash
export CLIENT_ID=... CLIENT_SECRET=... DOMAIN=http://127.0.0.1:5000
python app.py
```

For local OAuth, use a **`DOMAIN`** Strava accepts (e.g. `localhost` or `127.0.0.1`) and register the same host in your Strava API application settings.

## Tests

From the repository root, with dependencies installed (`pip install -r requirements.txt`):

```bash
python -m unittest discover -s tests -v
```

This matches the [GitHub Actions workflow](.github/workflows/tests.yml). The suite does not require Postgres or Strava credentials.

## Endpoints

### `GET /oauth/start`

Starts the OAuth 2.0 flow: redirects the user to Strava to approve scopes (`read`, `activity:read`). After approval, Strava redirects to **`/oauth/callback`**.

### `GET /oauth/callback`

Exchanges the authorization code for tokens, upserts **`athelete`** ( **`athlete_id`**, **`firstname`**, **`lastname`**, tokens, **`expires_at`** ), and ensures a push subscription exists when possible.

### `GET /webhook`

[Webhook validation](https://developers.strava.com/docs/webhooks/#subscription-validation): echoes **`hub.challenge`** when **`hub.verify_token`** matches **`VERIFY_TOKEN`**.

### `POST /webhook`

Receives webhook events. **`owner_id`** in the JSON body identifies the athlete for new **`activity`** rows (activity **`create`** events without **`owner_id`** are ignored). Handles activity create/delete and athlete deauthorization.

### `POST /cron`

Processes up to 10 pending **`activity`** rows using the owning athlete’s stored credentials. Should be polled periodically when deployed.
