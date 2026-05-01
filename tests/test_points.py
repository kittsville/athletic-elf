"""
Tests for points.activities_total_points against the documented scoring rules.

Run from repo root: python -m unittest discover -s tests -v
"""

import unittest
from datetime import datetime
from types import SimpleNamespace

from points import (
    activities_total_points,
    discipline_totals_for_activities,
    team_points,
)


def _activity(
    sport_type,
    *,
    distance=0.0,
    moving_time=0,
    start_date=None,
):
    return SimpleNamespace(
        sport_type=sport_type,
        distance=distance,
        moving_time=moving_time,
        start_date=start_date,
    )


_D1 = datetime(2025, 6, 1, 10, 0, 0)
_D2 = datetime(2025, 6, 2, 10, 0, 0)


class TestNoPoints(unittest.TestCase):
    """Base cases: nothing should score."""

    def test_empty_list(self):
        self.assertEqual(activities_total_points([]), 0)

    def test_unknown_sport_type(self):
        # Not in any scoring category (per points.py Strava enum intersection)
        acts = [_activity("AlpineSki", distance=100_000)]
        self.assertEqual(activities_total_points(acts), 0)

    def test_missing_sport_type(self):
        acts = [_activity(None, distance=10_000)]
        self.assertEqual(activities_total_points(acts), 0)

    def test_empty_string_sport_type(self):
        acts = [_activity("", distance=10_000)]
        self.assertEqual(activities_total_points(acts), 0)

    def test_below_threshold_single_activity(self):
        acts = [_activity("Ride", distance=4999)]
        self.assertEqual(activities_total_points(acts), 0)

    def test_hard_fitness_zero_moving_time(self):
        acts = [_activity("Workout", moving_time=0)]
        self.assertEqual(activities_total_points(acts), 0)

    def test_easy_fitness_no_start_date_no_points(self):
        # Easy category needs start_date for daily bucketing
        acts = [_activity("Yoga", moving_time=3600, start_date=None)]
        self.assertEqual(activities_total_points(acts), 0)


class TestCyclingRow(unittest.TestCase):
    """Cycling: 5 km = 1 pt (no daily cap)."""

    def test_ride_exactly_5km(self):
        acts = [_activity("Ride", distance=5000)]
        self.assertEqual(activities_total_points(acts), 1)

    def test_virtual_ride_10km_two_points(self):
        acts = [_activity("VirtualRide", distance=10_000)]
        self.assertEqual(activities_total_points(acts), 2)

    def test_mountain_bike_ride_threshold(self):
        acts = [_activity("MountainBikeRide", distance=5000)]
        self.assertEqual(activities_total_points(acts), 1)

    def test_gravel_ride_threshold(self):
        acts = [_activity("GravelRide", distance=5000)]
        self.assertEqual(activities_total_points(acts), 1)


class TestEbikeRow(unittest.TestCase):
    """E-bike: 10 km = 1 pt (separate bucket from human-powered cycling)."""

    def test_ebike_ride_exactly_10km(self):
        acts = [_activity("EBikeRide", distance=10_000)]
        self.assertEqual(activities_total_points(acts), 1)

    def test_emountain_bike_ride_20km_two_points(self):
        acts = [_activity("EMountainBikeRide", distance=20_000)]
        self.assertEqual(activities_total_points(acts), 2)

    def test_ebike_below_10km_zero_points(self):
        acts = [_activity("EBikeRide", distance=9999)]
        self.assertEqual(activities_total_points(acts), 0)

    def test_two_ebike_activities_sum_distance_bucket(self):
        acts = [
            _activity("EBikeRide", distance=6000),
            _activity("EMountainBikeRide", distance=4000),
        ]
        self.assertEqual(activities_total_points(acts), 1)

    def test_ebike_and_cycling_separate_point_buckets(self):
        # 5 km human-powered = 1 pt; 5 km e-bike = 0 (needs 10 km)
        acts = [
            _activity("Ride", distance=5000),
            _activity("EBikeRide", distance=5000),
        ]
        self.assertEqual(activities_total_points(acts), 1)

    def test_discipline_cycle_km_includes_ebike(self):
        acts = [
            _activity("Ride", distance=1000),
            _activity("EBikeRide", distance=2000),
        ]
        t = discipline_totals_for_activities(acts)
        self.assertAlmostEqual(t["cycle_km"], 3.0)


