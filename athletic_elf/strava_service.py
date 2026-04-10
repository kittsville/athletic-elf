"""Strava REST API calls and activity enrichment."""

from datetime import datetime, timezone

import requests as http_client
from flask import current_app

from .extensions import db
from .models import Activity, Athelete
from .utils import domain_base, parse_strava_datetime


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
