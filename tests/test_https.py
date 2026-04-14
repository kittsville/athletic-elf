"""HTTPS enforcement and session cookie flags."""

import unittest

from athletic_elf.config import Config
from athletic_elf.factory import create_app


class _HttpsEnforcedConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-https-enforced-xx"
    VERIFY_TOKEN = "test-verify-token-https"
    ENFORCE_HTTPS = True
    CRON_SECRET = "test-cron-secret"
    AUTO_CREATE_TABLES = True
    DOMAIN = "https://example.com"
    CLIENT_ID = "1"
    CLIENT_SECRET = "x"
    HUB_OPTIONS = ("North Hub",)
    DEPARTMENT_OPTIONS = ("Engineering",)


class TestHttpsEnforcement(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_HttpsEnforcedConfig)

    def test_session_cookie_flags_when_enforced(self):
        self.assertTrue(self.app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], "Lax")

    def test_http_request_returns_403_plain_text(self):
        client = self.app.test_client()
        r = client.get("/", base_url="http://127.0.0.1/")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.mimetype, "text/plain")
        self.assertIn("HTTPS", r.get_data(as_text=True))

    def test_https_request_allowed(self):
        client = self.app.test_client()
        r = client.get("/", base_url="https://127.0.0.1/")
        self.assertNotEqual(r.status_code, 403)

    def test_http_with_x_forwarded_proto_https_allowed(self):
        client = self.app.test_client()
        r = client.get(
            "/",
            base_url="http://127.0.0.1/",
            headers={"X-Forwarded-Proto": "https"},
        )
        self.assertNotEqual(r.status_code, 403)
