import hashlib
import logging
import os
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import requests as http_client
from flask import (
    Flask,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy

from points import activities_total_points

app = Flask(__name__)
# Python’s root “last resort” handler only emits WARNING+, and app.logger often has no
# handlers, so INFO would be dropped. Attach a stderr handler when nothing is configured.
if not app.logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    app.logger.addHandler(_h)
app.logger.setLevel(logging.INFO)

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
OAUTH_SCOPES = "read,activity:read,profile:read_all"

SESSION_COOKIE_NAME = "elf_session"
SESSION_TTL = timedelta(hours=48)


def _parse_app_developer_ids() -> frozenset[int]:
    raw = os.getenv("APP_DEVELOPER_IDS", "") or ""
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            continue
    return frozenset(ids)


APP_DEVELOPER_IDS = _parse_app_developer_ids()


def _domain_base() -> str:
    if not DOMAIN:
        raise RuntimeError("DOMAIN environment variable is not set")
    d = DOMAIN.strip().rstrip("/")
    if not d.startswith("http"):
        d = f"https://{d}"
    return d


def _oauth_redirect_uri() -> str:
    return f"{_domain_base()}/oauth/callback"


def _athlete_display_name(firstname: str, lastname: str) -> str:
    parts = [firstname.strip(), lastname.strip()]
    return " ".join(p for p in parts if p) or "—"


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
    firstname = db.Column(db.String(255), nullable=False, default="")
    lastname = db.Column(db.String(255), nullable=False, default="")
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


class BrowserSession(db.Model):
    """Persistent browser session row; cookie holds the secret, DB stores its hash."""

    __tablename__ = "session"
    id = db.Column(db.Integer, primary_key=True)
    athelete_id = db.Column(
        db.Integer, db.ForeignKey("athelete.id"), nullable=False, index=True
    )
    hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)


def _hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _create_browser_session(athelete_pk: int) -> tuple[str, datetime]:
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + SESSION_TTL
    db.session.add(
        BrowserSession(
            athelete_id=athelete_pk,
            hash=_hash_session_token(raw),
            expires_at=expires_at,
        )
    )
    return raw, expires_at


def _current_athlete_from_request() -> Athelete | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    h = _hash_session_token(token)
    now = datetime.now(timezone.utc)
    bs = (
        BrowserSession.query.filter_by(hash=h)
        .filter(BrowserSession.expires_at > now)
        .first()
    )
    if bs is None:
        return None
    return db.session.get(Athelete, bs.athelete_id)


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
                db.session.add(Activity(activity_id=activity_id, athlete_id=owner_id))
                db.session.commit()
        elif body.get("aspect_type") == "delete":
            Activity.query.filter_by(activity_id=activity_id).delete()
            db.session.commit()

    return "EVENT_RECEIVED", 200


@app.get("/")
def index():
    athlete = _current_athlete_from_request()
    if athlete is None:
        return render_template("index.html", logged_in=False)
    strava_id = int(athlete.athlete_id)
    name = _athlete_display_name(athlete.firstname, athlete.lastname)
    is_app_developer = strava_id in APP_DEVELOPER_IDS
    return render_template(
        "index.html",
        logged_in=True,
        strava_id=strava_id,
        name=name,
        is_app_developer=is_app_developer,
    )


@app.post("/delete-my-data")
def delete_my_data():
    athlete = _current_athlete_from_request()
    if athlete is None:
        return redirect(url_for("index"))
    pk = athlete.id
    strava_athlete_id = athlete.athlete_id
    BrowserSession.query.filter_by(athelete_id=pk).delete(synchronize_session=False)
    Activity.query.filter_by(athlete_id=strava_athlete_id).delete(
        synchronize_session=False
    )
    Athelete.query.filter_by(id=pk).delete(synchronize_session=False)
    db.session.commit()
    resp = redirect(url_for("index"))
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        "",
        max_age=0,
        expires=0,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
        path="/",
    )
    return resp


@app.post("/logout")
def logout():
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        h = _hash_session_token(token)
        BrowserSession.query.filter_by(hash=h).delete(synchronize_session=False)
        db.session.commit()
    resp = redirect(url_for("index"))
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        "",
        max_age=0,
        expires=0,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
        path="/",
    )
    return resp


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
    firstname = athlete_info.get("firstname") or ""
    lastname = athlete_info.get("lastname") or ""

    row = Athelete.query.filter_by(athlete_id=aid).first()
    if row is None:
        row = Athelete(
            athlete_id=aid,
            firstname=firstname,
            lastname=lastname,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
        )
        db.session.add(row)
        new_registration = True
    else:
        row.firstname = firstname
        row.lastname = lastname
        row.access_token = data["access_token"]
        row.refresh_token = data["refresh_token"]
        row.expires_at = data["expires_at"]
        new_registration = False

    if new_registration:
        app.logger.info(
            "New athlete registered via OAuth: athlete_id=%s firstname=%s lastname=%s",
            aid,
            firstname,
            lastname,
        )
    else:
        app.logger.info(
            "Athlete re-authenticated via OAuth: athlete_id=%s firstname=%s lastname=%s",
            aid,
            firstname,
            lastname,
        )

    db.session.flush()
    session_token, session_expires = _create_browser_session(row.id)
    db.session.commit()

    try:
        _ensure_push_subscription()
    except Exception as ex:
        print(f"push subscription (may already exist): {ex}")

    body = (
        f"Registered athlete {aid} ({_athlete_display_name(firstname, lastname)}). "
        "Webhook subscription created if this was the first registration for this app."
    )
    resp = make_response(body, 200)
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        session_token,
        max_age=int(SESSION_TTL.total_seconds()),
        expires=session_expires,
        httponly=True,
        samesite="Lax",
        secure=request.is_secure,
        path="/",
    )
    return resp


@app.post("/cron")
def cron():
    now = datetime.now(timezone.utc)
    removed_sessions = BrowserSession.query.filter(
        BrowserSession.expires_at < now
    ).delete(synchronize_session=False)
    n = process_activities(10)
    db.session.commit()
    summary = f"Processed {n} activities, removed {removed_sessions} expired session(s)"
    app.logger.info(summary)
    return (
        summary,
        200,
    )


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
        if athelete:
            fn = athelete.firstname or ""
            ln = athelete.lastname or ""
        else:
            fn, ln = "", ""
        pts = activities_total_points(acts)
        rows.append(
            {
                "firstname": fn,
                "lastname": ln,
                "athlete_id": athlete_id,
                "points": pts,
            }
        )

    rows.sort(key=lambda r: (-r["points"], r["athlete_id"]))
    return render_template("results.html", rows=rows)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 80))
    app.run(host="0.0.0.0", port=port)
