"""Discipline leaderboard data for /leaders and shared scored-activity grouping."""

from collections import defaultdict

from points import activities_total_points, discipline_totals_for_activities

from .models import Activity, Athlete
from .utils import athlete_display_name


def activities_by_athlete_scored() -> dict[int, list[Activity]]:
    """Activities with a start_date, grouped by Strava athlete id."""
    activities = (
        Activity.query.filter(
            Activity.athlete_id.isnot(None),
            Activity.start_date.isnot(None),
        )
        .order_by(Activity.athlete_id, Activity.id)
        .all()
    )
    by_athlete: defaultdict[int, list] = defaultdict(list)
    for a in activities:
        by_athlete[int(a.athlete_id)].append(a)
    return by_athlete


# slug, title, description, column heading for stat, sort key ("points" = total activity points)
LEADERBOARD_SPECS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "shark",
        "The Shark 🏊",
        "For the pool sharks hitting those 400m intervals.",
        "Swimming (km)",
        "swim_km",
    ),
    (
        "explorer",
        "The Explorer 🥾",
        "For the high-volume steppers who never take the elevator.",
        "Walking (km)",
        "walk_km",
    ),
    (
        "powerhouse",
        "The Powerhouse 💪",
        "For the heavy lifters, HIIT enthusiasts, and football/basketball players.",
        "Hard fitness (min)",
        "hard_min",
    ),
    (
        "centurion",
        "The Centurion 🚴‍♀️",
        "For the road warriors and Peloton fans.",
        "Cycling (km)",
        "cycle_km",
    ),
    (
        "marathoner",
        "The Marathoner 🏃‍♂️",
        "For those putting in the pavement miles.",
        "Running (km)",
        "run_km",
    ),
    (
        "zen",
        "The Zen Master 🧘‍♂️",
        "For the mobility and recovery specialists.",
        "Yoga (min)",
        "zen_min",
    ),
    (
        "mvp",
        "The MVP 🏆",
        "Person with the most total points.",
        "Points",
        "points",
    ),
)


def leaderboard_sections() -> list[dict[str, object]]:
    """Top 10 per discipline for the leaders page."""
    by_athlete = activities_by_athlete_scored()
    if not by_athlete:
        return [
            {
                "slug": s[0],
                "title": s[1],
                "description": s[2],
                "stat_header": s[3],
                "rows": [],
            }
            for s in LEADERBOARD_SPECS
        ]

    athlete_ids = list(by_athlete.keys())
    profiles = {
        int(a.athlete_id): a
        for a in Athlete.query.filter(Athlete.athlete_id.in_(athlete_ids)).all()
    }

    def _name(aid: int) -> str:
        p = profiles.get(aid)
        if p is None:
            return f"Athlete {aid}"
        return athlete_display_name(p.firstname or "", p.lastname or "")

    def _hub(aid: int) -> str:
        p = profiles.get(aid)
        if p is None:
            return "—"
        return (p.hub or "").strip() or "—"

    stats_rows: list[tuple[int, dict[str, float | int], int]] = []
    for aid, acts in by_athlete.items():
        d = discipline_totals_for_activities(acts)
        pts = activities_total_points(acts)
        stats_rows.append((aid, d, pts))

    def top10(stat_key: str) -> list[dict[str, object]]:
        eligible: list[tuple[int, dict[str, float | int], int]] = []
        for aid, d, pts in stats_rows:
            if stat_key == "points":
                if int(pts) <= 0:
                    continue
            elif float(d[stat_key]) <= 0:
                continue
            eligible.append((aid, d, pts))

        if stat_key == "points":
            ranked = sorted(
                eligible,
                key=lambda t: (-int(t[2]), int(t[0])),
            )
        else:
            ranked = sorted(
                eligible,
                key=lambda t: (-float(t[1][stat_key]), int(t[0])),
            )
        out: list[dict[str, object]] = []
        for rank, (aid, d, pts) in enumerate(ranked[:10], start=1):
            if stat_key == "points":
                stat_display = str(int(pts))
            elif stat_key.endswith("_km"):
                stat_display = f"{float(d[stat_key]):.1f} km"
            else:
                stat_display = f"{int(d[stat_key])} min"
            out.append(
                {
                    "rank": rank,
                    "name": _name(aid),
                    "hub": _hub(aid),
                    "stat_display": stat_display,
                }
            )
        return out

    return [
        {
            "slug": s[0],
            "title": s[1],
            "description": s[2],
            "stat_header": s[3],
            "rows": top10(s[4]),
        }
        for s in LEADERBOARD_SPECS
    ]
