"""Flask application factory."""

import logging
import os

import click
from flask import Flask, Response, current_app, g, redirect, request, url_for

from .config import (
    Config,
    parse_activity_start_epoch,
    parse_app_developer_ids,
    parse_comma_options,
)
from .extensions import db
from .session import current_athlete_from_request
from .utils import athlete_role_label, format_moving_time


def _request_is_https() -> bool:
    """True if the active request is served over HTTPS (direct or via X-Forwarded-Proto)."""
    if request.is_secure:
        return True
    forwarded = (
        (request.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
    )
    return forwarded == "https"


def create_app(config_class: type = Config) -> Flask:
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    app = Flask(
        __name__,
        template_folder=os.path.join(pkg_dir, "..", "templates"),
    )
    app.config.from_object(config_class)
    if not (str(app.config.get("VERIFY_TOKEN") or "").strip()):
        raise ValueError(
            "VERIFY_TOKEN must be set (environment variable or on the Flask Config class). "
            "It is used for Strava subscription validation and the webhook URL path."
        )
    app.config["SESSION_COOKIE_SECURE"] = bool(app.config.get("ENFORCE_HTTPS"))
    app.config["APP_DEVELOPER_IDS"] = parse_app_developer_ids()
    app.config["ACTIVITY_FETCH_AFTER_EPOCH"] = parse_activity_start_epoch(
        app.config.get("ACTIVITY_START_DATE")
    )
    # Allow tests (or custom Config subclasses) to pin options; otherwise env wins.
    if getattr(config_class, "HUB_OPTIONS", None) is None:
        app.config["HUB_OPTIONS"] = parse_comma_options(
            os.environ.get("HUB_OPTIONS"),
            "North Hub,South Hub,East Hub,West Hub",
        )
    if getattr(config_class, "DEPARTMENT_OPTIONS", None) is None:
        app.config["DEPARTMENT_OPTIONS"] = parse_comma_options(
            os.environ.get("DEPARTMENT_OPTIONS"),
            "Engineering,Sales,Marketing,Operations",
        )

    if not app.logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)

    db.init_app(app)
    app.add_template_global(athlete_role_label)
    app.add_template_global(format_moving_time)

    from . import models  # noqa: F401 — register models with SQLAlchemy

    from .blueprints import cron, main, oauth, webhook

    app.register_blueprint(main.bp)
    app.register_blueprint(cron.bp)
    app.register_blueprint(oauth.bp)
    app.register_blueprint(webhook.bp)

    endpoints_requiring_session = frozenset(
        {
            "main.delete_my_data",
            "main.hub_department_form",
            "main.leaders",
            "main.results",
            "main.athletes",
            "main.athlete_activities",
            "main.athletes_make_organiser",
            "main.bonuses",
            "main.bonus_delete",
        }
    )
    endpoints_skip_session_lookup = frozenset(
        {"webhook.webhook_get", "webhook.webhook_post", "cron.cron"}
    )

    @app.before_request
    def require_https_when_enforced():
        if not current_app.config.get("ENFORCE_HTTPS"):
            return None
        if _request_is_https():
            return None
        return Response(
            "This site must be accessed via HTTPS.\n",
            status=403,
            mimetype="text/plain",
        )

    @app.before_request
    def attach_session_athlete():
        if request.endpoint in endpoints_skip_session_lookup:
            g.current_athlete = None
            return
        g.current_athlete = current_athlete_from_request()
        if (
            request.endpoint in endpoints_requiring_session
            and g.current_athlete is None
        ):
            return redirect(url_for("main.index"))

    @app.cli.command("init-db")
    def init_db_command() -> None:
        """Create database tables. Run once per deploy (release phase / job), not per web worker."""
        with app.app_context():
            db.create_all()
        click.echo("init-db: tables created if missing.")

    if app.config.get("AUTO_CREATE_TABLES"):
        with app.app_context():
            db.create_all()

    return app
