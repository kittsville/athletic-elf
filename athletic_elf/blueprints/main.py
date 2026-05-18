"""HTML pages, logout, and data deletion."""

import math
import secrets
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
    points_by_athlete_competition_totals,
    points_by_athlete_for_results_table,
    recalculate_week_scores,
)
from ..extensions import db
from ..leaderboard import activities_by_athlete_scored, leaderboard_sections
from ..models import (
    AUDIT_TYPE_ACTIVITY_RESYNC_TRIGGERED,
    Activity,
    Athlete,
    AuditItem,
    Ban,
    Bonus,
    BrowserSession,
    Week,
    WeekScore,
)
from ..session import BROWSER_TOKEN_SESSION_KEY, hash_session_token
from ..strava_service import deauthorize_athlete
from ..team_scoring import summaries_by_hub_and_department
from ..utils import (
    athlete_display_name,
    athlete_hub_department_complete,
    athlete_role_label,
    banned_strava_id_hash,
)

bp = Blueprint("main", __name__)

_ATHLETES_SORTABLE = frozenset({"name", "hub", "department", "score", "role"})
_ATHLETES_FILTERS = frozenset({"all", "hub", "department"})


def _points_by_athlete_strava_id() -> dict[int, float]:
    """Total points per Strava athlete id (activities with a start_date only)."""
    by_athlete = activities_by_athlete_scored()
    return {aid: activities_total_points(acts) for aid, acts in by_athlete.items()}


def _can_perform_organiser_tasks(athlete: Athlete) -> bool:
    return (
        athlete.is_organiser
        or int(athlete.athlete_id) in current_app.config["APP_DEVELOPER_IDS"]
    )


def _can_perform_app_developer_tasks(athlete: Athlete) -> bool:
    return int(athlete.athlete_id) in current_app.config["APP_DEVELOPER_IDS"]


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
    return {
        "show_organiser_nav": show_organiser_nav,
        "block_signups": bool(current_app.config["BLOCK_SIGNUPS"]),
    }


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
    activity_points = {a.id: activities_total_points([a]) for a in scored}
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
        activity_points=activity_points,
    )


def _hub_department_from_form() -> tuple[str, str] | None:
    hubs = current_app.config["HUB_OPTIONS"]
    departments = current_app.config["DEPARTMENT_OPTIONS"]
    hub = (request.form.get("hub") or "").strip()
    department = (request.form.get("department") or "").strip()
    if hub not in hubs or department not in departments:
        return None
    return hub, department


