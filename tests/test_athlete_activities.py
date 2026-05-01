"""Organiser-only per-athlete activity list at /athletes/<id>."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

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


class TestAthletesMakeInactive(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestHubDeptConfig)
        self.app.config["TESTING"] = True
        self.app.config["APP_DEVELOPER_IDS"] = frozenset()
        self.client = self.app.test_client()

    def _login(self, token: str) -> None:
        with self.client.session_transaction() as sess:
            sess[BROWSER_TOKEN_SESSION_KEY] = token

    def _seed(self) -> tuple[str, int]:
        with self.app.app_context():
            organiser = Athlete(
                athlete_id=931_001,
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
                athlete_id=931_002,
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
            db.session.commit()
            return raw, int(target.athlete_id)

    def test_organiser_post_sets_inactive(self):
        token, target_pk = self._seed()
        self._login(token)
        rv = self.client.post(
            f"/athletes/{target_pk}/make-inactive",
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.location.endswith("/athletes"))
        with self.app.app_context():
            row = Athlete.query.filter_by(athlete_id=target_pk).one()
            self.assertFalse(row.is_active)

    def test_non_organiser_forbidden(self):
        with self.app.app_context():
            u = Athlete(
                athlete_id=931_010,
                firstname="P",
                lastname="eer",
                access_token="at",
                refresh_token="rt",
                expires_at=2_000_000_000,
                hub="North Hub",
                department="Engineering",
                is_organiser=False,
            )
            victim = Athlete(
                athlete_id=931_011,
                firstname="V",
                lastname="ic",
                access_token="at3",
                refresh_token="rt3",
                expires_at=2_000_000_000,
                hub="North Hub",
                department="Engineering",
            )
            db.session.add_all([u, victim])
            db.session.flush()
            raw, _ = create_browser_session(int(u.athlete_id))
            db.session.commit()
        self._login(raw)
        rv = self.client.post("/athletes/931011/make-inactive")
        self.assertEqual(rv.status_code, 403)
        with self.app.app_context():
            self.assertTrue(Athlete.query.filter_by(athlete_id=931_011).one().is_active)

    def test_cannot_deactivate_organiser_target(self):
        token, _ = self._seed()
        self._login(token)
        rv = self.client.post("/athletes/931001/make-inactive")
        self.assertEqual(rv.status_code, 403)


class TestAthletesResyncActivities(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestHubDeptConfig)
        self.app.config["TESTING"] = True
        self.app.config["APP_DEVELOPER_IDS"] = frozenset()
        self.client = self.app.test_client()

    def _login(self, token: str) -> None:
        with self.client.session_transaction() as sess:
            sess[BROWSER_TOKEN_SESSION_KEY] = token

    def _seed_organiser_target_and_activity(self) -> tuple[str, int]:
        with self.app.app_context():
            organiser = Athlete(
                athlete_id=932_001,
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
                athlete_id=932_002,
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
                    activity_id=78_001,
                    athlete_id=932_002,
                    distance=3000.0,
                    sport_type="Ride",
                    start_date=datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc),
                    moving_time=900,
                )
            )
            db.session.commit()
            return raw, int(target.athlete_id)

    def test_resync_deletes_activities_and_schedules_sync(self):
        token, target_pk = self._seed_organiser_target_and_activity()
        self._login(token)
        with patch(
            "athletic_elf.blueprints.main.schedule_initial_activity_sync"
        ) as mock_sync:
            rv = self.client.post(
                f"/athletes/{target_pk}/resync-activities",
                follow_redirects=False,
            )
        self.assertEqual(rv.status_code, 302)
        self.assertTrue(rv.location.endswith("/athletes"))
        mock_sync.assert_called_once()
        args = mock_sync.call_args[0]
        self.assertIs(args[0], self.app)
        self.assertEqual(args[1], target_pk)
        with self.app.app_context():
            self.assertEqual(
                Activity.query.filter_by(athlete_id=target_pk).count(),
                0,
            )

    def test_resync_forbidden_for_non_organiser(self):
        token, target_pk = self._seed_organiser_target_and_activity()
        with self.app.app_context():
            peer = Athlete(
                athlete_id=932_010,
                firstname="P",
                lastname="eer",
                access_token="at3",
                refresh_token="rt3",
                expires_at=2_000_000_000,
                hub="North Hub",
                department="Engineering",
                is_organiser=False,
            )
            db.session.add(peer)
            db.session.flush()
            peer_token, _ = create_browser_session(int(peer.athlete_id))
            db.session.commit()
        self._login(peer_token)
        with patch(
            "athletic_elf.blueprints.main.schedule_initial_activity_sync"
        ) as mock_sync:
            rv = self.client.post(f"/athletes/{target_pk}/resync-activities")
        self.assertEqual(rv.status_code, 403)
        mock_sync.assert_not_called()
        with self.app.app_context():
            self.assertEqual(
                Activity.query.filter_by(athlete_id=target_pk).count(),
                1,
            )

    def test_resync_unknown_athlete_404(self):
        with self.app.app_context():
            organiser = Athlete(
                athlete_id=932_101,
                firstname="O",
                lastname="r",
                access_token="at",
                refresh_token="rt",
                expires_at=2_000_000_000,
                hub="North Hub",
                department="Engineering",
                is_organiser=True,
            )
            db.session.add(organiser)
            db.session.flush()
            raw, _ = create_browser_session(int(organiser.athlete_id))
            db.session.commit()
        self._login(raw)
        with patch(
            "athletic_elf.blueprints.main.schedule_initial_activity_sync"
        ) as mock_sync:
            rv = self.client.post("/athletes/999999999/resync-activities")
        self.assertEqual(rv.status_code, 404)
        mock_sync.assert_not_called()


class TestAthletesIndexPage(unittest.TestCase):
    """Organiser-only /athletes table and query-param sorting."""

    def setUp(self):
        self.app = create_app(_TestHubDeptConfig)
        self.app.config["TESTING"] = True
        self.app.config["APP_DEVELOPER_IDS"] = frozenset()
        self.client = self.app.test_client()

    def _login(self, token: str) -> None:
        with self.client.session_transaction() as sess:
            sess[BROWSER_TOKEN_SESSION_KEY] = token

    def _seed_sorted_table(self) -> str:
        """Organiser Zed (id first), then Amy, Bob — default list order follows ids."""
        with self.app.app_context():
            organiser = Athlete(
                athlete_id=940_001,
                firstname="Zed",
                lastname="Organiser",
                access_token="at",
                refresh_token="rt",
                expires_at=2_000_000_000,
                hub="North Hub",
                department="Engineering",
                is_organiser=True,
            )
            amy = Athlete(
                athlete_id=940_002,
                firstname="Amy",
                lastname="Participant",
                access_token="at2",
                refresh_token="rt2",
                expires_at=2_000_000_000,
                hub="South Hub",
                department="Sales",
            )
            bob = Athlete(
                athlete_id=940_003,
                firstname="Bob",
                lastname="Participant",
                access_token="at3",
                refresh_token="rt3",
                expires_at=2_000_000_000,
                hub="East Hub",
                department="Marketing",
            )
            db.session.add_all([organiser, amy, bob])
            db.session.flush()
            raw, _ = create_browser_session(int(organiser.athlete_id))
            db.session.commit()
            return raw

    def test_non_organiser_forbidden(self):
        with self.app.app_context():
            u = Athlete(
                athlete_id=940_010,
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
        rv = self.client.get("/athletes")
        self.assertEqual(rv.status_code, 403)

    def test_sort_name_asc(self):
        token = self._seed_sorted_table()
        self._login(token)
        rv = self.client.get("/athletes?sort=name&order=asc")
        self.assertEqual(rv.status_code, 200)
        text = rv.get_data(as_text=True)
        pos_amy = text.find("Amy Participant")
        pos_bob = text.find("Bob Participant")
        pos_zed = text.find("Zed Organiser")
        self.assertLess(pos_amy, pos_bob)
        self.assertLess(pos_bob, pos_zed)

    def test_invalid_sort_param_ignored(self):
        token = self._seed_sorted_table()
        self._login(token)
        rv = self.client.get("/athletes?sort=notacolumn&order=desc")
        self.assertEqual(rv.status_code, 200)
        text = rv.get_data(as_text=True)
        pos_zed = text.find("Zed Organiser")
        pos_amy = text.find("Amy Participant")
        pos_bob = text.find("Bob Participant")
        self.assertLess(pos_zed, pos_amy)
        self.assertLess(pos_amy, pos_bob)

    def test_sort_score_desc_tiebreak_by_id(self):
        token = self._seed_sorted_table()
        self._login(token)
        with self.app.app_context():
            db.session.add(
                Activity(
                    activity_id=77_902,
                    athlete_id=940_002,
                    distance=50_000.0,
                    sport_type="Run",
                    start_date=datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc),
                    moving_time=3600,
                )
            )
            db.session.commit()
        rv = self.client.get("/athletes?sort=score&order=desc")
        self.assertEqual(rv.status_code, 200)
        text = rv.get_data(as_text=True)
        pos_amy = text.find("Amy Participant")
        pos_bob = text.find("Bob Participant")
        pos_zed = text.find("Zed Organiser")
        self.assertLess(pos_amy, pos_bob)
        self.assertLess(pos_bob, pos_zed)

    def test_filter_hub_shows_only_same_hub(self):
        token = self._seed_sorted_table()
        self._login(token)
        rv = self.client.get("/athletes?filter=hub")
        self.assertEqual(rv.status_code, 200)
        text = rv.get_data(as_text=True)
        self.assertIn("Zed Organiser", text)
        self.assertNotIn("Amy Participant", text)
        self.assertNotIn("Bob Participant", text)

    def test_filter_department_shows_only_same_department(self):
        token = self._seed_sorted_table()
        self._login(token)
        rv = self.client.get("/athletes?filter=department")
        self.assertEqual(rv.status_code, 200)
        text = rv.get_data(as_text=True)
        self.assertIn("Zed Organiser", text)
        self.assertNotIn("Amy Participant", text)
        self.assertNotIn("Bob Participant", text)

    def test_invalid_filter_param_shows_all(self):
        token = self._seed_sorted_table()
        self._login(token)
        rv = self.client.get("/athletes?filter=notafilter")
        self.assertEqual(rv.status_code, 200)
        text = rv.get_data(as_text=True)
        self.assertIn("Zed Organiser", text)
        self.assertIn("Amy Participant", text)
        self.assertIn("Bob Participant", text)

    def test_filter_preserves_sort_query(self):
        token = self._seed_sorted_table()
        self._login(token)
        rv = self.client.get("/athletes?filter=hub&sort=name&order=asc")
        self.assertEqual(rv.status_code, 200)
        text = rv.get_data(as_text=True)
        self.assertIn("sort=name", text)
        self.assertIn("order=asc", text)
        self.assertIn("filter=hub", text)
