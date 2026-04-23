"""MCP server: per-athlete read-only tools over competition data.

The tools read `_current_athlete` (a ContextVar) rather than taking an athlete
argument, so an athlete can never accidentally query another's data. The var
is set by `AuthMiddleware` after it validates the bearer token against
``Athlete.mcp_key`` (SHA-256 of the raw key).
"""

import contextvars
from datetime import datetime, timezone

from asgiref.wsgi import WsgiToAsgi
from flask import Flask
from mcp.server.fastmcp import FastMCP
from points import (
    CYCLING_METERS_PER_POINT,
    EASY_FITNESS_DAILY_CAP_POINTS,
    RUNNING_METERS_PER_POINT,
    SECONDS_PER_EASY_FITNESS_POINT,
    SECONDS_PER_HARD_FITNESS_POINT,
    SWIMMING_METERS_PER_POINT,
    TEAM_MIN_SIZE_FOR_SCORE,
    TEAM_TOP_FRACTION,
    WALKING_METERS_PER_POINT,
    activities_total_points,
    discipline_totals_for_activities,
)
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount

from .competition_periods import period_specs_for_config
from .leaderboard import LEADERBOARD_SPECS
from .models import Activity, Athlete
from .session import hash_session_token
from .utils import athlete_display_name

_current_athlete: contextvars.ContextVar[Athlete] = contextvars.ContextVar(
    "mcp_current_athlete"
)


def _athlete_activities_with_start_date() -> list[Activity]:
    a = _current_athlete.get()
    return (
        Activity.query.filter(
            Activity.athlete_id == int(a.athlete_id),
            Activity.start_date.isnot(None),
        )
        .order_by(Activity.start_date.desc())
        .all()
    )


def tool_get_my_profile() -> dict:
    """Your athlete profile: name, hub, department, role flags."""
    a = _current_athlete.get()
    return {
        "athlete_id": int(a.athlete_id),
        "name": athlete_display_name(a.firstname or "", a.lastname or ""),
        "hub": (a.hub or "").strip() or None,
        "department": (a.department or "").strip() or None,
        "is_organiser": bool(a.is_organiser),
        "is_active": bool(a.is_active),
    }


def tool_get_my_recent_activities(limit: int = 10) -> list[dict]:
    """Your most recent scored activities (newest first). `limit` is clamped to 1..100."""
    a = _current_athlete.get()
    n = max(1, min(int(limit), 100))
    rows = (
        Activity.query.filter(
            Activity.athlete_id == int(a.athlete_id),
            Activity.start_date.isnot(None),
        )
        .order_by(Activity.start_date.desc())
        .limit(n)
        .all()
    )
    return [
        {
            "activity_id": int(r.activity_id),
            "sport_type": r.sport_type,
            "distance_m": float(r.distance) if r.distance is not None else None,
            "moving_time_s": int(r.moving_time) if r.moving_time is not None else None,
            "start_date": r.start_date.replace(tzinfo=timezone.utc).isoformat()
            if r.start_date
            else None,
        }
        for r in rows
    ]


def tool_get_my_points_breakdown() -> dict:
    """Your total competition points plus per-discipline volume (km, minutes)."""
    acts = _athlete_activities_with_start_date()
    return {
        "total_points": int(activities_total_points(acts)),
        "disciplines": discipline_totals_for_activities(acts),
        "activity_count": len(acts),
    }


def tool_get_competition_schedule() -> dict:
    """Competition window, closed vs open scoring periods, and time left in the current period."""
    from flask import current_app

    start = current_app.config["COMPETITION_START_DATETIME"]
    end = current_app.config["COMPETITION_END_DATETIME"]
    boundaries = current_app.config.get("WEEK_BOUNDARY_DATETIMES") or ()
    specs = period_specs_for_config(start, boundaries, end)
    now = datetime.now(timezone.utc)
    open_period = next((s for s in specs if now < s.end_exclusive), None)
    return {
        "competition_start": start.isoformat(),
        "competition_end": end.isoformat(),
        "now": now.isoformat(),
        "period_count": len(specs),
        "current_period": {
            "index": open_period.index,
            "canonical_start": open_period.canonical_start.isoformat(),
            "end_exclusive": open_period.end_exclusive.isoformat(),
            "seconds_remaining": int((open_period.end_exclusive - now).total_seconds()),
        }
        if open_period is not None
        else None,
    }


