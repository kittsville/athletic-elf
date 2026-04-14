"""Hub and department summary rows from per-athlete points and bonuses."""

from collections import defaultdict

from points import team_points
from sqlalchemy.orm import load_only

from .extensions import db
from .models import Athlete, Bonus


def summaries_by_hub_and_department(
    points_by: dict[int, int],
    hub_options: list[str],
    department_options: list[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Per hub/department: team_points() over each member's total points (see points.team_points)."""
    hub_set = frozenset(hub_options)
    dept_set = frozenset(department_options)
    hub_member_points: defaultdict[str, list[int]] = defaultdict(list)
    dept_member_points: defaultdict[str, list[int]] = defaultdict(list)
    athletes = Athlete.query.options(
        load_only(
            Athlete.athlete_id, Athlete.hub, Athlete.department, Athlete.is_active
        )
    ).all()
    for a in athletes:
        if not a.is_active:
            continue
        aid = int(a.athlete_id)
        pts = points_by.get(aid, 0)
        h = (a.hub or "").strip()
        if h in hub_set:
            hub_member_points[h].append(pts)
        d = (a.department or "").strip()
        if d in dept_set:
            dept_member_points[d].append(pts)
    hub_bonus: defaultdict[str, int] = defaultdict(int)
    dept_bonus: defaultdict[str, int] = defaultdict(int)
    for b in Bonus.query.all():
        t = b.target.strip()
        if t in hub_set:
            hub_bonus[t] += int(b.points)
        elif t in dept_set:
            dept_bonus[t] += int(b.points)

    hub_rows = [
        {
            "name": h,
            "athlete_count": len(hub_member_points[h]),
            "points": float(team_points(hub_member_points[h])) + hub_bonus.get(h, 0),
        }
        for h in hub_options
    ]
    dept_rows = [
        {
            "name": d,
            "athlete_count": len(dept_member_points[d]),
            "points": float(team_points(dept_member_points[d])) + dept_bonus.get(d, 0),
        }
        for d in department_options
    ]
    hub_rows.sort(key=lambda r: (-float(r["points"]), str(r["name"])))
    dept_rows.sort(key=lambda r: (-float(r["points"]), str(r["name"])))
    return hub_rows, dept_rows
