import datetime
import secrets
import uuid as uuid_lib

from flask import abort, current_app, jsonify, redirect, render_template, request, send_file, url_for

from ..services.auth import (
    admin_required,
    get_current_user,
    high_admin_required,
    is_logged_in,
    renter_required,
    role_rank,
)
from ..services.properties import BLANK_PROPERTY, UploadValidationError
from ..services.registry import get_services
from ..services.security import rate_limit


ALLOWED_ROLES = ("renter", "admin", "high_admin")

# Contract PDF upload constraints. PDFs are validated by sniffing the magic
# bytes (%PDF-) and capping size at 16 MB — same ceiling as image uploads.
MAX_CONTRACT_PDF_BYTES = 16 * 1024 * 1024
PDF_MAGIC = b"%PDF-"


def _current_role() -> str:
    return (get_current_user() or {}).get("role", "guest")


def _can_act_on(actor_role: str, target_role: str) -> bool:
    # Admins may only act on users whose rank is strictly lower than their own,
    # and may only assign roles strictly below their own rank. Only high_admin
    # may promote to admin or high_admin.
    return role_rank(actor_role) > role_rank(target_role)


@admin_required
def add_listing():
    return render_template(
        "edit_listing.html",
        property_id="new",
        property=BLANK_PROPERTY,
        user=get_current_user(),
    )


@admin_required
def edit_listing(property_id):
    services = get_services()
    property_data = services.properties.get_property(property_id)
    if not property_data:
        return "Property not found", 404
    return render_template(
        "edit_listing.html",
        property_id=property_id,
        property=property_data,
        user=get_current_user(),
    )


@admin_required
def save_edit(id):
    services = get_services()
    actor_email = (get_current_user() or {}).get("email", "anonymous") if is_logged_in() else "anonymous"
    try:
        if id == "new":
            services.properties.create_property(request.form, actor_email)
            try:
                services.notifications.log_site_change(actor_email, "property_created", {"id_or_new": id})
            except Exception:
                pass
            return redirect(url_for("manage_listings"))
        services.properties.update_property(id, request.form, actor_email)
        try:
            services.notifications.log_site_change(actor_email, "property_updated", {"id": id})
        except Exception:
            pass
        return redirect(url_for("manage_listings"))
    except KeyError:
        return "Property not found", 404
    except Exception as exc:
        services.notifications.log_and_notify_error("Save Edit Error", f"Error saving edits for {id}: {exc}")
        return "Failed to save changes. Please try again.", 500


@admin_required
def upload_image(uuid):
    services = get_services()
    if "file" not in request.files:
        message = "No file part"
        services.notifications.log_and_notify_error("Upload Error", message)
        return jsonify(success=False, message=message), 400
    uploaded_file = request.files["file"]
    if uploaded_file.filename == "":
        message = "No selected file"
        services.notifications.log_and_notify_error("Upload Error", message)
        return jsonify(success=False, message=message), 400
    actor_email = (get_current_user() or {}).get("email", "anonymous")
    try:
        relative_url = services.properties.upload_image(
            uuid, uploaded_file, request.url_root, actor_email
        )
    except UploadValidationError as exc:
        return jsonify(success=False, message=str(exc)), 400
    except Exception as exc:
        services.notifications.log_and_notify_error(
            "Upload Error", f"Unexpected upload failure for {uuid}: {exc}"
        )
        return jsonify(success=False, message="Upload failed."), 500
    try:
        services.notifications.log_site_change(actor_email, "property_image_uploaded", {"id": uuid, "url": relative_url})
    except Exception:
        pass
    return jsonify(success=True, new_image_url=relative_url)


@admin_required
def image_edit_notify():
    services = get_services()
    # Do NOT accept client-supplied URLs — they could be exfiltration targets
    # or used to amplify spam. Just notify that an edit occurred.
    try:
        services.notifications.notify_image_edit(
            ["(See admin console for details.)"]
        )
        return jsonify(message="Notification sent."), 200
    except Exception as exc:
        services.notifications.log_and_notify_error(
            "Image Edit Notification Error",
            f"Failed to notify image edit: {exc}",
        )
        return jsonify(message="Failed to send notification."), 500


