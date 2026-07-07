from flask import render_template, send_from_directory

from ..services.registry import get_services


def manifest():
    services = get_services()
    return send_from_directory(services.config.base_dir, "manifest.webmanifest", mimetype="application/manifest+json")


def manifest_json():
    services = get_services()
    return send_from_directory(services.config.base_dir, "manifest.webmanifest", mimetype="application/manifest+json")


def service_worker():
    services = get_services()
    response = send_from_directory(services.config.base_dir, "service-worker.js")
    response.headers["Cache-Control"] = "no-cache"
    return response


def offline():
    return render_template("offline.html", title="Offline")


def robots():
    services = get_services()
    return send_from_directory(services.config.base_dir, "robots.txt", mimetype="text/plain")


def favicon():
    # Browsers request /favicon.ico directly (address bar, bookmarks) even
    # with a <link rel="icon"> present; serve the app icon there so it stops
    # 404ing and shows up in the tab.
    services = get_services()
    return send_from_directory(
        services.config.base_dir / "static",
        "web_light_rd_SI@1x.png",
        mimetype="image/png",
    )


def google_site_verification():
    # Google Search Console HTML-file ownership check: Google fetches this at
    # the site root and confirms the contents match. Served from the app root,
    # not /static, because Google requires the exact top-level path.
    services = get_services()
    return send_from_directory(
        services.config.base_dir, "google425c45881532a134.html", mimetype="text/html"
    )


def register_pwa_routes(app) -> None:
    app.add_url_rule("/manifest.webmanifest", endpoint="manifest", view_func=manifest)
    app.add_url_rule("/manifest.json", endpoint="manifest_json", view_func=manifest_json)
    app.add_url_rule("/service-worker.js", endpoint="service_worker", view_func=service_worker)
    app.add_url_rule("/offline", endpoint="offline", view_func=offline)
    app.add_url_rule("/favicon.ico", endpoint="favicon", view_func=favicon)
    app.add_url_rule("/robots.txt", endpoint="robots", view_func=robots)
    app.add_url_rule(
        "/google425c45881532a134.html",
        endpoint="google_site_verification",
        view_func=google_site_verification,
    )
