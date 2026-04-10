"""Flask application factory."""

import logging
import os

from flask import Flask, g, redirect, request, url_for

from .config import (
    Config,
    parse_activity_start_epoch,
    parse_app_developer_ids,
    parse_comma_options,
)
from .extensions import db
from .session import current_athlete_from_request
from .utils import athlete_role_label


def create_app(config_class: type = Config) -> Flask:
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    app = Flask(
        __name__,
        template_folder=os.path.join(pkg_dir, "..", "templates"),
    )
    app.config.from_object(config_class)
    app.config["APP_DEVELOPER_IDS"] = parse_app_developer_ids()
    app.config["ACTIVITY_FETCH_AFTER_EPOCH"] = parse_activity_start_epoch(
        app.config.get("ACTIVITY_START_DATE")
    )
    app.config["HUB_OPTIONS"] = parse_comma_options(
        os.environ.get("HUB_OPTIONS"),
        "North Hub,South Hub,East Hub,West Hub",
    )
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

    from . import models  # noqa: F401 — register models with SQLAlchemy

    from .blueprints import main, oauth, webhook

    app.register_blueprint(main.bp)
    app.register_blueprint(oauth.bp)
    app.register_blueprint(webhook.bp)

    endpoints_requiring_session = frozenset(
        {
            "main.delete_my_data",
            "main.hub_department_form",
            "main.results",
            "main.atheletes",
            "main.atheletes_make_organiser",
        }
    )
    endpoints_skip_session_lookup = frozenset(
        {"webhook.webhook_get", "webhook.webhook_post", "main.cron"}
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

    with app.app_context():
        db.create_all()

    return app
