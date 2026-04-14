"""Application configuration loaded from the environment."""

import logging
import os
from datetime import date, datetime, timedelta, timezone

_log = logging.getLogger(__name__)


def parse_app_developer_ids() -> frozenset[int]:
    raw = os.getenv("APP_DEVELOPER_IDS", "") or ""
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return frozenset(ids)


def parse_comma_options(raw: str | None, default: str) -> tuple[str, ...]:
    """Split a comma-separated list into non-empty stripped labels (for Hub/Department pickers)."""
    s = (raw if raw is not None else default).strip() or default
    return tuple(p.strip() for p in s.split(",") if p.strip())


def parse_activity_start_epoch(iso_value: str | None) -> int | None:
    """
    Competition start instant as Unix epoch seconds for Strava's `after` query param.

    Accepts ISO 8601 datetimes (with optional `Z`) or a date-only `YYYY-MM-DD`
    (interpreted as midnight UTC).
    """
    if not iso_value:
        return None
    raw = iso_value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            d = date.fromisoformat(raw)
        except ValueError:
            _log.warning(
                "Invalid ACTIVITY_START_DATE %r; skipping historical sync", raw
            )
            return None
        dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    else:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp())


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _secret_key_from_env() -> str:
    """Flask session signing key; must be a long random value from SECRET_KEY in production."""
    raw = os.environ.get("SECRET_KEY", "").strip()
    if raw:
        return raw
    if os.environ.get("FLASK_ENV") == "production":
        raise ValueError(
            "SECRET_KEY must be set in the environment for secure Flask sessions in production."
        )
    return "dev-change-me"


class Config:
    SECRET_KEY = _secret_key_from_env()

    # Branding (optional favicon URL; name defaults for page titles/headings).
    APP_NAME = os.getenv("APP_NAME", "Athletic Elf").strip() or "Athletic Elf"
    APP_FAVICON = os.getenv("APP_FAVICON", "").strip() or None
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql://strava:strava@localhost:5432/strava"
    ).replace("postgres://", "postgresql://")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # When true, create_app() calls db.create_all(). Disabled by default in production so
    # multiple booting instances do not race on DDL; use `flask init-db` or a release job.
    AUTO_CREATE_TABLES = _env_bool(
        "AUTO_CREATE_TABLES",
        default=os.environ.get("FLASK_ENV") != "production",
    )

    VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "STRAVA")
    # Shared secret for POST /cron (e.g. Heroku Scheduler: curl -H "Authorization: Bearer …").
    CRON_SECRET = (os.getenv("CRON_SECRET", "") or "").strip() or None
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")
    DOMAIN = os.getenv("DOMAIN")

    STRAVA_API_BASE = "https://www.strava.com/api/v3"
    STRAVA_OAUTH_AUTHORIZE = "https://www.strava.com/oauth/authorize"
    STRAVA_OAUTH_TOKEN = "https://www.strava.com/oauth/token"
    OAUTH_SCOPES = "read,activity:read,profile:read_all"

    SESSION_TTL = timedelta(hours=48)
    PERMANENT_SESSION_LIFETIME = SESSION_TTL

    # ISO 8601: competition start (e.g. 2025-06-01T00:00:00Z). Used for backfill `after`.
    ACTIVITY_START_DATE = os.environ.get("ACTIVITY_START_DATE", "").strip() or None
    # Strava allows up to 200 per page for GET /athlete/activities.
    STRAVA_ACTIVITIES_PAGE_SIZE = 200
