"""Audit trail for Strava sync and organiser actions."""

import unittest
from unittest.mock import MagicMock, patch

from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.models import (
    AUDIT_TYPE_STRAVA_ACTIVITIES_PULLED,
    Athlete,
    AuditItem,
)
from athletic_elf.strava_service import sync_activities_since_competition_start

from tests.test_hub_department import _TestHubDeptConfig


class TestStravaActivitiesPulledAudit(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestHubDeptConfig)
        self.app.config["TESTING"] = True

    def test_sync_logs_audit_with_activity_count(self):
        athlete_id = 940_001
        summary = {
            "id": 99_001,
            "sport_type": "Run",
            "distance": 1000.0,
            "start_date": "2026-03-01T10:00:00Z",
            "moving_time": 600,
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [summary]
        mock_resp.raise_for_status = MagicMock()

        with self.app.app_context():
            db.session.add(
                Athlete(
                    athlete_id=athlete_id,
                    firstname="A",
                    lastname="B",
                    access_token="tok",
                    refresh_token="rt",
                    expires_at=9_999_999_999,
                    hub="North Hub",
                    department="Engineering",
                )
            )
            db.session.commit()

            with patch(
                "athletic_elf.strava_service.http_client.get",
                return_value=mock_resp,
            ):
                n = sync_activities_since_competition_start(athlete_id)

            self.assertEqual(n, 1)
            row = AuditItem.query.filter_by(
                audit_type=AUDIT_TYPE_STRAVA_ACTIVITIES_PULLED,
                source="app",
                target=str(athlete_id),
            ).one()
            self.assertEqual(row.context, {"activities": 1})


if __name__ == "__main__":
    unittest.main()
