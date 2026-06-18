"""JWT authentication helpers for the /api/v1/ mobile API layer.

All /api/v1/ routes are CSRF-exempt (see security.py) and rely on a short-lived
JWT in the Authorization header instead. The token payload mirrors the session
user dict so the rest of the auth machinery (role checks, etc.) can be reused.
"""

from __future__ import annotations

import datetime
from functools import wraps
from typing import TYPE_CHECKING

import jwt
from flask import abort, g, jsonify, request

if TYPE_CHECKING:
    from ..config import AppConfig


_ALGORITHM = "HS256"


def create_api_token(user: dict, config: "AppConfig") -> str:
    now = datetime.datetime.utcnow()
    payload = {
        "sub": user.get("id") or user.get("email", ""),
        "email": (user.get("email") or "").lower(),
        "name": user.get("name", ""),
        "role": user.get("role", "guest"),
        "iat": now,
        "exp": now + datetime.timedelta(hours=config.jwt_expiry_hours),
    }
    return jwt.encode(payload, config.jwt_secret_key, algorithm=_ALGORITHM)


def verify_api_token(token: str, config: "AppConfig") -> dict | None:
    try:
        payload = jwt.decode(token, config.jwt_secret_key, algorithms=[_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None


def _extract_bearer_token() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


def _get_api_user(config: "AppConfig") -> dict | None:
    token = _extract_bearer_token()
    if not token:
        return None
    return verify_api_token(token, config)


def _api_error(message: str, code: int):
    return jsonify({"error": message, "code": code}), code


def api_auth_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        from .registry import get_services
        config = get_services().config
        user = _get_api_user(config)
        if not user:
            return _api_error("Authentication required.", 401)
        g.api_user = user
        return view_func(*args, **kwargs)
    return wrapped


def api_renter_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        from .registry import get_services
        config = get_services().config
        user = _get_api_user(config)
        if not user:
            return _api_error("Authentication required.", 401)
        if user.get("role") not in ("renter", "admin", "high_admin"):
            return _api_error("Renter access required.", 403)
        g.api_user = user
        return view_func(*args, **kwargs)
    return wrapped


def api_admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        from .registry import get_services
        config = get_services().config
        user = _get_api_user(config)
        if not user:
            return _api_error("Authentication required.", 401)
        if user.get("role") not in ("admin", "high_admin"):
            return _api_error("Admin access required.", 403)
        g.api_user = user
        return view_func(*args, **kwargs)
    return wrapped


def api_high_admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        from .registry import get_services
        config = get_services().config
        user = _get_api_user(config)
        if not user:
            return _api_error("Authentication required.", 401)
        if user.get("role") != "high_admin":
            return _api_error("High admin access required.", 403)
        g.api_user = user
        return view_func(*args, **kwargs)
    return wrapped
