"""SQLAlchemy models."""

from .extensions import db


class Athlete(db.Model):
    __tablename__ = "athlete"
    athlete_id = db.Column(db.BigInteger, primary_key=True)
    firstname = db.Column(db.String(255), nullable=False, default="")
    lastname = db.Column(db.String(255), nullable=False, default="")
    access_token = db.Column(db.Text, nullable=False)
    refresh_token = db.Column(db.Text, nullable=False)
    expires_at = db.Column(db.Integer, nullable=False)
    hub = db.Column(db.String(255), nullable=True, index=True)
    department = db.Column(db.String(255), nullable=True, index=True)
    is_organiser = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class Activity(db.Model):
    __tablename__ = "activity"
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.BigInteger, nullable=False, unique=True)
    athlete_id = db.Column(
        db.BigInteger,
        db.ForeignKey("athlete.athlete_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    distance = db.Column(db.Float, nullable=True)
    sport_type = db.Column(db.String(255), nullable=True)
    start_date = db.Column(db.DateTime, nullable=True)
    moving_time = db.Column(db.Integer, nullable=True)
    week_id = db.Column(
        db.Integer,
        db.ForeignKey("week.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    week = db.relationship("Week", back_populates="activities")


class Week(db.Model):
    """One closed scoring time window (aligned with config period index)."""

    __tablename__ = "week"

    id = db.Column(db.Integer, primary_key=True)
    period_index = db.Column(db.Integer, nullable=False, unique=True, index=True)
    summarized_at = db.Column(db.DateTime(timezone=True), nullable=False)

    scores = db.relationship(
        "WeekScore",
        back_populates="week",
        cascade="all, delete-orphan",
    )
    activities = db.relationship("Activity", back_populates="week")


class WeekScore(db.Model):
    """Frozen hub or department team points when a week boundary is summarized."""

    __tablename__ = "week_score"
    __table_args__ = (
        db.UniqueConstraint(
            "week_id",
            "team_scope",
            "target",
            name="uq_week_score_scope_target",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(
        db.Integer,
        db.ForeignKey("week.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target = db.Column(db.String(255), nullable=False)
    team_scope = db.Column(db.String(32), nullable=False)
    points = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)

    week = db.relationship("Week", back_populates="scores")


class BrowserSession(db.Model):
    """Persistent browser session row; cookie holds the secret, DB stores its hash."""

    __tablename__ = "session"
    id = db.Column(db.Integer, primary_key=True)
    athlete_id = db.Column(
        db.BigInteger,
        db.ForeignKey("athlete.athlete_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)


class Bonus(db.Model):
    """Manual bonus points for weekly challenges (e.g. photo of the week)."""

    __tablename__ = "bonus"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    points = db.Column(db.Integer, nullable=False)
    target = db.Column(db.String(255), nullable=False, index=True)
    athlete_id = db.Column(
        db.BigInteger,
        db.ForeignKey("athlete.athlete_id", ondelete="CASCADE"),
        nullable=False,
    )

    awardee = db.relationship("Athlete", foreign_keys=[athlete_id])
