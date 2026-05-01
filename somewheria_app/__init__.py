import os
import threading
import time
import traceback
from datetime import datetime, timedelta

from flask import Flask, render_template, request
from werkzeug.exceptions import HTTPException

from .config import AppConfig
from .routes.admin_routes import register_admin_routes
from .routes.auth_routes import register_auth_routes
from .routes.public_routes import register_public_routes
from .routes.pwa_routes import register_pwa_routes
from .routes.ticket_routes import register_ticket_routes
from .services.analytics import AnalyticsTracker
from .services.appointments import AppointmentService
from .services.auth import AuthService
from .services.console import setup_console_logger
from .services.notifications import NotificationService
from .services.properties import PropertyService
from .services.registry import Services, set_services
from .services.security import register_csrf, register_security_headers
from .services.storage import FileStorageService
from .services.tickets import TicketService


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
    tickets = TicketService(config, storage, notifications)

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
            tickets=tickets,
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
    register_ticket_routes(app)

    @app.errorhandler(404)
    def page_not_found(_error):
        return render_template("404.html", title="Page Not Found"), 404

    @app.errorhandler(401)
    def unauthorized_error(_error):
        return render_template("401.html", title="Unauthorized"), 401

    @app.errorhandler(403)
    def forbidden_error(_error):
        return render_template("403.html", title="Forbidden"), 403

    @app.errorhandler(502)
    def bad_gateway_error(_error):
        return render_template("502.html", title="Bad Gateway"), 502

    @app.errorhandler(503)
    def service_unavailable_error(_error):
        return render_template("503.html", title="Service Unavailable"), 503

    @app.errorhandler(504)
    def gateway_timeout_error(_error):
        return render_template("504.html", title="Gateway Timeout"), 504

    # --- Crash safety: bare 503 + email alert ---
    # When an unhandled exception escapes a route, return an empty body (no
    # template, no DB, no cache, no AWS — nothing that could itself fail) and
    # email the admin in a background thread. Emails are rate-limited per
    # error fingerprint so a sustained outage doesn't mailbomb the inbox.
    crash_email_state = {
        "last_sent_by_key": {},
        "lock": threading.Lock(),
        "cooldown_seconds": 600,  # 10 minutes per unique error fingerprint
    }

    def _send_crash_email_async(subject: str, body: str, fingerprint: str) -> None:
        now = time.time()
        cooldown = crash_email_state["cooldown_seconds"]
        with crash_email_state["lock"]:
            sent = crash_email_state["last_sent_by_key"]
            last = sent.get(fingerprint, 0)
            if now - last < cooldown:
                return  # Already alerted on this error recently; skip.
            # Bound the dict so a long-running process that hits many distinct
            # error fingerprints can't grow this map without limit. Anything
            # older than the cooldown window can no longer suppress emails, so
            # it's safe to drop.
            cutoff = now - cooldown
            stale = [key for key, ts in sent.items() if ts < cutoff]
            for key in stale:
                del sent[key]
            sent[fingerprint] = now

        def _worker() -> None:
            try:
                notifications.send_email(subject, body)
            except Exception as mail_err:  # pragma: no cover - best-effort
                print(f"[crash-handler] Could not send alert email: {mail_err}")

        threading.Thread(target=_worker, daemon=True).start()

    def _crash_response(exc: BaseException):
        try:
            tb = traceback.format_exc()
        except Exception:
            tb = "(traceback unavailable)"

        try:
            path = request.path
            method = request.method
            ua = request.headers.get("User-Agent", "")
            remote = request.headers.get("X-Forwarded-For", request.remote_addr or "")
        except Exception:
            path, method, ua, remote = "(unknown)", "(unknown)", "(unknown)", "(unknown)"

        fingerprint = f"{type(exc).__name__}:{path}"[:200]

        body = (
            "The website hit an unhandled error and served the bare fallback response.\n\n"
            f"Time:    {datetime.utcnow().isoformat()}Z\n"
            f"Request: {method} {path}\n"
            f"Client:  {remote}\n"
            f"Agent:   {ua}\n"
            f"Error:   {type(exc).__name__}: {exc}\n\n"
            f"Traceback:\n{tb}\n"
        )
        _send_crash_email_async(
            subject=f"[somewheria_app] crash on {method} {path}",
            body=body,
            fingerprint=fingerprint,
        )

        try:
            app.logger.exception("Unhandled exception serving %s %s", method, path)
        except Exception:
            pass

        # Empty body, status 503. Visitor sees a blank page; nothing on the
        # site is rendered. Content-Length: 0 keeps it explicitly empty.
        return ("", 503, {"Content-Type": "text/plain; charset=utf-8", "Content-Length": "0"})

    @app.errorhandler(500)
    def internal_server_error(error):
        return _crash_response(error)

    @app.errorhandler(Exception)
    def unhandled_exception(error):
        # Don't intercept Flask's own HTTP exceptions (401, 403, 404, 502, etc.) —
        # those have dedicated handlers above and shouldn't trigger crash emails.
        if isinstance(error, HTTPException):
            return error
        return _crash_response(error)

    # Periodic background cache refresh removed to cut AWS API Gateway / Lambda
    # costs. The cache is now populated on demand by /for-rent and /for-rent.json
    # (synchronous refresh on every page load) and after admin mutations.

    return app
