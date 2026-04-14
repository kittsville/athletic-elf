"""HTML pages, logout, data deletion, and cron."""

import secrets
import threading
from collections import defaultdict
from datetime import datetime, timezone

from flask import (
    Blueprint,
    abort,
    current_app,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from points import activities_total_points, discipline_totals_for_activities, team_points
from sqlalchemy.orm import joinedload, load_only

from ..extensions import db
from ..models import Activity, Athlete, Bonus, BrowserSession
from ..session import BROWSER_TOKEN_SESSION_KEY, hash_session_token
from ..strava_service import process_activities
from ..utils import (
    activity_start_date_for_display,
    athlete_display_name,
    athlete_hub_department_complete,
)

bp = Blueprint("main", __name__)


def _activities_by_athlete_scored() -> dict[int, list[Activity]]:
    """Activities with a start_date, grouped by Strava athlete id."""
    activities = (
        Activity.query.filter(
            Activity.athlete_id.isnot(None),
            Activity.start_date.isnot(None),
        )
        .order_by(Activity.athlete_id, Activity.id)
        .all()
    )
    by_athlete: defaultdict[int, list] = defaultdict(list)
    for a in activities:
        by_athlete[int(a.athlete_id)].append(a)
    return by_athlete


def _points_by_athlete_strava_id() -> dict[int, int]:
    """Total points per Strava athlete id (activities with a start_date only)."""
    by_athlete = _activities_by_athlete_scored()
    return {aid: activities_total_points(acts) for aid, acts in by_athlete.items()}


def _summaries_by_hub_and_department(
    points_by: dict[int, int],
    hub_options: list[str],
    department_options: list[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Per hub/department: team_points() over each member's total points (see points.team_points)."""
    hub_set = frozenset(hub_options)
    dept_set = frozenset(department_options)
    hub_member_points: defaultdict[str, list[int]] = defaultdict(list)
    dept_member_points: defaultdict[str, list[int]] = defaultdict(list)
    athletes = Athlete.query.options(
        load_only(Athlete.athlete_id, Athlete.hub, Athlete.department)
    ).all()
    for a in athletes:
        aid = int(a.athlete_id)
        pts = points_by.get(aid, 0)
        h = (a.hub or "").strip()
        if h in hub_set:
            hub_member_points[h].append(pts)
        d = (a.department or "").strip()
        if d in dept_set:
            dept_member_points[d].append(pts)
    hub_bonus: defaultdict[str, int] = defaultdict(int)
    dept_bonus: defaultdict[str, int] = defaultdict(int)
    for b in Bonus.query.all():
        t = b.target.strip()
        if t in hub_set:
            hub_bonus[t] += int(b.points)
        elif t in dept_set:
            dept_bonus[t] += int(b.points)

    hub_rows = [
        {
            "name": h,
            "athlete_count": len(hub_member_points[h]),
            "points": float(team_points(hub_member_points[h])) + hub_bonus.get(h, 0),
        }
        for h in hub_options
    ]
    dept_rows = [
        {
            "name": d,
            "athlete_count": len(dept_member_points[d]),
            "points": float(team_points(dept_member_points[d])) + dept_bonus.get(d, 0),
        }
        for d in department_options
    ]
    hub_rows.sort(key=lambda r: (-float(r["points"]), str(r["name"])))
    dept_rows.sort(key=lambda r: (-float(r["points"]), str(r["name"])))
    return hub_rows, dept_rows


def _can_perform_organiser_tasks(athlete: Athlete) -> bool:
    return (
        athlete.is_organiser
        or int(athlete.athlete_id) in current_app.config["APP_DEVELOPER_IDS"]
    )


def _activities_for_athlete(athlete_strava_id: int) -> list[Activity]:
    return (
        Activity.query.filter_by(athlete_id=athlete_strava_id)
        .order_by(
            Activity.start_date.is_(None),
            Activity.start_date.desc(),
            Activity.id.desc(),
        )
        .all()
    )


@bp.context_processor
def inject_nav_context():
    athlete = getattr(g, "current_athlete", None)
    show_organiser_nav = athlete is not None and _can_perform_organiser_tasks(athlete)
    return {"show_organiser_nav": show_organiser_nav}


@bp.get("/")
def index():
    athlete = g.current_athlete
    if athlete is None:
        return render_template("index.html", logged_in=False)
    strava_id = int(athlete.athlete_id)
    name = athlete_display_name(athlete.firstname, athlete.lastname)
    is_app_developer = strava_id in current_app.config["APP_DEVELOPER_IDS"]
    activities = _activities_for_athlete(strava_id)
    scored = [a for a in activities if a.start_date is not None]
    team_points = activities_total_points(scored)
    hub_display = (athlete.hub or "").strip() or "—"
    department_display = (athlete.department or "").strip() or "—"
    return render_template(
        "index.html",
        logged_in=True,
        strava_id=strava_id,
        name=name,
        hub_display=hub_display,
        department_display=department_display,
        is_app_developer=is_app_developer,
        is_organiser=bool(athlete.is_organiser),
        activities=activities,
        team_points=team_points,
        activity_start_display=activity_start_date_for_display(
            current_app.config.get("ACTIVITY_START_DATE")
        ),
    )


@bp.route("/form", methods=["GET", "POST"])
def hub_department_form():
    athlete = g.current_athlete
    hubs = current_app.config["HUB_OPTIONS"]
    departments = current_app.config["DEPARTMENT_OPTIONS"]

    if request.method == "GET":
        if athlete_hub_department_complete(athlete.hub, athlete.department):
            return redirect(url_for("main.index"))
        return render_template(
            "hub_department_form.html",
            hub_options=hubs,
            department_options=departments,
        )

    if athlete_hub_department_complete(athlete.hub, athlete.department):
        abort(400)

    hub = (request.form.get("hub") or "").strip()
    department = (request.form.get("department") or "").strip()
    if hub not in hubs or department not in departments:
        abort(400)

    athlete.hub = hub
    athlete.department = department
    db.session.commit()
    return redirect(url_for("main.index"))


@bp.get("/gdpr")
def gdpr():
    return render_template("gdpr.html")


@bp.post("/delete-my-data")
def delete_my_data():
    athlete = g.current_athlete
    Athlete.query.filter_by(athlete_id=athlete.athlete_id).delete(
        synchronize_session=False
    )
    db.session.commit()
    session.pop(BROWSER_TOKEN_SESSION_KEY, None)
    return redirect(url_for("main.index"))


@bp.post("/logout")
def logout():
    token = session.get(BROWSER_TOKEN_SESSION_KEY)
    if token:
        h = hash_session_token(token)
        BrowserSession.query.filter_by(hash=h).delete(synchronize_session=False)
        db.session.commit()
    session.pop(BROWSER_TOKEN_SESSION_KEY, None)
    return redirect(url_for("main.index"))


def _cron_authorization_ok(expected: str, authorization: str | None) -> bool:
    if not authorization or not authorization.startswith("Bearer "):
        return False
    presented = authorization.removeprefix("Bearer ").strip()
    return secrets.compare_digest(presented, expected)


def _run_cron_maintenance(app) -> None:
    """Session cleanup and activity enrichment; must run inside ``app.app_context()``."""
    with app.app_context():
        try:
            now = datetime.now(timezone.utc)
            removed_sessions = BrowserSession.query.filter(
                BrowserSession.expires_at < now
            ).delete(synchronize_session=False)
            n = process_activities(50)
            db.session.commit()
            summary = (
                f"Processed {n} activities, removed {removed_sessions} expired session(s)"
            )
            app.logger.info(summary)
        except Exception:
            db.session.rollback()
            app.logger.exception("cron maintenance failed")


@bp.post("/cron")
def cron():
    expected = current_app.config.get("CRON_SECRET")
    if not expected:
        abort(
            503,
            description="Cron is not configured (set CRON_SECRET in the environment).",
        )
    if not _cron_authorization_ok(expected, request.headers.get("Authorization")):
        abort(403)
    app = current_app._get_current_object()
    threading.Thread(
        target=_run_cron_maintenance,
        args=(app,),
        daemon=True,
    ).start()
    return ("Processing Started", 200)


# slug, title, description, column heading for stat, sort key ("points" = total activity points)
_LEADERBOARD_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "shark",
        "The Shark",
        "Most swimming km: for the pool sharks hitting those 400m intervals.",
        "Swimming (km)",
        "swim_km",
    ),
    (
        "explorer",
        "The Explorer",
        "Most walking km: for the high-volume steppers who never take the elevator.",
        "Walking (km)",
        "walk_km",
    ),
    (
        "powerhouse",
        "The Powerhouse",
        "Most hard fitness minutes: for the heavy lifters, HIIT enthusiasts, and "
        "football/basketball players.",
        "Hard fitness (min)",
        "hard_min",
    ),
    (
        "centurion",
        "The Centurion",
        "Most cycling km: for the road warriors and Peloton fans.",
        "Cycling (km)",
        "cycle_km",
    ),
    (
        "marathoner",
        "The Marathoner",
        "Most running km: for those putting in the pavement miles.",
        "Running (km)",
        "run_km",
    ),
    (
        "zen",
        "The Zen Master",
        "Most yoga or stretching minutes: for the mobility and recovery specialists.",
        "Yoga / stretching (min)",
        "zen_min",
    ),
    (
        "mvp",
        "The MVP",
        "Person with the most total points.",
        "Points",
        "points",
    ),
)


def _leaderboard_sections() -> list[dict[str, object]]:
    """Top 10 per discipline for the public leaders page."""
    by_athlete = _activities_by_athlete_scored()
    if not by_athlete:
        return [
            {
                "slug": s[0],
                "title": s[1],
                "description": s[2],
                "stat_header": s[3],
                "rows": [],
            }
            for s in _LEADERBOARD_SPECS
        ]

    athlete_ids = list(by_athlete.keys())
    profiles = {
        int(a.athlete_id): a
        for a in Athlete.query.filter(Athlete.athlete_id.in_(athlete_ids)).all()
    }

    def _name(aid: int) -> str:
        p = profiles.get(aid)
        if p is None:
            return f"Athlete {aid}"
        return athlete_display_name(p.firstname or "", p.lastname or "")

    stats_rows: list[tuple[int, dict[str, float | int], int]] = []
    for aid, acts in by_athlete.items():
        d = discipline_totals_for_activities(acts)
        pts = activities_total_points(acts)
        stats_rows.append((aid, d, pts))

    def top10(stat_key: str) -> list[dict[str, object]]:
        if stat_key == "points":
            ranked = sorted(
                stats_rows,
                key=lambda t: (-int(t[2]), int(t[0])),
            )
        else:
            ranked = sorted(
                stats_rows,
                key=lambda t: (-float(t[1][stat_key]), int(t[0])),
            )
        out: list[dict[str, object]] = []
        for rank, (aid, d, pts) in enumerate(ranked[:10], start=1):
            if stat_key == "points":
                stat_display = str(int(pts))
            elif stat_key.endswith("_km"):
                stat_display = f"{float(d[stat_key]):.1f} km"
            else:
                stat_display = f"{int(d[stat_key])} min"
            out.append({"rank": rank, "name": _name(aid), "stat_display": stat_display})
        while len(out) < 10:
            out.append({"rank": len(out) + 1, "name": "—", "stat_display": "—"})
        return out

    return [
        {
            "slug": s[0],
            "title": s[1],
            "description": s[2],
            "stat_header": s[3],
            "rows": top10(s[4]),
        }
        for s in _LEADERBOARD_SPECS
    ]


@bp.get("/leaders")
def leaders():
    sections = _leaderboard_sections()
    return render_template("leaders.html", sections=sections)


@bp.get("/results")
def results():
    athlete = g.current_athlete
    show_athlete_points = _can_perform_organiser_tasks(athlete)
    points_by = _points_by_athlete_strava_id()
    hubs = current_app.config["HUB_OPTIONS"]
    departments = current_app.config["DEPARTMENT_OPTIONS"]
    hub_summary, department_summary = _summaries_by_hub_and_department(
        points_by, hubs, departments
    )
    rows: list[dict[str, object]] = []
    if show_athlete_points:
        for athlete_id, pts in points_by.items():
            profile = Athlete.query.filter_by(athlete_id=athlete_id).first()
            if profile:
                fn = profile.firstname or ""
                ln = profile.lastname or ""
            else:
                fn, ln = "", ""
            rows.append(
                {
                    "firstname": fn,
                    "lastname": ln,
                    "athlete_id": athlete_id,
                    "points": pts,
                }
            )

        rows.sort(key=lambda r: (-r["points"], r["athlete_id"]))
    return render_template(
        "results.html",
        rows=rows,
        show_athlete_points=show_athlete_points,
        hub_summary=hub_summary,
        department_summary=department_summary,
    )


@bp.get("/athletes")
def athletes():
    athlete = g.current_athlete
    if not _can_perform_organiser_tasks(athlete):
        abort(403)
    roster = (
        Athlete.query.options(
            load_only(
                Athlete.athlete_id,
                Athlete.firstname,
                Athlete.lastname,
                Athlete.hub,
                Athlete.department,
                Athlete.is_organiser,
            )
        )
        .order_by(Athlete.athlete_id.asc())
        .all()
    )
    dev_ids = current_app.config["APP_DEVELOPER_IDS"]
    points_by = _points_by_athlete_strava_id()
    table_rows = [
        {
            "athlete_pk": a.athlete_id,
            "athlete_id": a.athlete_id,
            "name": athlete_display_name(a.firstname or "", a.lastname or ""),
            "hub": (a.hub or "").strip() or "—",
            "department": (a.department or "").strip() or "—",
            "score": points_by.get(int(a.athlete_id), 0),
            "is_organiser": bool(a.is_organiser),
            "is_app_developer": int(a.athlete_id) in dev_ids,
        }
        for a in roster
    ]
    return render_template("athletes.html", rows=table_rows)


@bp.get("/athletes/<int:athlete_id>")
def athlete_activities(athlete_id: int):
    actor = g.current_athlete
    if not _can_perform_organiser_tasks(actor):
        abort(403)
    target = db.session.get(Athlete, athlete_id)
    if target is None:
        abort(404)
    activities = _activities_for_athlete(athlete_id)
    scored = [a for a in activities if a.start_date is not None]
    team_points_val = activities_total_points(scored)
    viewed_name = athlete_display_name(target.firstname or "", target.lastname or "")
    return render_template(
        "athlete_activities.html",
        viewed_athlete_id=athlete_id,
        viewed_name=viewed_name,
        activities=activities,
        team_points=team_points_val,
        activity_start_display=activity_start_date_for_display(
            current_app.config.get("ACTIVITY_START_DATE")
        ),
    )


@bp.post("/athletes/<int:athlete_pk>/make-organiser")
def athletes_make_organiser(athlete_pk: int):
    actor = g.current_athlete
    if not _can_perform_organiser_tasks(actor):
        abort(403)
    target = db.session.get(Athlete, athlete_pk)
    if target is None:
        abort(404)
    target.is_organiser = True
    db.session.commit()
    return redirect(url_for("main.athletes"))


def _bonuses_table_rows() -> list[dict[str, object]]:
    rows = (
        Bonus.query.options(joinedload(Bonus.awardee))
        .order_by(Bonus.created_at.desc())
        .all()
    )
    out: list[dict[str, object]] = []
    for b in rows:
        aw = b.awardee
        out.append(
            {
                "bonus_id": b.id,
                "created_at": b.created_at,
                "name": b.name,
                "points": b.points,
                "target": b.target,
                "awardee_name": athlete_display_name(
                    aw.firstname or "", aw.lastname or ""
                ),
                "awardee_athlete_id": int(b.athlete_id),
            }
        )
    return out


@bp.route("/bonuses", methods=["GET", "POST"])
def bonuses():
    actor = g.current_athlete
    if not _can_perform_organiser_tasks(actor):
        abort(403)
    hubs = current_app.config["HUB_OPTIONS"]
    departments = current_app.config["DEPARTMENT_OPTIONS"]
    hub_set = frozenset(hubs)
    dept_set = frozenset(departments)
    error: str | None = None

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        raw_target = request.form.get("target") or ""
        kind, _, target_value = raw_target.partition("|")
        target_value = target_value.strip()
        points_raw = (request.form.get("points") or "").strip()
        resolved_target: str | None = None

        if not name or len(name) > 255:
            error = "Name is required and must be at most 255 characters."
        else:
            try:
                points_val = int(points_raw)
            except ValueError:
                error = "Points must be an integer."
            else:
                if points_val < 1:
                    error = "Points must be at least 1."
                elif kind == "hub" and target_value in hub_set:
                    resolved_target = target_value
                elif kind == "department" and target_value in dept_set:
                    resolved_target = target_value
                else:
                    error = (
                        "Target must be a hub or department from the configured lists."
                    )

        if error is None and resolved_target is not None:
            db.session.add(
                Bonus(
                    created_at=datetime.now(timezone.utc),
                    name=name,
                    points=points_val,
                    target=resolved_target,
                    athlete_id=actor.athlete_id,
                )
            )
            db.session.commit()
            return redirect(url_for("main.bonuses"))

    return render_template(
        "bonuses.html",
        rows=_bonuses_table_rows(),
        hub_options=hubs,
        department_options=departments,
        error=error,
    )


@bp.post("/bonuses/<int:bonus_id>/delete")
def bonus_delete(bonus_id: int):
    actor = g.current_athlete
    if not _can_perform_organiser_tasks(actor):
        abort(403)
    row = db.session.get(Bonus, bonus_id)
    if row is None:
        abort(404)
    db.session.delete(row)
    db.session.commit()
    return redirect(url_for("main.bonuses"))
