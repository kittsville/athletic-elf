"""Browser session cookie ↔ database row."""

import hashlib
import secrets
from datetime import datetime, timezone

from flask import current_app, request

from .extensions import db
from .models import Athelete, BrowserSession


def _session_cookie_name() -> str:
    return current_app.config["SESSION_COOKIE_NAME"]


def _session_ttl():
    return current_app.config["SESSION_TTL"]


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_browser_session(athelete_pk: int) -> tuple[str, datetime]:
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + _session_ttl()
    db.session.add(
        BrowserSession(
            athelete_id=athelete_pk,
            hash=hash_session_token(raw),
            expires_at=expires_at,
        )
    )
    return raw, expires_at


def current_athlete_from_request() -> Athelete | None:
    token = request.cookies.get(_session_cookie_name())
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
    return db.session.get(Athelete, bs.athelete_id)
