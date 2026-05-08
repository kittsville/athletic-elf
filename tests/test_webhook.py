"""Strava webhook path token and subscription callback URL."""

import unittest
from urllib.parse import quote

from datetime import datetime, timezone

from athletic_elf.config import Config
from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.models import (
    AUDIT_TYPE_WEBHOOK_ACTIVITY_CREATE,
    AUDIT_TYPE_WEBHOOK_ACTIVITY_DELETE,
    AUDIT_TYPE_WEBHOOK_ACTIVITY_UPDATE,
    Activity,
    AuditItem,
)
from athletic_elf.utils import strava_webhook_callback_url


class _WebhookConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-for-webhook-tests-xx"
    ENFORCE_HTTPS = False
    CRON_SECRET = "test-cron-secret"
    AUTO_CREATE_TABLES = True
    DOMAIN = "http://127.0.0.1:5000"
    VERIFY_TOKEN = "my-verify-secret"
    CLIENT_ID = "1"
    CLIENT_SECRET = "x"
    HUB_OPTIONS = ("North Hub",)
    DEPARTMENT_OPTIONS = ("Engineering",)
    COMPETITION_START_DATETIME = "2020-01-01T00:00:00+00:00"
    WEEK_BOUNDARIES = ""
    COMPETITION_END_DATETIME = "2030-01-01T00:00:00+00:00"


class TestStravaWebhookCallbackUrl(unittest.TestCase):
    def test_encodes_special_characters_in_path(self):
        app = create_app(_WebhookConfig)
        with app.app_context():
            url = strava_webhook_callback_url("a b")
        self.assertEqual(url, "http://127.0.0.1:5000/webhook/a%20b")

    def test_rejects_empty_token(self):
        app = create_app(_WebhookConfig)
        with app.app_context():
            with self.assertRaises(ValueError):
                strava_webhook_callback_url("  ")


class TestWebhookRoutes(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_WebhookConfig)
        self.client = self.app.test_client()

    def test_legacy_webhook_path_returns_404(self):
        r = self.client.get("/webhook")
        self.assertEqual(r.status_code, 404)

    def test_wrong_path_token_returns_404(self):
        q = "hub.mode=subscribe&hub.verify_token=my-verify-secret&hub.challenge=abc"
        r = self.client.get(f"/webhook/wrong-token?{q}")
        self.assertEqual(r.status_code, 404)

    def test_validation_get_succeeds_when_path_and_query_match(self):
        q = "hub.mode=subscribe&hub.verify_token=my-verify-secret&hub.challenge=abc123"
        r = self.client.get(f"/webhook/my-verify-secret?{q}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json, {"hub.challenge": "abc123"})

    def test_validation_get_403_when_query_token_wrong(self):
        q = "hub.mode=subscribe&hub.verify_token=other&hub.challenge=abc"
        r = self.client.get(f"/webhook/my-verify-secret?{q}")
        self.assertEqual(r.status_code, 403)

    def test_post_accepts_payload_with_correct_path(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()
        body = {
            "aspect_type": "create",
            "object_type": "activity",
            "object_id": 999001,
            "owner_id": 888001,
        }
        r = self.client.post(
            "/webhook/my-verify-secret",
            json=body,
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, b"EVENT_RECEIVED")
        with self.app.app_context():
            audit = AuditItem.query.filter_by(
                audit_type=AUDIT_TYPE_WEBHOOK_ACTIVITY_CREATE,
                source="strava",
                target="999001",
            ).one()
            self.assertEqual(audit.context, {"athlete_id": 888001})

    def test_post_returns_404_for_wrong_path(self):
        r = self.client.post("/webhook/other", json={"object_type": "activity"})
        self.assertEqual(r.status_code, 404)

    def test_activity_update_clears_fields_for_cron_refetch(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            row = Activity(
                activity_id=777_001,
                athlete_id=888_001,
                distance=5000.0,
                sport_type="Run",
                start_date=datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
                moving_time=3600,
            )
            db.session.add(row)
            db.session.commit()
            preserved_id = row.id

        body = {
            "aspect_type": "update",
            "object_type": "activity",
            "object_id": 777_001,
            "owner_id": 888_001,
        }
        r = self.client.post("/webhook/my-verify-secret", json=body)
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            again = Activity.query.filter_by(activity_id=777_001).one()
            self.assertEqual(again.id, preserved_id)
            self.assertEqual(int(again.athlete_id), 888_001)
            self.assertIsNone(again.distance)
            self.assertIsNone(again.sport_type)
            self.assertIsNone(again.start_date)
            self.assertIsNone(again.moving_time)
            audit = AuditItem.query.filter_by(
                audit_type=AUDIT_TYPE_WEBHOOK_ACTIVITY_UPDATE,
                source="strava",
                target="777001",
            ).one()
            self.assertEqual(audit.context, {"athlete_id": 888001})

    def test_activity_delete_logs_audit(self):
        with self.app.app_context():
            db.drop_all()
            db.create_all()
            db.session.add(
                Activity(
                    activity_id=555_001,
                    athlete_id=444_001,
                    distance=1000.0,
                    sport_type="Run",
                    start_date=datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
                    moving_time=300,
                )
            )
            db.session.commit()

        body = {
            "aspect_type": "delete",
            "object_type": "activity",
            "object_id": 555_001,
            "owner_id": 444_001,
        }
        r = self.client.post("/webhook/my-verify-secret", json=body)
        self.assertEqual(r.status_code, 200)

        with self.app.app_context():
            self.assertIsNone(
                Activity.query.filter_by(activity_id=555_001).first(),
            )
            audit = AuditItem.query.filter_by(
                audit_type=AUDIT_TYPE_WEBHOOK_ACTIVITY_DELETE,
                source="strava",
                target="555001",
            ).one()
            self.assertEqual(audit.context, {"athlete_id": 444001})


class TestCreateAppRequiresVerifyToken(unittest.TestCase):
    class _EmptyVerify(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SECRET_KEY = "test-secret-key-empty-verify-xx"
        ENFORCE_HTTPS = False
        VERIFY_TOKEN = ""

    def test_create_app_raises_when_verify_token_empty(self):
        with self.assertRaises(ValueError) as ctx:
            create_app(self._EmptyVerify)
        self.assertIn("VERIFY_TOKEN", str(ctx.exception))


class TestWebhookEncodedPathSegment(unittest.TestCase):
    """Path segment must match raw VERIFY_TOKEN; URL may be percent-encoded."""

    class _Tok(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SECRET_KEY = "test-secret-key-webhook-encoded-xx"
        ENFORCE_HTTPS = False
        CRON_SECRET = "test-cron-secret"
        AUTO_CREATE_TABLES = True
        DOMAIN = "http://127.0.0.1:5000"
        VERIFY_TOKEN = "a b"
        CLIENT_ID = "1"
        CLIENT_SECRET = "x"
        COMPETITION_START_DATETIME = "2020-01-01T00:00:00+00:00"
        WEEK_BOUNDARIES = ""
        COMPETITION_END_DATETIME = "2030-01-01T00:00:00+00:00"

    def test_get_with_encoded_path(self):
        app = create_app(self._Tok)
        client = app.test_client()
        seg = quote("a b", safe="")
        q = "hub.mode=subscribe&hub.verify_token=a%20b&hub.challenge=z"
        r = client.get(f"/webhook/{seg}?{q}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json, {"hub.challenge": "z"})