def _classify_contract_status(contract: dict) -> str:
    """Normalize a contract's status into one of: active / pending / ended.

    Falls back to the explicit ``status`` field, then infers from dates if the
    field is unset or unrecognized. The classification is used purely for the
    dashboard summary — admins can still set any free-form status string.
    """
    raw = (contract.get("status") or "").strip().lower()
    if raw in {"active", "current"}:
        return "active"
    if raw in {"pending", "upcoming", "draft"}:
        return "pending"
    if raw in {"ended", "expired", "terminated", "closed"}:
        return "ended"
    # Infer from end_date if status is unrecognized.
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


def _backfill_contract_ids(services, contracts_for_email: list[dict], email: str) -> list[dict]:
    """Old contracts written before PDFs were a thing have no ``id``. Stamp
    one in (and persist the back-fill) so renter links can target them."""
    if not contracts_for_email:
        return contracts_for_email
    needs_save = False
    for contract in contracts_for_email:
        if not contract.get("id"):
            contract["id"] = uuid_lib.uuid4().hex
            needs_save = True
        contract.setdefault("pdf_filename", "")
        # Annotate a normalized status for templates without mutating the
        # canonical free-form ``status`` field admins set in the form.
        contract["status_class"] = _classify_contract_status(contract)
    if needs_save:
        try:
            all_contracts = services.storage.get_renter_contracts()
            all_contracts[email] = contracts_for_email
            services.storage.save_renter_contracts(all_contracts)
        except Exception:
            pass
    return contracts_for_email


@renter_required
def renter_dashboard():
    services = get_services()
    user = get_current_user()
    email = user["email"].lower()
    contracts = _backfill_contract_ids(
        services, services.storage.get_renter_contracts().get(email, []), email
    )
    my_tickets = services.tickets.list_tickets(submitter=email)
    open_ticket_count = sum(
        1 for t in my_tickets if t.get("status") in {"open", "in_progress", "awaiting_parts"}
    )
    recent_tickets = my_tickets[:3]
    return render_template(
        "renter_dashboard.html",
        user=user,
        contracts=contracts,
        recent_tickets=recent_tickets,
        open_ticket_count=open_ticket_count,
        total_ticket_count=len(my_tickets),
        title="Renter Dashboard",
    )


@high_admin_required
def analytics_dashboard():
    services = get_services()
    metrics, chart_data = services.analytics.dashboard_data(len(services.properties.get_cached_properties()))
    return render_template(
        "analytics_dashboard.html",
        user=get_current_user(),
        metrics=metrics,
        chart_data=chart_data,
        title="Site Analytics",
    )


