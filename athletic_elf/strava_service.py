"""Strava REST API calls and activity enrichment."""

from datetime import datetime, timezone

import requests as http_client
from flask import current_app

from .extensions import db
from .models import Activity, Athelete
from .utils import domain_base, parse_strava_datetime


def _apply_summary_activity_payload(row: Activity, payload: dict, athlete_strava_id: int) -> None:
    """Map a SummaryActivity JSON object onto our Activity row."""
    row.activity_id = int(payload["id"])
    meta = payload.get("athlete")
    if isinstance(meta, dict) and meta.get("id") is not None:
        row.athlete_id = int(meta["id"])
    else:
        row.athlete_id = athlete_strava_id
    row.distance = payload.get("distance")
    st = payload.get("sport_type")
    if st is None:
        row.sport_type = None
    else:
        row.sport_type = st if isinstance(st, str) else str(st)
    sd = payload.get("start_date")
    row.start_date = parse_strava_datetime(sd) if sd else None
    row.moving_time = payload.get("moving_time")


def sync_activities_since_competition_start(athlete_pk: int) -> int:
    """
    Paginate GET /athlete/activities with `after` = competition start epoch;
    upsert SummaryActivity rows for this athlete's Strava account.
    """
    cfg = current_app.config
    after = cfg.get("ACTIVITY_FETCH_AFTER_EPOCH")
    if after is None:
        current_app.logger.info(
            "ACTIVITY_START_DATE not set or invalid; skipping initial activity backfill "
            "for athlete pk=%s",
            athlete_pk,
        )
        return 0

    athlete = db.session.get(Athelete, athlete_pk)
    if athlete is None:
        current_app.logger.warning(
            "Initial activity sync: no athlete row for pk=%s", athlete_pk
        )
        return 0

    api_base = cfg["STRAVA_API_BASE"]
    per_page = int(cfg.get("STRAVA_ACTIVITIES_PAGE_SIZE") or 200)
    per_page = min(max(per_page, 1), 200)
    url = f"{api_base}/athlete/activities"
    strava_athlete_id = int(athlete.athlete_id)
    total_upserted = 0
    page = 1

    while True:
        maybe_refresh_athlete_token(athlete)
        r = http_client.get(
            url,
            params={
                "after": after,
                "page": page,
                "per_page": per_page,
            },
            headers={"Authorization": f"Bearer {athlete.access_token}"},
            timeout=60,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        if not isinstance(batch, list):
            current_app.logger.error(
                "Unexpected /athlete/activities response for athlete pk=%s: %r",
                athlete_pk,
                batch,
            )
            break

        for payload in batch:
            if not isinstance(payload, dict) or payload.get("id") is None:
                continue
            aid = int(payload["id"])
            existing = Activity.query.filter_by(activity_id=aid).first()
            if existing is None:
                existing = Activity(activity_id=aid)
                db.session.add(existing)
            _apply_summary_activity_payload(existing, payload, strava_athlete_id)

        db.session.commit()
        total_upserted += len(batch)

        if len(batch) < per_page:
            break
        page += 1

    return total_upserted


def list_push_subscriptions():
    cfg = current_app.config
    r = http_client.get(
        f"{cfg['STRAVA_API_BASE']}/push_subscriptions",
        params={"client_id": cfg["CLIENT_ID"], "client_secret": cfg["CLIENT_SECRET"]},
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and data.get("id") is not None:
        return [data]
    return []


def ensure_push_subscription():
    """
    Strava allows one push subscription per application. Callback is /webhook;
    each event includes owner_id in the JSON body.
    """
    cfg = current_app.config
    subs = list_push_subscriptions()
    if subs:
        return subs[0].get("id")
    callback_url = f"{domain_base()}/webhook"
    r = http_client.post(
        f"{cfg['STRAVA_API_BASE']}/push_subscriptions",
        data={
            "client_id": cfg["CLIENT_ID"],
            "client_secret": cfg["CLIENT_SECRET"],
            "callback_url": callback_url,
            "verify_token": cfg["VERIFY_TOKEN"],
        },
    )
    r.raise_for_status()
    created = r.json()
    return created.get("id")


def maybe_refresh_athlete_token(athlete: Athelete) -> None:
    cfg = current_app.config
    now = int(datetime.now(timezone.utc).timestamp())
    if now < athlete.expires_at - 300:
        return
    r = http_client.post(
        cfg["STRAVA_OAUTH_TOKEN"],
        data={
            "client_id": cfg["CLIENT_ID"],
            "client_secret": cfg["CLIENT_SECRET"],
            "grant_type": "refresh_token",
            "refresh_token": athlete.refresh_token,
        },
    )
    r.raise_for_status()
    data = r.json()
    athlete.access_token = data["access_token"]
    if data.get("refresh_token"):
        athlete.refresh_token = data["refresh_token"]
    athlete.expires_at = data["expires_at"]


def process_activities(limit=10):
    cfg = current_app.config
    api_base = cfg["STRAVA_API_BASE"]
    pending = (
        Activity.query.filter(Activity.start_date.is_(None))
        .order_by(Activity.id)
        .limit(limit)
        .all()
    )
    processed = 0
    for snapshot in pending:
        activity = Activity.query.filter(
            Activity.id == snapshot.id, Activity.start_date.is_(None)
        ).first()
        if activity is None:
            continue
        if activity.athlete_id is None:
            print(f"skip activity {activity.activity_id}: no athlete_id")
            continue
        athlete = Athelete.query.filter_by(athlete_id=activity.athlete_id).first()
        if athlete is None:
            print(f"skip activity {activity.activity_id}: no athelete row")
            continue
        maybe_refresh_athlete_token(athlete)
        resp = http_client.get(
            f"{api_base}/activities/{activity.activity_id}",
            headers={"Authorization": f"Bearer {athlete.access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"distance: {data['distance']}")
        print(f"athlete id: {data['athlete']['id']}")
        activity.athlete_id = data["athlete"]["id"]
        activity.distance = data["distance"]
        activity.sport_type = data["sport_type"]
        activity.start_date = parse_strava_datetime(data["start_date"])
        activity.moving_time = data["moving_time"]
        processed += 1
    return processed
