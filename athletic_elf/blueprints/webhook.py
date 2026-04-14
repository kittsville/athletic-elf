"""Strava push subscription webhook."""

from flask import Blueprint, current_app, jsonify, request

from ..extensions import db
from ..models import Activity, Athlete

bp = Blueprint("webhook", __name__)


def _challenge_response():
    cfg = current_app.config
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == cfg["VERIFY_TOKEN"]:
            print("WEBHOOK_VERIFIED")
            return jsonify({"hub.challenge": challenge})
        return "", 403

    return "", 400


@bp.get("/webhook")
def webhook_get():
    return _challenge_response()


@bp.post("/webhook")
def webhook_post():
    print("webhook event received!", request.args, request.json)

    body = request.json or {}
    owner_id = body.get("owner_id")

    if body.get("object_type") == "athlete" and body.get("aspect_type") == "update":
        updates = body.get("updates") or {}
        if updates.get("authorized") == "false":
            Athlete.query.filter_by(athlete_id=body["object_id"]).delete()
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
