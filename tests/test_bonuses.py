"""Bonus points model, organiser-only UI, and hub/department results totals."""

import unittest
from datetime import datetime, timezone

from athletic_elf.team_scoring import summaries_by_hub_and_department
from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.models import Athlete, Bonus
from athletic_elf.session import BROWSER_TOKEN_SESSION_KEY, create_browser_session

from tests.test_hub_department import _TestHubDeptConfig


class TestBonusHubDepartmentTotals(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestHubDeptConfig)
        self.app.config["TESTING"] = True

    def test_hub_and_department_summaries_include_bonus_points(self):
        with self.app.app_context():
            for i in range(5):
                db.session.add(
                    Athlete(
                        athlete_id=910_000 + i,
                        firstname="A",
                        lastname=str(i),
                        access_token="t",
                        refresh_token="r",
                        expires_at=2_000_000_000,
                        hub="North Hub",
                        department="Engineering",
                    )
                )
            db.session.add(
                Bonus(
                    created_at=datetime.now(timezone.utc),
                    name="Photo of the week",
                    points=7,
                    target="North Hub",
                    athlete_id=910_000,
                )
            )
            db.session.add(
                Bonus(
                    created_at=datetime.now(timezone.utc),
                    name="Dept challenge",
                    points=3,
                    target="Engineering",
                    athlete_id=910_000,
                )
            )
            db.session.commit()

            points_by = {910_000 + i: 10 for i in range(5)}
            hub_rows, dept_rows = summaries_by_hub_and_department(
                points_by,
                list(self.app.config["HUB_OPTIONS"]),
                list(self.app.config["DEPARTMENT_OPTIONS"]),
            )
            north = next(r for r in hub_rows if r["name"] == "North Hub")
            eng = next(r for r in dept_rows if r["name"] == "Engineering")
            # team_points([10,10,10,10,10]) = mean(top 4) = 10
            self.assertAlmostEqual(north["points"], 17.0)
            self.assertAlmostEqual(eng["points"], 13.0)


class TestBonusesPage(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_TestHubDeptConfig)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _login(self, token: str) -> None:
        with self.client.session_transaction() as sess:
            sess[BROWSER_TOKEN_SESSION_KEY] = token

    def _make_user(self, *, organiser: bool = False) -> tuple[Athlete, str]:
        with self.app.app_context():
            a = Athlete(
                athlete_id=920_001,
                firstname="O",
                lastname="R",
                access_token="at",
                refresh_token="rt",
                expires_at=2_000_000_000,
                hub="North Hub",
                department="Engineering",
                is_organiser=organiser,
            )
            db.session.add(a)
            db.session.flush()
            raw, _ = create_browser_session(int(a.athlete_id))
            db.session.commit()
            return a, raw

    def test_bonuses_forbidden_for_non_organiser(self):
        _, token = self._make_user(organiser=False)
        self._login(token)
        rv = self.client.get("/bonuses")
        self.assertEqual(rv.status_code, 403)

    def test_organiser_can_add_and_delete_bonus(self):
        _, token = self._make_user(organiser=True)
        self._login(token)
        rv = self.client.get("/bonuses")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"Add bonus", rv.data)

        rv = self.client.post(
            "/bonuses",
            data={
                "name": "Weekly photo",
                "points": "5",
                "target": "hub|North Hub",
            },
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        with self.app.app_context():
            b = Bonus.query.one()
            self.assertEqual(b.name, "Weekly photo")
            self.assertEqual(b.points, 5)
            self.assertEqual(b.target, "North Hub")
            self.assertEqual(int(b.athlete_id), 920_001)

        rv = self.client.post(
            f"/bonuses/{b.id}/delete",
            follow_redirects=False,
        )
        self.assertEqual(rv.status_code, 302)
        with self.app.app_context():
            self.assertIsNone(Bonus.query.first())
