import os
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

import requests as http_client
from flask import Flask, redirect, render_template_string, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy

from points import activities_total_points

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "postgresql://strava:strava@localhost:5432/strava"
)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")
db = SQLAlchemy(app)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "STRAVA")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
DOMAIN = os.getenv("DOMAIN")
STRAVA_API_BASE = "https://www.strava.com/api/v3"
STRAVA_OAUTH_AUTHORIZE = "https://www.strava.com/oauth/authorize"
STRAVA_OAUTH_TOKEN = "https://www.strava.com/oauth/token"

# Scopes: read (profile), activity:read for activity details + webhooks for non-private activities
OAUTH_SCOPES = "read,activity:read"


def _domain_base() -> str:
    if not DOMAIN:
        raise RuntimeError("DOMAIN environment variable is not set")
    d = DOMAIN.strip().rstrip("/")
    if not d.startswith("http"):
        d = f"https://{d}"
    return d


def _oauth_redirect_uri() -> str:
    return f"{_domain_base()}/oauth/callback"


def _parse_strava_datetime(iso: str) -> datetime:
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class Athelete(db.Model):
    __tablename__ = "athelete"
    id = db.Column(db.Integer, primary_key=True)
    athlete_id = db.Column(db.BigInteger, nullable=False, unique=True)
    username = db.Column(db.String(255), nullable=False, default="")
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.Integer, nullable=False)


class Activity(db.Model):
    __tablename__ = "activity"
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.BigInteger, nullable=False)
    athlete_id = db.Column(db.BigInteger, nullable=True)
    distance = db.Column(db.Float, nullable=True)
    sport_type = db.Column(db.String(255), nullable=True)
    start_date = db.Column(db.DateTime, nullable=True)
    moving_time = db.Column(db.Integer, nullable=True)


with app.app_context():
    db.create_all()


