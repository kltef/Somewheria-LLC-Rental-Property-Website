import datetime
from xml.sax.saxutils import escape

from flask import Response, jsonify, render_template, request, url_for

from ..services.auth import (
    get_current_user,
    high_admin_required,
    login_required,
)
from ..services.registry import get_services
from ..services.security import rate_limit
from ..services.timeutil import utcnow_iso
from ..services.validation import is_valid_email


MAX_NAME_LEN = 120
MAX_CONTACT_LEN = 200
MAX_DESCRIPTION_LEN = 4000
MAX_DATE_LEN = 10

# Cap appointment requests at a year out. Legitimate viewings happen within a
# few weeks; nothing realistic needs more lead time, and an unbounded date
# field invites year-9999 junk that wastes admin attention.
MAX_APPOINTMENT_DAYS_AHEAD = 365

ALLOWED_CONTACT_METHODS = {"email", "phone", "text", "sms", "call"}


def home():
    return render_template(
        "home.html",
        title="Home",
        meta_description=(
            "Somewheria, LLC is a family-run landlord renting clean, well-kept homes. "
            "See what's available, learn how renting with us works, and get in touch."
        ),
    )


@login_required
def manage_listings():
    services = get_services()
    return render_template(
        "manage_listings.html",
        title="Manage Listings",
        properties=services.properties.get_cached_properties(),
        hidden_ids=services.properties.hidden_listing_ids(),
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
    properties = services.properties.get_visible_properties()
    count = len(properties)
    if count:
        meta_description = (
            f"{count} rental home{'s' if count != 1 else ''} available now from "
            "Somewheria, LLC. Filter by bedrooms, bathrooms, rent, and pets, then apply online."
        )
    else:
        meta_description = (
            "Browse rental homes from Somewheria, LLC. Check back for new listings, "
            "or join the notify list to hear about openings first."
        )
    return render_template(
        "for_rent.html",
        properties=properties,
        title="For Rent",
        meta_description=meta_description,
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
    properties = services.properties.get_visible_properties()
    return jsonify(services.properties.serialize_properties(properties))


@rate_limit(limit=6, window_seconds=60, methods=("GET", "POST"))
def for_rent_refresh_json():
    services = get_services()
    actor_email = (get_current_user() or {}).get("email", "anonymous")
    services.properties.trigger_background_refresh(actor_email)
    return jsonify(services.properties.serialize_properties(services.properties.get_visible_properties()))


def _property_meta_description(prop: dict) -> str:
    """A concise, factual search-result summary for one listing.

    Prefers the listing's own blurb/description; otherwise builds one from the
    structured facts. Kept near ~155 chars, the length Google typically shows.
    """
    blurb = (prop.get("blurb") or prop.get("description") or "").strip()
    if blurb:
        summary = " ".join(blurb.split())
        return summary[:157].rstrip() + "…" if len(summary) > 158 else summary

    name = (prop.get("name") or "Rental home").strip()
    bits = []
    beds, baths = prop.get("bedrooms"), prop.get("bathrooms")
    if beds not in (None, "", "N/A"):
        bits.append(f"{beds} bed")
    if baths not in (None, "", "N/A"):
        bits.append(f"{baths} bath")
    rent = prop.get("rent")
    if rent not in (None, "", "N/A"):
        # Trim a trailing ".0" the upstream sends (rent comes back as a float),
        # so search snippets read "$1500/mo" not "$1500.0/mo".
        try:
            rent = f"{float(rent):g}"
        except (TypeError, ValueError):
            pass
        bits.append(f"${rent}/mo")
    address = (prop.get("address") or "").strip()
    lead = f"{name} — " + ", ".join(bits) if bits else name
    tail = f" in {address}." if address and address != "N/A" else "."
    return f"{lead}{tail} Available to rent from Somewheria, LLC. See photos and details, or request a tour."


def property_details(uuid):
    services = get_services()
    # A deactivated listing is unpublished: hide the page from the public,
    # but let admins keep previewing it (e.g. before reactivating).
    if services.properties.is_listing_hidden(uuid):
        role = (get_current_user() or {}).get("role", "")
        if role not in ("admin", "high_admin"):
            return "Property not found", 404
    property_info = services.properties.get_property(uuid)
    if not property_info:
        # Cold cache (e.g. just after a restart) or a listing that wasn't in
        # the last refresh: fetch the single record directly so a visitor
        # arriving straight at this URL — a crawler following the sitemap, a
        # shared link — gets the page instead of a 404.
        try:
            property_info = services.properties.fetch_property_record(uuid)
        except Exception as exc:
            services.properties.logger.warning(
                "Direct fetch failed for property %s: %s", uuid, exc
            )
            property_info = None
    if not property_info:
        return "Property not found", 404

    appointments = services.appointments.load()
    booked_dates = sorted(list(appointments.get(uuid, set())))
    nowdate = datetime.date.today().strftime("%Y-%m-%d")
    normalized = services.properties.normalize_property(property_info)
    # Use the listing's own thumbnail as the social-share image when it's a
    # real URL (S3 photos are absolute https); skip base64/relative values.
    thumb = normalized.get("thumbnail") or ""
    og_image = thumb if thumb.startswith("http") else None
    return render_template(
        "property_details.html",
        property=normalized,
        nowdate=nowdate,
        booked_dates=booked_dates,
        title=property_info.get("name", "Property"),
        meta_description=_property_meta_description(normalized),
        og_image=og_image,
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
    if requested_date > datetime.date.today() + datetime.timedelta(days=MAX_APPOINTMENT_DAYS_AHEAD):
        return jsonify(
            success=False,
            error=f"Date must be within {MAX_APPOINTMENT_DAYS_AHEAD} days.",
        ), 400

    property_name = services.properties.fetch_live_property_name(uuid)
    if not property_name:
        return jsonify(success=False, error="Property not found."), 404

    iso_date = requested_date.isoformat()
    if not services.appointments.book(uuid, iso_date):
        return jsonify(success=False, error="That date is already booked."), 409

    message = (
        "Appointment requested!\n\n"
        f"Property: {property_name}\n"
        f"Requested by: {name}\n"
        f"For date: {iso_date}\n"
        f"Contact method: {contact_method}\n"
        f"Contact info: {contact_info}\n"
        f"Requested at: {utcnow_iso()}"
    )
    services.notifications.send_email("Viewing Appointment Request", message)
    return jsonify(success=True)


def about():
    return render_template(
        "about.html",
        title="About",
        meta_description=(
            "About Somewheria, LLC — a family-run rental company focused on well-kept "
            "homes and responsive, person-to-person service for renters."
        ),
    )


_CONTACT_META_DESCRIPTION = (
    "Contact Somewheria, LLC about renting a home, scheduling a tour, or a "
    "current tenancy. Reach a real person by phone or email."
)


def contact(sent=False, error=None, form=None):
    return render_template(
        "contact.html",
        title="Contact",
        meta_description=_CONTACT_META_DESCRIPTION,
        sent=sent,
        error=error,
        form=form or {},
    )


@rate_limit(limit=5, window_seconds=600)
def contact_submit():
    # The form used to be ``action="mailto:..."`` — it never reached the
    # server, and on machines with no mail client the button did nothing at
    # all. Mirror the appointment flow instead: validate, email the
    # configured recipient, confirm on-page.
    services = get_services()
    name = (request.form.get("name") or "").strip()[:MAX_NAME_LEN]
    email = (request.form.get("email") or "").strip()[:254]
    message = (request.form.get("message") or "").strip()[:MAX_DESCRIPTION_LEN]
    form = {"name": name, "email": email, "message": message}
    if not name or not message or not is_valid_email(email):
        return contact(error="Name, a valid email, and a message are required.", form=form), 400
    delivered = services.notifications.send_email(
        "Contact Form Message",
        f"From: {name} <{email}>\n\n{message}",
    )
    if not delivered:
        services.notifications.log_and_notify_error(
            "Contact Form Delivery Failure",
            f"Contact message from {name} <{email}> could not be emailed.",
        )
        return contact(
            error=(
                "Sorry — we couldn't send your message just now. "
                "Please call or email us directly."
            ),
            form=form,
        ), 503
    return contact(sent=True)


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


def sitemap():
    """XML sitemap of the public, indexable pages plus every live listing.

    Referenced from robots.txt so Google/Bing can discover all listing pages
    without having to crawl them one link at a time. Admin, renter, and auth
    pages are intentionally excluded — they aren't public content.
    """
    services = get_services()
    # Best-effort refresh so freshly-listed properties appear; fall back to
    # cache (mirrors the /for-rent behavior) rather than failing the sitemap.
    try:
        services.properties.refresh_cache()
    except Exception as exc:
        services.properties.logger.warning("Sitemap refresh failed, using cache: %s", exc)

    def loc(endpoint, **values):
        return escape(url_for(endpoint, _external=True, _scheme="https", **values))

    urls = [
        (loc("home"), "weekly", "1.0"),
        (loc("for_rent"), "daily", "0.9"),
        (loc("about"), "monthly", "0.5"),
        (loc("contact"), "monthly", "0.5"),
        (loc("register"), "yearly", "0.3"),
    ]
    for prop in services.properties.get_visible_properties():
        prop_id = prop.get("id")
        if prop_id:
            urls.append((loc("property_details", uuid=prop_id), "weekly", "0.8"))

    entries = "".join(
        f"<url><loc>{u}</loc><changefreq>{freq}</changefreq><priority>{pri}</priority></url>"
        for u, freq, pri in urls
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{entries}</urlset>"
    )
    return Response(body, mimetype="application/xml")


def register_public_routes(app) -> None:
    app.add_url_rule("/", endpoint="home", view_func=home)
    app.add_url_rule("/sitemap.xml", endpoint="sitemap", view_func=sitemap)
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
    app.add_url_rule("/contact", endpoint="contact_submit", view_func=contact_submit, methods=["POST"])
    app.add_url_rule("/logs", endpoint="view_logs", view_func=view_logs)
    app.add_url_rule("/report-issue", endpoint="report_issue_form", view_func=report_issue_form, methods=["GET"])
    app.add_url_rule("/report-issue", endpoint="report_issue", view_func=report_issue, methods=["POST"])