@high_admin_required
def admin_status():
    services = get_services()
    config = services.config
    property_count = len(services.properties.get_cached_properties())
    pending_registrations = services.storage.get_pending_registrations()
    user_roles = services.storage.get_user_roles()
    registered_routes = set(current_app.view_functions.keys())

    def route_ready(*endpoints):
        return all(endpoint in registered_routes for endpoint in endpoints)

    metrics = {
        "properties_cached": property_count,
        "pending_registrations": len(pending_registrations),
        "known_users": len(user_roles),
        "cache_refresh_interval": f"{config.cache_refresh_interval}s",
    }

    service_status = [
        {
            "label": "Property API Base",
            "detail": "Configured" if config.api_base_url else "Missing",
            "ok": bool(config.api_base_url),
        },
        {
            "label": "Google OAuth",
            "detail": "Configured" if config.google_client_id and config.google_client_secret else "Client credentials missing",
            "ok": bool(config.google_client_id and config.google_client_secret),
        },
        {
            "label": "Email Notifications",
            "detail": "Ready" if services.notifications._email_password() else "EMAIL_APP_PASSWORD is not configured",
            "ok": bool(services.notifications._email_password()),
        },
        {
            "label": "Background Refresh",
            "detail": "Enabled" if not config.disable_background_threads else "Disabled for this process",
            "ok": not config.disable_background_threads,
        },
    ]

    # Do not disclose absolute paths to the UI; report only presence/absence.
    file_status = [
        {
            "label": "Application Log",
            "detail": "Present" if config.log_file.exists() else "Missing",
            "ok": config.log_file.exists(),
        },
        {
            "label": "Change Log",
            "detail": "Present" if config.change_log_file.exists() else "Missing",
            "ok": config.change_log_file.exists(),
        },
        {
            "label": "Appointments File",
            "detail": "Present" if config.property_appointments_file.exists() else "Missing",
            "ok": config.property_appointments_file.exists(),
        },
        {
            "label": "User Roles File",
            "detail": "Present" if config.user_roles_file.exists() else "Missing",
            "ok": config.user_roles_file.exists(),
        },
    ]

    website_status = [
        {
            "label": "Public Pages",
            "detail": "Home, For Rent, About, and Contact routes are registered",
            "ok": route_ready("home", "for_rent", "about", "contact"),
        },
        {
            "label": "Authentication",
            "detail": "Login route is active"
            if route_ready("login")
            else "Login route is missing",
            "ok": route_ready("login"),
        },
        {
            "label": "Google Sign-In",
            "detail": "OAuth credentials are configured"
            if config.google_client_id and config.google_client_secret
            else "OAuth credentials are missing",
            "ok": route_ready("google_login", "google_callback")
            and bool(config.google_client_id and config.google_client_secret),
        },
        {
            "label": "Property Listings",
            "detail": f"For Rent pages are online with {property_count} cached properties",
            "ok": route_ready("for_rent", "for_rent_json", "property_details"),
        },
        {
            "label": "Appointment Requests",
            "detail": "Scheduling endpoint is available"
            if route_ready("schedule_appointment")
            else "Scheduling endpoint is missing",
            "ok": route_ready("schedule_appointment"),
        },
        {
            "label": "Admin Tools",
            "detail": "Status, users, contracts, registrations, and tickets pages are registered",
            "ok": route_ready(
                "admin_status",
                "admin_users",
                "admin_contracts",
                "admin_registrations",
                "admin_ticket_list",
            ),
        },
        {
            "label": "PWA Support",
            "detail": "Manifest and service worker files are available",
            "ok": route_ready("manifest", "manifest_json", "service_worker")
            and config.static_dir.joinpath("manifest.webmanifest").exists()
            and config.base_dir.joinpath("service-worker.js").exists(),
        },
    ]

    return render_template(
        "admin_status.html",
        title="System Status",
        metrics=metrics,
        service_status=service_status,
        file_status=file_status,
        website_status=website_status,
        user=get_current_user(),
    )


