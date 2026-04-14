"""/leaders page (session required) and discipline_totals_for_activities."""

import unittest
from datetime import datetime, timezone

from athletic_elf.config import Config
from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.models import Activity, Athlete
from athletic_elf.session import BROWSER_TOKEN_SESSION_KEY, create_browser_session
from points import discipline_totals_for_activities


class _LeadersTestConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-for-leaders-tests-xx"
    VERIFY_TOKEN = "test-verify-token-leaders"
    ENFORCE_HTTPS = False
    CRON_SECRET = "test-cron-secret"
    AUTO_CREATE_TABLES = True
    HUB_OPTIONS = ("North Hub",)
    DEPARTMENT_OPTIONS = ("Engineering",)


_D0 = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestDisciplineTotals(unittest.TestCase):
    def test_sums_match_strava_buckets(self):
        acts = [
            Activity(
                activity_id=1,
                athlete_id=1,
                distance=1000,
                sport_type="Swim",
                start_date=_D0,
                moving_time=60,
            ),
            Activity(
                activity_id=2,
                athlete_id=1,
                distance=2000,
                sport_type="Walk",
                start_date=_D0,
                moving_time=60,
            ),
            Activity(
                activity_id=3,
                athlete_id=1,
                distance=3000,
                sport_type="Ride",
                start_date=_D0,
                moving_time=60,
            ),
            Activity(
                activity_id=4,
                athlete_id=1,
                distance=1600,
                sport_type="Run",
                start_date=_D0,
                moving_time=60,
            ),
            Activity(
                activity_id=5,
                athlete_id=1,
                distance=0,
                sport_type="Yoga",
                start_date=_D0,
                moving_time=120,
            ),
            Activity(
                activity_id=6,
                athlete_id=1,
                distance=0,
                sport_type="WeightTraining",
                start_date=_D0,
                moving_time=900,
            ),
        ]
        t = discipline_totals_for_activities(acts)
        self.assertAlmostEqual(t["swim_km"], 1.0)
        self.assertAlmostEqual(t["walk_km"], 2.0)
        self.assertAlmostEqual(t["cycle_km"], 3.0)
        self.assertAlmostEqual(t["run_km"], 1.6)
        self.assertEqual(t["zen_min"], 2)
        self.assertEqual(t["hard_min"], 15)


class TestLeadersPage(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_LeadersTestConfig)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_anonymous_get_leaders_redirects_home(self):
        r = self.client.get("/leaders", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        loc = r.headers.get("Location", "")
        self.assertTrue(loc == "/" or loc.endswith("/"), msg=loc)

    def test_ordering_swimming(self):
        browser_token = ""
        with self.app.app_context():
            db.session.add_all(
                [
                    Athlete(
                        athlete_id=101,
                        firstname="Amy",
                        lastname="Swimmer",
                        access_token="a",
                        refresh_token="r",
                        expires_at=2_000_000_000,
                    ),
                    Athlete(
                        athlete_id=102,
                        firstname="Bob",
                        lastname="Wade",
                        access_token="a",
                        refresh_token="r",
                        expires_at=2_000_000_000,
                    ),
                    Activity(
                        activity_id=901,
                        athlete_id=101,
                        distance=800,
                        sport_type="Swim",
                        start_date=_D0,
                        moving_time=600,
                    ),
                    Activity(
                        activity_id=902,
                        athlete_id=102,
                        distance=400,
                        sport_type="Swim",
                        start_date=_D0,
                        moving_time=300,
                    ),
                ]
            )
            db.session.commit()
            browser_token, _ = create_browser_session(101)
            db.session.commit()

        with self.client.session_transaction() as sess:
            sess[BROWSER_TOKEN_SESSION_KEY] = browser_token

        r = self.client.get("/leaders")
        self.assertEqual(r.status_code, 200)
        self.assertLess(
            r.data.find(b"Amy Swimmer"),
            r.data.find(b"Bob Wade"),
        )


if __name__ == "__main__":
    unittest.main()
