"""Strava OAuth deauthorization when blocking or deleting athletes."""

import unittest
from unittest.mock import MagicMock, patch

from athletic_elf.factory import create_app
from athletic_elf.models import Athlete
from athletic_elf.strava_service import deauthorize_athlete

from tests.test_hub_department import _TestHubDeptConfig


class TestDeauthorizeAthlete(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestHubDeptConfig)
        self.app.config["TESTING"] = True
        self.post_patcher = patch(
            "athletic_elf.strava_service.http_client.post",
            autospec=True,
        )
        self.mock_post = self.post_patcher.start()

    def tearDown(self):
        self.post_patcher.stop()

    def _athlete(self) -> Athlete:
        return Athlete(
            athlete_id=880_001,
            firstname="Sam",
            lastname="River",
            access_token="access-tok",
            refresh_token="refresh-tok",
            expires_at=2_000_000_000,
            hub="North Hub",
            department="Engineering",
        )

    def test_posts_access_token_to_deauthorize_endpoint(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.ok = True
        self.mock_post.return_value = resp
        athlete = self._athlete()
        with self.app.app_context():
            deauthorize_athlete(athlete)
        self.mock_post.assert_called_once()
        args, kwargs = self.mock_post.call_args
        self.assertEqual(args[0], self.app.config["STRAVA_OAUTH_DEAUTHORIZE"])
        self.assertEqual(kwargs["params"]["access_token"], "access-tok")

    def test_401_returns_without_raising(self):
        resp = MagicMock()
        resp.status_code = 401
        resp.ok = False
        self.mock_post.return_value = resp
        athlete = self._athlete()
        with self.app.app_context():
            deauthorize_athlete(athlete)
        self.mock_post.assert_called_once()

    def test_other_errors_raise(self):
        resp = MagicMock()
        resp.status_code = 503
        resp.ok = False
        resp.raise_for_status.side_effect = Exception("503")
        self.mock_post.return_value = resp
        athlete = self._athlete()
        with self.app.app_context():
            with self.assertRaises(Exception):
                deauthorize_athlete(athlete)
