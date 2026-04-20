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


def register_pwa_routes(app) -> None:
    app.add_url_rule("/manifest.webmanifest", endpoint="manifest", view_func=manifest)
    app.add_url_rule("/manifest.json", endpoint="manifest_json", view_func=manifest_json)
    app.add_url_rule("/service-worker.js", endpoint="service_worker", view_func=service_worker)
    app.add_url_rule("/offline", endpoint="offline", view_func=offline)
