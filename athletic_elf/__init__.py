"""Athletic Elf Flask application package."""

import logging
import os

from flask import Flask, g, redirect, request, url_for

from .config import Config, parse_app_developer_ids
from .extensions import db
from .session import current_athlete_from_request


def create_app(config_class: type = Config) -> Flask:
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    app = Flask(
        __name__,
        template_folder=os.path.join(pkg_dir, "..", "templates"),
    )
    app.config.from_object(config_class)
    app.config["APP_DEVELOPER_IDS"] = parse_app_developer_ids()

    if not app.logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)

    db.init_app(app)

    from . import models  # noqa: F401 — register models with SQLAlchemy

    from .blueprints import main, oauth, webhook

    app.register_blueprint(main.bp)
    app.register_blueprint(oauth.bp)
    app.register_blueprint(webhook.bp)

    endpoints_requiring_session = frozenset({"main.delete_my_data"})
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
