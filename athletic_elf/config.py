"""Application configuration loaded from the environment."""

import os
from datetime import timedelta


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


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql://strava:strava@localhost:5432/strava"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "STRAVA")
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")
    DOMAIN = os.getenv("DOMAIN")

    STRAVA_API_BASE = "https://www.strava.com/api/v3"
    STRAVA_OAUTH_AUTHORIZE = "https://www.strava.com/oauth/authorize"
    STRAVA_OAUTH_TOKEN = "https://www.strava.com/oauth/token"
    OAUTH_SCOPES = "read,activity:read,profile:read_all"

    SESSION_COOKIE_NAME = "elf_session"
    SESSION_TTL = timedelta(hours=48)
