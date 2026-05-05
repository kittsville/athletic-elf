import math
from collections import defaultdict

# Strava DetailedActivity.sport_type values (see Strava API) -> scoring category
_SCORE_CYCLING = frozenset({"Ride", "VirtualRide", "MountainBikeRide", "GravelRide"})
_SCORE_EBIKE = frozenset({"EBikeRide", "EMountainBikeRide"})
_SCORE_RUNNING = frozenset({"Run", "VirtualRun", "TrailRun"})
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
        "Pilates",
    }
)
# Table types intersected with Strava sport_type enum (Stretching/Dance etc. omitted)
_SCORE_EASY_FITNESS = frozenset(
    {
        "Yoga",
        "TableTennis",
        "Badminton",
        "Windsurf",
        "Kitesurf",
        "Sail",
        "Volleyball",
        "Padel",
        "Cricket",
    }
)

# Yoga for leaderboards (not full easy-fitness set); "stretching" in copy is informal.
_SCORE_ZEN_MASTER = frozenset({"Yoga"})

CYCLING_METERS_PER_POINT = 5000
EBIKE_METERS_PER_POINT = 10_000
RUNNING_METERS_PER_POINT = 1600
WALKING_METERS_PER_POINT = 2000
SWIMMING_METERS_PER_POINT = 400
SECONDS_PER_HARD_FITNESS_POINT = 15 * 60
SECONDS_PER_EASY_FITNESS_POINT = 30 * 60
EASY_FITNESS_DAILY_CAP_POINTS = 5
TEAM_MIN_SIZE_FOR_SCORE = 5
TEAM_TOP_FRACTION = 0.8


def activities_total_points(activities) -> float:
    """
    Total points (float) for a user's activities using distance (meters),
    moving_time (seconds), sport_type, and start_date (for easy-fitness daily cap).

    Distance-based categories (cycling, running, walking, swimming): distances are
    summed within the category, then divided by the threshold (e.g. total cycling
    meters / 5000), producing fractional points.

    Hard fitness: total moving time summed across hard-fitness activities, then
    divided by 900 seconds per point.

    Easy fitness: per calendar day (from start_date), total moving time summed,
    points = min(total / 1800, 5).

    Activities without a recognized sport_type contribute 0.
    """
    sum_cycling = 0.0
    sum_ebike = 0.0
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

        if st in _SCORE_EBIKE:
            sum_ebike += dist
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

    total = 0.0
    total += sum_cycling / CYCLING_METERS_PER_POINT
    total += sum_ebike / EBIKE_METERS_PER_POINT
    total += sum_running / RUNNING_METERS_PER_POINT
    total += sum_walking / WALKING_METERS_PER_POINT
    total += sum_swim / SWIMMING_METERS_PER_POINT
    total += sum_hard_time / SECONDS_PER_HARD_FITNESS_POINT

    for _day, mt_day in easy_time_by_day.items():
        total += min(
            mt_day / SECONDS_PER_EASY_FITNESS_POINT,
            EASY_FITNESS_DAILY_CAP_POINTS,
        )

    return total


def discipline_totals_for_activities(activities):
    """
    Per-athlete discipline volumes for public leaderboards (activities with
    start_date are typically passed in — same rows as scoring).

    Returns a dict: swim_km, walk_km, cycle_km, run_km (float), hard_min,
    zen_min (int minutes, floor of summed moving_time). cycle_km includes
    traditional cycling and e-bike (Centurion leaderboard) distances.
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
        elif st in _SCORE_CYCLING or st in _SCORE_EBIKE:
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


def team_points(per_athlete_points: list[float]) -> float:
    """
    Score for a hub or department team from each member's total points.

    If the team has fewer than five athletes, the team scores 0. Otherwise the
    score is the mean of the highest-scoring subset whose size is the smallest
    whole number at least 80% of the team (e.g. 5 athletes → top 4 averaged).
    """
    n = len(per_athlete_points)
    if n < TEAM_MIN_SIZE_FOR_SCORE:
        return 0.0
    k = math.ceil(TEAM_TOP_FRACTION * n)
    ordered = sorted(per_athlete_points, reverse=True)
    top = ordered[:k]
    return sum(top) / k
