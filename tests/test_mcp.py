"""MCP auth middleware + tool smoke tests."""

import asyncio
import unittest
from datetime import datetime

from starlette.testclient import TestClient

from athletic_elf.config import Config
from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.mcp_app import (
    AuthMiddleware,
    _current_athlete,
    build_asgi_app,
    build_mcp,
    tool_get_competition_schedule,
    tool_get_my_points_breakdown,
    tool_get_my_profile,
    tool_get_my_recent_activities,
    tool_get_scoring_rules,
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

    def test_rejects_empty_bearer(self):
        sent = asyncio.run(_invoke(self.mw, [(b"authorization", b"Bearer   ")]))
        self.assertEqual(sent[0]["status"], 401)
        self.assertEqual(sent[1]["body"], b"empty bearer token")
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

    def test_recent_activities_excludes_other_athletes(self):
        # Another athlete with a huge ride should never surface for athlete 777.
        with self.app.app_context():
            db.session.add(
                Athlete(
                    athlete_id=555,
                    firstname="Other",
                    lastname="Person",
                    access_token="at",
                    refresh_token="rt",
                    expires_at=9_999_999_999,
                )
            )
            db.session.add(
                Activity(
                    activity_id=9999,
                    athlete_id=555,
                    distance=50000.0,
                    sport_type="Ride",
                    moving_time=7200,
                    start_date=datetime(2026, 4, 3, 9, 0, 0),
                )
            )
            db.session.commit()
        out = self._with_athlete(tool_get_my_recent_activities, limit=100)
        self.assertNotIn(9999, [r["activity_id"] for r in out])
        self.assertEqual(len(out), 2)

    def test_points_breakdown_excludes_other_athletes(self):
        with self.app.app_context():
            db.session.add(
                Athlete(
                    athlete_id=556,
                    firstname="Stranger",
                    lastname="X",
                    access_token="at",
                    refresh_token="rt",
                    expires_at=9_999_999_999,
                )
            )
            db.session.add(
                Activity(
                    activity_id=9998,
                    athlete_id=556,
                    distance=100000.0,  # would be 20 cycling points if leaked
                    sport_type="Ride",
                    moving_time=3600,
                    start_date=datetime(2026, 4, 4, 9, 0, 0),
                )
            )
            db.session.commit()
        out = self._with_athlete(tool_get_my_points_breakdown)
        # Unchanged from test_points_breakdown: 3 points, 2 activities.
        self.assertEqual(out["total_points"], 3)
        self.assertEqual(out["activity_count"], 2)

    def test_recent_activities_limit_clamped_to_100(self):
        # Insert 150 activities; expect at most 100 back even when limit=1000.
        with self.app.app_context():
            for i in range(150):
                db.session.add(
                    Activity(
                        activity_id=20000 + i,
                        athlete_id=777,
                        distance=1000.0,
                        sport_type="Run",
                        moving_time=300,
                        start_date=datetime(2025, 1, 1, 0, i % 60, 0),
                    )
                )
            db.session.commit()
        out = self._with_athlete(tool_get_my_recent_activities, limit=1000)
        self.assertEqual(len(out), 100)

    def test_recent_activities_excludes_unscored_stubs(self):
        # Webhook-created stubs (start_date IS NULL) must not surface.
        with self.app.app_context():
            db.session.add(Activity(activity_id=30001, athlete_id=777, start_date=None))
            db.session.commit()
        out = self._with_athlete(tool_get_my_recent_activities, limit=10)
        self.assertNotIn(30001, [r["activity_id"] for r in out])

    def test_competition_schedule(self):
        out = self._with_athlete(tool_get_competition_schedule)
        self.assertEqual(out["competition_start"], "2020-01-01T00:00:00+00:00")
        self.assertEqual(out["competition_end"], "2030-01-01T00:00:00+00:00")
        self.assertEqual(out["period_count"], 1)
        # Test config has a 10-year window starting 2020; current period is open.
        self.assertIsNotNone(out["current_period"])
        self.assertEqual(out["current_period"]["index"], 0)
        self.assertGreater(out["current_period"]["seconds_remaining"], 0)

    def test_scoring_rules(self):
        # No athlete context needed — purely static.
        out = tool_get_scoring_rules()
        self.assertEqual(out["cycling_km_per_point"], 5.0)
        self.assertEqual(out["easy_fitness_daily_cap_points"], 5)
        self.assertEqual(out["team_min_size_for_score"], 5)
        slugs = {lb["slug"] for lb in out["leaderboards"]}
        self.assertIn("mvp", slugs)
        self.assertIn("marathoner", slugs)


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


class TestAsgiCors(unittest.TestCase):
    """End-to-end CORS + auth wiring on the combined ASGI app."""

    def setUp(self):
        self.flask_app = create_app(_TestMcpConfig)
        self.flask_app.config["TESTING"] = True
        self.raw = "cors-test-mcp-key"
        with self.flask_app.app_context():
            db.session.add(
                Athlete(
                    athlete_id=321,
                    firstname="Cors",
                    lastname="Tester",
                    access_token="at",
                    refresh_token="rt",
                    expires_at=9_999_999_999,
                    mcp_key=hash_session_token(self.raw),
                )
            )
            db.session.commit()
        self.asgi = build_asgi_app(self.flask_app)

    def test_preflight_from_localhost_allowed(self):
        with TestClient(self.asgi) as c:
            r = c.options(
                "/mcp",
                headers={
                    "Origin": "http://localhost:6274",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization,content-type",
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.headers.get("access-control-allow-origin"), "http://localhost:6274"
        )

    def test_preflight_from_127_0_0_1_allowed(self):
        with TestClient(self.asgi) as c:
            r = c.options(
                "/mcp",
                headers={
                    "Origin": "http://127.0.0.1:9999",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization",
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.headers.get("access-control-allow-origin"), "http://127.0.0.1:9999"
        )

    def test_preflight_from_external_origin_denied(self):
        with TestClient(self.asgi) as c:
            r = c.options(
                "/mcp",
                headers={
                    "Origin": "https://evil.example.com",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "authorization",
                },
            )
        self.assertIsNone(r.headers.get("access-control-allow-origin"))

    def test_401_carries_cors_header_for_localhost(self):
        # Without CORS wrapping auth, browsers can't read the 401 body.
        with TestClient(self.asgi) as c:
            r = c.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={
                    "Origin": "http://localhost:6274",
                    "Accept": "application/json, text/event-stream",
                },
            )
        self.assertEqual(r.status_code, 401)
        self.assertEqual(
            r.headers.get("access-control-allow-origin"), "http://localhost:6274"
        )

    def test_authed_post_goes_through_and_carries_cors_header(self):
        with TestClient(self.asgi) as c:
            r = c.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
                headers={
                    "Origin": "http://localhost:6274",
                    "Accept": "application/json, text/event-stream",
                    "Authorization": f"Bearer {self.raw}",
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.headers.get("access-control-allow-origin"), "http://localhost:6274"
        )
        self.assertEqual(r.json()["result"]["serverInfo"]["name"], "athletic-elf-coach")

    def test_flask_catchall_unaffected_by_mcp_cors(self):
        with TestClient(self.asgi) as c:
            r = c.get("/")
        self.assertEqual(r.status_code, 200)
        # Flask path should not get CORS headers — only /mcp does.
        self.assertIsNone(r.headers.get("access-control-allow-origin"))
