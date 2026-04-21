"""Repair-ticket routes.

Three audiences:

* Public / logged-in users: submit a ticket, view their own list, view detail.
* Admins: see every ticket, change status/priority/assignment, add notes.

Routes register themselves via ``register_ticket_routes(app)`` which is wired
from ``somewheria_app/__init__.py``.
"""

from __future__ import annotations

from flask import redirect, render_template, request, url_for

from ..services.auth import (
    admin_required,
    get_current_user,
    is_logged_in,
    login_required,
)
from ..services.registry import get_services
from ..services.security import rate_limit
from ..services.tickets import (
    ALLOWED_CATEGORIES,
    ALLOWED_PRIORITIES,
    ALLOWED_STATUSES,
    OPEN_STATUSES,
)


def _actor_email() -> str:
    user = get_current_user() or {}
    return (user.get("email") or "").lower()


def _is_admin() -> bool:
    role = (get_current_user() or {}).get("role", "")
    return role in ("admin", "high_admin")


# ---------------------------------------------------------------- submit / list

def _renter_email_default(services, email: str) -> bool:
    """Look up the renter's 'email status updates' preference (default on)."""
    if not email:
        return False
    profile = services.storage.get_renter_profiles().get(email.lower()) or {}
    return bool(profile.get("email_status_updates", True))


def ticket_new_form():
    services = get_services()
    properties = services.properties.get_cached_properties() or []
    user = get_current_user() or {}
    prefill_property = (request.args.get("property_id") or "").strip()
    email_default = _renter_email_default(services, user.get("email", "")) if user.get("email") else False
    return render_template(
        "ticket_new.html",
        title="Submit a Repair Ticket",
        properties=properties,
        prefill_property=prefill_property,
        user=user,
        categories=ALLOWED_CATEGORIES,
        priorities=ALLOWED_PRIORITIES,
        email_default=email_default,
    )


@rate_limit(limit=5, window_seconds=600)
def ticket_new_submit():
    services = get_services()
    user = get_current_user() or {}
    submitter_email = (user.get("email") or "").lower()

    property_id = (request.form.get("property_id") or "").strip()
    property_name = ""
    if property_id:
        info = services.properties.get_property(property_id)
        if info:
            property_name = info.get("name", "")

    payload = {
        "title": request.form.get("title", ""),
        "description": request.form.get("description", ""),
        "category": request.form.get("category", "other"),
        "priority": request.form.get("priority", "normal"),
        "submitter_name": request.form.get("submitter_name") or user.get("name") or "",
        "contact": request.form.get("contact", ""),
        "property_id": property_id,
        "property_name": property_name,
        "email_updates": bool(request.form.get("email_updates")),
    }

    try:
        ticket = services.tickets.create_ticket(payload, submitter_email)
    except ValueError as exc:
        properties = services.properties.get_cached_properties() or []
        return render_template(
            "ticket_new.html",
            title="Submit a Repair Ticket",
            properties=properties,
            prefill_property=property_id,
            user=user,
            categories=ALLOWED_CATEGORIES,
            priorities=ALLOWED_PRIORITIES,
            email_default=_renter_email_default(services, submitter_email),
            error=str(exc),
            form=payload,
        ), 400

    if is_logged_in():
        return redirect(url_for("ticket_detail", ticket_id=ticket["id"]))
    return render_template(
        "ticket_new.html",
        title="Ticket Submitted",
        properties=[],
        prefill_property="",
        user=user,
        categories=ALLOWED_CATEGORIES,
        priorities=ALLOWED_PRIORITIES,
        confirmation=ticket,
    )


@login_required
def ticket_list_mine():
    services = get_services()
    email = _actor_email()
    tickets = services.tickets.list_tickets(submitter=email)
    return render_template(
        "my_tickets.html",
        title="My Tickets",
        tickets=tickets,
        open_statuses=OPEN_STATUSES,
    )


