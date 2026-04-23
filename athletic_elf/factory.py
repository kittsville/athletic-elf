"""Flask application factory."""

import logging
import os

import click
from flask import (
    Flask,
    Response,
    current_app,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

from .competition_periods import period_specs_for_config
from .config import (
    Config,
    parse_app_developer_ids,
    parse_comma_options,
    parse_competition_start_epoch,
    parse_datetime_utc,
    parse_week_boundary_datetimes,
)
from .extensions import db
from .jinja_filters import utc_time
from .session import current_athlete_from_request
from .utils import athlete_role_label, format_moving_time


def _require_competition_schedule(app: Flask) -> None:
    """Weekly hub/department scoring requires a valid competition window (see competition_periods)."""
    start = app.config.get("COMPETITION_START_DATETIME")
    boundaries = app.config.get("WEEK_BOUNDARY_DATETIMES") or ()
    end = app.config.get("COMPETITION_END_DATETIME")
    if start is None:
        raise ValueError(
            "COMPETITION_START_DATETIME must be set to a valid ISO 8601 datetime "
            "(competition start; used for Strava backfill and scoring periods)."
        )
    if end is None:
        raise ValueError(
            "COMPETITION_END_DATETIME must be set to a valid ISO 8601 datetime "
            "(competition end; final scoring period boundary and activity cutoff)."
        )
    specs = period_specs_for_config(start, boundaries, end)
    if not specs:
        raise ValueError(
            "Competition schedule must define at least one scoring period: "
            "ensure COMPETITION_END_DATETIME is after COMPETITION_START_DATETIME, and that "
            "WEEK_BOUNDARIES (comma-separated period ends) together with COMPETITION_END_DATETIME "
            "produce at least one period end strictly after the competition start."
        )


def _show_organiser_nav_for_template() -> bool:
    """Match main blueprint nav: organisers and app developers see organiser links."""
    athlete = getattr(g, "current_athlete", None)
    if athlete is None:
        return False
    return (
        bool(athlete.is_organiser)
        or int(athlete.athlete_id) in current_app.config["APP_DEVELOPER_IDS"]
    )


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
    start_raw = app.config.get("COMPETITION_START_DATETIME")
    end_raw = app.config.get("COMPETITION_END_DATETIME")
    start_str = start_raw.strip() if isinstance(start_raw, str) else None
    end_str = end_raw.strip() if isinstance(end_raw, str) else None
    app.config["ACTIVITY_FETCH_AFTER_EPOCH"] = parse_competition_start_epoch(start_str)
    app.config["COMPETITION_START_DATETIME"] = parse_datetime_utc(start_str)
    app.config["WEEK_BOUNDARY_DATETIMES"] = parse_week_boundary_datetimes(
        app.config.get("WEEK_BOUNDARIES") or ""
    )
    app.config["COMPETITION_END_DATETIME"] = parse_datetime_utc(end_str)
    _require_competition_schedule(app)
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
    app.jinja_env.filters["utc_time"] = utc_time

    from . import models  # noqa: F401 — register models with SQLAlchemy

    from .blueprints import cron, main, oauth, webhook

    app.register_blueprint(main.bp)
    app.register_blueprint(cron.bp)
    app.register_blueprint(oauth.bp)
    app.register_blueprint(webhook.bp)

    @app.errorhandler(404)
    def http_not_found(_e):
        return (
            render_template(
                "errors/404.html",
                show_organiser_nav=_show_organiser_nav_for_template(),
            ),
            404,
        )

    @app.errorhandler(403)
    def http_forbidden(_e):
        return (
            render_template(
                "errors/403.html",
                show_organiser_nav=_show_organiser_nav_for_template(),
            ),
            403,
        )

    endpoints_requiring_session = frozenset(
        {
            "main.delete_my_data",
            "main.settings",
            "main.settings_generate_mcp_key",
            "main.hub_department_form",
            "main.leaders",
            "main.results",
            "main.athletes",
            "main.athlete_activities",
            "main.athletes_make_organiser",
            "main.athletes_make_inactive",
            "main.athletes_resync_activities",
            "main.bonuses",
            "main.bonus_delete",
            "main.weeks",
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
