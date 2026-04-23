"""Settings page and MCP key generation."""

import re
import unittest
from urllib.parse import urlparse

from athletic_elf.config import Config
from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.models import Athlete
from athletic_elf.session import (
    BROWSER_TOKEN_SESSION_KEY,
    create_browser_session,
    hash_session_token,
)


class _TestSettingsConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-for-settings-mcp-xx"
    VERIFY_TOKEN = "test-verify-token-settings"
    ENFORCE_HTTPS = False
    CRON_SECRET = "test-cron-secret"
    AUTO_CREATE_TABLES = True
    HUB_OPTIONS = ("North Hub", "South Hub", "East Hub", "West Hub")
    DEPARTMENT_OPTIONS = ("Engineering", "Sales", "Marketing", "Operations")
    COMPETITION_START_DATETIME = "2020-01-01T00:00:00+00:00"
    WEEK_BOUNDARIES = ""
    COMPETITION_END_DATETIME = "2030-01-01T00:00:00+00:00"


class TestSettingsMcpKey(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestSettingsConfig)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _login_as(self, browser_token: str) -> None:
        with self.client.session_transaction() as sess:
            sess[BROWSER_TOKEN_SESSION_KEY] = browser_token

    def _make_athlete(self) -> tuple[int, str]:
        with self.app.app_context():
            a = Athlete(
                athlete_id=888001,
                firstname="S",
                lastname="T",
                access_token="at",
                refresh_token="rt",
                expires_at=9_999_999_999,
                hub="North Hub",
                department="Engineering",
            )
            db.session.add(a)
            db.session.commit()
            aid = int(a.athlete_id)
            token, _ = create_browser_session(aid)
            db.session.commit()
            return aid, token

    def test_settings_requires_login(self):
        r = self.client.get("/settings", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        loc = r.headers.get("Location", "")
        self.assertEqual((urlparse(loc).path or "/").rstrip("/") or "/", "/")

        r2 = self.client.get("/settings", follow_redirects=True)
        self.assertEqual(r2.status_code, 200)
        self.assertIn(b"You are not signed in", r2.data)

    def test_get_settings_when_logged_in(self):
        _aid, token = self._make_athlete()
        self._login_as(token)
        r = self.client.get("/settings")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Settings", r.data)
        self.assertIn(b"MCP access", r.data)
        self.assertIn(b"Generate MCP key", r.data)

    def test_post_mcp_key_stores_hash_and_shows_plaintext_once(self):
        athlete_id, token = self._make_athlete()
        self._login_as(token)
        r = self.client.post("/settings/mcp-key")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"will not be shown again", r.data)
        m = re.search(
            r'<pre class="mcp-key-value"[^>]*>\s*([^<]+)\s*</pre>',
            r.get_data(as_text=True),
        )
        self.assertIsNotNone(m, msg="expected revealed MCP key in <pre>")
        plain = m.group(1).strip()
        self.assertGreater(len(plain), 20)

        with self.app.app_context():
            row = db.session.get(Athlete, athlete_id)
            assert row is not None
            self.assertEqual(row.mcp_key, hash_session_token(plain))

        r2 = self.client.get("/settings")
        self.assertEqual(r2.status_code, 200)
        self.assertNotIn(plain.encode(), r2.data)
        self.assertIn(b"Regenerate MCP key", r2.data)

    def test_regenerate_replaces_mcp_key(self):
        athlete_id, token = self._make_athlete()
        self._login_as(token)
        r1 = self.client.post("/settings/mcp-key")
        m1 = re.search(
            r'<pre class="mcp-key-value"[^>]*>\s*([^<]+)\s*</pre>',
            r1.get_data(as_text=True),
        )
        assert m1 is not None
        plain1 = m1.group(1).strip()

        r2 = self.client.post("/settings/mcp-key")
        m2 = re.search(
            r'<pre class="mcp-key-value"[^>]*>\s*([^<]+)\s*</pre>',
            r2.get_data(as_text=True),
        )
        assert m2 is not None
        plain2 = m2.group(1).strip()
        self.assertNotEqual(plain1, plain2)

        with self.app.app_context():
            row = db.session.get(Athlete, athlete_id)
            assert row is not None
            self.assertEqual(row.mcp_key, hash_session_token(plain2))
