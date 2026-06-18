"""Mobile REST API — /api/v1/

All routes here:
- Are CSRF-exempt (security.py skips /api/v1/ paths)
- Use JWT in the Authorization: Bearer header for auth
- Return JSON exclusively
- Follow the register_*_routes(app) pattern

Auth flow: mobile app gets a Google ID token via @react-native-google-signin/
google-signin, POSTs it to /api/v1/auth/google, receives a signed JWT. That
JWT is sent with every subsequent request.
"""

from __future__ import annotations

import datetime

from flask import g, jsonify, request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from ..services.jwt_auth import (
    api_admin_required,
    api_auth_required,
    api_renter_required,
    create_api_token,
    verify_api_token,
)
from ..services.registry import get_services
from ..services.security import rate_limit
from ..services.tickets import (
    ALLOWED_CATEGORIES,
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    MAX_DESCRIPTION_LEN,
    MAX_NAME_LEN,
    MAX_NOTE_LEN,
    MAX_TITLE_LEN,
)

MAX_CONTACT_LEN = 200
MAX_DATE_LEN = 10
ALLOWED_CONTACT_METHODS = {"email", "phone", "text", "sms", "call"}
ALLOWED_DOMAINS = ("@ekbergproperties.com", "@somewheria.com")


def _ok(data: dict | list):
    return jsonify(data), 200


def _created(data: dict):
    return jsonify(data), 201


def _err(message: str, code: int = 400):
    return jsonify({"error": message}), code


def _actor_email() -> str:
    return (g.api_user.get("email") or "").lower()


# ------------------------------------------------------------------ auth


def api_auth_google():
    data = request.get_json(silent=True) or {}
    id_token_str = (data.get("id_token") or "").strip()
    if not id_token_str:
        return _err("id_token is required.", 400)

    services = get_services()
    config = services.config
    if not config.google_client_id:
        return _err("Google OAuth is not configured on this server.", 503)

    try:
        id_info = id_token.verify_oauth2_token(
            id_token_str,
            google_requests.Request(),
            config.google_client_id,
        )
    except Exception:
        return _err("Invalid or expired Google ID token.", 401)

    user_email = (id_info.get("email") or "").lower()
    if not any(user_email.endswith(domain) for domain in ALLOWED_DOMAINS):
        return _err("Only ekbergproperties.com or somewheria.com accounts are allowed.", 401)

    role = services.auth.get_user_role(user_email)
    if role == "guest":
        return _err("Access denied. Please request an account.", 401)

    user = {
        "id": id_info.get("sub", ""),
        "email": id_info.get("email", ""),
        "name": id_info.get("name", ""),
        "picture": id_info.get("picture", ""),
        "given_name": id_info.get("given_name", ""),
        "family_name": id_info.get("family_name", ""),
        "role": role,
    }
    token = create_api_token(user, config)
    services.analytics.record_login(user_email)
    return _ok({"token": token, "user": {"email": user["email"], "name": user["name"], "role": role}})


@api_auth_required
def api_auth_refresh():
    services = get_services()
    user = g.api_user
    token = create_api_token(user, services.config)
    return _ok({"token": token, "user": {"email": user["email"], "name": user["name"], "role": user["role"]}})


# ------------------------------------------------------------------ properties (public)


def api_properties_list():
    services = get_services()
    try:
        services.properties.refresh_cache()
    except Exception as exc:
        services.properties.logger.warning(
            "API /api/v1/properties refresh failed, serving cache: %s", exc
        )
    props = services.properties.get_cached_properties()
    return _ok({"properties": services.properties.serialize_properties(props)})


def api_property_detail(uuid: str):
    services = get_services()
    prop = services.properties.get_property(uuid)
    if not prop:
        return _err("Property not found.", 404)
    return _ok(services.properties.normalize_property(prop))


