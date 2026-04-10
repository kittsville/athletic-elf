"""Small helpers (formatting, OAuth base URL, Strava datetimes)."""

from datetime import datetime, timezone

from flask import current_app


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


def athlete_hub_department_complete(hub: str | None, department: str | None) -> bool:
    h = (hub or "").strip()
    d = (department or "").strip()
    return bool(h and d)


def athlete_display_name(firstname: str, lastname: str) -> str:
    parts = [firstname.strip(), lastname.strip()]
    return " ".join(p for p in parts if p) or "—"


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
