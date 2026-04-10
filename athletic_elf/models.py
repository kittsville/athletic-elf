"""SQLAlchemy models."""

from .extensions import db


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
