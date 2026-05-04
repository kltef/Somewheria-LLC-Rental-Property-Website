from dataclasses import dataclass

from flask import current_app


@dataclass
class Services:
    config: object
    analytics: object
    notifications: object
    storage: object
    appointments: object
    auth: object
    properties: object
    tickets: object


def set_services(app, services: Services) -> None:
    app.extensions["somewheria_services"] = services


def get_services() -> Services:
    return current_app.extensions["somewheria_services"]