class TestRunningRow(unittest.TestCase):
    """Running: 1.6 km = 1 pt."""

    def test_run_exactly_1600m(self):
        acts = [_activity("Run", distance=1600)]
        self.assertEqual(activities_total_points(acts), 1)

    def test_virtual_run_3200m_two_points(self):
        acts = [_activity("VirtualRun", distance=3200)]
        self.assertEqual(activities_total_points(acts), 2)


class TestWalkingRow(unittest.TestCase):
    """Walking: 2.0 km = 1 pt."""

    def test_walk_exactly_2km(self):
        acts = [_activity("Walk", distance=2000)]
        self.assertEqual(activities_total_points(acts), 1)

    def test_hike_4km_two_points(self):
        acts = [_activity("Hike", distance=4000)]
        self.assertEqual(activities_total_points(acts), 2)

    def test_golf_threshold(self):
        acts = [_activity("Golf", distance=2000)]
        self.assertEqual(activities_total_points(acts), 1)


class TestSwimmingRow(unittest.TestCase):
    """Swimming: 400 m = 1 pt."""

    def test_swim_exactly_400m(self):
        acts = [_activity("Swim", distance=400)]
        self.assertEqual(activities_total_points(acts), 1)

    def test_swim_800m_two_points(self):
        acts = [_activity("Swim", distance=800)]
        self.assertEqual(activities_total_points(acts), 2)


class TestHardFitnessRow(unittest.TestCase):
    """Hard fitness: 15 min = 1 pt."""

    def test_each_hard_fitness_one_point(self):
        # Strava sport_type names (see Strava DetailedActivity.sport_type)
        sports = (
            "WeightTraining",
            "Crossfit",
            "HighIntensityIntervalTraining",
            "Workout",
            "Soccer",
            "Tennis",
            "Squash",
            "Pickleball",
            "Racquetball",
            "RockClimbing",
            "Rowing",
            "Kayaking",
            "Canoeing",
            "Surfing",
            "InlineSkate",
            "Basketball",
            "Pilates",
        )
        moving_time = 15 * 60
        for sport_type in sports:
            with self.subTest(sport_type=sport_type):
                acts = [_activity(sport_type, moving_time=moving_time)]
                self.assertEqual(activities_total_points(acts), 1)

    def test_crossfit_30_minutes_two_points(self):
        acts = [_activity("Crossfit", moving_time=30 * 60)]
        self.assertEqual(activities_total_points(acts), 2)

    def test_hiit_14_minutes_no_point(self):
        acts = [_activity("HighIntensityIntervalTraining", moving_time=14 * 60)]
        self.assertEqual(activities_total_points(acts), 0)

    def test_pilates_without_start_date_still_scores(self):
        acts = [_activity("Pilates", moving_time=15 * 60, start_date=None)]
        self.assertEqual(activities_total_points(acts), 1)


class TestEasyFitnessRow(unittest.TestCase):
    """Easy fitness: 30 min = 1 pt, max 5 pts / day."""

    def test_each_easy_fitness_supported_sport_types(self):
        sports = (
            "Yoga",
            "TableTennis",
            "Badminton",
            "Windsurf",
            "Kitesurf",
            "Sail",
            "Volleyball",
            "Padel",
        )
        for sport_type in sports:
            with self.subTest(sport_type=sport_type, rule="30min_one_point"):
                acts = [_activity(sport_type, moving_time=30 * 60, start_date=_D1)]
                self.assertEqual(activities_total_points(acts), 1)
            with self.subTest(sport_type=sport_type, rule="60min_two_points"):
                acts = [_activity(sport_type, moving_time=60 * 60, start_date=_D1)]
                self.assertEqual(activities_total_points(acts), 2)

    def test_daily_cap_five_on_same_day(self):
        # Six separate 30-minute blocks -> 6 raw easy points, capped at 5
        acts = [
            _activity("Yoga", moving_time=30 * 60, start_date=_D1),
            _activity("Badminton", moving_time=30 * 60, start_date=_D1),
            _activity("TableTennis", moving_time=30 * 60, start_date=_D1),
            _activity("Windsurf", moving_time=30 * 60, start_date=_D1),
            _activity("Kitesurf", moving_time=30 * 60, start_date=_D1),
            _activity("Sail", moving_time=30 * 60, start_date=_D1),
        ]
        self.assertEqual(activities_total_points(acts), 5)

    def test_separate_days_not_capped_together(self):
        acts = [
            _activity("Yoga", moving_time=30 * 60 * 6, start_date=_D1),
            _activity("Yoga", moving_time=30 * 60 * 6, start_date=_D2),
        ]
        # 5 pts per day (capped from 6 raw points)
        self.assertEqual(activities_total_points(acts), 10)