@high_admin_required
def admin_dashboard_combined():
    services = get_services()
    error = None
    success = None
    actor_email = (get_current_user() or {}).get("email", "")
    actor_role = _current_role()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:254]
        action = request.form.get("action", "").strip()[:32]
        if not email:
            error = "No email provided."
        elif email == actor_email.lower():
            error = "You cannot modify your own account here."
        elif action == "delete":
            target_role = services.auth.get_user_role(email)
            if not _can_act_on(actor_role, target_role):
                error = "You cannot modify a user at or above your own role."
            elif services.storage.delete_user_role(email):
                success = f"User {email} removed."
                services.notifications.log_site_change(actor_email, "user_deleted", {"email": email})
            else:
                error = "User not found."
        elif action == "update":
            new_role = request.form.get("role", "").strip()
            target_role = services.auth.get_user_role(email)
            if new_role not in ALLOWED_ROLES:
                error = "Invalid role."
            elif not _can_act_on(actor_role, target_role) or not _can_act_on(actor_role, new_role):
                error = "You cannot assign a role at or above your own."
            else:
                services.storage.set_user_role(email, new_role)
                success = f"Role for {email} updated to {new_role}."
                services.notifications.log_site_change(
                    actor_email,
                    "user_role_updated",
                    {"email": email, "role": new_role},
                )
        elif action == "add":
            new_role = request.form.get("role", "renter").strip()
            user_roles = services.storage.get_user_roles()
            if email in user_roles and user_roles.get(email) != "revoked":
                error = "User already exists."
            elif new_role not in ALLOWED_ROLES:
                error = "Invalid role."
            elif not _can_act_on(actor_role, new_role):
                error = "You cannot assign a role at or above your own."
            else:
                services.storage.set_user_role(email, new_role)
                success = f"User {email} added as {new_role}."
                services.notifications.log_site_change(
                    actor_email,
                    "user_added",
                    {"email": email, "role": new_role},
                )
    metrics, chart_data = services.analytics.dashboard_data(len(services.properties.get_cached_properties()))
    ticket_summary = services.tickets.summary()
    return render_template(
        "admin_dashboard.html",
        user=get_current_user(),
        metrics=metrics,
        chart_data=chart_data,
        users=list(services.storage.get_user_roles().items()),
        ticket_summary=ticket_summary,
        error=error,
        success=success,
        title="Admin Dashboard",
    )


@rate_limit(limit=3, window_seconds=600)
def register():
    services = get_services()
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:120]
        email = request.form.get("email", "").strip().lower()[:254]
        reason = request.form.get("reason", "").strip()[:2000]
        if not name or not email or "@" not in email:
            return render_template("register.html", error="Name and a valid email are required.")
        existing = services.storage.get_pending_registrations()
        if any(item.get("email", "").lower() == email for item in existing):
            # Do not re-notify on duplicate to prevent SMTP abuse.
            return render_template("register.html", success=True)
        services.storage.add_pending_registration({"name": name, "email": email, "reason": reason})
        services.notifications.send_email(
            "New Registration Request",
            f"Name: {name}\nEmail: {email}\nReason: {reason}\nApprove at /admin/registrations",
        )
        return render_template("register.html", success=True)
    return render_template("register.html")


@admin_required
def admin_registrations():
    services = get_services()
    pending = services.storage.get_pending_registrations()
    if request.method == "POST":
        action = request.form.get("action")
        email = request.form.get("email", "").strip().lower()
        if not email:
            return render_template(
                "admin_registrations.html",
                pending=pending,
                title="Pending Registrations",
                error="No email provided.",
            )
        if action == "approve":
            services.storage.set_user_role(email, "renter")
            services.storage.remove_pending_registration(email)
            services.notifications.send_email(
                "Registration Approved",
                "Your registration for Somewheria has been approved. You can now log in.",
            )
        elif action == "reject":
            services.storage.remove_pending_registration(email)
            services.notifications.send_email(
                "Registration Rejected",
                "Your registration for Somewheria was not approved at this time.",
            )
        else:
            return render_template(
                "admin_registrations.html",
                pending=pending,
                title="Pending Registrations",
                error="Invalid action.",
            )
        pending = services.storage.get_pending_registrations()
    return render_template("admin_registrations.html", pending=pending, title="Pending Registrations")


@admin_required
def admin_users():
    services = get_services()
    error = None
    success = None
    users = list(services.storage.get_user_roles().items())
    actor_email = (get_current_user() or {}).get("email", "").lower()
    actor_role = _current_role()
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:254]
        new_role = request.form.get("role", "").strip()
        action = request.form.get("action", "").strip()[:32]
        target_role = services.auth.get_user_role(email) if email else "guest"
        if not email:
            error = "No email provided."
        elif email == actor_email:
            error = "You cannot modify your own account here."
        elif action == "delete":
            if not _can_act_on(actor_role, target_role):
                error = "You cannot modify a user at or above your own role."
            elif services.storage.delete_user_role(email):
                success = f"User {email} removed."
            else:
                error = "User not found."
        elif new_role in ALLOWED_ROLES:
            if not _can_act_on(actor_role, target_role) or not _can_act_on(actor_role, new_role):
                error = "You cannot assign a role at or above your own."
            else:
                services.storage.set_user_role(email, new_role)
                success = f"Role for {email} updated to {new_role}."
        else:
            error = "Invalid role."
        users = list(services.storage.get_user_roles().items())
    return render_template("admin_users.html", users=users, error=error, success=success, title="User Management")


