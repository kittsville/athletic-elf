"""Small helpers (formatting, OAuth base URL, Strava datetimes)."""

from datetime import datetime, timezone
from urllib.parse import quote

from flask import current_app

from .config import parse_activity_start_epoch


def domain_base() -> str:
    d = current_app.config.get("DOMAIN")
    if not d:
        raise RuntimeError("DOMAIN environment variable is not set")
    d = str(d).strip().rstrip("/")
    if not d.startswith("http"):
        d = f"https://{d}"
    return d


def oauth_redirect_uri() -> str:
    return f"{domain_base()}/oauth/callback"


def strava_webhook_callback_url(verify_token: str) -> str:
    """
    Full push-subscription callback URL registered with Strava.

    The path ends with a percent-encoded copy of ``verify_token`` so POST events
    are not accepted at a guessable public path.
    """
    vt = verify_token.strip()
    if not vt:
        raise ValueError(
            "VERIFY_TOKEN must be non-empty to build a webhook callback URL"
        )
    return f"{domain_base()}/webhook/{quote(vt, safe='')}"


def athlete_hub_department_complete(hub: str | None, department: str | None) -> bool:
    h = (hub or "").strip()
    d = (department or "").strip()
    return bool(h and d)


def athlete_display_name(firstname: str, lastname: str) -> str:
    parts = [firstname.strip(), lastname.strip()]
    return " ".join(p for p in parts if p) or "—"


def athlete_role_label(is_app_developer: bool, is_organiser: bool) -> str:
    """Homepage / directory role: app dev first, then organiser, else participant."""
    if is_app_developer:
        return "App developer"
    if is_organiser:
        return "Competition Organiser"
    return "Participant"


def format_moving_time(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    minutes = max(0, int(seconds)) // 60
    return f"{minutes} min"


def parse_strava_datetime(iso: str) -> datetime:
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def activity_start_date_for_display(iso_value: str | None) -> str | None:
    """Competition start as shown on the home page; None if unset or invalid."""
    epoch = parse_activity_start_epoch(iso_value)
    if epoch is None:
        return None
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.strftime("%d %B %Y, %H:%M UTC")
