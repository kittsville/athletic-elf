"""OAuth callback: Strava token exchange and signup blocking."""

import unittest
from unittest.mock import MagicMock, patch

from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.models import Athlete

from tests.test_hub_department import _TestHubDeptConfig


class _OAuthCallbackConfig(_TestHubDeptConfig):
    CLIENT_ID = "test-client-id"
    CLIENT_SECRET = "test-client-secret"
    DOMAIN = "https://oauth-test.example"


_TOKEN_PAYLOAD = {
    "access_token": "new-access",
    "refresh_token": "new-refresh",
    "expires_at": 2_000_000_000,
    "athlete": {"id": 424242, "firstname": "Sam", "lastname": "River"},
}


class TestOAuthCallback(unittest.TestCase):
    def setUp(self):
        self.token_patcher = patch(
            "athletic_elf.blueprints.oauth.http_client.post",
            autospec=True,
        )
        self.mock_post = self.token_patcher.start()
        self.mock_resp = MagicMock()
        self.mock_resp.raise_for_status = MagicMock()
        self.mock_resp.json.return_value = _TOKEN_PAYLOAD
        self.mock_post.return_value = self.mock_resp

        self.sub_patcher = patch(
            "athletic_elf.blueprints.oauth.ensure_push_subscription",
            autospec=True,
        )
        self.sub_patcher.start()

        self.sync_patcher = patch(
            "athletic_elf.blueprints.oauth.schedule_initial_activity_sync",
            autospec=True,
        )
        self.sync_patcher.start()

    def tearDown(self):
        self.sync_patcher.stop()
        self.sub_patcher.stop()
        self.token_patcher.stop()

    def test_block_signups_returns_403_without_creating_athlete(self):
        class _Blocked(_OAuthCallbackConfig):
            BLOCK_SIGNUPS = True

        app = create_app(_Blocked)
        app.config["TESTING"] = True
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["oauth_state"] = "st"

        rv = client.get(
            "/oauth/callback",
            query_string={"code": "auth-code", "state": "st"},
        )
        self.assertEqual(rv.status_code, 403)
        self.assertIn(b"disabled", rv.data.lower())

        with app.app_context():
            self.assertIsNone(Athlete.query.filter_by(athlete_id=424242).first())

    def test_block_signups_allows_existing_athlete_login(self):
        class _Blocked(_OAuthCallbackConfig):
            BLOCK_SIGNUPS = True

        app = create_app(_Blocked)
        app.config["TESTING"] = True

        with app.app_context():
            db.session.add(
                Athlete(
                    athlete_id=424242,
                    firstname="Old",
                    lastname="Name",
                    access_token="old-at",
                    refresh_token="old-rt",
                    expires_at=1,
                    hub="North Hub",
                    department="Engineering",
                )
            )
            db.session.commit()

        client = app.test_client()
        with client.session_transaction() as sess:
            sess["oauth_state"] = "st"

        rv = client.get(
            "/oauth/callback",
            query_string={"code": "auth-code", "state": "st"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)

        with app.app_context():
            row = Athlete.query.filter_by(athlete_id=424242).one()
            self.assertEqual(row.access_token, "new-access")
            self.assertEqual(row.refresh_token, "new-refresh")

    def test_signups_allowed_when_not_blocked(self):
        app = create_app(_OAuthCallbackConfig)
        app.config["TESTING"] = True
        client = app.test_client()

        with client.session_transaction() as sess:
            sess["oauth_state"] = "st"

        rv = client.get(
            "/oauth/callback",
            query_string={"code": "auth-code", "state": "st"},
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)

        with app.app_context():
            row = Athlete.query.filter_by(athlete_id=424242).one()
            self.assertEqual(row.firstname, "Sam")


if __name__ == "__main__":
    unittest.main()
