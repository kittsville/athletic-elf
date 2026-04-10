"""HTML pages, logout, data deletion, and cron."""

from collections import defaultdict
from datetime import datetime, timezone

from flask import Blueprint, current_app, g, redirect, render_template, session, url_for
from points import activities_total_points

from ..extensions import db
from ..models import Activity, Athelete, BrowserSession
from ..session import BROWSER_TOKEN_SESSION_KEY, hash_session_token
from ..strava_service import process_activities
from ..utils import athlete_display_name, format_moving_time

bp = Blueprint("main", __name__)


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
    return render_template(
        "index.html",
        logged_in=True,
        strava_id=strava_id,
        name=name,
        is_app_developer=is_app_developer,
        activities=activities,
        team_points=team_points,
        format_moving_time=format_moving_time,
    )


@bp.post("/delete-my-data")
def delete_my_data():
    athlete = g.current_athlete
    pk = athlete.id
    strava_athlete_id = athlete.athlete_id
    BrowserSession.query.filter_by(athelete_id=pk).delete(synchronize_session=False)
    Activity.query.filter_by(athlete_id=strava_athlete_id).delete(
        synchronize_session=False
    )
    Athelete.query.filter_by(id=pk).delete(synchronize_session=False)
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
    activities = (
        Activity.query.filter(
            Activity.athlete_id.isnot(None),
            Activity.start_date.isnot(None),
        )
        .order_by(Activity.athlete_id, Activity.id)
        .all()
    )
    by_athlete = defaultdict(list)
    for a in activities:
        by_athlete[a.athlete_id].append(a)

    rows = []
    for athlete_id, acts in by_athlete.items():
        athelete = Athelete.query.filter_by(athlete_id=athlete_id).first()
        if athelete:
            fn = athelete.firstname or ""
            ln = athelete.lastname or ""
        else:
            fn, ln = "", ""
        pts = activities_total_points(acts)
        rows.append(
            {
                "firstname": fn,
                "lastname": ln,
                "athlete_id": athlete_id,
                "points": pts,
            }
        )

    rows.sort(key=lambda r: (-r["points"], r["athlete_id"]))
    return render_template("results.html", rows=rows)
