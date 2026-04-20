"""Competition period boundaries, summarization, and results scoring."""

import unittest
from datetime import datetime, timedelta, timezone

from athletic_elf.competition_periods import (
    aggregates_frozen_team_scores,
    period_specs_for_config,
    points_by_athlete_competition_totals,
    points_by_athlete_for_results_table,
    summarize_due_periods_loop,
)
from athletic_elf.config import Config, parse_week_boundary_datetimes
from athletic_elf.extensions import db
from athletic_elf.factory import create_app
from athletic_elf.models import Activity, Athlete, Week, WeekScore
from athletic_elf.session import BROWSER_TOKEN_SESSION_KEY, create_browser_session

from tests.test_hub_department import _TestHubDeptConfig


class _CompetitionWeekConfig(_TestHubDeptConfig):
    COMPETITION_START_DATETIME = "2026-05-01T00:00:00+00:00"
    WEEK_BOUNDARIES = "2026-05-10T00:00:00+00:00,2026-05-20T00:00:00+00:00"
    COMPETITION_END_DATETIME = "2026-05-31T00:00:00+00:00"


class TestFactoryRequiresCompetitionSchedule(unittest.TestCase):
    def test_create_app_raises_when_activity_end_missing(self):
        class _NoEnd(Config):
            SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
            SECRET_KEY = "test-secret-key-factory-competition-xx"
            VERIFY_TOKEN = "verify"
            COMPETITION_START_DATETIME = "2020-01-01T00:00:00+00:00"
            WEEK_BOUNDARIES = ""
            COMPETITION_END_DATETIME = None

        with self.assertRaises(ValueError) as ctx:
            create_app(_NoEnd)
        self.assertIn("COMPETITION_END_DATETIME", str(ctx.exception))


class TestParseWeekBoundaries(unittest.TestCase):
    def test_comma_separated_sorted_unique(self):
        t = parse_week_boundary_datetimes(
            "2026-05-20T00:00:00+00:00,2026-05-10T00:00:00+00:00"
        )
        self.assertEqual(len(t), 2)
        self.assertLess(t[0], t[1])


class TestPeriodSpecs(unittest.TestCase):
    def test_grace_on_second_period_lower_bound(self):
        start = datetime(2026, 5, 1, tzinfo=timezone.utc)
        b0 = datetime(2026, 5, 10, tzinfo=timezone.utc)
        b1 = datetime(2026, 5, 20, tzinfo=timezone.utc)
        end = datetime(2026, 5, 31, tzinfo=timezone.utc)
        specs = period_specs_for_config(start, (b0, b1), end)
        self.assertEqual(len(specs), 3)
        self.assertEqual(specs[0].eligible_lower, start)
        self.assertEqual(specs[1].eligible_lower, max(start, b0 - timedelta(hours=12)))
        self.assertEqual(specs[1].canonical_start, b0)

    def test_only_activity_end_creates_single_period(self):
        start = datetime(2026, 5, 1, tzinfo=timezone.utc)
        end = datetime(2026, 5, 31, tzinfo=timezone.utc)
        specs = period_specs_for_config(start, (), end)
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].end_exclusive, end)


class TestSummarizePeriod(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_CompetitionWeekConfig)
        self.app.config["TESTING"] = True

    def test_summarize_assigns_period_index_and_inserts_scores(self):
        with self.app.app_context():
            for i in range(5):
                db.session.add(
                    Athlete(
                        athlete_id=930_000 + i,
                        firstname="T",
                        lastname=str(i),
                        access_token="tok",
                        refresh_token="ref",
                        expires_at=2_000_000_000,
                        hub="North Hub",
                        department="Engineering",
                    )
                )
            t_act = datetime(2026, 5, 5, 15, 0, 0, tzinfo=timezone.utc)
            for i in range(5):
                db.session.add(
                    Activity(
                        activity_id=840_000 + i,
                        athlete_id=930_000 + i,
                        sport_type="Ride",
                        distance=5000.0,
                        start_date=t_act,
                        moving_time=1800,
                    )
                )
            db.session.commit()

            now = datetime(2026, 5, 11, tzinfo=timezone.utc)
            n = summarize_due_periods_loop(self.app, now=now)
            self.assertEqual(n, 1)
            db.session.commit()

            wk0 = Week.query.filter_by(period_index=0).one()
            self.assertGreaterEqual(
                WeekScore.query.filter_by(week_id=wk0.id).count(), 2
            )
            act = Activity.query.filter_by(activity_id=840_000).first()
            self.assertIsNotNone(act)
            self.assertEqual(act.week_id, wk0.id)

            hubs = list(self.app.config["HUB_OPTIONS"])
            depts = list(self.app.config["DEPARTMENT_OPTIONS"])
            hub_f, dept_f = aggregates_frozen_team_scores(hubs, depts)
            self.assertGreater(hub_f.get("North Hub", 0.0), 0.0)


