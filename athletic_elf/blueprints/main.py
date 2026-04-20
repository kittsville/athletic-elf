"""HTML pages, logout, and data deletion."""

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
from points import activities_total_points
from sqlalchemy.orm import joinedload, load_only

from ..background import schedule_initial_activity_sync
from ..competition_periods import (
    aggregates_frozen_team_scores,
    period_spec_for_index,
    points_by_athlete_for_results_table,
)
from ..extensions import db
from ..leaderboard import activities_by_athlete_scored, leaderboard_sections
from ..models import Activity, Athlete, Bonus, BrowserSession, Week, WeekScore
from ..session import BROWSER_TOKEN_SESSION_KEY, hash_session_token
from ..team_scoring import summaries_by_hub_and_department
from ..utils import (
    activity_start_date_for_display,
    athlete_display_name,
    athlete_hub_department_complete,
)

bp = Blueprint("main", __name__)


def _points_by_athlete_strava_id() -> dict[int, int]:
    """Total points per Strava athlete id (activities with a start_date only)."""
    by_athlete = activities_by_athlete_scored()
    return {aid: activities_total_points(acts) for aid, acts in by_athlete.items()}


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
        is_active=bool(athlete.is_active),
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


@bp.get("/leaders")
def leaders():
    sections = leaderboard_sections()
    return render_template("leaders.html", sections=sections)


@bp.get("/results")
def results():
    athlete = g.current_athlete
    show_athlete_points = _can_perform_organiser_tasks(athlete)
    points_by = points_by_athlete_for_results_table(current_app)
    hubs = current_app.config["HUB_OPTIONS"]
    departments = current_app.config["DEPARTMENT_OPTIONS"]
    hub_frozen, dept_frozen = aggregates_frozen_team_scores(
        list(hubs), list(departments)
    )
    hub_summary, department_summary = summaries_by_hub_and_department(
        points_by, hubs, departments
    )
    for row in hub_summary:
        row["points"] = float(row["points"]) + hub_frozen.get(str(row["name"]), 0.0)
    for row in department_summary:
        row["points"] = float(row["points"]) + dept_frozen.get(str(row["name"]), 0.0)
    hub_summary.sort(key=lambda r: (-float(r["points"]), str(r["name"])))
    department_summary.sort(key=lambda r: (-float(r["points"]), str(r["name"])))
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


@bp.get("/organiser/weeks")
def organiser_weeks():
    athlete = g.current_athlete
    if not _can_perform_organiser_tasks(athlete):
        abort(403)
    weeks = Week.query.order_by(Week.period_index.asc()).all()
    sections: list[dict[str, object]] = []
    for wk in weeks:
        scores = (
            WeekScore.query.filter_by(week_id=wk.id)
            .order_by(WeekScore.team_scope.asc(), WeekScore.target.asc())
            .all()
        )
        spec = period_spec_for_index(current_app, wk.period_index)
        sections.append(
            {
                "week": wk,
                "spec": spec,
                "scores": scores,
                "computed_at": wk.summarized_at,
            }
        )
    return render_template("organiser_weeks.html", sections=sections)


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
                Athlete.is_active,
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
            "is_active": bool(a.is_active),
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


@bp.post("/athletes/<int:athlete_pk>/make-inactive")
def athletes_make_inactive(athlete_pk: int):
    actor = g.current_athlete
    if not _can_perform_organiser_tasks(actor):
        abort(403)
    target = db.session.get(Athlete, athlete_pk)
    if target is None:
        abort(404)
    if (
        target.is_organiser
        or int(target.athlete_id) in current_app.config["APP_DEVELOPER_IDS"]
    ):
        abort(403)
    target.is_active = False
    db.session.commit()
    return redirect(url_for("main.athletes"))


@bp.post("/athletes/<int:athlete_pk>/resync-activities")
def athletes_resync_activities(athlete_pk: int):
    actor = g.current_athlete
    if not _can_perform_organiser_tasks(actor):
        abort(403)
    target = db.session.get(Athlete, athlete_pk)
    if target is None:
        abort(404)
    Activity.query.filter_by(athlete_id=target.athlete_id).delete(
        synchronize_session=False
    )
    db.session.commit()
    schedule_initial_activity_sync(
        current_app._get_current_object(), int(target.athlete_id)
    )
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