def tool_get_scoring_rules() -> dict:
    """Scoring thresholds the app uses (distance/time → points, daily caps, team formula)."""
    return {
        "cycling_km_per_point": CYCLING_METERS_PER_POINT / 1000,
        "running_km_per_point": RUNNING_METERS_PER_POINT / 1000,
        "walking_km_per_point": WALKING_METERS_PER_POINT / 1000,
        "swimming_m_per_point": SWIMMING_METERS_PER_POINT,
        "hard_fitness_minutes_per_point": SECONDS_PER_HARD_FITNESS_POINT // 60,
        "easy_fitness_minutes_per_point": SECONDS_PER_EASY_FITNESS_POINT // 60,
        "easy_fitness_daily_cap_points": EASY_FITNESS_DAILY_CAP_POINTS,
        "team_min_size_for_score": TEAM_MIN_SIZE_FOR_SCORE,
        "team_top_fraction": TEAM_TOP_FRACTION,
        "leaderboards": [
            {"slug": slug, "title": title, "stat_header": stat_header}
            for slug, title, _desc, stat_header, _sort in LEADERBOARD_SPECS
        ],
    }


def build_mcp() -> FastMCP:
    """Build a FastMCP instance with athlete-scoped tools. Stateless HTTP + JSON response."""
    mcp = FastMCP(
        "athletic-elf-coach",
        instructions=(
            "Per-athlete coach over Athletic Elf competition data. All tools are "
            "read-only and scoped to the authenticated athlete."
        ),
        stateless_http=True,
        json_response=True,
    )
    mcp.tool(name="get_my_profile")(tool_get_my_profile)
    mcp.tool(name="get_my_recent_activities")(tool_get_my_recent_activities)
    mcp.tool(name="get_my_points_breakdown")(tool_get_my_points_breakdown)
    mcp.tool(name="get_competition_schedule")(tool_get_competition_schedule)
    mcp.tool(name="get_scoring_rules")(tool_get_scoring_rules)
    return mcp


class AuthMiddleware:
    """ASGI middleware: look up athlete by `sha256(Bearer token) == Athlete.mcp_key`.

    Pushes a Flask app context and sets ``_current_athlete`` for the duration of
    the inner call, so tool functions can use Flask-SQLAlchemy queries directly.
    """

    def __init__(self, asgi_app, flask_app: Flask):
        self.asgi_app = asgi_app
        self.flask_app = flask_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return
        auth = ""
        for name, value in scope.get("headers") or ():
            if name == b"authorization":
                auth = value.decode("latin-1")
                break
        prefix = "Bearer "
        if not auth.startswith(prefix):
            await _reject(send, 401, "missing bearer token")
            return
        raw = auth[len(prefix) :].strip()
        if not raw:
            await _reject(send, 401, "empty bearer token")
            return
        with self.flask_app.app_context():
            athlete = Athlete.query.filter_by(mcp_key=hash_session_token(raw)).first()
            if athlete is None:
                await _reject(send, 401, "invalid bearer token")
                return
            token = _current_athlete.set(athlete)
            try:
                await self.asgi_app(scope, receive, send)
            finally:
                _current_athlete.reset(token)


def build_asgi_app(flask_app: Flask) -> Starlette:
    """Combined ASGI app: FastMCP Streamable HTTP at /mcp, Flask WSGI at everything else.

    CORS wraps auth (so preflight and 401 responses carry allow-origin headers for
    localhost tools), auth wraps the MCP handler (so tool calls see the current athlete).
    """
    mcp = build_mcp()
    starlette_app = mcp.streamable_http_app()

    mcp_route = starlette_app.router.routes[0]
    mcp_route.app = CORSMiddleware(
        AuthMiddleware(mcp_route.app, flask_app),
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )

    starlette_app.router.routes.append(Mount("/", app=WsgiToAsgi(flask_app)))
    return starlette_app


async def _reject(send, status: int, detail: str) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"www-authenticate", b'Bearer realm="mcp"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": detail.encode("utf-8")})
