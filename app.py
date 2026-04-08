import os

import requests as http_client
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "postgresql://strava:strava@localhost:5432/strava"
)
db = SQLAlchemy(app)

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "STRAVA")
STRAVA_ACCESS_TOKEN = os.environ["STRAVA_ACCESS_TOKEN"]
STRAVA_API_BASE = "https://www.strava.com/api/v3"


class Task(db.Model):
    __tablename__ = "task"
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.BigInteger, nullable=False)


class Activity(db.Model):
    __tablename__ = "activity"
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.BigInteger, nullable=False)
    athlete_id = db.Column(db.BigInteger, nullable=False)
    distance = db.Column(db.Float, nullable=False)


with app.app_context():
    db.create_all()


def process_activity(activity_id):
    resp = http_client.get(
        f"{STRAVA_API_BASE}/activities/{activity_id}",
        headers={"Authorization": f"Bearer {STRAVA_ACCESS_TOKEN}"},
    )
    resp.raise_for_status()
    data = resp.json()
    print(f"distance: {data['distance']}")
    print(f"athlete id: {data['athlete']['id']}")
    record = Activity(
        activity_id=activity_id,
        athlete_id=data["athlete"]["id"],
        distance=data["distance"],
    )
    db.session.add(record)


@app.post("/webhook")
def webhook_post():
    print("webhook event received!", request.args, request.json)

    body = request.json
    if body.get("object_type") == "activity":
        activity_id = body["object_id"]
        if body.get("aspect_type") == "create":
            task = Task(activity_id=activity_id)
            db.session.add(task)
            db.session.commit()
        elif body.get("aspect_type") == "delete":
            Activity.query.filter_by(activity_id=activity_id).delete()
            db.session.commit()

    return "EVENT_RECEIVED", 200


@app.post("/cron")
def cron():
    tasks = Task.query.limit(10).all()
    for task in tasks:
        process_activity(task.activity_id)
        db.session.delete(task)
    db.session.commit()
    return f"Processed {len(tasks)} tasks", 200


@app.get("/webhook")
def webhook_get():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            return jsonify({"hub.challenge": challenge})
        else:
            return "", 403

    return "", 400


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 80))
    app.run(host="0.0.0.0", port=port)
