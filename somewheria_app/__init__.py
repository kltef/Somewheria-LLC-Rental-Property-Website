import os
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from flask import Flask, g, render_template, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import AppConfig
from .routes.admin_routes import register_admin_routes
from .routes.auth_routes import register_auth_routes
from .routes.public_routes import register_public_routes
from .routes.pwa_routes import register_pwa_routes
from .routes.ticket_routes import register_ticket_routes
from .routes.webhook_routes import register_webhook_routes
from .services.analytics import AnalyticsTracker
from .services.appointments import AppointmentService
from .services.auth import AuthService
from .services.console import setup_console_logger
from .services.jira import JiraClient
from .services.notifications import NotificationService
from .services.properties import PropertyService
from .services.registry import Services, set_services
from .services.security import register_csrf, register_security_headers
from .services.storage import FileStorageService
from .services.sql_storage import SqlStorageService
from .services.tickets import TicketService
from .services.zillow import ZillowPublisher


def _is_development() -> bool:
    return os.getenv("FLASK_ENV", "production").lower() in ("development", "dev", "local")


# Opaque-token character set for a trace id: alphanumerics and the three
# separators load balancers actually use. Anything outside this set (tabs,
# spaces, quotes, angle brackets, control chars, unicode) would corrupt one
# of the places the id ends up: the ConsoleFormatter's ``[{request_id}]``
# tag would split on an embedded tab or space; the echoed
# ``X-Request-Id`` response header would carry attacker-controlled text
# back to any downstream log parser; and JSON log ingestion tools that
# treat the value as a search key would trip on delimiters.
_SAFE_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]+")


def _sanitize_request_id(raw: str) -> str:
    """Return ``raw`` if it is a plausible opaque trace id, else ``""``.

    The previous flow accepted any truthy header value up to 64 chars, so a
    client-supplied ``X-Request-Id: "   "`` (whitespace) or ``"a\tb"`` (tab)
    became the id — and then landed in the ConsoleFormatter's ``[<id>]`` log
    tag, splitting the log line or producing an empty-looking tag. Reject the
    whole header rather than partially stripping so a caller who set a bad
    value gets a fresh UUID and can see (via the echoed response header)
    that we didn't honor their id.
    """
    if not raw:
        return ""
    trimmed = raw.strip()[:64]
    if not trimmed or not _SAFE_REQUEST_ID_RE.fullmatch(trimmed):
        return ""
    return trimmed


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
    # When the operator declares a trusted proxy chain in front of the
    # app, hand X-Forwarded-* parsing to Werkzeug's ProxyFix so it can
    # strip exactly the configured number of hops and expose the original
    # client IP as ``request.remote_addr``. Anything downstream
    # (rate limiter, crash log) keys off ``remote_addr`` and therefore
    # gains a real client IP. When count is 0, leave the WSGI stack
    # untouched so a client-supplied X-Forwarded-For can NOT spoof the
    # rate-limiter identity (the prior implementation honored the header
    # unconditionally and was bypassable from any unauthenticated POST).
    if config.trusted_proxy_count > 0:
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=config.trusted_proxy_count,
            x_proto=config.trusted_proxy_count,
            x_host=config.trusted_proxy_count,
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

    analytics = AnalyticsTracker(config.analytics_days, config)
    # Storage backend is feature-flagged. Default (USE_SQLITE_STORAGE unset
    # or "0") keeps the JSON-file FileStorageService — same behavior the app
    # has shipped with. Setting USE_SQLITE_STORAGE=1 swaps in SqlStorageService
    # which mirrors the same public API and reads/writes a SQLite database at
    # config.sqlite_file. Migration is via scripts/migrate_from_json.py.
    if config.use_sqlite_storage:
        storage = SqlStorageService(config)
    else:
        storage = FileStorageService(config)
    notifications = NotificationService(config, analytics)
    appointments = AppointmentService(config)
    auth = AuthService(config, storage)
    zillow = ZillowPublisher(config, notifications)
    properties = PropertyService(config, notifications, zillow=zillow, storage=storage)
    jira = JiraClient(config, notifications)
    tickets = TicketService(config, storage, notifications, jira=jira)

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
            zillow=zillow,
            jira=jira,
        ),
    )

    # Assign a short id to every request FIRST, before any other
    # before_request handler runs, so each log line that request emits carries
    # the same request_id and can be traced end to end. An inbound
    # X-Request-Id (e.g. from a future load balancer) is honored when present.
    @app.before_request
    def _assign_request_id():
        incoming = _sanitize_request_id(request.headers.get("X-Request-Id", ""))
        g.request_id = incoming or uuid.uuid4().hex[:8]

    # The role is resolved once at login and cached in the session cookie, so
    # without this hook a promotion/demotion/revocation made in the admin UI
    # (or an .env change + restart) has no effect until the user logs out and
    # back in — a demoted admin keeps admin access for up to 8 hours.
    # Re-resolve on every request so role changes are effective immediately;
    # the session copy is rewritten so templates reading
    # session['user']['role'] agree with what the decorators enforce.
    @app.before_request
    def _refresh_session_role():
        # Skipped under TESTING: route tests inject session users with
        # arbitrary roles that exist in no role list, and re-resolving would
        # demote them all to guest. Mirrors the register-form timing check.
        if app.config.get("TESTING"):
            return
        user = session.get("user")
        if user and user.get("email"):
            role = auth.get_user_role(user["email"])
            if user.get("role") != role:
                user["role"] = role
                session["user"] = user

    # Surface a dead email pipeline at startup instead of only as a per-send
    # WARNING: with EMAIL_APP_PASSWORD unset every notification (registration,
    # tickets, crash alerts) is silently skipped.
    if not os.getenv("EMAIL_APP_PASSWORD"):
        notifications.console.warning(
            "EMAIL_APP_PASSWORD is not set — ALL outbound email (registrations, "
            "tickets, contact form, crash alerts) will be skipped until it is "
            "configured in .env and the app is restarted."
        )

    app.before_request(analytics.before_request)
    app.after_request(analytics.after_request)

    @app.after_request
    def _tag_response_request_id(response):
        request_id = getattr(g, "request_id", None)
        if request_id:
            response.headers.setdefault("X-Request-Id", request_id)
        return response

    register_csrf(app)
    register_security_headers(app)

    register_auth_routes(app)
    register_public_routes(app)
    register_admin_routes(app)
    register_pwa_routes(app)
    register_ticket_routes(app)
    register_webhook_routes(app)

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
            endpoint = request.endpoint or ""
            ua = request.headers.get("User-Agent", "")
            # ``remote_addr`` is the source of truth: it's either the direct
            # TCP peer or — when TRUSTED_PROXY_COUNT is set — the ProxyFix
            # normalized client IP. Reading X-Forwarded-For directly would
            # log a header an unauthenticated visitor controls.
            remote = request.remote_addr or ""
        except Exception:
            path, method, endpoint = "(unknown)", "(unknown)", ""
            ua, remote = "(unknown)", "(unknown)"

        # Fingerprint by Flask endpoint when available so /property/<uuid> et al.
        # collapse to a single suppression key instead of one per parameter
        # value — without this, a sustained crash on a dynamic route would
        # send one email per unique URL and defeat the 10-min cooldown.
        fingerprint = f"{type(exc).__name__}:{endpoint or path}"[:200]

        body = (
            "The website hit an unhandled error and served the bare fallback response.\n\n"
            f"Time:    {datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}Z\n"
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
