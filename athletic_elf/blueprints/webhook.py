"""Strava push subscription webhook."""

import secrets

from flask import Blueprint, abort, current_app, jsonify, request

from ..extensions import db
from ..models import (
    AUDIT_TYPE_WEBHOOK_ACTIVITY_CREATE,
    AUDIT_TYPE_WEBHOOK_ACTIVITY_DELETE,
    AUDIT_TYPE_WEBHOOK_ACTIVITY_UPDATE,
    Activity,
    Athlete,
    AuditItem,
)

bp = Blueprint("webhook", __name__)


def _path_verify_token_matches(webhook_verify_token: str) -> bool:
    expected = (current_app.config.get("VERIFY_TOKEN") or "").strip()
    if not expected:
        return False
    if len(webhook_verify_token) != len(expected):
        return False
    return secrets.compare_digest(webhook_verify_token, expected)


def _challenge_response():
    cfg = current_app.config
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    expected = (cfg.get("VERIFY_TOKEN") or "").strip()

    if mode and token:
        if (
            mode == "subscribe"
            and expected
            and len(token) == len(expected)
            and secrets.compare_digest(token, expected)
        ):
            current_app.logger.info("Strava webhook subscription validated (GET)")
            return jsonify({"hub.challenge": challenge})
        return "", 403

    return "", 400


@bp.get("/webhook/<webhook_verify_token>")
def webhook_get(webhook_verify_token: str):
    if not _path_verify_token_matches(webhook_verify_token):
        abort(404)
    return _challenge_response()


@bp.post("/webhook/<webhook_verify_token>")
def webhook_post(webhook_verify_token: str):
    if not _path_verify_token_matches(webhook_verify_token):
        abort(404)

    current_app.logger.debug("Strava webhook POST received")

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
        aspect_type = body.get("aspect_type")

        current_app.logger.info(
            "Strava webhook %s for activity %s by athlete %s",
            aspect_type,
            activity_id,
            owner_id if owner_id is not None else "unknown",
        )

        if aspect_type == "create":
            if owner_id is None:
                current_app.logger.warning(
                    "Webhook activity create missing owner_id; skipping insert"
                )
            else:
                db.session.add(Activity(activity_id=activity_id, athlete_id=owner_id))
                db.session.add(
                    AuditItem(
                        audit_type=AUDIT_TYPE_WEBHOOK_ACTIVITY_CREATE,
                        source="strava",
                        target=str(activity_id),
                        context={"athlete_id": int(owner_id)},
                    )
                )
                db.session.commit()
        elif aspect_type == "delete":
            existing = Activity.query.filter_by(activity_id=activity_id).first()
            athlete_for_audit = (
                int(owner_id)
                if owner_id is not None
                else (int(existing.athlete_id) if existing is not None else None)
            )
            Activity.query.filter_by(activity_id=activity_id).delete()
            if athlete_for_audit is not None:
                db.session.add(
                    AuditItem(
                        audit_type=AUDIT_TYPE_WEBHOOK_ACTIVITY_DELETE,
                        source="strava",
                        target=str(activity_id),
                        context={"athlete_id": athlete_for_audit},
                    )
                )
            db.session.commit()
        elif aspect_type == "update":
            row = Activity.query.filter_by(activity_id=activity_id).first()
            if row is not None:
                # Clear cached Strava fields so /cron enrichment refetches latest details.
                row.distance = None
                row.sport_type = None
                row.start_date = None
                row.moving_time = None
                db.session.add(
                    AuditItem(
                        audit_type=AUDIT_TYPE_WEBHOOK_ACTIVITY_UPDATE,
                        source="strava",
                        target=str(activity_id),
                        context={"athlete_id": int(row.athlete_id)},
                    )
                )
                db.session.commit()

    return "EVENT_RECEIVED", 200