@renter_required
def renter_profile():
    services = get_services()
    user = get_current_user()
    email = user["email"].lower()
    profiles = services.storage.get_renter_profiles()
    profile = profiles.get(
        email,
        {
            "name": user.get("name", ""),
            "contact": "",
            "email_status_updates": True,
            "rcs_status_updates": True,
        },
    )
    # Backfill fields for profiles created before the preference existed.
    profile.setdefault("email_status_updates", True)
    profile.setdefault("rcs_status_updates", True)
    success = None
    if request.method == "POST":
        profile["name"] = request.form.get("name", "").strip()[:120]
        profile["contact"] = request.form.get("contact", "").strip()[:200]
        profile["email_status_updates"] = bool(request.form.get("email_status_updates"))
        profile["rcs_status_updates"] = bool(request.form.get("rcs_status_updates"))
        profiles[email] = profile
        services.storage.save_renter_profiles(profiles)
        success = "Profile updated."
    return render_template("renter_profile.html", profile=profile, user=user, success=success, title="Edit Profile")


def _validate_contract_pdf(uploaded_file) -> bytes | None:
    """Read the upload, validate as a PDF (magic bytes + size). Returns bytes
    on success or None when no file was supplied. Raises ValueError on bad
    content."""
    if not uploaded_file or not getattr(uploaded_file, "filename", ""):
        return None
    raw = uploaded_file.stream.read(MAX_CONTRACT_PDF_BYTES + 1)
    if not raw:
        return None
    if len(raw) > MAX_CONTRACT_PDF_BYTES:
        raise ValueError("PDF exceeds maximum allowed size (16 MB).")
    if not raw.startswith(PDF_MAGIC):
        raise ValueError("Uploaded file is not a valid PDF.")
    # Be conservative: also ensure the declared filename ends with .pdf.
    if not uploaded_file.filename.lower().endswith(".pdf"):
        raise ValueError("Uploaded file must have a .pdf extension.")
    return raw


@admin_required
def admin_contracts():
    services = get_services()
    contracts_data = services.storage.get_renter_contracts()
    error = None
    success = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            renter_email = request.form.get("renter_email", "").strip().lower()[:254]
            property_name = request.form.get("property_name", "").strip()[:200]
            start_date = request.form.get("start_date", "").strip()[:32]
            end_date = request.form.get("end_date", "").strip()[:32]
            status = request.form.get("status", "Active").strip()[:32]
            if not all([renter_email, property_name, start_date, end_date]):
                error = "All fields are required."
            else:
                contract_id = uuid_lib.uuid4().hex
                pdf_filename = ""
                try:
                    pdf_bytes = _validate_contract_pdf(request.files.get("contract_pdf"))
                except ValueError as exc:
                    error = str(exc)
                    pdf_bytes = None
                if error is None:
                    if pdf_bytes:
                        pdf_filename = f"{contract_id}.pdf"
                        target = services.config.contract_upload_dir / pdf_filename
                        try:
                            ok = services.storage.save_binary_file(target, pdf_bytes)
                        except Exception:
                            ok = False
                        if not ok:
                            pdf_filename = ""
                            error = "Failed to save PDF; contract not added."
                if error is None:
                    contracts_data.setdefault(renter_email, []).append(
                        {
                            "id": contract_id,
                            "property_name": property_name,
                            "start_date": start_date,
                            "end_date": end_date,
                            "status": status,
                            "pdf_filename": pdf_filename,
                            "created_at": datetime.datetime.now().isoformat(),
                        }
                    )
                    services.storage.save_renter_contracts(contracts_data)
                    success = f"Contract added for {renter_email}."
        elif action == "delete":
            renter_email = request.form.get("renter_email", "").strip().lower()
            contract_index = request.form.get("contract_index")
            if renter_email and contract_index is not None:
                try:
                    contract_idx = int(contract_index)
                    if renter_email in contracts_data and 0 <= contract_idx < len(contracts_data[renter_email]):
                        removed = contracts_data[renter_email][contract_idx]
                        # Best-effort delete of the on-disk PDF.
                        pdf_filename = removed.get("pdf_filename") or ""
                        if pdf_filename:
                            try:
                                services.storage.delete_file(
                                    services.config.contract_upload_dir / pdf_filename
                                )
                            except Exception:
                                pass
                        del contracts_data[renter_email][contract_idx]
                        if not contracts_data[renter_email]:
                            del contracts_data[renter_email]
                        services.storage.save_renter_contracts(contracts_data)
                        success = f"Contract removed for {renter_email}."
                    else:
                        error = "Contract not found."
                except ValueError:
                    error = "Invalid contract index."
            else:
                error = "Missing required fields."
    return render_template(
        "admin_contracts.html",
        contracts=contracts_data,
        error=error,
        success=success,
        title="Contract Management",
    )


