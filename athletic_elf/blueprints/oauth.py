"""Strava OAuth 2.0 start and callback."""

import secrets
from urllib.parse import quote, urlencode

import requests as http_client
from flask import Blueprint, current_app, redirect, request, session, url_for

from ..background import schedule_initial_activity_sync
from ..extensions import db
from ..models import Athelete
from ..session import BROWSER_TOKEN_SESSION_KEY, create_browser_session
from ..strava_service import ensure_push_subscription
from ..utils import athlete_hub_department_complete, oauth_redirect_uri

bp = Blueprint("oauth", __name__)


@bp.get("/oauth/start")
def oauth_start():
    cfg = current_app.config
    if not cfg["CLIENT_ID"] or not cfg["CLIENT_SECRET"] or not cfg["DOMAIN"]:
        return (
            "Set CLIENT_ID, CLIENT_SECRET, and DOMAIN environment variables.",
            500,
        )
    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    redirect_uri = oauth_redirect_uri()
    q = urlencode(
        {
            "client_id": cfg["CLIENT_ID"],
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "approval_prompt": "force",
            "scope": cfg["OAUTH_SCOPES"],
            "state": state,
        },
        quote_via=quote,
    )
    return redirect(f"{cfg['STRAVA_OAUTH_AUTHORIZE']}?{q}")


@bp.get("/oauth/callback")
def oauth_callback():
    cfg = current_app.config
    if not cfg["CLIENT_ID"] or not cfg["CLIENT_SECRET"] or not cfg["DOMAIN"]:
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
        cfg["STRAVA_OAUTH_TOKEN"],
        data={
            "client_id": cfg["CLIENT_ID"],
            "client_secret": cfg["CLIENT_SECRET"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": oauth_redirect_uri(),
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
        current_app.logger.info(
            "New athlete registered via OAuth: athlete_id=%s firstname=%s lastname=%s",
            aid,
            firstname,
            lastname,
        )
    else:
        current_app.logger.info(
            "Athlete re-authenticated via OAuth: athlete_id=%s firstname=%s lastname=%s",
            aid,
            firstname,
            lastname,
        )

    db.session.flush()
    session_token, _ = create_browser_session(int(row.athlete_id))
    db.session.commit()

    try:
        ensure_push_subscription()
    except Exception as ex:
        print(f"push subscription (may already exist): {ex}")

    if new_registration:
        schedule_initial_activity_sync(
            current_app._get_current_object(), int(row.athlete_id)
        )

    session.permanent = True
    session[BROWSER_TOKEN_SESSION_KEY] = session_token
    if athlete_hub_department_complete(row.hub, row.department):
        return redirect(url_for("main.index"))
    return redirect(url_for("main.hub_department_form"))
