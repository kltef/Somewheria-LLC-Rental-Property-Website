import os
from datetime import timedelta

from flask import Flask, render_template

from .config import AppConfig
from .routes.admin_routes import register_admin_routes
from .routes.auth_routes import register_auth_routes
from .routes.public_routes import register_public_routes
from .routes.pwa_routes import register_pwa_routes
from .services.analytics import AnalyticsTracker
from .services.appointments import AppointmentService
from .services.auth import AuthService
from .services.console import setup_console_logger
from .services.notifications import NotificationService
from .services.properties import PropertyService
from .services.registry import Services, set_services
from .services.security import register_csrf, register_security_headers
from .services.storage import FileStorageService


def _is_development() -> bool:
    return os.getenv("FLASK_ENV", "production").lower() in ("development", "dev", "local")


def create_app() -> Flask:
    config = AppConfig()
    config.ensure_directories()

    # Only allow plaintext OAuth during local development; never in production.
    if _is_development():
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    else:
        os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)

    app = Flask(
        __name__,
        template_folder=str(config.template_dir),
        static_folder=str(config.static_dir),
        static_url_path="/static",
    )
    app.secret_key = config.secret_key
    app.config["DISABLE_BACKGROUND_THREADS"] = config.disable_background_threads
    app.config["SHOW_REQUEST_LOGS"] = True
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=not _is_development(),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=16 * 1024 * 1024,
    )
    setup_console_logger(config.console_log_level, config.log_file)

    analytics = AnalyticsTracker(config.analytics_days)
    storage = FileStorageService(config)
    notifications = NotificationService(config, analytics)
    appointments = AppointmentService(config)
    auth = AuthService(config, storage)
    properties = PropertyService(config, notifications)

    set_services(
        app,
        Services(
            config=config,
            analytics=analytics,
            notifications=notifications,
            storage=storage,
            appointments=appointments,
            auth=auth,
            properties=properties,
        ),
    )

    app.before_request(analytics.before_request)
    app.after_request(analytics.after_request)

    register_csrf(app)
    register_security_headers(app)

    register_auth_routes(app)
    register_public_routes(app)
    register_admin_routes(app)
    register_pwa_routes(app)

    @app.errorhandler(404)
    def page_not_found(_error):
        return render_template("404.html", title="Page Not Found"), 404

    @app.errorhandler(401)
    def unauthorized_error(_error):
        return render_template("401.html", title="Unauthorized"), 401

    @app.errorhandler(403)
    def forbidden_error(_error):
        return render_template("403.html", title="Forbidden"), 403

    @app.errorhandler(500)
    def internal_server_error(_error):
        return render_template("500.html", title="Server Error"), 500

    @app.errorhandler(502)
    def bad_gateway_error(_error):
        return render_template("502.html", title="Bad Gateway"), 502

    @app.errorhandler(503)
    def service_unavailable_error(_error):
        return render_template("503.html", title="Service Unavailable"), 503

    @app.errorhandler(504)
    def gateway_timeout_error(_error):
        return render_template("504.html", title="Gateway Timeout"), 504

    if not app.config["DISABLE_BACKGROUND_THREADS"]:
        properties.start_background_refresh()

    return app