@login_required
def ticket_detail(ticket_id: str):
    services = get_services()
    ticket = services.tickets.get_ticket(ticket_id)
    if not ticket:
        return render_template("404.html", title="Ticket Not Found"), 404

    # Renters can only see their own tickets; admins can see anything.
    if not _is_admin() and (ticket.get("submitted_by") or "") != _actor_email():
        return render_template("403.html", title="Forbidden"), 403

    return render_template(
        "ticket_detail.html",
        title=f"Ticket: {ticket.get('title', '')}",
        ticket=ticket,
        is_admin=_is_admin(),
        statuses=ALLOWED_STATUSES,
        priorities=ALLOWED_PRIORITIES,
    )


@login_required
def ticket_toggle_email(ticket_id: str):
    services = get_services()
    ticket = services.tickets.get_ticket(ticket_id)
    if not ticket:
        return render_template("404.html", title="Ticket Not Found"), 404
    if not _is_admin() and (ticket.get("submitted_by") or "") != _actor_email():
        return render_template("403.html", title="Forbidden"), 403
    enabled = bool(request.form.get("email_updates"))
    services.tickets.set_email_updates(ticket_id, enabled, _actor_email())
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@login_required
@rate_limit(limit=20, window_seconds=300)
def ticket_add_note(ticket_id: str):
    services = get_services()
    ticket = services.tickets.get_ticket(ticket_id)
    if not ticket:
        return render_template("404.html", title="Ticket Not Found"), 404
    if not _is_admin() and (ticket.get("submitted_by") or "") != _actor_email():
        return render_template("403.html", title="Forbidden"), 403

    text = request.form.get("note", "")
    try:
        services.tickets.add_note(ticket_id, text, _actor_email())
    except ValueError:
        pass  # silently drop empty notes; user stays on the page
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


# ----------------------------------------------------------------------- admin

@admin_required
def admin_ticket_list():
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

    summary = services.tickets.summary()
    return render_template(
        "admin_tickets.html",
        title="Repair Tickets",
        tickets=tickets,
        summary=summary,
        statuses=ALLOWED_STATUSES,
        priorities=ALLOWED_PRIORITIES,
        filter_status=status_filter,
        filter_priority=priority_filter,
        search=search,
    )


@admin_required
def admin_ticket_update(ticket_id: str):
    services = get_services()
    updates = {
        "status": request.form.get("status"),
        "priority": request.form.get("priority"),
        "assigned_to": request.form.get("assigned_to"),
    }
    # Trim unset keys so the service only considers fields the form submitted.
    updates = {k: v for k, v in updates.items() if v is not None}
    services.tickets.update_ticket(ticket_id, updates, _actor_email())
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


# ------------------------------------------------------------- registration

def register_ticket_routes(app) -> None:
    app.add_url_rule("/tickets/new", endpoint="ticket_new_form", view_func=ticket_new_form, methods=["GET"])
    app.add_url_rule("/tickets/new", endpoint="ticket_new_submit", view_func=ticket_new_submit, methods=["POST"])
    app.add_url_rule("/tickets", endpoint="ticket_list_mine", view_func=ticket_list_mine, methods=["GET"])
    app.add_url_rule(
        "/tickets/<ticket_id>",
        endpoint="ticket_detail",
        view_func=ticket_detail,
        methods=["GET"],
    )
    app.add_url_rule(
        "/tickets/<ticket_id>/notes",
        endpoint="ticket_add_note",
        view_func=ticket_add_note,
        methods=["POST"],
    )
    app.add_url_rule(
        "/tickets/<ticket_id>/email-updates",
        endpoint="ticket_toggle_email",
        view_func=ticket_toggle_email,
        methods=["POST"],
    )
    app.add_url_rule(
        "/admin/tickets",
        endpoint="admin_ticket_list",
        view_func=admin_ticket_list,
        methods=["GET"],
    )
    app.add_url_rule(
        "/admin/tickets/<ticket_id>",
        endpoint="admin_ticket_update",
        view_func=admin_ticket_update,
        methods=["POST"],
    )
