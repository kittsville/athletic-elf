"""Flask session token ↔ database row (hashed)."""

import hashlib
import secrets
from datetime import datetime, timezone

from flask import current_app, session

from .extensions import db
from .models import Athlete, BrowserSession

# Key in Flask's signed session cookie (distinct from oauth_state).
BROWSER_TOKEN_SESSION_KEY = "browser_token"


def _session_ttl():
    return current_app.config["SESSION_TTL"]


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_browser_session(athlete_id: int) -> tuple[str, datetime]:
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + _session_ttl()
    db.session.add(
        BrowserSession(
            athlete_id=athlete_id,
            hash=hash_session_token(raw),
            expires_at=expires_at,
        )
    )
    return raw, expires_at


def current_athlete_from_request() -> Athlete | None:
    token = session.get(BROWSER_TOKEN_SESSION_KEY)
    if not token:
        return None
    h = hash_session_token(token)
    now = datetime.now(timezone.utc)
    bs = (
        BrowserSession.query.filter_by(hash=h)
        .filter(BrowserSession.expires_at > now)
        .first()
    )
    if bs is None:
        return None
    return db.session.get(Athlete, bs.athlete_id)
