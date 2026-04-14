"""POST /cron: secret auth, fast response, background maintenance."""

import threading
import unittest
from unittest.mock import patch

from athletic_elf.config import Config
from athletic_elf.factory import create_app

from tests.test_hub_department import _TestHubDeptConfig


class _CronDisabledConfig(Config):
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SECRET_KEY = "test-secret-key-cron-disabled-xx"
    CRON_SECRET = None
    AUTO_CREATE_TABLES = True
    HUB_OPTIONS = ("North Hub",)
    DEPARTMENT_OPTIONS = ("Engineering",)


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
