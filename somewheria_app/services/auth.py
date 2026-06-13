import re
from functools import wraps

from flask import abort, jsonify, redirect, session, url_for

from .registry import get_services


# Pragmatic email pattern for boundary input validation. Intentionally tighter
# than the bare ``"@" in value`` check the public routes used to do — that let
# obvious junk like ``@``, ``a@``, ``@b``, and ``a@b`` through, which then
# stored garbage rows in pending_registrations / lead_captures and triggered an
# admin notification email per submission. The pattern requires a non-empty
# local part, a domain with at least one dot, and a TLD of two-plus letters;
# it is deliberately not a full RFC 5322 implementation (those are huge and
# still wrong) — boundary sanity, not strict conformance.
# ``fullmatch`` (no ^/$ anchors) is deliberate: with ``re.match`` and a
# trailing ``$``, Python's default-mode regex would still match an input
# with a trailing newline ("foo@bar.com\n"), defeating the validator for
# clients that POST raw form bodies without normalizing whitespace.
_EMAIL_PATTERN = re.compile(r"[A-Za-z0-9_.%+\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}")
# RFC 5321 caps the full address at 254 octets; reject anything longer at the
# boundary before regex-matching to keep pathological inputs cheap.
_EMAIL_MAX_LEN = 254


def is_valid_email(value) -> bool:
    """Return True when ``value`` looks like a real email address.

    Used at public input boundaries (registration, lead capture) where the
    consequence of accepting garbage is a stored pending row plus an admin
    notification email — both of which the dedup guards downstream can't
    fully neutralize for a hostile client cycling unique strings.
    """
    if not isinstance(value, str):
        return False
    if not value or len(value) > _EMAIL_MAX_LEN:
        return False
    return _EMAIL_PATTERN.fullmatch(value) is not None


class AuthService:
    def __init__(self, config, storage) -> None:
        self.config = config
        self.storage = storage

    def is_logged_in(self) -> bool:
        return "user" in session

    def current_user(self):
        return session.get("user")

    def whitelist_configured(self) -> bool:
        return bool(self.config.authorized_users)

    def get_user_role(self, email: str) -> str:
        email = email.lower()
        user_roles = self.storage.get_user_roles()
        if email in user_roles:
            role = user_roles[email]
            # A role of "revoked" is an explicit tombstone recorded when an
            # admin deletes the user. It prevents env-var fallbacks below from
            # silently restoring their access on the next login.
            if role == "revoked":
                return "guest"
            return role
        if email in self.config.high_admin_users:
            return "high_admin"
        if email in self.config.admin_users:
            return "admin"
        if email in self.config.authorized_users:
            return "renter"
        return "guest"

    def login_user(self, id_info: dict) -> dict:
        user_email = id_info["email"].lower()
        user = {
            "id": id_info["sub"],
            "email": id_info["email"],
            "name": id_info.get("name", ""),
            "picture": id_info.get("picture", ""),
            "given_name": id_info.get("given_name", ""),
            "family_name": id_info.get("family_name", ""),
            "role": self.get_user_role(user_email),
        }
        session["user"] = user
        return user


def is_logged_in() -> bool:
    return get_services().auth.is_logged_in()


def get_current_user():
    return get_services().auth.current_user()


def login_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapped


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        user = get_current_user() or {}
        if user.get("role") not in ("admin", "high_admin"):
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


def renter_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        user = get_current_user() or {}
        if user.get("role") not in ("renter", "admin", "high_admin"):
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


def high_admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login"))
        user = get_current_user() or {}
        if user.get("role") != "high_admin":
            abort(403)
        return view_func(*args, **kwargs)

    return wrapped


def auth_status_payload():
    if is_logged_in():
        user = get_current_user() or {}
        return jsonify(
            {
                "authenticated": True,
                "user": {
                    "email": user.get("email", ""),
                    "name": user.get("name", ""),
                    "role": user.get("role", "guest"),
                },
            }
        )
    return jsonify({"authenticated": False, "user": None})


ROLE_RANK = {"guest": 0, "renter": 1, "admin": 2, "high_admin": 3}


def role_rank(role: str) -> int:
    return ROLE_RANK.get((role or "guest").lower(), 0)