def _athlete_managed_by_organiser(target: Athlete) -> bool:
    return not (
        target.is_organiser
        or int(target.athlete_id) in current_app.config["APP_DEVELOPER_IDS"]
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

    parsed = _hub_department_from_form()
    if parsed is None:
        abort(400)
    hub, department = parsed

    athlete.hub = hub
    athlete.department = department
    db.session.commit()
    return redirect(url_for("main.index"))


def _render_settings_page(mcp_key_revealed: str | None = None):
    athlete = g.current_athlete
    has_mcp_key = bool(athlete and athlete.mcp_key)
    return render_template(
        "settings.html",
        has_mcp_key=has_mcp_key,
        mcp_key_revealed=mcp_key_revealed,
    )


@bp.get("/settings")
def settings():
    return _render_settings_page()


@bp.post("/settings/mcp-key")
def settings_generate_mcp_key():
    athlete = g.current_athlete
    raw = secrets.token_urlsafe(32)
    athlete.mcp_key = hash_session_token(raw)
    db.session.commit()
    return _render_settings_page(mcp_key_revealed=raw)


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
    athlete_points_for_table = (
        points_by_athlete_competition_totals(current_app)
        if show_athlete_points
        else points_by
    )
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
        for athlete_id, pts in athlete_points_for_table.items():
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
def weeks():
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
    return render_template(
        "weeks.html",
        sections=sections,
        can_recalculate_weeks=_can_perform_app_developer_tasks(athlete),
    )


@bp.post("/organiser/weeks/<int:week_id>/recalculate")
def week_recalculate(week_id: int):
    athlete = g.current_athlete
    if not _can_perform_app_developer_tasks(athlete):
        abort(403)
    week = db.session.get(Week, week_id)
    if week is None:
        abort(404)
    try:
        recalculate_week_scores(current_app, week)
    except ValueError:
        abort(400)
    db.session.commit()
    return redirect(url_for("main.weeks", _anchor=f"week-heading-{week.id}"))


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
    filter_mode = (request.args.get("filter") or "all").strip().lower()
    if filter_mode not in _ATHLETES_FILTERS:
        filter_mode = "all"
    my_hub = (athlete.hub or "").strip()
    my_dept = (athlete.department or "").strip()
    if filter_mode == "hub":
        if my_hub:
            roster = [
                a
                for a in roster
                if (a.hub or "").strip().casefold() == my_hub.casefold()
            ]
        else:
            roster = [a for a in roster if not (a.hub or "").strip()]
    elif filter_mode == "department":
        if my_dept:
            roster = [
                a
                for a in roster
                if (a.department or "").strip().casefold() == my_dept.casefold()
            ]
        else:
            roster = [a for a in roster if not (a.department or "").strip()]
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
    sort_col = (request.args.get("sort") or "").strip().lower()
    if sort_col not in _ATHLETES_SORTABLE:
        sort_col = None
    sort_order = (request.args.get("order") or "asc").strip().lower()
    if sort_order not in ("asc", "desc"):
        sort_order = "asc"
    if sort_col is not None:
        reverse = sort_order == "desc"

        def _row_sort_key(row: dict[str, object]) -> tuple[object, int]:
            aid = int(row["athlete_id"])
            if sort_col == "name":
                return (str(row["name"]).casefold(), aid)
            if sort_col == "hub":
                return (str(row["hub"]).casefold(), aid)
            if sort_col == "department":
                return (str(row["department"]).casefold(), aid)
            if sort_col == "score":
                return (float(row["score"]), aid)
            label = athlete_role_label(
                bool(row["is_app_developer"]),
                bool(row["is_organiser"]),
                bool(row["is_active"]),
            )
            return (label.casefold(), aid)

        table_rows.sort(key=_row_sort_key, reverse=reverse)
    athletes_sort_qs: dict[str, str] = {}
    if sort_col is not None:
        athletes_sort_qs["sort"] = sort_col
        athletes_sort_qs["order"] = sort_order
    return render_template(
        "athletes.html",
        rows=table_rows,
        athletes_sort=sort_col,
        athletes_order=sort_order,
        athletes_filter=filter_mode,
        athletes_sort_qs=athletes_sort_qs,
    )


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
    activity_points = {a.id: activities_total_points([a]) for a in scored}
    viewed_name = athlete_display_name(target.firstname or "", target.lastname or "")
    dev_ids = current_app.config["APP_DEVELOPER_IDS"]
    return render_template(
        "athlete_overview.html",
        viewed_athlete_id=athlete_id,
        viewed_name=viewed_name,
        viewed_is_organiser=bool(target.is_organiser),
        viewed_is_app_developer=int(target.athlete_id) in dev_ids,
        viewed_is_active=bool(target.is_active),
        activities=activities,
        team_points=team_points_val,
        activity_points=activity_points,
    )


@bp.post("/athletes/<int:athlete_id>/activities/<int:activity_pk>/delete")
def athlete_activity_delete(athlete_id: int, activity_pk: int):
    actor = g.current_athlete
    if not _can_perform_organiser_tasks(actor):
        abort(403)
    target = db.session.get(Athlete, athlete_id)
    if target is None:
        abort(404)
    activity = db.session.get(Activity, activity_pk)
    if activity is None or int(activity.athlete_id) != int(athlete_id):
        abort(404)
    db.session.delete(activity)
    db.session.commit()
    return redirect(url_for("main.athlete_activities", athlete_id=athlete_id))


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
    return redirect(url_for("main.athlete_activities", athlete_id=athlete_pk))


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
    return redirect(url_for("main.athlete_activities", athlete_id=athlete_pk))


@bp.route("/athletes/<int:athlete_pk>/move", methods=["GET", "POST"])
def athletes_move_hub_department(athlete_pk: int):
    actor = g.current_athlete
    if not _can_perform_organiser_tasks(actor):
        abort(403)
    target = db.session.get(Athlete, athlete_pk)
    if target is None:
        abort(404)
    if not _athlete_managed_by_organiser(target):
        abort(403)

    hubs = current_app.config["HUB_OPTIONS"]
    departments = current_app.config["DEPARTMENT_OPTIONS"]
    viewed_name = athlete_display_name(target.firstname or "", target.lastname or "")

    if request.method == "GET":
        selected_hub = target.hub if target.hub in hubs else None
        selected_department = (
            target.department if target.department in departments else None
        )
        return render_template(
            "athlete_hub_department.html",
            viewed_athlete_id=athlete_pk,
            viewed_name=viewed_name,
            hub_options=hubs,
            department_options=departments,
            selected_hub=selected_hub,
            selected_department=selected_department,
        )

    parsed = _hub_department_from_form()
    if parsed is None:
        abort(400)
    hub, department = parsed
    target.hub = hub
    target.department = department
    db.session.commit()
    return redirect(url_for("main.athlete_activities", athlete_id=athlete_pk))


@bp.post("/athletes/<int:athlete_pk>/delete")
def athletes_delete(athlete_pk: int):
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
    deauthorize_athlete(target)
    want_ban = (request.form.get("ban") or "").strip() == "1"
    if want_ban:
        db.session.add(
            Ban(
                banned_id_hash=banned_strava_id_hash(int(target.athlete_id)),
                created_at=datetime.now(timezone.utc),
                banned_by_athlete_id=int(actor.athlete_id),
            )
        )
    tid = int(target.athlete_id)
    Ban.query.filter_by(banned_by_athlete_id=tid).delete(synchronize_session=False)
    db.session.delete(target)
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
    db.session.add(
        AuditItem(
            audit_type=AUDIT_TYPE_ACTIVITY_RESYNC_TRIGGERED,
            source=str(actor.athlete_id),
            target=str(target.athlete_id),
        )
    )
    db.session.commit()
    schedule_initial_activity_sync(
        current_app._get_current_object(), int(target.athlete_id)
    )
    return redirect(url_for("main.athlete_activities", athlete_id=athlete_pk))


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
                points_val = float(points_raw)
            except ValueError:
                error = "Points must be a number."
            else:
                if not math.isfinite(points_val) or points_val <= 0:
                    error = "Points must be a finite number greater than 0."
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
