"""Jinja template filters."""

from datetime import datetime, timezone

from markupsafe import Markup, escape


def utc_time(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> Markup:
    """
    Render a UTC instant as <time datetime="…">…</time> for machine-readable dates.

    Naive datetimes are treated as UTC (matches Strava activity storage).
    """
    if value is None or not isinstance(value, datetime):
        return Markup("")
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    iso = escape(dt.isoformat().replace("+00:00", "Z"))
    human = escape(dt.strftime(fmt) + " UTC")
    return Markup(f'<time datetime="{iso}">{human}</time>')
