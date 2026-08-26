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


def parse_oauth_scopes_csv(raw: str | None) -> frozenset[str]:
    """Comma-separated Strava OAuth scopes (authorize `scope` param or callback `scope`)."""
    if not raw:
        return frozenset()
    return frozenset(p.strip() for p in raw.split(",") if p.strip())


def parse_datetime_utc(iso_value: str | None) -> datetime | None:
    """
    Parse ISO 8601 datetime or date-only string into an aware UTC datetime.

    Returns None when unset or invalid (warnings logged for invalid input).
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
            _log.warning("Invalid datetime %r; treating as unset", raw)
            return None
        dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    else:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    return dt


def parse_competition_start_epoch(iso_value: str | None) -> int | None:
    """
    Competition start instant as Unix epoch seconds for Strava's `after` query param.

    Accepts ISO 8601 datetimes (with optional `Z`) or a date-only `YYYY-MM-DD`
    (interpreted as midnight UTC).
    """
    dt = parse_datetime_utc(iso_value)
    if dt is None:
        if iso_value and str(iso_value).strip():
            _log.warning(
                "Invalid COMPETITION_START_DATETIME %r; skipping historical sync",
                iso_value,
            )
        return None
    return int(dt.timestamp())


def parse_week_boundary_datetimes(raw: str | None) -> tuple[datetime, ...]:
    """Comma-separated ISO timestamps (same rules as COMPETITION_START_DATETIME), sorted unique."""
    if not raw or not str(raw).strip():
        return ()
    out: list[datetime] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        dt = parse_datetime_utc(part)
        if dt is not None:
            out.append(dt)
    out.sort()
    # de-dupe while preserving order
    seen: set[datetime] = set()
    unique: list[datetime] = []
    for dt in out:
        if dt not in seen:
            seen.add(dt)
            unique.append(dt)
    return tuple(unique)


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
    # Coolify SOURCE_COMMIT (optional); shown in site footer when set.
    BUILD_COMMIT = os.getenv("SOURCE_COMMIT", "").strip() or None
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

    # Strava subscription verify_token (GET query) and secret webhook path segment.
    # create_app() refuses to start if this is empty after loading config.
    VERIFY_TOKEN = (os.getenv("VERIFY_TOKEN", "") or "").strip()
    # Shared secret for POST /cron (e.g. Coolify scheduled task: curl -H "Authorization: Bearer …").
    CRON_SECRET = (os.getenv("CRON_SECRET", "") or "").strip() or None
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")
    # Public base URL for OAuth + webhooks. Locally set in .env (e.g. ngrok);
    # on Coolify prefer DOMAIN=$COOLIFY_URL (must resolve to a single URL).
    DOMAIN = os.getenv("DOMAIN")

    STRAVA_API_BASE = "https://www.strava.com/api/v3"
    STRAVA_OAUTH_AUTHORIZE = "https://www.strava.com/oauth/authorize"
    STRAVA_OAUTH_TOKEN = "https://www.strava.com/oauth/token"
    STRAVA_OAUTH_DEAUTHORIZE = "https://www.strava.com/oauth/deauthorize"
    OAUTH_SCOPES = "read,activity:read_all,profile:read_all"

    SESSION_TTL = timedelta(hours=48)
    PERMANENT_SESSION_LIFETIME = SESSION_TTL

    # Reject plain-HTTP requests with 403 when True (default in production). Trusts
    # X-Forwarded-Proto from a reverse proxy when present.
    ENFORCE_HTTPS = _env_bool(
        "ENFORCE_HTTPS",
        default=os.environ.get("FLASK_ENV") == "production",
    )
    # When true, OAuth callback rejects athletes not already in the database; existing athletes
    # can still sign in.
    BLOCK_SIGNUPS = _env_bool("BLOCK_SIGNUPS", default=False)
    # SESSION_COOKIE_SECURE is applied in create_app() from the resolved ENFORCE_HTTPS flag.
    SESSION_COOKIE_SAMESITE = "Lax"

    # Required at app startup (validated in create_app): ISO 8601 competition start; Strava
    # backfill uses this as `after` epoch. create_app replaces this string with an aware UTC
    # datetime on the same config key.
    COMPETITION_START_DATETIME = (
        os.environ.get("COMPETITION_START_DATETIME", "").strip() or None
    )
    # Comma-separated ISO instants: end of each scoring period (exclusive upper bound on
    # activity start_date). Merged with COMPETITION_END_DATETIME. May be empty if only one
    # period from COMPETITION_START_DATETIME to COMPETITION_END_DATETIME is needed.
    WEEK_BOUNDARIES = os.environ.get("WEEK_BOUNDARIES", "").strip() or None
    # Required at app startup: final period boundary; activities starting after this are
    # excluded. create_app replaces this string with an aware UTC datetime on the same key.
    COMPETITION_END_DATETIME = (
        os.environ.get("COMPETITION_END_DATETIME", "").strip() or None
    )
    # Strava allows up to 200 per page for GET /athlete/activities.
    STRAVA_ACTIVITIES_PAGE_SIZE = 200
