"""POST /cron: secret auth, fast response, background maintenance."""

import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from athletic_elf.config import Config
from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.models import Athlete

from tests.test_hub_department import _TestHubDeptConfig


class _CronDisabledConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-cron-disabled-xx"
    VERIFY_TOKEN = "test-verify-token-cron-disabled"
    ENFORCE_HTTPS = False
    CRON_SECRET = None
    AUTO_CREATE_TABLES = True
    HUB_OPTIONS = ("North Hub",)
    DEPARTMENT_OPTIONS = ("Engineering",)
    COMPETITION_START_DATETIME = "2020-01-01T00:00:00+00:00"
    WEEK_BOUNDARIES = ""
    COMPETITION_END_DATETIME = "2030-01-01T00:00:00+00:00"


class _ImmediateThread(threading.Thread):
    """Runs the target on the calling thread when ``start()`` is used (for tests)."""

    def start(self) -> None:
        if self._target:
            self._target(*self._args, **self._kwargs)


class TestCronEndpoint(unittest.TestCase):
    def test_returns_503_when_cron_secret_unset(self):
        app = create_app(_CronDisabledConfig)
        app.config["TESTING"] = True
        client = app.test_client()
        r = client.post("/cron", headers={"Authorization": "Bearer anything"})
        self.assertEqual(r.status_code, 503)

    def test_returns_403_without_authorization(self):
        app = create_app(_TestHubDeptConfig)
        app.config["TESTING"] = True
        client = app.test_client()
        r = client.post("/cron")
        self.assertEqual(r.status_code, 403)

    def test_returns_403_for_wrong_secret(self):
        app = create_app(_TestHubDeptConfig)
        app.config["TESTING"] = True
        client = app.test_client()
        r = client.post("/cron", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(r.status_code, 403)

    @patch("athletic_elf.blueprints.cron.threading.Thread", _ImmediateThread)
    def test_accepts_bearer_secret_and_runs_maintenance_inline(self):
        app = create_app(_TestHubDeptConfig)
        app.config["TESTING"] = True
        client = app.test_client()
        r = client.post("/cron", headers={"Authorization": "Bearer test-cron-secret"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_data(as_text=True), "Processing Started")

    @patch("athletic_elf.blueprints.cron.summarize_due_periods_loop", return_value=0)
    @patch("athletic_elf.blueprints.cron.process_activities", return_value=0)
    @patch("athletic_elf.blueprints.cron.threading.Thread", _ImmediateThread)
    def test_deletes_incomplete_athletes_older_than_24h(self, _proc, _summ) -> None:
        app = create_app(_TestHubDeptConfig)
        app.config["TESTING"] = True
        now = datetime.now(timezone.utc)
        stale_ts = now - timedelta(hours=25)
        recent_ts = now - timedelta(hours=1)
        with app.app_context():
            db.session.add_all(
                [
                    Athlete(
                        athlete_id=910_001,
                        firstname="Old",
                        lastname="Incomplete",
                        access_token="a",
                        refresh_token="r",
                        expires_at=2_000_000_000,
                        hub=None,
                        department=None,
                        created_at=stale_ts,
                    ),
                    Athlete(
                        athlete_id=910_002,
                        firstname="Young",
                        lastname="Incomplete",
                        access_token="a",
                        refresh_token="r",
                        expires_at=2_000_000_000,
                        hub=None,
                        department=None,
                        created_at=recent_ts,
                    ),
                    Athlete(
                        athlete_id=910_003,
                        firstname="Old",
                        lastname="Complete",
                        access_token="a",
                        refresh_token="r",
                        expires_at=2_000_000_000,
                        hub="North Hub",
                        department="Engineering",
                        created_at=stale_ts,
                    ),
                ]
            )
            db.session.commit()

        client = app.test_client()
        client.post("/cron", headers={"Authorization": "Bearer test-cron-secret"})

        with app.app_context():
            ids = {
                row.athlete_id
                for row in Athlete.query.filter(
                    Athlete.athlete_id.in_((910_001, 910_002, 910_003))
                ).all()
            }
        self.assertEqual(ids, {910_002, 910_003})