@rate_limit(limit=5, window_seconds=600)
def api_property_schedule(uuid: str):
    services = get_services()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:MAX_NAME_LEN]
    date = (data.get("date") or "").strip()[:MAX_DATE_LEN]
    contact_method = (data.get("contact_method") or "").strip().lower()[:32]
    contact_info = (data.get("contact_info") or "").strip()[:MAX_CONTACT_LEN]

    if not name or not contact_info:
        return _err("Name and contact info are required.")
    if contact_method and contact_method not in ALLOWED_CONTACT_METHODS:
        return _err("Invalid contact method.")
    try:
        requested_date = datetime.date.fromisoformat(date)
    except Exception:
        return _err("Invalid date.")
    if requested_date < datetime.date.today():
        return _err("Date cannot be in the past.")

    property_name = services.properties.fetch_live_property_name(uuid)
    if not property_name:
        return _err("Property not found.", 404)

    message = (
        "Appointment requested!\n\n"
        f"Property: {property_name}\n"
        f"Requested by: {name}\n"
        f"For date: {date}\n"
        f"Contact method: {contact_method}\n"
        f"Contact info: {contact_info}\n"
        f"Requested at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    services.notifications.send_email("Viewing Appointment Request", message)
    return _ok({"success": True})


# ------------------------------------------------------------------ renter profile


@api_renter_required
def api_profile_get():
    services = get_services()
    email = _actor_email()
    profiles = services.storage.get_renter_profiles()
    profile = profiles.get(email) or {}
    return _ok({
        "email": email,
        "name": profile.get("name", g.api_user.get("name", "")),
        "contact": profile.get("contact", ""),
        "email_status_updates": bool(profile.get("email_status_updates", True)),
        "rcs_status_updates": bool(profile.get("rcs_status_updates", True)),
    })


@api_renter_required
def api_profile_patch():
    services = get_services()
    email = _actor_email()
    data = request.get_json(silent=True) or {}

    profiles = services.storage.get_renter_profiles()
    profile = profiles.get(email) or {}

    if "name" in data:
        profile["name"] = (data["name"] or "").strip()[:MAX_NAME_LEN]
    if "contact" in data:
        profile["contact"] = (data["contact"] or "").strip()[:MAX_CONTACT_LEN]
    if "email_status_updates" in data:
        profile["email_status_updates"] = bool(data["email_status_updates"])
    if "rcs_status_updates" in data:
        profile["rcs_status_updates"] = bool(data["rcs_status_updates"])

    profiles[email] = profile
    services.storage.save_renter_profiles(profiles)
    return _ok({"success": True, "profile": profile})


# ------------------------------------------------------------------ contracts


@api_renter_required
def api_contracts_list():
    import uuid as uuid_lib
    services = get_services()
    email = _actor_email()
    all_contracts = services.storage.get_renter_contracts()
    contracts = all_contracts.get(email, [])

    # Backfill IDs and normalize status (mirrors admin_routes._backfill_contract_ids)
    needs_save = False
    for contract in contracts:
        if not contract.get("id"):
            contract["id"] = uuid_lib.uuid4().hex
            needs_save = True
        contract.setdefault("pdf_filename", "")
        contract["status_class"] = _classify_contract_status(contract)
    if needs_save:
        try:
            all_contracts[email] = contracts
            services.storage.save_renter_contracts(all_contracts)
        except Exception:
            pass

    # Provide a relative URL for PDF download — mobile can open in browser
    result = []
    for c in contracts:
        entry = dict(c)
        entry["pdf_url"] = f"/contracts/{c['id']}/download" if c.get("pdf_filename") else ""
        result.append(entry)

    return _ok({"contracts": result})


def _classify_contract_status(contract: dict) -> str:
    raw = (contract.get("status") or "").strip().lower()
    if raw in {"active", "current"}:
        return "active"
    if raw in {"pending", "upcoming", "draft"}:
        return "pending"
    if raw in {"ended", "expired", "terminated", "closed"}:
        return "ended"
    end_date = (contract.get("end_date") or "").strip()
    if end_date:
        try:
            end = datetime.date.fromisoformat(end_date[:10])
            if end < datetime.date.today():
                return "ended"
        except ValueError:
            pass
    start_date = (contract.get("start_date") or "").strip()
    if start_date:
        try:
            start = datetime.date.fromisoformat(start_date[:10])
            if start > datetime.date.today():
                return "pending"
        except ValueError:
            pass
    return "active"


# ------------------------------------------------------------------ tickets (renter)


@api_renter_required
def api_tickets_list():
    services = get_services()
    email = _actor_email()
    tickets = services.tickets.list_tickets(submitter=email)
    return _ok({"tickets": tickets})


@rate_limit(limit=5, window_seconds=600)
@api_renter_required
def api_ticket_create():
    services = get_services()
    email = _actor_email()
    data = request.get_json(silent=True) or {}

    property_id = (data.get("property_id") or "").strip()
    property_name = ""
    if property_id:
        info = services.properties.get_property(property_id)
        if info:
            property_name = info.get("name", "")

    payload = {
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "category": data.get("category", "other"),
        "priority": data.get("priority", "normal"),
        "submitter_name": (data.get("submitter_name") or g.api_user.get("name") or "").strip(),
        "contact": data.get("contact", ""),
        "property_id": property_id,
        "property_name": property_name,
        "email_updates": bool(data.get("email_updates", True)),
    }

    try:
        ticket = services.tickets.create_ticket(payload, email)
    except ValueError as exc:
        return _err(str(exc))

    return _created({"ticket": ticket})


@api_renter_required
def api_ticket_detail(ticket_id: str):
    services = get_services()
    email = _actor_email()
    role = g.api_user.get("role", "")
    ticket = services.tickets.get_ticket(ticket_id)
    if not ticket:
        return _err("Ticket not found.", 404)
    is_admin = role in ("admin", "high_admin")
    if not is_admin and (ticket.get("submitted_by") or "") != email:
        return _err("Forbidden.", 403)
    return _ok({"ticket": ticket})


@api_renter_required
def api_ticket_patch(ticket_id: str):
    services = get_services()
    email = _actor_email()
    ticket = services.tickets.get_ticket(ticket_id)
    if not ticket:
        return _err("Ticket not found.", 404)
    if (ticket.get("submitted_by") or "") != email:
        return _err("Forbidden.", 403)

    data = request.get_json(silent=True) or {}
    if "email_updates" in data:
        updated = services.tickets.set_email_updates(ticket_id, bool(data["email_updates"]), email)
        return _ok({"ticket": updated})
    return _ok({"ticket": ticket})


@rate_limit(limit=20, window_seconds=300)
@api_renter_required
def api_ticket_add_note(ticket_id: str):
    services = get_services()
    email = _actor_email()
    role = g.api_user.get("role", "")
    ticket = services.tickets.get_ticket(ticket_id)
    if not ticket:
        return _err("Ticket not found.", 404)
    is_admin = role in ("admin", "high_admin")
    if not is_admin and (ticket.get("submitted_by") or "") != email:
        return _err("Forbidden.", 403)

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return _err("Note text is required.")
    try:
        updated = services.tickets.add_note(ticket_id, text, email)
    except ValueError as exc:
        return _err(str(exc))
    return _ok({"ticket": updated})


# ------------------------------------------------------------------ push tokens


@api_auth_required
def api_push_token():
    services = get_services()
    email = _actor_email()
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return _err("token is required.")
    services.push_notifications.register_token(email, token)
    return _ok({"success": True})


# ------------------------------------------------------------------ admin


@api_admin_required
def api_admin_dashboard():
    services = get_services()
    ticket_summary = services.tickets.summary()
    ticket_status_counts = services.tickets.status_counts()
    property_count = len(services.properties.get_cached_properties())
    pending_reg_count = len(services.storage.get_pending_registrations())
    return _ok({
        "tickets": ticket_summary,
        "ticket_status_counts": ticket_status_counts,
        "property_count": property_count,
        "pending_registrations": pending_reg_count,
    })


@api_admin_required
def api_admin_tickets_list():
    services = get_services()
    status_filter = (request.args.get("status") or "").strip().lower()
    priority_filter = (request.args.get("priority") or "").strip().lower()
    search = (request.args.get("q") or "").strip().lower()

    statuses = [status_filter] if status_filter in ALLOWED_STATUSES else None
    tickets = services.tickets.list_tickets(statuses=statuses)

    if priority_filter in ALLOWED_PRIORITIES:
        tickets = [t for t in tickets if t.get("priority") == priority_filter]
    if search:
        tickets = [
            t for t in tickets
            if search in (t.get("title", "") or "").lower()
            or search in (t.get("description", "") or "").lower()
            or search in (t.get("submitted_by", "") or "").lower()
            or search in (t.get("property_name", "") or "").lower()
        ]

    return _ok({"tickets": tickets, "summary": services.tickets.summary()})


@api_admin_required
def api_admin_ticket_patch(ticket_id: str):
    services = get_services()
    data = request.get_json(silent=True) or {}
    updates = {k: data[k] for k in ("status", "priority", "assigned_to") if k in data}
    if not updates:
        return _err("No updatable fields provided.")
    ticket = services.tickets.update_ticket(ticket_id, updates, _actor_email())
    if not ticket:
        return _err("Ticket not found.", 404)
    return _ok({"ticket": ticket})


@api_admin_required
def api_admin_users():
    services = get_services()
    roles = services.storage.get_user_roles()
    users = [
        {"email": email, "role": role}
        for email, role in roles.items()
        if role != "revoked"
    ]
    return _ok({"users": users})


@api_admin_required
def api_admin_registrations_list():
    services = get_services()
    return _ok({"registrations": services.storage.get_pending_registrations()})


@api_admin_required
def api_admin_registration_action():
    services = get_services()
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    action = (data.get("action") or "").strip().lower()

    if not email or action not in ("approve", "reject"):
        return _err("email and action (approve/reject) are required.")

    registrations = services.storage.get_pending_registrations()
    target = next((r for r in registrations if (r.get("email") or "").lower() == email), None)
    if not target:
        return _err("Registration not found.", 404)

    services.storage.remove_pending_registration(email)
    if action == "approve":
        services.storage.set_user_role(email, "renter")
        try:
            services.notifications.send_email(
                "Registration Approved",
                f"Your registration has been approved. You can now log in.",
                to=email,
            )
        except Exception:
            pass

    return _ok({"success": True, "action": action, "email": email})


@api_admin_required
def api_admin_listings_list():
    services = get_services()
    try:
        services.properties.refresh_cache()
    except Exception as exc:
        services.properties.logger.warning(
            "Admin API listings refresh failed, serving cache: %s", exc
        )
    props = services.properties.get_cached_properties()
    return _ok({"properties": services.properties.serialize_properties(props)})


@api_admin_required
def api_admin_listing_toggle_sale(uuid: str):
    services = get_services()
    actor_email = _actor_email()
    try:
        services.properties.toggle_sale(uuid, actor_email)
    except KeyError:
        return _err("Property not found.", 404)
    except Exception as exc:
        return _err(f"Failed to update property: {exc}", 500)
    return _ok({"success": True})


@api_admin_required
def api_admin_listing_delete(uuid: str):
    services = get_services()
    actor_email = _actor_email()
    try:
        services.properties.delete_property(uuid, actor_email)
    except KeyError:
        return _err("Property not found.", 404)
    except Exception as exc:
        return _err(f"Failed to delete property: {exc}", 500)
    return _ok({"success": True})


# ------------------------------------------------------------------ registration


def register_api_routes(app) -> None:
    # Auth
    app.add_url_rule("/api/v1/auth/google", endpoint="api_auth_google", view_func=api_auth_google, methods=["POST"])
    app.add_url_rule("/api/v1/auth/refresh", endpoint="api_auth_refresh", view_func=api_auth_refresh, methods=["POST"])

    # Properties (public)
    app.add_url_rule("/api/v1/properties", endpoint="api_properties_list", view_func=api_properties_list, methods=["GET"])
    app.add_url_rule("/api/v1/properties/<uuid>", endpoint="api_property_detail", view_func=api_property_detail, methods=["GET"])
    app.add_url_rule("/api/v1/properties/<uuid>/schedule", endpoint="api_property_schedule", view_func=api_property_schedule, methods=["POST"])

    # Profile
    app.add_url_rule("/api/v1/profile", endpoint="api_profile_get", view_func=api_profile_get, methods=["GET"])
    app.add_url_rule("/api/v1/profile", endpoint="api_profile_patch", view_func=api_profile_patch, methods=["PATCH"])

    # Contracts
    app.add_url_rule("/api/v1/contracts", endpoint="api_contracts_list", view_func=api_contracts_list, methods=["GET"])

    # Tickets
    app.add_url_rule("/api/v1/tickets", endpoint="api_tickets_list", view_func=api_tickets_list, methods=["GET"])
    app.add_url_rule("/api/v1/tickets", endpoint="api_ticket_create", view_func=api_ticket_create, methods=["POST"])
    app.add_url_rule("/api/v1/tickets/<ticket_id>", endpoint="api_ticket_detail", view_func=api_ticket_detail, methods=["GET"])
    app.add_url_rule("/api/v1/tickets/<ticket_id>", endpoint="api_ticket_patch", view_func=api_ticket_patch, methods=["PATCH"])
    app.add_url_rule("/api/v1/tickets/<ticket_id>/notes", endpoint="api_ticket_add_note", view_func=api_ticket_add_note, methods=["POST"])

    # Push tokens
    app.add_url_rule("/api/v1/push-token", endpoint="api_push_token", view_func=api_push_token, methods=["POST"])

    # Admin
    app.add_url_rule("/api/v1/admin/dashboard", endpoint="api_admin_dashboard", view_func=api_admin_dashboard, methods=["GET"])
    app.add_url_rule("/api/v1/admin/tickets", endpoint="api_admin_tickets_list", view_func=api_admin_tickets_list, methods=["GET"])
    app.add_url_rule("/api/v1/admin/tickets/<ticket_id>", endpoint="api_admin_ticket_patch", view_func=api_admin_ticket_patch, methods=["PATCH"])
    app.add_url_rule("/api/v1/admin/users", endpoint="api_admin_users", view_func=api_admin_users, methods=["GET"])
    app.add_url_rule("/api/v1/admin/registrations", endpoint="api_admin_registrations_list", view_func=api_admin_registrations_list, methods=["GET"])
    app.add_url_rule("/api/v1/admin/registrations", endpoint="api_admin_registration_action", view_func=api_admin_registration_action, methods=["POST"])
    app.add_url_rule("/api/v1/admin/listings", endpoint="api_admin_listings_list", view_func=api_admin_listings_list, methods=["GET"])
    app.add_url_rule("/api/v1/admin/listings/<uuid>/toggle-sale", endpoint="api_admin_listing_toggle_sale", view_func=api_admin_listing_toggle_sale, methods=["POST"])
    app.add_url_rule("/api/v1/admin/listings/<uuid>", endpoint="api_admin_listing_delete", view_func=api_admin_listing_delete, methods=["DELETE"])
