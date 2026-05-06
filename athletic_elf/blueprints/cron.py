"""POST /cron: bearer auth and background maintenance (sessions, activity enrichment)."""

import secrets
import threading
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, request
from sqlalchemy import func, or_

from ..competition_periods import summarize_due_periods_loop
from ..extensions import db
from ..models import Athlete, BrowserSession
from ..strava_service import process_activities

bp = Blueprint("cron", __name__)


def _athlete_missing_hub_or_department_clause():
    """Matches rows where ``athlete_hub_department_complete`` would be false."""
    h_len = func.length(func.trim(func.coalesce(Athlete.hub, "")))
    d_len = func.length(func.trim(func.coalesce(Athlete.department, "")))
    return or_(h_len == 0, d_len == 0)


def cron_authorization_ok(expected: str, authorization: str | None) -> bool:
    if not authorization or not authorization.startswith("Bearer "):
        return False
    presented = authorization.removeprefix("Bearer ").strip()
    return secrets.compare_digest(presented, expected)


def run_cron_maintenance(app) -> None:
    """
    Session cleanup, activity enrichment, then period summarization.

    Commits after cleanup and enrichment so Strava work is persisted even if
    summarization fails later. A second commit persists summarization.
    Must run inside ``app.app_context()``.
    """
    with app.app_context():
        try:
            now = datetime.now(timezone.utc)
            removed_sessions = BrowserSession.query.filter(
                BrowserSession.expires_at < now
            ).delete(synchronize_session=False)
            cutoff = now - timedelta(hours=24)
            removed_incomplete = Athlete.query.filter(
                Athlete.created_at < cutoff,
                _athlete_missing_hub_or_department_clause(),
            ).delete(synchronize_session=False)
            n = process_activities(75)
            db.session.commit()
            n_periods = summarize_due_periods_loop(app)
            db.session.commit()
            summary = (
                f"Processed {n} activities, removed {removed_sessions} expired session(s), "
                f"removed {removed_incomplete} stale incomplete athlete(s), "
                f"summarized {n_periods} competition period(s)"
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
    if not cron_authorization_ok(expected, request.headers.get("Authorization")):
        abort(403)
    app = current_app._get_current_object()
    threading.Thread(
        target=run_cron_maintenance,
        args=(app,),
        daemon=True,
    ).start()
    return ("Processing Started", 200)
