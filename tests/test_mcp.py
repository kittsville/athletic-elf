"""MCP auth middleware + tool smoke tests."""

import asyncio
import unittest
from datetime import datetime

from athletic_elf.config import Config
from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.mcp_app import (
    AuthMiddleware,
    _current_athlete,
    build_mcp,
    tool_get_my_points_breakdown,
    tool_get_my_profile,
    tool_get_my_recent_activities,
)
from athletic_elf.models import Activity, Athlete
from athletic_elf.session import hash_session_token


class _TestMcpConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-for-mcp-tests-xx"
    VERIFY_TOKEN = "test-verify-token-mcp"
    ENFORCE_HTTPS = False
    CRON_SECRET = None
    AUTO_CREATE_TABLES = True
    HUB_OPTIONS = ("North Hub",)
    DEPARTMENT_OPTIONS = ("Engineering",)
    COMPETITION_START_DATETIME = "2020-01-01T00:00:00+00:00"
    WEEK_BOUNDARIES = ""
    COMPETITION_END_DATETIME = "2030-01-01T00:00:00+00:00"


class _RecordingInner:
    """Minimal ASGI app that records whether it was called."""

    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})


async def _invoke(middleware, headers: list[tuple[bytes, bytes]]) -> list[dict]:
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "POST", "path": "/", "headers": headers}
    await middleware(scope, receive, send)
    return sent


class TestAuthMiddleware(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestMcpConfig)
        self.app.config["TESTING"] = True
        self.inner = _RecordingInner()
        self.mw = AuthMiddleware(self.inner, self.app)

    def test_rejects_missing_header(self):
        sent = asyncio.run(_invoke(self.mw, []))
        self.assertEqual(sent[0]["status"], 401)
        self.assertFalse(self.inner.called)

    def test_rejects_non_bearer(self):
        sent = asyncio.run(_invoke(self.mw, [(b"authorization", b"Basic foo")]))
        self.assertEqual(sent[0]["status"], 401)
        self.assertFalse(self.inner.called)

    def test_rejects_unknown_token(self):
        sent = asyncio.run(
            _invoke(self.mw, [(b"authorization", b"Bearer nonexistent-token")])
        )
        self.assertEqual(sent[0]["status"], 401)
        self.assertFalse(self.inner.called)

    def test_accepts_valid_token_and_sets_current_athlete(self):
        raw = "secret-mcp-key-value"
        with self.app.app_context():
            a = Athlete(
                athlete_id=123,
                firstname="A",
                lastname="B",
                access_token="at",
                refresh_token="rt",
                expires_at=9_999_999_999,
                mcp_key=hash_session_token(raw),
            )
            db.session.add(a)
            db.session.commit()

        observed_id: list[int] = []

        async def inner_checks_current(scope, receive, send):
            observed_id.append(int(_current_athlete.get().athlete_id))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        mw = AuthMiddleware(inner_checks_current, self.app)
        sent = asyncio.run(_invoke(mw, [(b"authorization", f"Bearer {raw}".encode())]))
        self.assertEqual(sent[0]["status"], 200)
        self.assertEqual(observed_id, [123])

    def test_passes_through_non_http_scope(self):
        # Lifespan events must not be intercepted.
        reached = []

        async def inner(scope, receive, send):
            reached.append(scope["type"])

        mw = AuthMiddleware(inner, self.app)

        async def noop_receive():
            return {"type": "lifespan.startup"}

        async def noop_send(_msg):
            pass

        asyncio.run(mw({"type": "lifespan"}, noop_receive, noop_send))
        self.assertEqual(reached, ["lifespan"])


class TestMcpTools(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestMcpConfig)
        self.app.config["TESTING"] = True
        with self.app.app_context():
            self.athlete = Athlete(
                athlete_id=777,
                firstname="Runs",
                lastname="Fast",
                access_token="at",
                refresh_token="rt",
                expires_at=9_999_999_999,
                hub="North Hub",
                department="Engineering",
            )
            db.session.add(self.athlete)
            db.session.add(
                Activity(
                    activity_id=1001,
                    athlete_id=777,
                    distance=1600.0,
                    sport_type="Run",
                    moving_time=540,
                    start_date=datetime(2026, 4, 1, 6, 0, 0),
                )
            )
            db.session.add(
                Activity(
                    activity_id=1002,
                    athlete_id=777,
                    distance=10000.0,
                    sport_type="Ride",
                    moving_time=1800,
                    start_date=datetime(2026, 4, 2, 18, 0, 0),
                )
            )
            db.session.commit()

    def _with_athlete(self, fn, *args, **kwargs):
        with self.app.app_context():
            athlete = db.session.get(Athlete, 777)
            token = _current_athlete.set(athlete)
            try:
                return fn(*args, **kwargs)
            finally:
                _current_athlete.reset(token)

    def test_profile(self):
        out = self._with_athlete(tool_get_my_profile)
        self.assertEqual(out["athlete_id"], 777)
        self.assertEqual(out["name"], "Runs Fast")
        self.assertEqual(out["hub"], "North Hub")

    def test_recent_activities_newest_first_scoped_to_me(self):
        out = self._with_athlete(tool_get_my_recent_activities, limit=5)
        self.assertEqual([r["activity_id"] for r in out], [1002, 1001])
        self.assertEqual(out[0]["sport_type"], "Ride")

    def test_points_breakdown(self):
        out = self._with_athlete(tool_get_my_points_breakdown)
        # 1600m run = 1 pt, 10000m cycle = 2 pts
        self.assertEqual(out["total_points"], 3)
        self.assertEqual(out["activity_count"], 2)
        self.assertAlmostEqual(out["disciplines"]["cycle_km"], 10.0)
        self.assertAlmostEqual(out["disciplines"]["run_km"], 1.6)


class TestBuildMcp(unittest.TestCase):
    def test_tools_registered(self):
        mcp = build_mcp()
        names = {t.name for t in asyncio.run(mcp.list_tools())}
        self.assertEqual(
            names,
            {
                "get_my_profile",
                "get_my_recent_activities",
                "get_my_points_breakdown",
                "get_competition_schedule",
                "get_scoring_rules",
            },
        )