def _find_contract_for_email(services, email: str, contract_id: str):
    """Return (contract_dict, list_index) for a given (email, contract_id),
    or (None, None) if not found."""
    contracts = services.storage.get_renter_contracts().get(email, [])
    for idx, contract in enumerate(contracts):
        if contract.get("id") == contract_id:
            return contract, idx
    return None, None


@renter_required
def contract_detail(contract_id: str):
    services = get_services()
    user = get_current_user()
    email = user["email"].lower()
    is_admin = user.get("role") in ("admin", "high_admin")
    contract = None
    owner_email = email
    if is_admin:
        # Admins can view any contract — search across renters.
        for renter_email, items in services.storage.get_renter_contracts().items():
            for item in items:
                if item.get("id") == contract_id:
                    contract = item
                    owner_email = renter_email
                    break
            if contract:
                break
    else:
        # Make sure the renter's own contracts have IDs (backfill for old data).
        _backfill_contract_ids(
            services, services.storage.get_renter_contracts().get(email, []), email
        )
        contract, _ = _find_contract_for_email(services, email, contract_id)
    if not contract:
        return render_template("404.html", title="Contract Not Found"), 404
    contract.setdefault("status_class", _classify_contract_status(contract))
    return render_template(
        "contract_detail.html",
        contract=contract,
        owner_email=owner_email,
        is_admin=is_admin,
        title=f"Contract: {contract.get('property_name', '')}",
        user=user,
    )


@renter_required
def contract_download(contract_id: str):
    services = get_services()
    user = get_current_user()
    email = user["email"].lower()
    is_admin = user.get("role") in ("admin", "high_admin")
    contract = None
    if is_admin:
        for items in services.storage.get_renter_contracts().values():
            for item in items:
                if item.get("id") == contract_id:
                    contract = item
                    break
            if contract:
                break
    else:
        contract, _ = _find_contract_for_email(services, email, contract_id)
    if not contract:
        abort(404)
    pdf_filename = contract.get("pdf_filename") or ""
    if not pdf_filename:
        abort(404)
    # Defense-in-depth: only the bare filename component is honoured. The
    # filename was generated server-side as ``<uuid>.pdf`` so it should never
    # contain separators, but be paranoid in case of hand-edited storage.
    if "/" in pdf_filename or "\\" in pdf_filename or ".." in pdf_filename:
        abort(404)
    pdf_path = (services.config.contract_upload_dir / pdf_filename).resolve()
    upload_root = services.config.contract_upload_dir.resolve()
    try:
        pdf_path.relative_to(upload_root)
    except ValueError:
        abort(404)
    if not pdf_path.exists():
        abort(404)
    try:
        return send_file(
            str(pdf_path),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"contract-{contract_id[:8]}.pdf",
        )
    except Exception:
        abort(404)


