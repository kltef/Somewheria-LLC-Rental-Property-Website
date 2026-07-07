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

        merged = [
            {
                "email": email,
                "role": role,
                "source": "config" if email in env_emails and email not in file_roles else "file",
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
