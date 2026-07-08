from flask import redirect, render_template, request, session, url_for
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from ..services.console import get_console_logger
from ..services.auth import auth_status_payload, is_logged_in
from ..services.registry import get_services

logger = get_console_logger("auth")


def oauth_not_configured_response():
    return (
        render_template(
            "oauth_unavailable.html",
            title="Google Sign-In Unavailable",
        ),
        503,
    )


def _landing_redirect():
    """Where a logged-in user starts: admins land in the admin panel, not on
    a public page. The Dashboard is high_admin-only, so plain admins land on
    Users (any admin page renders the panel sidebar)."""
    role = (session.get("user") or {}).get("role", "")
    if role == "high_admin":
        return redirect(url_for("admin_dashboard_combined"))
    if role == "admin":
        return redirect(url_for("admin_users"))
    return redirect(url_for("manage_listings"))


def login():
    services = get_services()
    if is_logged_in():
        return _landing_redirect()
    if request.method == "POST":
        return _landing_redirect()
    return render_template(
        "login.html",
        title="Login",
        whitelist_configured=services.auth.whitelist_configured(),
    )


def google_login():
    services = get_services()
    config = services.config
    if not config.google_client_id or not config.google_client_secret:
        return oauth_not_configured_response()

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": config.google_client_id,
                "client_secret": config.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [config.google_redirect_uri],
            }
        },
        scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/userinfo.email",
        ],
    )
    flow.redirect_uri = config.google_redirect_uri
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="select_account",
    )
    session["oauth_state"] = state
    # google_auth_oauthlib >= 1.0 enables PKCE: authorization_url() above sent
    # a code_challenge, and the token exchange must present the matching
    # code_verifier. The callback builds a fresh Flow (this one dies with the
    # request), so the verifier has to ride along in the session like state.
    # getattr: older google_auth_oauthlib (and the tests' FlowMock) have no
    # code_verifier attribute — those flows don't do PKCE, so None is correct.
    session["oauth_code_verifier"] = getattr(flow, "code_verifier", None)
    return redirect(authorization_url)


def google_callback():
    services = get_services()
    config = services.config
    if not config.google_client_id or not config.google_client_secret:
        return oauth_not_configured_response()

    expected_state = session.pop("oauth_state", None)
    code_verifier = session.pop("oauth_code_verifier", None)
    returned_state = request.args.get("state", "")
    if not expected_state or expected_state != returned_state:
        logger.warning("OAuth state mismatch (expected=%r got=%r)", bool(expected_state), bool(returned_state))
        return render_template(
            "login.html",
            title="Login",
            error="Authentication failed: invalid state. Please try again.",
        ), 400

    try:
        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": config.google_client_id,
                    "client_secret": config.google_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [config.google_redirect_uri],
                }
            },
            scopes=[
                "openid",
                "https://www.googleapis.com/auth/userinfo.profile",
                "https://www.googleapis.com/auth/userinfo.email",
            ],
            # Reuse the PKCE verifier from the login leg; a fresh Flow would
            # otherwise autogenerate a new one and Google rejects the exchange
            # with (invalid_grant) Missing code verifier.
            code_verifier=code_verifier,
        )
        flow.redirect_uri = config.google_redirect_uri
        flow.fetch_token(authorization_response=request.url)

        credentials = flow.credentials
        id_info = id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            config.google_client_id,
        )
        user_email = id_info["email"].lower()
        role = services.auth.get_user_role(user_email)
        # Two ways in: a company-domain account, OR an account that has been
        # explicitly approved — a renter admitted through the registration
        # flow (their role is stored as "renter"), or anyone named in the
        # AUTHORIZED_USERS / ADMIN_USERS / HIGH_ADMIN_USERS env lists. This is
        # what lets an approved Gmail/Outlook applicant actually sign in; the
        # domain check alone used to turn them away before this point.
        domain_ok = user_email.endswith(("@ekbergproperties.com", "@somewheria.com"))
        approved = role != "guest" or user_email in config.authorized_users
        if not domain_ok and not approved:
            return (
                render_template(
                    "login.html",
                    title="Login",
                    error=(
                        "Access denied. Sign in with a Somewheria account, or request "
                        "an account and an admin will approve your email."
                    ),
                ),
                401,
            )
        if config.authorized_users and user_email not in config.authorized_users and role == "guest":
            services.notifications.log_and_notify_error(
                "Unauthorized Login Attempt",
                f"Unauthorized access attempt by: {user_email}",
            )
            return render_template(
                "login.html",
                title="Login",
                error="Access denied. Your email is not authorized to use this application.",
            )
        if role == "guest":
            services.notifications.log_and_notify_error(
                "Unauthorized Login Attempt",
                f"Unrecognized account denied: {user_email}",
            )
            return render_template(
                "login.html",
                title="Login",
                error="Access denied. Please request an account.",
            )
        # Rotate the session id to defend against session fixation.
        session.clear()
        user = services.auth.login_user(id_info)
        services.analytics.record_login(user["email"].lower())
        logger.info("Successful login for %s", user_email)
        return _landing_redirect()
    except Exception as exc:
        services.notifications.log_and_notify_error(
            "Google OAuth Error",
            f"Google OAuth callback error: {exc}",
        )
        return render_template("login.html", title="Login", error="Authentication failed. Please try again.")


def logout():
    session.pop("user", None)
    return redirect(url_for("home"))


def auth_status():
    return auth_status_payload()


def register_auth_routes(app) -> None:
    app.add_url_rule("/login", endpoint="login", view_func=login, methods=["GET", "POST"])
    app.add_url_rule("/google/login", endpoint="google_login", view_func=google_login)
    app.add_url_rule("/google/callback", endpoint="google_callback", view_func=google_callback)
    app.add_url_rule("/logout", endpoint="logout", view_func=logout)
    app.add_url_rule("/auth/status", endpoint="auth_status", view_func=auth_status)
