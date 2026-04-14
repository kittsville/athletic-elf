import math
from collections import defaultdict

# Strava DetailedActivity.sport_type values (see Strava API) -> scoring category
_SCORE_CYCLING = frozenset({"Ride", "VirtualRide", "MountainBikeRide", "GravelRide"})
_SCORE_RUNNING = frozenset({"Run", "VirtualRun"})
_SCORE_WALKING = frozenset({"Walk", "Hike", "Golf"})
_SCORE_SWIMMING = frozenset({"Swim"})
_SCORE_HARD_FITNESS = frozenset(
    {
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
    }
)
# Table types intersected with Strava sport_type enum (Stretching/Cricket/Dance etc. omitted)
_SCORE_EASY_FITNESS = frozenset(
    {
        "Yoga",
        "Pilates",
        "TableTennis",
        "Badminton",
        "Windsurf",
        "Kitesurf",
        "Sail",
        "Volleyball",
        "Padel",
    }
)

# Yoga / Pilates for leaderboards (not full easy-fitness set); "stretching" in copy is informal.
_SCORE_ZEN_MASTER = frozenset({"Yoga", "Pilates"})

_EASY_FITNESS_DAILY_CAP = 5
_SECONDS_PER_EASY_POINT = 30 * 60
_SECONDS_PER_HARD_POINT = 15 * 60


def activities_total_points(activities):
    """
    Total integer points for a user's activities using distance (meters),
    moving_time (seconds), sport_type, and start_date (for easy-fitness daily cap).

    Distance-based categories (cycling, running, walking, swimming): distances are
    summed within the category, then thresholds applied once (e.g. total cycling
    meters // 5000).

    Hard fitness: total moving time summed across hard-fitness activities, then
    // 900 seconds per point.

    Easy fitness: per calendar day (from start_date), total moving time summed,
    points = min(total // 1800, 5).

    Activities without a recognized sport_type contribute 0.
    """
    sum_cycling = 0.0
    sum_running = 0.0
    sum_walking = 0.0
    sum_swim = 0.0
    sum_hard_time = 0
    easy_time_by_day = defaultdict(int)

    for a in activities:
        st = a.sport_type
        if not st:
            continue
        dist = float(a.distance or 0)
        mt = int(a.moving_time or 0)

        if st in _SCORE_EASY_FITNESS:
            if a.start_date is not None:
                day = a.start_date.date()
                easy_time_by_day[day] += mt
            continue

        if st in _SCORE_CYCLING:
            sum_cycling += dist
        elif st in _SCORE_RUNNING:
            sum_running += dist
        elif st in _SCORE_WALKING:
            sum_walking += dist
        elif st in _SCORE_SWIMMING:
            sum_swim += dist
        elif st in _SCORE_HARD_FITNESS:
            sum_hard_time += mt

    total = 0
    total += int(sum_cycling // 5000)
    total += int(sum_running // 1600)
    total += int(sum_walking // 2000)
    total += int(sum_swim // 400)
    total += sum_hard_time // _SECONDS_PER_HARD_POINT

    for _day, mt_day in easy_time_by_day.items():
        total += min(mt_day // _SECONDS_PER_EASY_POINT, _EASY_FITNESS_DAILY_CAP)

    return total


def discipline_totals_for_activities(activities):
    """
    Per-athlete discipline volumes for public leaderboards (activities with
    start_date are typically passed in — same rows as scoring).

    Returns a dict: swim_km, walk_km, cycle_km, run_km (float), hard_min,
    zen_min (int minutes, floor of summed moving_time).
    """
    sum_cycling = 0.0
    sum_running = 0.0
    sum_walking = 0.0
    sum_swim = 0.0
    sum_hard_time = 0
    sum_zen_time = 0

    for a in activities:
        st = a.sport_type
        if not st:
            continue
        dist = float(a.distance or 0)
        mt = int(a.moving_time or 0)

        if st in _SCORE_ZEN_MASTER:
            sum_zen_time += mt
        elif st in _SCORE_CYCLING:
            sum_cycling += dist
        elif st in _SCORE_RUNNING:
            sum_running += dist
        elif st in _SCORE_WALKING:
            sum_walking += dist
        elif st in _SCORE_SWIMMING:
            sum_swim += dist
        elif st in _SCORE_HARD_FITNESS:
            sum_hard_time += mt

    return {
        "swim_km": sum_swim / 1000.0,
        "walk_km": sum_walking / 1000.0,
        "cycle_km": sum_cycling / 1000.0,
        "run_km": sum_running / 1000.0,
        "hard_min": sum_hard_time // 60,
        "zen_min": sum_zen_time // 60,
    }


def team_points(per_athlete_points: list[int]) -> float:
    """
    Score for a hub or department team from each member's total points.

    If the team has fewer than five athletes, the team scores 0. Otherwise the
    score is the mean of the highest-scoring subset whose size is the smallest
    whole number at least 80% of the team (e.g. 5 athletes → top 4 averaged).
    """
    n = len(per_athlete_points)
    if n < 5:
        return 0.0
    k = math.ceil(0.8 * n)
    ordered = sorted(per_athlete_points, reverse=True)
    top = ordered[:k]
    return sum(top) / k
