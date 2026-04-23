# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Verify before finishing

CI (`.github/workflows/tests.yml`) runs on Python 3.12 and gates PRs on two jobs. Match both locally **before** completing any change:

```bash
# test job
pip install -r requirements.txt
python3 -m unittest discover -s tests -v

# format job
pip install -r requirements-dev.txt
ruff format --check .        # to apply: ruff format .
```

If you use a venv at `.venv`, call `.venv/bin/python` so imports resolve against the interpreter where you installed deps. The system `python3` without `pip install -r requirements.txt` into it commonly fails with `ModuleNotFoundError` on `test_hub_department`.

Run a single test file / case:
```bash
python3 -m unittest tests.test_points -v
python3 -m unittest tests.test_cron.TestCronEndpoint.test_returns_403_for_wrong_secret -v
```

## Running the app locally

Postgres comes from `docker-compose.yml` (`docker compose up -d`). Strava needs a public URL for webhooks, so run `ngrok http 80` and set the ngrok URL as `DOMAIN` in `.env` (and as the Authorization Callback Domain on the Strava app). Then `python3 app.py`. See `README.md` for the full env-var table; the app **refuses to start** if `VERIFY_TOKEN`, `COMPETITION_START_DATETIME`, or `COMPETITION_END_DATETIME` are missing/invalid (`create_app` raises `ValueError`).

## Architecture

### Flask application factory (`athletic_elf/factory.py`)
`create_app` validates config strictly at startup (VERIFY_TOKEN non-empty; competition schedule produces ≥1 scoring period), mutates config keys in place (e.g. `COMPETITION_START_DATETIME` string → aware UTC `datetime`), and registers four blueprints: `main`, `cron`, `oauth`, `webhook`. Two `before_request` hooks do (a) HTTPS enforcement when `ENFORCE_HTTPS` (trusts `X-Forwarded-Proto`) and (b) browser-session lookup that populates `g.current_athlete` and redirects to `/` for endpoints in `endpoints_requiring_session`. Webhook and cron endpoints are listed in `endpoints_skip_session_lookup` so they never trigger session DB reads. A Flask CLI command `flask --app app init-db` creates tables (Procfile `release`), since `AUTO_CREATE_TABLES` defaults to `False` in production to avoid multiple workers racing on DDL.

### Strava integration
- **OAuth** (`blueprints/oauth.py`): `/oauth/start` → Strava → `/oauth/callback` stores tokens in `athlete`, creates a hashed `BrowserSession` row, then calls `ensure_push_subscription()` and — for first-time registrations — `schedule_initial_activity_sync` which spawns a **daemon thread** (in-process; see `background.py`). Replace with a queue for horizontal scale.
- **Webhook** (`blueprints/webhook.py`): the Strava push subscription callback URL embeds `VERIFY_TOKEN` (URL-encoded) as the last path segment, so `POST /webhook/<token>` is not guessable. `secrets.compare_digest` is used for both path and `hub.verify_token` comparison. Strava allows **one subscription per application** — `ensure_push_subscription` reconciles an existing mismatched callback by deleting and recreating.
- **Activity enrichment** (`strava_service.py`): webhook `create` events insert a stub `Activity` (activity_id + owner_id only). `POST /cron` runs `process_activities(50)` which fetches SummaryActivity JSON per pending row. `sync_activities_since_competition_start` paginates `GET /athlete/activities?after=<epoch>` for first-time registrations. Both call `maybe_refresh_athlete_token` when an access token is within 5 min of expiry.

### Scoring (`points.py` — top-level module, not inside the package)
`points.py` imports as `from points import activities_total_points, team_points, discipline_totals_for_activities`. Scoring rules (thresholds, daily easy-fitness cap of 5, per-sport type sets) live there; don't duplicate them. `team_points(per_athlete_points)` computes a hub/department's score as the mean of the top `ceil(0.8 * n)` scorers (0 if fewer than 5 active members).

### Competition periods (`athletic_elf/competition_periods.py`)
`COMPETITION_START_DATETIME`, optional `WEEK_BOUNDARIES` (comma-separated ISO instants), and `COMPETITION_END_DATETIME` define a sequence of `PeriodSpec`s (one per end instant strictly after start). Period 0's eligibility lower bound equals the competition start; later periods back off by `GRACE_AFTER_PREVIOUS_BOUNDARY = 12 hours` so late-logged activities still count in their intended period. When a period's `end_exclusive` is reached, `summarize_next_due_period` creates a `Week` row plus per-hub/per-department `WeekScore` rows and attributes each `Activity.week_id`. `summarize_due_periods_loop` runs inside `/cron` after activity enrichment.

**Team totals on `/results`** merge two sources: frozen `WeekScore` rows for closed periods (`aggregates_frozen_team_scores`) + live `team_points` over the open period's unattributed activities. When editing scoring or the results page, preserve this split — never double-count.

### Data model (`athletic_elf/models.py`)
Six tables: `athlete` (Strava tokens + hub/department + is_organiser/is_active flags), `activity` (unique `activity_id`, nullable Strava fields until enriched, `week_id` FK once attributed), `week` / `week_score` (frozen period results), `session` (SHA-256 hash of the cookie secret + TTL), `bonus` (manual organiser-awarded points targeting a hub or department). Naive `datetime` columns (activity `start_date`) are treated as UTC throughout.

### Authorization
Three roles derived at request time, not stored as columns:
- **App developer** — Strava ID in `APP_DEVELOPER_IDS` env var.
- **Organiser** — `athlete.is_organiser = True`.
- **Participant** — everyone else (or `Inactive Participant` when `is_active = False`).

`_can_perform_organiser_tasks` (in `blueprints/main.py`) = organiser OR app developer. Guard all `/organiser/...`, `/bonuses`, `/athletes/...` management endpoints with this check — participants get `403`.

## Testing conventions

Tests live under `tests/` and use `unittest`. A test's `Config` subclass pins `SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"`, `AUTO_CREATE_TABLES = True`, explicit `HUB_OPTIONS` / `DEPARTMENT_OPTIONS` tuples, and narrow competition bounds (e.g. `2020-01-01` → `2030-01-01`). Reuse `_TestHubDeptConfig` from `tests/test_hub_department.py` rather than re-deriving one. For code that spawns a daemon thread (`/cron`, initial activity sync), patch `threading.Thread` with an immediate-run shim like `_ImmediateThread` in `tests/test_cron.py` so assertions can observe side effects synchronously.

## Formatting

Ruff target is `py310` with 88-column line length (`pyproject.toml`). CI fails on `ruff format --check .` — run `ruff format .` after any Python edit.
