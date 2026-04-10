"""Fire-and-forget background tasks (in-process; see docstrings for limits)."""

import threading

from flask import Flask


def schedule_initial_activity_sync(app: Flask, athlete_pk: int) -> None:
    """
    Run Strava historical activity import without blocking the OAuth response.

    Uses a daemon thread and an application context. Suitable for single-process
    deploys; for horizontal scale use a real queue (Celery, RQ, etc.).
    """

    def run() -> None:
        with app.app_context():
            from .strava_service import sync_activities_since_competition_start

            try:
                n = sync_activities_since_competition_start(athlete_pk)
                app.logger.info(
                    "Initial Strava activity sync finished for athlete pk=%s "
                    "(%s activities in API pages)",
                    athlete_pk,
                    n,
                )
            except Exception:
                app.logger.exception(
                    "Initial Strava activity sync failed for athlete pk=%s", athlete_pk
                )

    t = threading.Thread(
        target=run,
        name=f"strava-initial-sync-{athlete_pk}",
        daemon=True,
    )
    t.start()
