"""Competition week boundaries, summarization, and live vs frozen team scoring."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from points import activities_total_points, team_points
from sqlalchemy import func

from .extensions import db
from .models import Activity, Week, WeekScore
from .team_scoring import hub_and_department_member_point_lists

GRACE_AFTER_PREVIOUS_BOUNDARY = timedelta(hours=12)
SCOPE_HUB = "hub"
SCOPE_DEPARTMENT = "department"


@dataclass(frozen=True)
class PeriodSpec:
    index: int
    eligible_lower: datetime
    canonical_start: datetime
    end_exclusive: datetime


def normalized_period_endpoints(
    activity_start: datetime,
    boundary_datetimes: tuple[datetime, ...],
    activity_end: datetime,
) -> tuple[datetime, ...]:
    """Sorted period end instants strictly after ``activity_start``."""
    ends = sorted(boundary_datetimes)
    ends = sorted(frozenset(ends) | {activity_end})
    return tuple(e for e in ends if e > activity_start)


def period_specs_for_config(
    activity_start: datetime,
    boundary_datetimes: tuple[datetime, ...],
    activity_end: datetime,
) -> list[PeriodSpec]:
    ends = normalized_period_endpoints(activity_start, boundary_datetimes, activity_end)
    if not ends:
        return []
    specs: list[PeriodSpec] = []
    for idx, end_exclusive in enumerate(ends):
        if idx == 0:
            eligible_lower = activity_start
            canonical_start = activity_start
        else:
            prev_boundary = ends[idx - 1]
            eligible_lower = max(
                activity_start, prev_boundary - GRACE_AFTER_PREVIOUS_BOUNDARY
            )
            canonical_start = prev_boundary
        specs.append(
            PeriodSpec(
                index=idx,
                eligible_lower=eligible_lower,
                canonical_start=canonical_start,
                end_exclusive=end_exclusive,
            )
        )
    return specs


def _period_specs(app) -> list[PeriodSpec]:
    return period_specs_for_config(
        app.config["ACTIVITY_START_DATETIME"],
        app.config.get("WEEK_BOUNDARY_DATETIMES") or (),
        app.config["ACTIVITY_END_DATETIME"],
    )


def period_spec_for_index(app, period_index: int) -> PeriodSpec | None:
    """Window bounds for a period index (from config)."""
    specs = _period_specs(app)
    if period_index < 0 or period_index >= len(specs):
        return None
    return specs[period_index]


def closed_period_count() -> int:
    """How many scoring periods have been summarized (one ``Week`` row each)."""
    n = db.session.query(func.count(Week.id)).scalar()
    return int(n or 0)


def next_period_spec_to_summarize(app, now: datetime) -> PeriodSpec | None:
    specs = _period_specs(app)
    if not specs:
        return None
    closed = closed_period_count()
    if closed >= len(specs):
        return None
    spec = specs[closed]
    if now < spec.end_exclusive:
        return None
    return spec


def activities_for_period_summarize(spec: PeriodSpec) -> list[Activity]:
    q = Activity.query.filter(
        Activity.week_id.is_(None),
        Activity.start_date.isnot(None),
        Activity.start_date >= spec.eligible_lower,
        Activity.start_date < spec.end_exclusive,
    )
    return q.all()


def summarize_next_due_period(app, now: datetime | None = None) -> bool:
    """
    If the next period has ended and is not yet summarized, compute team scores,
    insert ``Week`` and ``WeekScore`` rows, and set ``Activity.week_id``.

    Returns True if a period was summarized.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    spec = next_period_spec_to_summarize(app, now)
    if spec is None:
        return False
    if Week.query.filter_by(period_index=spec.index).first():
        return False

    acts = activities_for_period_summarize(spec)
    by_athlete: defaultdict[int, list[Activity]] = defaultdict(list)
    for a in acts:
        by_athlete[int(a.athlete_id)].append(a)
    points_by = {
        aid: activities_total_points(alist) for aid, alist in by_athlete.items()
    }

    hubs = list(app.config["HUB_OPTIONS"])
    departments = list(app.config["DEPARTMENT_OPTIONS"])
    hub_members, dept_members = hub_and_department_member_point_lists(
        points_by, hubs, departments
    )

    computed_at = now
    week_row = Week(period_index=spec.index, summarized_at=computed_at)
    db.session.add(week_row)
    db.session.flush()

    for h in hubs:
        pts = float(team_points(hub_members[h]))
        db.session.add(
            WeekScore(
                week_id=week_row.id,
                target=h,
                team_scope=SCOPE_HUB,
                points=pts,
                created_at=computed_at,
            )
        )
    for d in departments:
        pts = float(team_points(dept_members[d]))
        db.session.add(
            WeekScore(
                week_id=week_row.id,
                target=d,
                team_scope=SCOPE_DEPARTMENT,
                points=pts,
                created_at=computed_at,
            )
        )

    for a in acts:
        a.week_id = week_row.id

    return True


def aggregates_frozen_team_scores(
    hub_options: list[str],
    department_options: list[str],
) -> tuple[dict[str, float], dict[str, float]]:
    """Sum frozen ``WeekScore`` points per hub / department name."""
    hub_totals: dict[str, float] = {h: 0.0 for h in hub_options}
    dept_totals: dict[str, float] = {d: 0.0 for d in department_options}
    hub_set = frozenset(hub_options)
    dept_set = frozenset(department_options)
    rows = (
        db.session.query(
            WeekScore.team_scope, WeekScore.target, func.sum(WeekScore.points)
        )
        .group_by(WeekScore.team_scope, WeekScore.target)
        .all()
    )
    for scope, target, total in rows:
        if total is None:
            continue
        t = (target or "").strip()
        if scope == SCOPE_HUB and t in hub_set:
            hub_totals[t] += float(total)
        elif scope == SCOPE_DEPARTMENT and t in dept_set:
            dept_totals[t] += float(total)
    return hub_totals, dept_totals


def _open_period_spec(app) -> PeriodSpec | None:
    specs = _period_specs(app)
    if not specs:
        return None
    n_closed = closed_period_count()
    if n_closed >= len(specs):
        return None
    return specs[n_closed]


def activities_for_live_team_window(app) -> list[Activity]:
    """Activities that count toward the in-progress period (not yet attributed)."""
    spec = _open_period_spec(app)
    if spec is None:
        return []
    q = Activity.query.filter(
        Activity.week_id.is_(None),
        Activity.start_date.isnot(None),
        Activity.start_date >= spec.eligible_lower,
        Activity.start_date < spec.end_exclusive,
    )
    return q.all()


def points_by_athlete_for_results_table(app) -> dict[int, int]:
    """
    Per-athlete points for the hub/department results tables: only the current
    open scoring period (activities not yet attributed), plus frozen ``WeekScore`` rows
    merged in at the route layer.
    """
    acts = activities_for_live_team_window(app)
    by_athlete: defaultdict[int, list[Activity]] = defaultdict(list)
    for a in acts:
        by_athlete[int(a.athlete_id)].append(a)
    return {aid: activities_total_points(alist) for aid, alist in by_athlete.items()}


def summarize_due_periods_loop(app, now: datetime | None = None) -> int:
    """Close every overdue period in order; returns how many periods were summarized."""
    if now is None:
        now = datetime.now(timezone.utc)
    n = 0
    while summarize_next_due_period(app, now):
        n += 1
    return n