def _list_push_subscriptions():
    r = http_client.get(
        f"{STRAVA_API_BASE}/push_subscriptions",
        params={"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and data.get("id") is not None:
        return [data]
    return []


def _ensure_push_subscription():
    """
    Strava allows one push subscription per application. Callback is /webhook;
    each event includes owner_id in the JSON body.
    """
    subs = _list_push_subscriptions()
    if subs:
        return subs[0].get("id")
    callback_url = f"{_domain_base()}/webhook"
    r = http_client.post(
        f"{STRAVA_API_BASE}/push_subscriptions",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "callback_url": callback_url,
            "verify_token": VERIFY_TOKEN,
        },
    )
    r.raise_for_status()
    created = r.json()
    return created.get("id")


def _maybe_refresh_athlete_token(athlete: Athelete) -> None:
    now = int(datetime.now(timezone.utc).timestamp())
    if now < athlete.expires_at - 300:
        return
    r = http_client.post(
        STRAVA_OAUTH_TOKEN,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
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
        _maybe_refresh_athlete_token(athlete)
        resp = http_client.get(
            f"{STRAVA_API_BASE}/activities/{activity.activity_id}",
            headers={"Authorization": f"Bearer {athlete.access_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"distance: {data['distance']}")
        print(f"athlete id: {data['athlete']['id']}")
        activity.athlete_id = data["athlete"]["id"]
        activity.distance = data["distance"]
        activity.sport_type = data["sport_type"]
        activity.start_date = _parse_strava_datetime(data["start_date"])
        activity.moving_time = data["moving_time"]
        processed += 1
    return processed


def _webhook_challenge_response():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            return jsonify({"hub.challenge": challenge})
        return "", 403

    return "", 400


@app.get("/webhook")
def webhook_get():
    return _webhook_challenge_response()


@app.post("/webhook")
def webhook_post():
    print("webhook event received!", request.args, request.json)

    body = request.json or {}
    owner_id = body.get("owner_id")

    if body.get("object_type") == "athlete" and body.get("aspect_type") == "update":
        updates = body.get("updates") or {}
        if updates.get("authorized") == "false":
            Athelete.query.filter_by(athlete_id=body["object_id"]).delete()
            db.session.commit()
            return "EVENT_RECEIVED", 200

    if body.get("object_type") == "activity":
        activity_id = body["object_id"]
        if body.get("aspect_type") == "create":
            if owner_id is None:
                print("webhook activity create missing owner_id; skipping insert")
            else:
                db.session.add(
                    Activity(activity_id=activity_id, athlete_id=owner_id)
                )
                db.session.commit()
        elif body.get("aspect_type") == "delete":
            Activity.query.filter_by(activity_id=activity_id).delete()
            db.session.commit()

    return "EVENT_RECEIVED", 200


@app.get("/oauth/start")
def oauth_start():
    if not CLIENT_ID or not CLIENT_SECRET or not DOMAIN:
        return (
            "Set CLIENT_ID, CLIENT_SECRET, and DOMAIN environment variables.",
            500,
        )
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    redirect_uri = _oauth_redirect_uri()
    q = urlencode(
        {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "approval_prompt": "force",
            "scope": OAUTH_SCOPES,
            "state": state,
        },
        quote_via=quote,
    )
    return redirect(f"{STRAVA_OAUTH_AUTHORIZE}?{q}")


@app.get("/oauth/callback")
def oauth_callback():
    if not CLIENT_ID or not CLIENT_SECRET or not DOMAIN:
        return (
            "Set CLIENT_ID, CLIENT_SECRET, and DOMAIN environment variables.",
            500,
        )
    err = request.args.get("error")
    if err:
        return f"OAuth error: {err}", 400
    if request.args.get("state") != session.get("oauth_state"):
        return "Invalid OAuth state", 400
    session.pop("oauth_state", None)
    code = request.args.get("code")
    if not code:
        return "Missing authorization code", 400

    redirect_uri = _oauth_redirect_uri()
    token_resp = http_client.post(
        STRAVA_OAUTH_TOKEN,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": _oauth_redirect_uri(),
        },
    )
    token_resp.raise_for_status()
    data = token_resp.json()
    athlete_info = data["athlete"]
    aid = athlete_info["id"]
    username = athlete_info.get("username") or ""

    row = Athelete.query.filter_by(athlete_id=aid).first()
    if row is None:
        row = Athelete(
            athlete_id=aid,
            username=username,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
        )
        db.session.add(row)
    else:
        row.username = username
        row.access_token = data["access_token"]
        row.refresh_token = data["refresh_token"]
        row.expires_at = data["expires_at"]

    db.session.commit()

    try:
        _ensure_push_subscription()
    except Exception as ex:
        print(f"push subscription (may already exist): {ex}")

    return (
        f"Registered athlete {aid} ({username}). "
        "Webhook subscription created if this was the first registration for this app.",
        200,
    )


@app.post("/cron")
def cron():
    n = process_activities(10)
    db.session.commit()
    return f"Processed {n} activities", 200


_RESULTS_PAGE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Results</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; }
    table { border-collapse: collapse; }
    th, td { border: 1px solid #ccc; padding: 0.5rem 1rem; text-align: left; }
    th { background: #f5f5f5; }
    tr:nth-child(even) { background: #fafafa; }
  </style>
</head>
<body>
  <h1>Points by athlete</h1>
  <table>
    <thead><tr><th>Username</th><th>Athlete ID</th><th>Points</th></tr></thead>
    <tbody>
    {% for row in rows %}
      <tr>
        <td>{{ row.username }}</td>
        <td>{{ row.athlete_id }}</td>
        <td>{{ row.points }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</body>
</html>
"""


@app.get("/results")
def results():
    activities = (
        Activity.query.filter(
            Activity.athlete_id.isnot(None),
            Activity.start_date.isnot(None),
        )
        .order_by(Activity.athlete_id, Activity.id)
        .all()
    )
    by_athlete = defaultdict(list)
    for a in activities:
        by_athlete[a.athlete_id].append(a)

    rows = []
    for athlete_id, acts in by_athlete.items():
        athelete = Athelete.query.filter_by(athlete_id=athlete_id).first()
        username = (
            athelete.username
            if athelete and athelete.username
            else "—"
        )
        pts = activities_total_points(acts)
        rows.append(
            {
                "username": username,
                "athlete_id": athlete_id,
                "points": pts,
            }
        )

    rows.sort(key=lambda r: (-r["points"], r["athlete_id"]))
    return render_template_string(_RESULTS_PAGE, rows=rows)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 80))
    app.run(host="0.0.0.0", port=port)
