from collections import defaultdict

# Strava DetailedActivity.sport_type values (see Strava API) -> scoring category
_SCORE_CYCLING = frozenset(
    {"Ride", "VirtualRide", "MountainBikeRide", "GravelRide"}
)
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
    }
)
# Table types intersected with Strava sport_type enum (Stretching/Cricket/Dance etc. omitted)
_SCORE_EASY_FITNESS = frozenset(
    {"Yoga", "Pilates", "TableTennis", "Badminton", "Windsurf", "Kitesurf", "Sail"}
)

_EASY_FITNESS_DAILY_CAP = 5
_SECONDS_PER_EASY_POINT = 30 * 60
_SECONDS_PER_HARD_POINT = 15 * 60


def activities_total_points(activities):
    """
    Total integer points for a user's activities using distance (meters),
    moving_time (seconds), sport_type, and start_date (for easy-fitness daily cap).
    Activities without a recognized sport_type contribute 0.
    """
    total = 0
    easy_points_by_day = defaultdict(int)

    for a in activities:
        st = a.sport_type
        if not st:
            continue
        dist = float(a.distance or 0)
        mt = int(a.moving_time or 0)

        if st in _SCORE_EASY_FITNESS:
            if a.start_date is not None:
                day = a.start_date.date()
                easy_points_by_day[day] += mt // _SECONDS_PER_EASY_POINT
            continue

        if st in _SCORE_CYCLING:
            total += int(dist // 5000)
        elif st in _SCORE_RUNNING:
            total += int(dist // 1600)
        elif st in _SCORE_WALKING:
            total += int(dist // 2000)
        elif st in _SCORE_SWIMMING:
            total += int(dist // 400)
        elif st in _SCORE_HARD_FITNESS:
            total += mt // _SECONDS_PER_HARD_POINT

    for _day, day_easy in easy_points_by_day.items():
        total += min(day_easy, _EASY_FITNESS_DAILY_CAP)

    return total
