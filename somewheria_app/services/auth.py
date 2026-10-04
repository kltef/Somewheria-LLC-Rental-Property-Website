from functools import wraps

from flask import abort, jsonify, redirect, session, url_for

from .registry import get_services


class AuthService:
    def __init__(self, config, storage) -> None:
        self.config = config
        self.storage = storage

    def is_logged_in(self) -> bool:
        return "user" in session

    def current_user(self):
        # ``login_user`` only ever writes a dict under ``session["user"]`` (see
        # below), so in normal operation this is just ``session.get("user")``.
        # A legacy session issued by a pre-dataclass build — or a hand-written
        # test harness that bypassed ``login_user`` — can still carry a non-dict
        # value under that key. ``session.get("user") or {}`` at the call sites
        # only coerces FALSY values ("", None, {}); a truthy non-dict (a bare
        # string, a number, a list) sails past that idiom and then raises
        # ``AttributeError: 'str' object has no attribute 'get'`` on the first
        # ``user.get(...)`` — soft-locking the visitor out via the crash
        # handler's empty 503 because the same session reads happen on every
        # subsequent request. Return ``None`` for anything that isn't a dict so
        # the standard ``get_current_user() or {}`` idiom resolves cleanly to an
        # empty dict. Mirrors the isinstance guards PRs #173 / #179 added for
        # non-string ``session["user"]["email"]`` values.
        user = session.get("user")
        return user if isinstance(user, dict) else None

    def whitelist_configured(self) -> bool:
        return bool(self.config.authorized_users)

    def get_user_role(self, email: str) -> str:
        # ``_refresh_session_role`` in ``create_app`` calls this on every
        # request with whatever ``session["user"]["email"]`` happens to hold.
        # In current code the OAuth callback validates ``email`` is a string
        # before ``login_user`` writes it, but a legacy session issued by a
        # pre-validation build (or a hand-written test harness that skipped
        # the OAuth path) can still carry a non-string value under that key.
        # ``email.lower()`` on an int / bool / None then AttributeErrors,
        # the crash handler serves an empty 503, and — because the same
        # before_request hook runs on every subsequent request — the visitor
        # is soft-locked out of the site until they clear cookies. Return
        # "guest" for anything that isn't a string, matching the "unknown
        # address" fallback below. Mirrors the isinstance guards PRs
        # #144-#171 added elsewhere for corrupted / hand-edited state.
        if not isinstance(email, str):
            return "guest"
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

    def all_user_roles(self) -> list[dict]:
        """Every account with a role, from .env AND user_roles.json.

        The user-management page used to read only ``user_roles.json``, so on
        a fresh deploy — where the admins are configured entirely through the
        ADMIN_USERS / HIGH_ADMIN_USERS env vars and no role has been changed
        in the UI yet — the page showed nothing. This merges both sources
        using the same precedence as :meth:`get_user_role`: a file entry wins
        over the env default, and a ``revoked`` tombstone hides the account.

        Each item is ``{"email", "role", "source"}`` where source is
        ``"config"`` (from .env) or ``"file"`` (assigned in the UI).
        """
        roles: dict[str, str] = {}
        # Env defaults first (lowest precedence), strongest role last so it
        # overwrites a weaker one for the same address.
        for email in self.config.authorized_users:
            roles[email.lower()] = "renter"
        for email in self.config.admin_users:
            roles[email.lower()] = "admin"
        for email in self.config.high_admin_users:
            roles[email.lower()] = "high_admin"
        env_emails = set(roles)

        file_roles = self.storage.get_user_roles()
        for email, role in file_roles.items():
            email = email.lower()
            if role == "revoked":
                roles.pop(email, None)  # deleted: hide even if env lists them
            else:
                roles[email] = role

        # The ``source`` tag below asks "does this account have a file entry?".
        # ``set_user_role`` lowercases on write, but a legacy / hand-edited
        # ``user_roles.json`` (or a direct SQL insert) can still carry a
        # mixed-case key like ``Admin@Example.com``. Comparing the lowered
        # email against ``file_roles`` directly then misses that entry and
        # mis-labels the row as "config" even though the file override is what
        # produced the role above. Compare against a lowered key set instead.
        file_role_emails = {email.lower() for email in file_roles}
        merged = [
            {
                "email": email,
                "role": role,
                "source": "config" if email in env_emails and email not in file_role_emails else "file",
            }
            for email, role in roles.items()
        ]
        merged.sort(key=lambda item: item["email"])
        return merged

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


def current_user_email() -> str:
    """Return the active session's email, lowercased, or ``""`` when
    missing / not a string.

    ``login_user`` lowercases on write and the OAuth callback validates
    ``raw_email`` is a non-empty string before writing it (PR #170 et al),
    but a legacy session issued by a pre-validation build — or a
    hand-written test harness that skipped the OAuth path — can still carry
    a non-string value under ``session["user"]["email"]``. Call sites that
    reach for ``.lower()`` directly on that raw value then AttributeError
    on an int / bool / None / dict and the crash handler serves an empty
    503, which — because the same page is often the user's landing page —
    soft-locks them out until they clear cookies. Returning ``""`` for
    anything that isn't a string mirrors the "unknown address" fallback
    :meth:`AuthService.get_user_role` already applies (PR #173) and lets
    the caller use the empty string as a never-matching lookup key.
    """
    user = get_current_user() or {}
    email = user.get("email")
    if not isinstance(email, str):
        return ""
    return email.strip().lower()


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
