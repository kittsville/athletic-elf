# Athletic Elf

[![Tests on main](https://github.com/kittsville/athletic-elf/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/kittsville/athletic-elf/actions/workflows/tests.yml?query=branch%3Amain)

A Flask app that receives [Strava webhook events](https://developers.strava.com/docs/webhooks/), fetches activity details via the Strava API, and stores them in a Postgres database. Athletes connect with [OAuth 2.0](https://developers.strava.com/docs/getting-started/#oauth); access and refresh tokens are stored per athlete.

## How it works

1. An athlete opens **`GET /oauth/start`** to authorize the app. Strava redirects back to **`GET /oauth/callback`** on your **`DOMAIN`** (must match your app’s [Authorization Callback Domain](https://www.strava.com/settings/api)). Tokens and profile are saved in the **`athlete`** table.
2. On the first successful registration for your Strava application, the app creates a [push subscription](https://developers.strava.com/docs/webhooks/) with callback URL **`{DOMAIN}/webhook/{VERIFY_TOKEN}`** (the token is URL-encoded in the path). Strava allows **only one subscription per application**. Each event includes **`owner_id`**, which is stored on new **`activity`** rows. **`VERIFY_TOKEN`** is required for the app to start (there is no default).
3. For new activities, a row is inserted into **`activity`** with **`activity_id`** and **`owner_id`** as **`athlete_id`** (pending enrichment).
4. **`POST /cron`** (with **`Authorization: Bearer`** matching **`CRON_SECRET`**) responds immediately, then processes up to 50 rows still missing **`start_date`** in the background (session cleanup runs there too). In production, schedule a job every ~2 minutes (e.g. Coolify scheduled task) that **`curl`**s that endpoint with the bearer header.
5. When an activity is deleted, the corresponding **`activity`** row is removed. Athlete deauthorization (`object_type: athlete`, `authorized: false`) removes the **`athlete`** row.

## Setup

### Prerequisites

- Python 3.10+
- Docker (for Postgres)

### Start the database

```bash
docker compose up -d
```

### Set up Virtual Environment

Don't go installing Python libraries to your system.

```bash
python3 -m venv .venv
```

Then every time you want to run the app make sure to enter the virtual environment

```bash
source .venv/bin/activate
```

### Install dependencies

```bash
pip3 install -r requirements.txt
```

That file is **not** merged with anything else automatically. For optional dev tooling (e.g. [Ruff](https://docs.astral.sh/ruff/) to match CI formatting checks), install explicitly:

```bash
pip3 install -r requirements-dev.txt
```

### Environment variables

Copy `.env.example` to `.env`. Install [direnv](https://direnv.net/) to avoid having to `source .env` every time you open a new shell.

Set up some of the values before starting the app. An explanation of their purpose:

| Variable             | Required | Default                                              | Description |
|----------------------|----------|------------------------------------------------------|-------------|
| `CLIENT_ID`          | Yes*     | —                                                    | Strava application Client ID |
| `CLIENT_SECRET`      | Yes*     | —                                                    | Strava application secret |
| `DOMAIN`             | Yes*     | —                                                    | Public base URL for OAuth redirect and webhooks (e.g. `https://yourapp.example` or `http://127.0.0.1:5000`). A scheme is added if omitted (`https://`). |
| `SECRET_KEY`         | No       | `dev-change-me`                                      | Flask session secret for OAuth `state` (set in production) |
| `ENFORCE_HTTPS`      | No       | `true` when **`FLASK_ENV=production`**, else `false` | When enabled: every request must be HTTPS ( **`403`** plain text if not); **`SESSION_COOKIE_SECURE`** is set to **`true`**. Behind a reverse proxy, ensure **`X-Forwarded-Proto: https`** is set. For local **`http://`** dev, set **`ENFORCE_HTTPS=0`**. |
| `BLOCK_SIGNUPS`      | No       | `false`                                              | When **`true`**, Strava OAuth rejects athletes who are not already in the database (**`403`** on **`/oauth/callback`**). Existing athletes can still sign in. |
| `VERIFY_TOKEN`       | Yes      | —                                                    | Long random secret: used in the push subscription **`verify_token`** field, echoed by Strava on validation **GET** as **`hub.verify_token`**, and embedded (URL-encoded) as the final path segment of the webhook URL so **`POST /webhook/...`** is not guessable |
| `CRON_SECRET`        | No†      | —                                                    | If unset, **`POST /cron`** returns **503**. If set, callers must send **`Authorization: Bearer <CRON_SECRET>`**. |
| `DATABASE_URL`       | No       | `postgresql://strava:strava@localhost:5432/strava`    | Postgres connection string |
| `COMPETITION_START_DATETIME` | Yes     | —                                                    | ISO 8601 competition start (e.g. `2025-06-01T00:00:00Z` or `2025-06-01` for midnight UTC). Required for the app to start (see `create_app`). On **first** OAuth signup, the app backfills the athlete’s activities from Strava with `GET /athlete/activities?after=<epoch>` in a background thread. |
| `COMPETITION_END_DATETIME`   | Yes     | —                                                    | ISO 8601 competition end (final scoring boundary). Required together with `COMPETITION_START_DATETIME` and optional `WEEK_BOUNDARIES` (see `create_app` validation). |
| `PORT`               | No       | `80`                                                 | Port the server listens on |

\*Required for OAuth and webhook registration; the app will return 500 from **`/oauth/start`** if they are missing.

†Set **`CRON_SECRET`** in production when using **`POST /cron`** (e.g. Coolify scheduled task: `curl -fsS -X POST -H "Authorization: Bearer $CRON_SECRET" "$DOMAIN/cron"` with **`CRON_SECRET`** and **`DOMAIN`** configured for the app).

The app process will not start unless **`VERIFY_TOKEN`** is set ( **`create_app`** raises **`ValueError`** if it is missing or blank).

The app always sets **`SESSION_COOKIE_SAMESITE = "Lax"`**. After loading config, **`create_app`** sets **`SESSION_COOKIE_SECURE`** from **`ENFORCE_HTTPS`** so session cookies are only sent over TLS when HTTPS is enforced.

If the public hostname is on Cloudflare, keep **SSL/TLS mode at Full (Strict)** when pointing DNS at Coolify (or any HTTPS origin). Flexible (HTTPS to visitors, HTTP to origin) causes a redirect loop with the proxy’s HTTPS redirect and with **`ENFORCE_HTTPS`**. Re-check that setting whenever you change the domain or DNS.

### ngrok

To run the app locally Strava needs a public URL to send the webhook events to. So install ngrok/tailscale/whatever:
```bash
brew install ngrok
```

Start ngrok:

```bash
ngrok http 80
```

Copy the public URL (e.g. `https://f744-45-148-12-62.ngrok-free.app`) and set that as the `DOMAIN` in your `.env` file. You'll also need the raw domain when creating an App on Strava (e.g. `f744-45-148-12-62.ngrok-free.app`)

### Set up a Strava App

You need to create a Strava App in order to get access to Strava data. [Create a Strava app](https://www.strava.com/settings/api) then copy the client ID and secret into your `.env` file. Set the _Authorization Callback Domain_ to your raw domain from ngrok (e.g. `f744-45-148-12-62.ngrok-free.app`). Every time you restart ngrok you'll need to copy the new domain into your `.env` file and update the _Authorization Callback Domain_ in your Strava app's settings.

### Run the app

Assuming you've already entered the virtual environment and in two separate tabs have Postgres + ngrok running:

```bash
python3 app.py
```

## Tests

Assuming you've already entered the virtual environment and installed dependencies:

```bash
python3 -m unittest discover -s tests -v
```

This matches the [GitHub Actions workflow](.github/workflows/tests.yml) (CI installs **`requirements.txt`** then runs **`python -m unittest discover -s tests -v`**). The suite does not require Postgres or Strava credentials.

**Note:** Using your system **`python3`** without installing **`requirements.txt`** into that interpreter often fails importing **`test_hub_department`** with **`ModuleNotFoundError`**. Use **`.venv/bin/python`** (or whichever environment has the deps) after **`pip install -r requirements.txt`**.

CI also runs **`ruff format --check .`** (see the **`format`** job in that workflow). To run the same check locally, install **`requirements-dev.txt`** and run **`ruff format --check .`**; use **`ruff format .`** to apply the project’s formatting.

## Endpoints

### `GET /oauth/start`

Starts the OAuth 2.0 flow: redirects the user to Strava to approve scopes (`read`, `activity:read_all`). After approval, Strava redirects to **`/oauth/callback`**.

### `GET /oauth/callback`

Exchanges the authorization code for tokens, upserts **`athlete`** ( **`athlete_id`**, **`firstname`**, **`lastname`**, tokens, **`expires_at`** ), and ensures a push subscription exists when possible.

### `GET /webhook/<VERIFY_TOKEN>`

[Webhook validation](https://developers.strava.com/docs/webhooks/#subscription-validation): the path segment must equal **`VERIFY_TOKEN`** (after URL decoding). Echoes **`hub.challenge`** as JSON when **`hub.mode`** is **`subscribe`** and **`hub.verify_token`** matches **`VERIFY_TOKEN`**. A bare **`GET /webhook`** is not registered.

### `POST /webhook/<VERIFY_TOKEN>`

Receives webhook events at the same path registered with Strava. **`owner_id`** in the JSON body identifies the athlete for new **`activity`** rows (activity **`create`** events without **`owner_id`** are ignored). Handles activity create/delete and athlete deauthorization.

### `POST /cron`

Requires **`Authorization: Bearer <CRON_SECRET>`** when **`CRON_SECRET`** is configured. Returns **200** with body **`Processing Started`** immediately, then enqueues maintenance (expired browser sessions removed; up to 50 pending **`activity`** rows enriched via Strava using stored tokens).
