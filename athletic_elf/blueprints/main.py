"""HTML pages, logout, data deletion, and cron."""

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
from points import activities_total_points, team_points
from sqlalchemy.orm import load_only

from ..extensions import db
from ..models import Activity, Athelete, BrowserSession
from ..session import BROWSER_TOKEN_SESSION_KEY, hash_session_token
from ..strava_service import process_activities
from ..utils import (
    activity_start_date_for_display,
    athlete_display_name,
    athlete_hub_department_complete,
    format_moving_time,
)

bp = Blueprint("main", __name__)


def _points_by_athlete_strava_id() -> dict[int, int]:
    """Total points per Strava athlete id (activities with a start_date only)."""
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
    athletes = Athelete.query.options(
        load_only(Athelete.athlete_id, Athelete.hub, Athelete.department)
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
    hub_rows = [
        {
            "name": h,
            "athlete_count": len(hub_member_points[h]),
            "points": team_points(hub_member_points[h]),
        }
        for h in hub_options
    ]
    dept_rows = [
        {
            "name": d,
            "athlete_count": len(dept_member_points[d]),
            "points": team_points(dept_member_points[d]),
        }
        for d in department_options
    ]
    hub_rows.sort(key=lambda r: (-float(r["points"]), str(r["name"])))
    dept_rows.sort(key=lambda r: (-float(r["points"]), str(r["name"])))
    return hub_rows, dept_rows


def _can_perform_organiser_tasks(athlete: Athelete) -> bool:
    if athlete.is_organiser:
        return True
    return int(athlete.athlete_id) in current_app.config["APP_DEVELOPER_IDS"]


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
    activities = (
        Activity.query.filter_by(athlete_id=strava_id)
        .order_by(
            Activity.start_date.is_(None),
            Activity.start_date.desc(),
            Activity.id.desc(),
        )
        .all()
    )
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
        format_moving_time=format_moving_time,
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
    strava_athlete_id = athlete.athlete_id
    BrowserSession.query.filter_by(athlete_id=strava_athlete_id).delete(
        synchronize_session=False
    )
    Activity.query.filter_by(athlete_id=strava_athlete_id).delete(
        synchronize_session=False
    )
    Athelete.query.filter_by(athlete_id=strava_athlete_id).delete(
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


@bp.post("/cron")
def cron():
    now = datetime.now(timezone.utc)
    removed_sessions = BrowserSession.query.filter(
        BrowserSession.expires_at < now
    ).delete(synchronize_session=False)
    n = process_activities(10)
    db.session.commit()
    summary = f"Processed {n} activities, removed {removed_sessions} expired session(s)"
    current_app.logger.info(summary)
    return (
        summary,
        200,
    )


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
            athelete = Athelete.query.filter_by(athlete_id=athlete_id).first()
            if athelete:
                fn = athelete.firstname or ""
                ln = athelete.lastname or ""
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


@bp.get("/atheletes")
def atheletes():
    athlete = g.current_athlete
    if not _can_perform_organiser_tasks(athlete):
        abort(403)
    atheletes = (
        Athelete.query.options(
            load_only(
                Athelete.athlete_id,
                Athelete.firstname,
                Athelete.lastname,
                Athelete.hub,
                Athelete.department,
                Athelete.is_organiser,
            )
        )
        .order_by(Athelete.athlete_id.asc())
        .all()
    )
    dev_ids = current_app.config["APP_DEVELOPER_IDS"]
    points_by = _points_by_athlete_strava_id()
    table_rows = [
        {
            "athelete_pk": a.athlete_id,
            "athlete_id": a.athlete_id,
            "name": athlete_display_name(a.firstname or "", a.lastname or ""),
            "hub": (a.hub or "").strip() or "—",
            "department": (a.department or "").strip() or "—",
            "score": points_by.get(int(a.athlete_id), 0),
            "is_organiser": bool(a.is_organiser),
            "is_app_developer": int(a.athlete_id) in dev_ids,
        }
        for a in atheletes
    ]
    return render_template("atheletes.html", rows=table_rows)


@bp.post("/atheletes/<int:athelete_pk>/make-organiser")
def atheletes_make_organiser(athelete_pk: int):
    actor = g.current_athlete
    if not _can_perform_organiser_tasks(actor):
        abort(403)
    target = db.session.get(Athelete, athelete_pk)
    if target is None:
        abort(404)
    target.is_organiser = True
    db.session.commit()
    return redirect(url_for("main.atheletes"))
