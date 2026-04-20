import datetime

from flask import current_app, jsonify, redirect, render_template, request, url_for

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
            return redirect(url_for("manage_listings"))
        services.properties.update_property(id, request.form, actor_email)
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


@renter_required
def renter_dashboard():
    services = get_services()
    user = get_current_user()
    email = user["email"].lower()
    contracts = services.storage.get_renter_contracts().get(email, [])
    return render_template("renter_dashboard.html", user=user, contracts=contracts, title="Renter Dashboard")


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
            "detail": "Status, users, contracts, and registrations pages are registered",
            "ok": route_ready("admin_status", "admin_users", "admin_contracts", "admin_registrations"),
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
    return render_template(
        "admin_dashboard.html",
        user=get_current_user(),
        metrics=metrics,
        chart_data=chart_data,
        users=list(services.storage.get_user_roles().items()),
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
    profile = profiles.get(email, {"name": user.get("name", ""), "contact": ""})
    success = None
    if request.method == "POST":
        profile["name"] = request.form.get("name", "").strip()[:120]
        profile["contact"] = request.form.get("contact", "").strip()[:200]
        profiles[email] = profile
        services.storage.save_renter_profiles(profiles)
        success = "Profile updated."
    return render_template("renter_profile.html", profile=profile, user=user, success=success, title="Edit Profile")


@admin_required
def admin_contracts():
    services = get_services()
    contracts_data = services.storage.get_renter_contracts()
    error = None
    success = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            renter_email = request.form.get("renter_email", "").strip().lower()
            property_name = request.form.get("property_name", "").strip()
            start_date = request.form.get("start_date", "").strip()
            end_date = request.form.get("end_date", "").strip()
            status = request.form.get("status", "Active").strip()
            if not all([renter_email, property_name, start_date, end_date]):
                error = "All fields are required."
            else:
                contracts_data.setdefault(renter_email, []).append(
                    {
                        "property_name": property_name,
                        "start_date": start_date,
                        "end_date": end_date,
                        "status": status,
                        "download_url": "#",
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


@admin_required
def delete_listing(id):
    services = get_services()
    actor_email = (get_current_user() or {}).get("email", "anonymous") if is_logged_in() else "anonymous"
    try:
        services.properties.delete_property(id, actor_email)
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
        "/delete-listing/<id>",
        endpoint="delete_listing",
        view_func=delete_listing,
        methods=["POST"],
    )
    app.add_url_rule("/toggle-sale/<id>", endpoint="toggle_sale", view_func=toggle_sale, methods=["POST"])