@admin_required
def delete_listing(id):
    services = get_services()
    actor_email = (get_current_user() or {}).get("email", "anonymous") if is_logged_in() else "anonymous"
    try:
        services.properties.delete_property(id, actor_email)
        try:
            services.notifications.log_site_change(actor_email, "property_deleted", {"id": id})
        except Exception:
            pass
        return redirect(url_for("manage_listings"))
    except Exception as exc:
        services.notifications.log_and_notify_error(
            "Property Delete Error",
            f"Failed to delete property {id} via API: {exc}",
        )
        return "Operation failed. Please try again.", 500


@admin_required
def toggle_sale(id):
    services = get_services()
    actor_email = (get_current_user() or {}).get("email", "anonymous") if is_logged_in() else "anonymous"
    try:
        services.properties.toggle_sale(id, actor_email)
        try:
            services.notifications.log_site_change(actor_email, "property_for_sale_toggled", {"id": id})
        except Exception:
            pass
        return redirect(url_for("manage_listings"))
    except KeyError:
        return "Property not found", 404
    except Exception as exc:
        services.notifications.log_and_notify_error(
            "Toggle Sale Error",
            f"Failed to toggle for_sale for {id}: {exc}",
        )
        return "Operation failed. Please try again.", 500


def register_admin_routes(app) -> None:
    app.add_url_rule("/add-listing", endpoint="add_listing", view_func=add_listing)
    app.add_url_rule("/edit-listing/<property_id>", endpoint="edit_listing", view_func=edit_listing)
    app.add_url_rule("/save-edit/<id>", endpoint="save_edit", view_func=save_edit, methods=["POST"])
    app.add_url_rule("/upload-image/<uuid>", endpoint="upload_image", view_func=upload_image, methods=["POST"])
    app.add_url_rule(
        "/image-edit-notify",
        endpoint="image_edit_notify",
        view_func=image_edit_notify,
        methods=["POST"],
    )
    app.add_url_rule("/renter-dashboard", endpoint="renter_dashboard", view_func=renter_dashboard)
    app.add_url_rule("/admin/analytics", endpoint="analytics_dashboard", view_func=analytics_dashboard)
    app.add_url_rule("/admin/status", endpoint="admin_status", view_func=admin_status)
    app.add_url_rule(
        "/admin/dashboard",
        endpoint="admin_dashboard_combined",
        view_func=admin_dashboard_combined,
        methods=["GET", "POST"],
    )
    app.add_url_rule("/register", endpoint="register", view_func=register, methods=["GET", "POST"])
    app.add_url_rule(
        "/admin/registrations",
        endpoint="admin_registrations",
        view_func=admin_registrations,
        methods=["GET", "POST"],
    )
    app.add_url_rule("/admin/users", endpoint="admin_users", view_func=admin_users, methods=["GET", "POST"])
    app.add_url_rule(
        "/renter/profile",
        endpoint="renter_profile",
        view_func=renter_profile,
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/admin/contracts",
        endpoint="admin_contracts",
        view_func=admin_contracts,
        methods=["GET", "POST"],
    )
    app.add_url_rule(
        "/contracts/<contract_id>",
        endpoint="contract_detail",
        view_func=contract_detail,
        methods=["GET"],
    )
    app.add_url_rule(
        "/contracts/<contract_id>/download",
        endpoint="contract_download",
        view_func=contract_download,
        methods=["GET"],
    )
    app.add_url_rule(
        "/delete-listing/<id>",
        endpoint="delete_listing",
        view_func=delete_listing,
        methods=["POST"],
    )
    app.add_url_rule("/toggle-sale/<id>", endpoint="toggle_sale", view_func=toggle_sale, methods=["POST"])
