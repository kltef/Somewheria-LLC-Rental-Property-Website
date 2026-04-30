import datetime

from flask import jsonify, render_template, request

from ..services.auth import (
    get_current_user,
    high_admin_required,
    is_logged_in,
    login_required,
    renter_required,
)
from ..services.registry import get_services
from ..services.security import rate_limit


MAX_NAME_LEN = 120
MAX_CONTACT_LEN = 200
MAX_DESCRIPTION_LEN = 4000
MAX_DATE_LEN = 10

ALLOWED_CONTACT_METHODS = {"email", "phone", "text", "sms", "call"}


def home():
    return render_template("home.html", title="Home")


@login_required
def manage_listings():
    services = get_services()
    return render_template(
        "manage_listings.html",
        title="Manage Listings",
        properties=services.properties.get_cached_properties(),
        user=get_current_user(),
    )


def report_issue_complete():
    return render_template("report_issue.html", title="Report an Issue", confirmation=True)


def for_rent():
    services = get_services()
    # Refresh property data from upstream on every page load.
    # Falls back to whatever is in cache if the upstream call fails.
    try:
        services.properties.refresh_cache()
    except Exception as exc:
        services.properties.logger.warning(
            "Synchronous refresh failed on /for-rent, serving cache: %s", exc
        )
    return render_template(
        "for_rent.html",
        properties=services.properties.get_cached_properties(),
        title="For Rent",
    )


def for_rent_json():
    services = get_services()
    # Refresh property data from upstream on every call.
    # Falls back to whatever is in cache if the upstream call fails.
    try:
        services.properties.refresh_cache()
    except Exception as exc:
        services.properties.logger.warning(
            "Synchronous refresh failed on /for-rent.json, serving cache: %s", exc
        )
    properties = services.properties.get_cached_properties()
    return jsonify(services.properties.serialize_properties(properties))


@renter_required
@rate_limit(limit=6, window_seconds=60, methods=("GET", "POST"))
def for_rent_refresh_json():
    services = get_services()
    actor_email = (get_current_user() or {}).get("email", "anonymous")
    services.properties.trigger_background_refresh(actor_email)
    return jsonify(services.properties.serialize_properties(services.properties.get_cached_properties()))


def property_details(uuid):
    services = get_services()
    property_info = services.properties.get_property(uuid)
    if not property_info:
        return "Property not found", 404

    appointments = services.appointments.load()
    booked_dates = sorted(list(appointments.get(uuid, set())))
    nowdate = datetime.date.today().strftime("%Y-%m-%d")
    return render_template(
        "property_details.html",
        property=services.properties.normalize_property(property_info),
        nowdate=nowdate,
        booked_dates=booked_dates,
        title=property_info.get("name", "Property"),
    )


@rate_limit(limit=5, window_seconds=600)
def schedule_appointment(uuid):
    services = get_services()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(success=False, error="Invalid payload."), 400
    name = (data.get("name") or "").strip()[:MAX_NAME_LEN]
    date = (data.get("date") or "").strip()[:MAX_DATE_LEN]
    contact_method = (data.get("contact_method") or "").strip().lower()[:32]
    contact_info = (data.get("contact_info") or "").strip()[:MAX_CONTACT_LEN]

    if not name or not contact_info:
        return jsonify(success=False, error="Name and contact info are required."), 400
    if contact_method and contact_method not in ALLOWED_CONTACT_METHODS:
        return jsonify(success=False, error="Invalid contact method."), 400
    try:
        requested_date = datetime.date.fromisoformat(date)
    except Exception:
        return jsonify(success=False, error="Invalid date."), 400
    if requested_date < datetime.date.today():
        return jsonify(success=False, error="Date cannot be in the past."), 400

    property_name = services.properties.fetch_live_property_name(uuid)
    if not property_name:
        return jsonify(success=False, error="Property not found."), 404

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
    return jsonify(success=True)


def about():
    return render_template("about.html", title="About")


def contact():
    return render_template("contact.html", title="Contact")


@high_admin_required
def view_logs():
    services = get_services()
    return render_template("logs.html", log_entries=services.notifications.read_logs(), title="Logger")


def report_issue_form():
    return render_template("report_issue.html", title="Report an Issue", confirmation=False)


@rate_limit(limit=3, window_seconds=600)
def report_issue():
    services = get_services()
    user_name = (request.form.get("name") or "").strip()[:MAX_NAME_LEN]
    issue_description = (request.form.get("description") or "").strip()[:MAX_DESCRIPTION_LEN]
    if not user_name or not issue_description:
        return "Name and description are required fields.", 400
    services.notifications.send_email(
        "User Reported Issue",
        f"Issue reported by {user_name}:\n\n{issue_description}",
    )
    return render_template(
        "report_issue.html",
        title="Report an Issue",
        confirmation=True,
        name=user_name,
        desc=issue_description,
    )


def register_public_routes(app) -> None:
    app.add_url_rule("/", endpoint="home", view_func=home)
    app.add_url_rule("/manage-listings", endpoint="manage_listings", view_func=manage_listings)
    app.add_url_rule("/report-issue-complete", endpoint="report_issue_complete", view_func=report_issue_complete)
    app.add_url_rule("/for-rent", endpoint="for_rent", view_func=for_rent)
    app.add_url_rule("/for-rent.json", endpoint="for_rent_json", view_func=for_rent_json)
    app.add_url_rule("/for-rent-refresh.json", endpoint="for_rent_refresh_json", view_func=for_rent_refresh_json)
    app.add_url_rule("/property/<uuid>", endpoint="property_details", view_func=property_details)
    app.add_url_rule(
        "/property/<uuid>/schedule",
        endpoint="schedule_appointment",
        view_func=schedule_appointment,
        methods=["POST"],
    )
    app.add_url_rule("/about", endpoint="about", view_func=about)
    app.add_url_rule("/contact", endpoint="contact", view_func=contact)
    app.add_url_rule("/logs", endpoint="view_logs", view_func=view_logs)
    app.add_url_rule("/report-issue", endpoint="report_issue_form", view_func=report_issue_form, methods=["GET"])
    app.add_url_rule("/report-issue", endpoint="report_issue", view_func=report_issue, methods=["POST"])
