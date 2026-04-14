"""Organiser-only per-athlete activity list at /athletes/<id>."""

import unittest
from datetime import datetime, timezone

from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.models import Activity, Athlete
from athletic_elf.session import BROWSER_TOKEN_SESSION_KEY, create_browser_session

from tests.test_hub_department import _TestHubDeptConfig


class TestAthleteActivitiesPage(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestHubDeptConfig)
        self.app.config["TESTING"] = True
        self.app.config["APP_DEVELOPER_IDS"] = frozenset()
        self.client = self.app.test_client()

    def _login(self, token: str) -> None:
        with self.client.session_transaction() as sess:
            sess[BROWSER_TOKEN_SESSION_KEY] = token

    def _seed_organiser_and_target(self) -> tuple[str, int]:
        """Returns (browser token, target Strava athlete id)."""
        with self.app.app_context():
            organiser = Athlete(
                athlete_id=930_001,
                firstname="Org",
                lastname="One",
                access_token="at",
                refresh_token="rt",
                expires_at=2_000_000_000,
                hub="North Hub",
                department="Engineering",
                is_organiser=True,
            )
            target = Athlete(
                athlete_id=930_002,
                firstname="Tar",
                lastname="Get",
                access_token="at2",
                refresh_token="rt2",
                expires_at=2_000_000_000,
                hub="South Hub",
                department="Sales",
            )
            db.session.add_all([organiser, target])
            db.session.flush()
            raw, _ = create_browser_session(int(organiser.athlete_id))
            db.session.add(
                Activity(
                    activity_id=77_001,
                    athlete_id=930_002,
                    distance=5000.0,
                    sport_type="Run",
                    start_date=datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc),
                    moving_time=1800,
                )
            )
            db.session.commit()
            return raw, int(target.athlete_id)

    def test_non_organiser_forbidden(self):
        with self.app.app_context():
            u = Athlete(
                athlete_id=930_010,
                firstname="P",
                lastname="art",
                access_token="at",
                refresh_token="rt",
                expires_at=2_000_000_000,
                hub="North Hub",
                department="Engineering",
                is_organiser=False,
            )
            db.session.add(u)
            db.session.flush()
            raw, _ = create_browser_session(int(u.athlete_id))
            db.session.commit()
        self._login(raw)
        rv = self.client.get("/athletes/930002")
        self.assertEqual(rv.status_code, 403)

    def test_organiser_sees_target_activities(self):
        token, target_id = self._seed_organiser_and_target()
        self._login(token)
        rv = self.client.get(f"/athletes/{target_id}")
        self.assertEqual(rv.status_code, 200)
        text = rv.get_data(as_text=True)
        self.assertIn("Tar Get", text)
        self.assertIn("Activities", text)
        self.assertIn(b"View on Strava", rv.data)
        self.assertIn(b"Run", rv.data)

    def test_unknown_athlete_404(self):
        token, _ = self._seed_organiser_and_target()
        self._login(token)
        rv = self.client.get("/athletes/999999999")
        self.assertEqual(rv.status_code, 404)
