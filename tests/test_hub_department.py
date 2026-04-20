"""Hub / Department signup form and OAuth redirect behavior."""

import unittest

from athletic_elf.config import Config, parse_comma_options
from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.models import Athlete
from athletic_elf.session import BROWSER_TOKEN_SESSION_KEY, create_browser_session
from athletic_elf.utils import athlete_hub_department_complete, athlete_role_label


class _TestHubDeptConfig(Config):
    """Isolate DB from developer DATABASE_URL; tables include hub/department columns."""

    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-for-hub-dept-tests-xx"
    VERIFY_TOKEN = "test-verify-token-hub-dept"
    ENFORCE_HTTPS = False
    CRON_SECRET = "test-cron-secret"
    AUTO_CREATE_TABLES = True
    # Fixed options so tests do not depend on HUB_OPTIONS / DEPARTMENT_OPTIONS in os.environ.
    HUB_OPTIONS = ("North Hub", "South Hub", "East Hub", "West Hub")
    DEPARTMENT_OPTIONS = ("Engineering", "Sales", "Marketing", "Operations")
    ACTIVITY_START_DATE = "2020-01-01T00:00:00+00:00"
    WEEK_BOUNDARIES = ""
    ACTIVITY_END_DATE = "2030-01-01T00:00:00+00:00"


class TestParseCommaOptions(unittest.TestCase):
    def test_splits_and_strips(self):
        self.assertEqual(
            parse_comma_options(" a , b , c ", "x"),
            ("a", "b", "c"),
        )

    def test_default_when_empty(self):
        self.assertEqual(
            parse_comma_options("", "One,Two"),
            ("One", "Two"),
        )


class TestAthleteRoleLabel(unittest.TestCase):
    def test_organiser_takes_precedence_over_inactive(self):
        self.assertEqual(
            athlete_role_label(False, True, is_active=False),
            "Competition Organiser",
        )

    def test_active_organiser(self):
        self.assertEqual(
            athlete_role_label(False, True, is_active=True),
            "Competition Organiser",
        )

    def test_inactive_participant(self):
        self.assertEqual(
            athlete_role_label(False, False, is_active=False),
            "Inactive Participant",
        )

    def test_app_developer_ignores_inactive_flag(self):
        self.assertEqual(
            athlete_role_label(True, False, is_active=False),
            "App developer",
        )


class TestAthleteHubDepartmentComplete(unittest.TestCase):
    def test_false_when_missing(self):
        self.assertFalse(athlete_hub_department_complete(None, None))
        self.assertFalse(athlete_hub_department_complete("", "Eng"))
        self.assertFalse(athlete_hub_department_complete("Hub", "  "))

    def test_true_when_both_non_empty(self):
        self.assertTrue(athlete_hub_department_complete("North", "Engineering"))


class TestHubDepartmentForm(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestHubDeptConfig)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _login_as(self, browser_token: str) -> None:
        with self.client.session_transaction() as sess:
            sess[BROWSER_TOKEN_SESSION_KEY] = browser_token

    def _make_athlete(
        self,
        *,
        hub: str | None = None,
        department: str | None = None,
    ) -> tuple[Athlete, str]:
        with self.app.app_context():
            a = Athlete(
                athlete_id=999001,
                firstname="T",
                lastname="E",
                access_token="at",
                refresh_token="rt",
                expires_at=2_000_000_000,
                hub=hub,
                department=department,
            )
            db.session.add(a)
            db.session.flush()
            raw, _ = create_browser_session(int(a.athlete_id))
            db.session.commit()
            return a, raw

    def test_get_form_when_incomplete(self):
        _, token = self._make_athlete()
        self._login_as(token)
        rv = self.client.get("/form")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"Please select your Hub and Department", rv.data)

    def test_get_redirects_when_complete(self):
        _, token = self._make_athlete(hub="North Hub", department="Engineering")
        self._login_as(token)
        rv = self.client.get("/form", follow_redirects=False)
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.location.endswith("/"))

    def test_post_saves_and_redirects(self):
        _, token = self._make_athlete()
        self._login_as(token)
        rv = self.client.post(
            "/form",
            data={"hub": "North Hub", "department": "Engineering"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.location.endswith("/"))
        with self.app.app_context():
            row = Athlete.query.filter_by(athlete_id=999001).one()
            self.assertEqual(row.hub, "North Hub")
            self.assertEqual(row.department, "Engineering")

    def test_post_400_when_already_complete(self):
        _, token = self._make_athlete(hub="North Hub", department="Engineering")
        self._login_as(token)
        rv = self.client.post(
            "/form",
            data={"hub": "South Hub", "department": "Sales"},
        )
        self.assertEqual(rv.status_code, 400)

    def test_post_400_invalid_option(self):
        _, token = self._make_athlete()
        self._login_as(token)
        rv = self.client.post(
            "/form",
            data={"hub": "Not A Real Hub", "department": "Engineering"},
        )
        self.assertEqual(rv.status_code, 400)


if __name__ == "__main__":
    unittest.main()
