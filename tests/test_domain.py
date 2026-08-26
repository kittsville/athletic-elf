"""DOMAIN config validation at app startup."""

import unittest

from athletic_elf.config import Config
from athletic_elf.factory import create_app


class _BaseDomainConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-for-domain-tests-xx"
    VERIFY_TOKEN = "test-verify-token-domain"
    ENFORCE_HTTPS = False
    CRON_SECRET = "test-cron-secret"
    AUTO_CREATE_TABLES = True
    HUB_OPTIONS = ("North Hub",)
    DEPARTMENT_OPTIONS = ("Engineering",)
    COMPETITION_START_DATETIME = "2020-01-01T00:00:00+00:00"
    WEEK_BOUNDARIES = ""
    COMPETITION_END_DATETIME = "2030-01-01T00:00:00+00:00"


class TestDomainValidation(unittest.TestCase):
    def test_single_domain_allowed(self):
        class _Cfg(_BaseDomainConfig):
            DOMAIN = "https://example.com"

        app = create_app(_Cfg)
        self.assertEqual(app.config["DOMAIN"], "https://example.com")

    def test_comma_separated_domain_rejected(self):
        class _Cfg(_BaseDomainConfig):
            DOMAIN = "https://a.example,https://b.example"

        with self.assertRaises(ValueError) as ctx:
            create_app(_Cfg)
        self.assertIn("single URL", str(ctx.exception))

    def test_missing_domain_allowed_at_startup(self):
        class _Cfg(_BaseDomainConfig):
            DOMAIN = None

        create_app(_Cfg)