class TestGraceStragglerSecondPeriod(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_CompetitionWeekConfig)
        self.app.config["TESTING"] = True

    def test_late_synced_activity_before_boundary_counts_in_next_period(self):
        with self.app.app_context():
            for i in range(5):
                db.session.add(
                    Athlete(
                        athlete_id=931_000 + i,
                        firstname="G",
                        lastname=str(i),
                        access_token="tok",
                        refresh_token="ref",
                        expires_at=2_000_000_000,
                        hub="North Hub",
                        department="Engineering",
                    )
                )
            t0 = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
            for i in range(5):
                db.session.add(
                    Activity(
                        activity_id=841_000 + i,
                        athlete_id=931_000 + i,
                        sport_type="Ride",
                        distance=5000.0,
                        start_date=t0,
                        moving_time=1800,
                    )
                )
            db.session.commit()

            summarize_due_periods_loop(
                self.app,
                now=datetime(2026, 5, 11, tzinfo=timezone.utc),
            )
            db.session.commit()

            db.session.add(
                Activity(
                    activity_id=841_999,
                    athlete_id=931_000,
                    sport_type="Ride",
                    distance=5000.0,
                    start_date=datetime(2026, 5, 9, 20, 0, 0, tzinfo=timezone.utc),
                    moving_time=1800,
                )
            )
            db.session.commit()

            summarize_due_periods_loop(
                self.app,
                now=datetime(2026, 5, 21, tzinfo=timezone.utc),
            )
            db.session.commit()

            late = Activity.query.filter_by(activity_id=841_999).one()
            wk1 = Week.query.filter_by(period_index=1).one()
            self.assertEqual(late.week_id, wk1.id)
            self.assertIsNotNone(WeekScore.query.filter_by(week_id=wk1.id).first())


class TestOrganiserWeeksPage(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_CompetitionWeekConfig)
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def _login(self, token: str) -> None:
        with self.client.session_transaction() as sess:
            sess[BROWSER_TOKEN_SESSION_KEY] = token

    def _make_organiser(self) -> str:
        with self.app.app_context():
            a = Athlete(
                athlete_id=932_001,
                firstname="O",
                lastname="W",
                access_token="at",
                refresh_token="rt",
                expires_at=2_000_000_000,
                hub="North Hub",
                department="Engineering",
                is_organiser=True,
            )
            db.session.add(a)
            db.session.flush()
            raw, _ = create_browser_session(int(a.athlete_id))
            db.session.commit()
            return raw

    def test_weeks_requires_role(self):
        with self.app.app_context():
            a = Athlete(
                athlete_id=932_002,
                firstname="U",
                lastname="ser",
                access_token="at",
                refresh_token="rt",
                expires_at=2_000_000_000,
                hub="North Hub",
                department="Engineering",
                is_organiser=False,
            )
            db.session.add(a)
            db.session.flush()
            raw, _ = create_browser_session(int(a.athlete_id))
            db.session.commit()
        self._login(raw)
        rv = self.client.get("/organiser/weeks")
        self.assertEqual(rv.status_code, 403)

    def test_organiser_can_view_weeks_page(self):
        self._login(self._make_organiser())
        rv = self.client.get("/organiser/weeks")
        self.assertEqual(rv.status_code, 200)
        self.assertIn(b"Weekly Scores", rv.data)


class TestResultsPointsWithWeeklyConfig(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_CompetitionWeekConfig)
        self.app.config["TESTING"] = True

    def test_points_by_athlete_for_results_respects_open_period(self):
        with self.app.app_context():
            db.session.add(
                Athlete(
                    athlete_id=933_000,
                    firstname="R",
                    lastname="X",
                    access_token="at",
                    refresh_token="rt",
                    expires_at=2_000_000_000,
                    hub="North Hub",
                    department="Engineering",
                )
            )
            inside = datetime(2026, 5, 5, tzinfo=timezone.utc)
            outside = datetime(2026, 4, 1, tzinfo=timezone.utc)
            db.session.add(
                Activity(
                    activity_id=842_000,
                    athlete_id=933_000,
                    sport_type="Ride",
                    distance=5000.0,
                    start_date=inside,
                    moving_time=1800,
                )
            )
            db.session.add(
                Activity(
                    activity_id=842_001,
                    athlete_id=933_000,
                    sport_type="Ride",
                    distance=5000.0,
                    start_date=outside,
                    moving_time=1800,
                )
            )
            db.session.commit()

            pb = points_by_athlete_for_results_table(self.app)
            self.assertEqual(pb.get(933_000), 1)
            comp = points_by_athlete_competition_totals(self.app)
            self.assertEqual(comp.get(933_000), 1)


class TestCompetitionTotalsAcrossWeeks(unittest.TestCase):
    def setUp(self):
        self.app = create_app(_CompetitionWeekConfig)
        self.app.config["TESTING"] = True

    def test_competition_totals_include_summarized_activities(self):
        """Live results table excludes week_id rows; competition totals must not."""
        with self.app.app_context():
            db.session.add(
                Athlete(
                    athlete_id=934_000,
                    firstname="S",
                    lastname="um",
                    access_token="at",
                    refresh_token="rt",
                    expires_at=2_000_000_000,
                    hub="North Hub",
                    department="Engineering",
                )
            )
            db.session.add(
                Activity(
                    activity_id=843_000,
                    athlete_id=934_000,
                    sport_type="Ride",
                    distance=5000.0,
                    start_date=datetime(2026, 5, 5, tzinfo=timezone.utc),
                    moving_time=1800,
                )
            )
            db.session.commit()

            summarize_due_periods_loop(
                self.app, now=datetime(2026, 5, 11, tzinfo=timezone.utc)
            )
            db.session.commit()

            self.assertEqual(points_by_athlete_for_results_table(self.app), {})
            comp = points_by_athlete_competition_totals(self.app)
            self.assertEqual(comp.get(934_000), 1)