class TestAccumulationAcrossActivities(unittest.TestCase):
    """Multiple activities / sports summing to thresholds."""

    def test_multiple_activities_combined_still_below_threshold_zero_points(self):
        acts = [
            _activity("Ride", distance=2000),
            _activity("Ride", distance=2000),
            _activity("Run", distance=400),
            _activity("Run", distance=400),
            _activity("Workout", moving_time=7 * 60),
            _activity("Workout", moving_time=7 * 60),
            _activity("Yoga", moving_time=10 * 60, start_date=_D1),
            _activity("Yoga", moving_time=10 * 60, start_date=_D1),
        ]
        # 4 km bike, 800 m run, 14 min hard, 20 min easy same day — each bucket < 1 pt
        self.assertEqual(activities_total_points(acts), 0)

    def test_cycling_two_rides_sum_to_5km(self):
        acts = [
            _activity("Ride", distance=2500),
            _activity("Ride", distance=2500),
        ]
        self.assertEqual(activities_total_points(acts), 1)

    def test_mixed_cycling_sport_types_share_distance_bucket(self):
        acts = [
            _activity("Ride", distance=2500),
            _activity("MountainBikeRide", distance=2500),
        ]
        self.assertEqual(activities_total_points(acts), 1)

    def test_running_two_runs_sum_to_1600m(self):
        acts = [
            _activity("Run", distance=800),
            _activity("Run", distance=800),
        ]
        self.assertEqual(activities_total_points(acts), 1)

    def test_walking_two_walks_sum_to_2km(self):
        acts = [
            _activity("Walk", distance=1000),
            _activity("Hike", distance=1000),
        ]
        self.assertEqual(activities_total_points(acts), 1)

    def test_swim_two_lengths_sum_to_400m(self):
        acts = [
            _activity("Swim", distance=200),
            _activity("Swim", distance=200),
        ]
        self.assertEqual(activities_total_points(acts), 1)

    def test_hard_fitness_two_sessions_sum_to_15_min(self):
        acts = [
            _activity("Rowing", moving_time=7 * 60 + 30),
            _activity("Rowing", moving_time=7 * 60 + 30),
        ]
        self.assertEqual(activities_total_points(acts), 1)

    def test_multiple_sports_one_point_each_category(self):
        acts = [
            _activity("GravelRide", distance=5000),
            _activity("VirtualRun", distance=1600),
            _activity("Golf", distance=2000),
            _activity("Swim", distance=400),
            _activity("Tennis", moving_time=15 * 60),
            _activity("Badminton", moving_time=30 * 60, start_date=_D1),
        ]
        # 1 + 1 + 1 + 1 + 1 + 1 = 6
        self.assertEqual(activities_total_points(acts), 6)


class TestTeamPoints(unittest.TestCase):
    """team_points: top-80% mean, minimum 5 athletes."""

    def test_fewer_than_five_athletes_scores_zero(self):
        self.assertEqual(team_points([]), 0.0)
        self.assertEqual(team_points([100]), 0.0)
        self.assertEqual(team_points([10, 20, 30]), 0.0)
        self.assertEqual(team_points([1, 2, 3, 4]), 0.0)

    def test_five_athletes_averages_top_four(self):
        # 80% of 5 → 4; top scores 50, 40, 30, 20 (drop 10)
        self.assertEqual(team_points([10, 20, 30, 40, 50]), 35.0)

    def test_six_athletes_averages_top_five(self):
        # ceil(0.8 * 6) = 5; drop lowest (1)
        self.assertEqual(team_points([1, 2, 3, 4, 5, 6]), 4.0)

    def test_ten_athletes_averages_top_eight(self):
        # ceil(0.8 * 10) = 8; drop 1 and 2
        self.assertEqual(team_points([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]), 6.5)


if __name__ == "__main__":
    unittest.main()
