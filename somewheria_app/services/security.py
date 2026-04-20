import secrets
import time
from collections import defaultdict, deque
from functools import wraps
from threading import Lock

from flask import abort, g, jsonify, request, session


CSRF_SESSION_KEY = "_csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_FORM_FIELD = "_csrf_token"
CSRF_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
# Endpoints that are intentionally stateless and must not require CSRF
# (e.g. Google OAuth callback arrives via GET redirect from Google).
CSRF_EXEMPT_ENDPOINTS = {"google_callback", "static"}


def _get_or_create_csrf_token() -> str:
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_token() -> str:
    return _get_or_create_csrf_token()


def _extract_submitted_token() -> str:
    token = request.headers.get(CSRF_HEADER_NAME, "")
    if token:
        return token
    if request.form:
        token = request.form.get(CSRF_FORM_FIELD, "")
        if token:
            return token
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        if isinstance(payload, dict):
            token = payload.get(CSRF_FORM_FIELD, "") or ""
    return token or ""


def _csrf_before_request() -> None:
    # Ensure a token exists for any authenticated session so templates can render it.
    _get_or_create_csrf_token()
    if request.method in CSRF_SAFE_METHODS:
        return
    endpoint = request.endpoint or ""
    if endpoint in CSRF_EXEMPT_ENDPOINTS:
        return
    expected = session.get(CSRF_SESSION_KEY, "")
    submitted = _extract_submitted_token()
    if not expected or not submitted or not secrets.compare_digest(expected, submitted):
        abort(400, description="CSRF token missing or invalid.")


def register_csrf(app) -> None:
    app.before_request(_csrf_before_request)

    @app.context_processor
    def _inject_csrf_token():
        return {"csrf_token": csrf_token}


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}

# Content Security Policy. The existing templates use Tailwind via CDN and
# inline <script>/<style> blocks, so unsafe-inline is required; keep it tight
# elsewhere. img-src includes data: for base64 thumbnails used on the site.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://accounts.google.com https://apis.google.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.tailwindcss.com; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "img-src 'self' data: https:; "
    "connect-src 'self' https://accounts.google.com; "
    "frame-src https://accounts.google.com; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self' https://accounts.google.com"
)


def register_security_headers(app) -> None:
    @app.after_request
    def _apply_security_headers(response):
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
        return response


class _RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


_limiter = _RateLimiter()


def _client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "0.0.0.0"


def rate_limit(limit: int, window_seconds: int, *, methods: tuple[str, ...] = ("POST",)):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if request.method in methods:
                key = f"{request.endpoint}:{_client_ip()}"
                if not _limiter.check(key, limit, window_seconds):
                    if request.accept_mimetypes.best == "application/json" or request.is_json:
                        return (
                            jsonify({"error": "Too many requests. Please slow down."}),
                            429,
                        )
                    return (
                        "Too many requests. Please slow down and try again later.",
                        429,
                    )
            return view_func(*args, **kwargs)

        return wrapped

    return decorator
