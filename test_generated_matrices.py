import copy
import datetime
import importlib
import os
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

from somewheria_app.services.auth import AuthService
from somewheria_app.services.notifications import NotificationService
from somewheria_app.services.properties import PropertyService
from somewheria_app.services.storage import FileStorageService


os.environ["DISABLE_BACKGROUND_THREADS"] = "1"

website_app = importlib.import_module("website_app")


class DummyForm:
    def __init__(self, values=None, lists=None):
        self.values = values or {}
        self.lists = lists or {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getlist(self, key):
        return list(self.lists.get(key, []))


class GeneratedRouteMatrixTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        website_app.app.config.update(TESTING=True)

    def setUp(self):
        self.app = website_app.app
        self.client = self.app.test_client()
        self.services = self.app.extensions["somewheria_services"]
        with self.services.properties.cache_lock:
            self.original_cache = copy.deepcopy(self.services.properties.cache)
            self.services.properties.cache = []
        self.original_google_client_id = self.services.config.google_client_id
        self.original_google_client_secret = self.services.config.google_client_secret
        self.seed_property()

    def tearDown(self):
        with self.services.properties.cache_lock:
            self.services.properties.cache = self.original_cache
        self.services.config.google_client_id = self.original_google_client_id
        self.services.config.google_client_secret = self.original_google_client_secret

    def seed_property(self, property_id="prop-1", **overrides):
        property_data = {
            "id": property_id,
            "name": "Maple House",
            "address": "123 Main St",
            "rent": "1500",
            "deposit": "1500",
            "bedrooms": "2",
            "bathrooms": "1",
            "sqft": "900",
            "lease_length": "12 months",
            "included_amenities": ["Parking", "Laundry"],
            "pets_allowed": "Yes",
            "ada_accessible": "Yes",
            "blurb": "A bright rental home.",
            "description": "Comfortable home close to transit.",
            "photos": ["https://example.com/photo1.jpg", "https://example.com/photo2.jpg"],
            "thumbnail": "https://example.com/thumb.jpg",
            "status": "Active",
        }
        property_data.update(overrides)
        with self.services.properties.cache_lock:
            self.services.properties.cache = [property_data]
        return property_data

    def login_as(self, role):
        with self.client.session_transaction() as session:
            session["user"] = {
                "id": f"{role}-id",
                "email": f"{role}@example.com",
                "name": f"{role.title()} User",
                "role": role,
            }

    def common_patches(self):
        stack = ExitStack()
        stack.enter_context(patch.object(self.services.storage, "get_pending_registrations", return_value=[]))
        stack.enter_context(patch.object(self.services.storage, "get_user_roles", return_value={}))
        stack.enter_context(patch.object(self.services.storage, "get_renter_contracts", return_value={}))
        stack.enter_context(patch.object(self.services.storage, "get_renter_profiles", return_value={}))
        stack.enter_context(patch.object(self.services.notifications, "read_logs", return_value=[]))
        stack.enter_context(patch.object(self.services.appointments, "load", return_value={}))
        stack.enter_context(patch.object(self.services.analytics, "dashboard_data", return_value=({}, {"labels": []})))
        return stack

    def request_for_role(self, method, path, role=None, **kwargs):
        with self.common_patches() as stack:
            if role:
                self.login_as(role)
            if path == "/save-edit/new":
                stack.enter_context(patch.object(self.services.properties, "create_property"))
            if path == "/save-edit/prop-1":
                stack.enter_context(patch.object(self.services.properties, "update_property"))
            if path == "/delete-listing/prop-1":
                stack.enter_context(patch.object(self.services.properties, "delete_property"))
            if path == "/toggle-sale/prop-1":
                stack.enter_context(patch.object(self.services.properties, "toggle_sale"))
            if path == "/property/prop-1/schedule":
                stack.enter_context(patch.object(self.services.properties, "fetch_live_property_name", return_value="Maple House"))
                stack.enter_context(patch.object(self.services.notifications, "send_email"))
            if path == "/google/login":
                self.services.config.google_client_id = "client-id"
                self.services.config.google_client_secret = "client-secret"
                flow_mock = type("FlowMock", (), {})()
                flow_mock.redirect_uri = None
                flow_mock.authorization_url = lambda **_kwargs: ("https://accounts.google.com/o/oauth2/auth?mock=1", "state-123")
                stack.enter_context(patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow_mock))
            response = getattr(self.client, method)(path, follow_redirects=False, **kwargs)
        return response


class GeneratedServiceMatrixTestCase(unittest.TestCase):
    def setUp(self):
        self.notifications = Mock()
        self.property_service = PropertyService(
            SimpleNamespace(api_base_url="https://api.example.com", upload_dir=Path(".")),
            self.notifications,
        )
        self.auth_storage = Mock()
        self.auth_service = AuthService(
            SimpleNamespace(
                authorized_users={"renter@example.com"},
                admin_users={"admin@example.com"},
                high_admin_users={"owner@example.com"},
            ),
            self.auth_storage,
        )
        self.notification_service = NotificationService(
            SimpleNamespace(
                email_sender="sender@example.com",
                email_recipient="recipient@example.com",
                log_file=Path("application.log"),
                change_log_file=Path("site_changes.log"),
            ),
            Mock(),
        )
        self.file_storage = FileStorageService(
            SimpleNamespace(
                registration_file=Path("registrations.json"),
                user_roles_file=Path("roles.json"),
                renter_profile_file=Path("profiles.json"),
                contracts_file=Path("contracts.json"),
            )
        )


ROLE_STATUSES = {
    "anon": None,
    "renter": "renter",
    "admin": "admin",
    "high_admin": "high_admin",
}

GET_ROUTE_MATRIX = [
    ("/", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/about", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/contact", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/for-rent", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/property/prop-1", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/offline", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/report-issue", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/report-issue-complete", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/register", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/login", {"anon": 200, "renter": 302, "admin": 302, "high_admin": 302}),
    ("/logs", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/manifest.json", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/service-worker.js", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/auth/status", {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/manage-listings", {"anon": 302, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/renter-dashboard", {"anon": 302, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/renter/profile", {"anon": 302, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/add-listing", {"anon": 302, "renter": 403, "admin": 200, "high_admin": 200}),
    ("/edit-listing/prop-1", {"anon": 302, "renter": 403, "admin": 200, "high_admin": 200}),
    ("/admin/status", {"anon": 302, "renter": 403, "admin": 200, "high_admin": 200}),
    ("/admin/users", {"anon": 302, "renter": 403, "admin": 200, "high_admin": 200}),
    ("/admin/contracts", {"anon": 302, "renter": 403, "admin": 200, "high_admin": 200}),
    ("/admin/registrations", {"anon": 302, "renter": 403, "admin": 200, "high_admin": 200}),
    ("/admin/analytics", {"anon": 302, "renter": 403, "admin": 403, "high_admin": 200}),
    ("/admin/dashboard", {"anon": 302, "renter": 403, "admin": 403, "high_admin": 200}),
    ("/google/login", {"anon": 302, "renter": 302, "admin": 302, "high_admin": 302}),
]

POST_ROUTE_MATRIX = [
    ("/register", {"data": {"name": "Jamie", "email": "jamie@example.com", "reason": "Need access"}}, {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/report-issue", {"data": {"name": "Jamie", "description": "Broken button"}}, {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200}),
    (
        "/property/prop-1/schedule",
        {
            "json": {
                "name": "Alex",
                "date": (datetime.date.today() + datetime.timedelta(days=7)).isoformat(),
                "contact_method": "email",
                "contact_info": "alex@example.com",
            }
        },
        {"anon": 200, "renter": 200, "admin": 200, "high_admin": 200},
    ),
    ("/renter/profile", {"data": {"name": "Jamie", "contact": "555-0100"}}, {"anon": 302, "renter": 200, "admin": 200, "high_admin": 200}),
    ("/admin/users", {"data": {"email": "", "role": "renter", "action": "update"}}, {"anon": 302, "renter": 403, "admin": 200, "high_admin": 200}),
    ("/admin/registrations", {"data": {"action": "approve", "email": ""}}, {"anon": 302, "renter": 403, "admin": 200, "high_admin": 200}),
    ("/admin/contracts", {"data": {"action": "add"}}, {"anon": 302, "renter": 403, "admin": 200, "high_admin": 200}),
    ("/admin/dashboard", {"data": {"action": "add", "email": "", "role": "admin"}}, {"anon": 302, "renter": 403, "admin": 403, "high_admin": 200}),
    ("/save-edit/new", {"data": {"name": "Maple House"}}, {"anon": 302, "renter": 403, "admin": 302, "high_admin": 302}),
    ("/save-edit/prop-1", {"data": {"name": "Maple House"}}, {"anon": 302, "renter": 403, "admin": 302, "high_admin": 302}),
    ("/delete-listing/prop-1", {"data": {}}, {"anon": 302, "renter": 403, "admin": 302, "high_admin": 302}),
    ("/toggle-sale/prop-1", {"data": {}}, {"anon": 302, "renter": 403, "admin": 302, "high_admin": 302}),
    ("/upload-image/prop-1", {"data": {}, "content_type": "multipart/form-data"}, {"anon": 302, "renter": 403, "admin": 400, "high_admin": 400}),
    ("/image-edit-notify", {"json": {"images": []}}, {"anon": 302, "renter": 403, "admin": 200, "high_admin": 200}),
]

NAV_EXPECTATIONS = {
    "anon": {
        "present": ["Login"],
        "missing": ["Manage Listings", "Renter Dashboard", "Status", "Admin Panel", "Logout"],
    },
    "renter": {
        "present": ["Manage Listings", "Renter Dashboard", "Logout"],
        "missing": ["Login", "Status", "Admin Panel"],
    },
    "admin": {
        "present": ["Manage Listings", "Renter Dashboard", "Status", "Logout"],
        "missing": ["Login", "Admin Panel"],
    },
    "high_admin": {
        "present": ["Manage Listings", "Renter Dashboard", "Status", "Admin Panel", "Logout"],
        "missing": ["Login"],
    },
}

PAGE_SNIPPETS = [
    ("/", None, b"Welcome to Somewheria, LLC."),
    ("/about", None, b"About"),
    ("/contact", None, b"Contact"),
    ("/login", None, b"Login"),
    ("/register", None, b"Register"),
    ("/offline", None, b"Offline"),
    ("/report-issue", None, b"Report"),
    ("/property/prop-1", None, b"Property Details"),
    ("/manage-listings", "admin", b"Manage Listings"),
    ("/admin/status", "admin", b"System Status"),
    ("/admin/dashboard", "high_admin", b"Admin Dashboard"),
    ("/renter/profile", "renter", b"Edit Profile"),
]

ADA_CASES = [
    (True, "Yes"),
    (False, "No"),
    ("yes", "Yes"),
    ("YES", "Yes"),
    ("Yes", "Yes"),
    ("true", "Yes"),
    ("TRUE", "Yes"),
    ("1", "Yes"),
    ("no", "No"),
    ("NO", "No"),
    ("No", "No"),
    ("false", "No"),
    ("FALSE", "No"),
    ("0", "No"),
    ("maybe", "Unknown"),
    ("", "Unknown"),
    (None, "Unknown"),
]

ADA_KEYS = ["ada_accessible", "accessible", "accessibility", "is_accessible", "wheelchair_accessible"]

PETS_CASES = [
    ({"pets_allowed": True}, "Yes"),
    ({"pets_allowed": False}, "No"),
    ({"pets_allowed": "Unknown"}, "Unknown"),
    ({"pets_allowed": "No"}, "No"),
    ({"included_amenities": ["Pet Friendly"]}, "Yes"),
    ({"included_amenities": ["pet wash"]}, "Yes"),
    ({"included_amenities": ["Parking"]}, "Unknown"),
    ({"description": "Pets welcome here"}, "Yes"),
    ({"description": "A quiet home"}, "Unknown"),
    ({"description": "Pet deposit required"}, "Yes"),
    ({"description": "No pets allowed"}, "Yes"),
    ({"included_amenities": ["Laundry"], "description": "Pets considered"}, "Yes"),
    ({"included_amenities": ["Laundry"], "description": ""}, "Unknown"),
    ({"included_amenities": [], "description": "pet-friendly unit"}, "Yes"),
    ({"included_amenities": [], "description": "family rental"}, "Unknown"),
    ({"pets_allowed": True, "description": "No pets"}, "Yes"),
    ({"pets_allowed": False, "included_amenities": ["Pet Friendly"]}, "No"),
    ({"pets_allowed": "Unknown", "included_amenities": ["Pet Friendly"]}, "Yes"),
    ({"pets_allowed": "Unknown", "description": "pets ok"}, "Yes"),
    ({}, "Unknown"),
]

DEFAULT_FIELD_EXPECTATIONS = {
    "bedrooms": "N/A",
    "bathrooms": "N/A",
    "rent": "N/A",
    "sqft": "N/A",
    "deposit": "N/A",
    "address": "N/A",
    "lease_length": "12 months",
    "name": "Property",
    "description": "",
    "blurb": "",
    "photos": [],
    "thumbnail": "",
}

SERIALIZE_CASES = [
    ({"tags": {"a"}}, ["a"]),
    ({"tags": {"a", "b"}}, ["a", "b"]),
    ({"amenities": {"Parking"}}, ["Parking"]),
    ({"amenities": {"Parking", "Laundry"}}, ["Parking", "Laundry"]),
    ({"ids": {"1", "2", "3"}}, ["1", "2", "3"]),
    ({"features": set()}, []),
    ({"misc": {"x", "y"}}, ["x", "y"]),
    ({"roles": {"admin"}}, ["admin"]),
    ({"roles": {"admin", "renter"}}, ["admin", "renter"]),
    ({"markers": {"north", "south"}}, ["north", "south"]),
]

PAYLOAD_CASES = [
    ({"pets_allowed": "Yes", "custom_amenities": "Garden"}, ["Parking"], True, ["Parking", "Garden"]),
    ({"pets_allowed": "No", "custom_amenities": "Garden,Storage"}, ["Parking"], False, ["Parking", "Garden", "Storage"]),
    ({"pets_allowed": "Unknown", "custom_amenities": ""}, ["Parking"], False, ["Parking"]),
    ({"pets_allowed": "Yes", "custom_amenities": "  "}, ["Parking"], True, ["Parking"]),
    ({"pets_allowed": "No", "custom_amenities": "Storage "}, ["Laundry"], False, ["Laundry", "Storage"]),
    ({"pets_allowed": "Yes", "custom_amenities": "Deck, Patio"}, ["Laundry"], True, ["Laundry", "Deck", "Patio"]),
    ({"pets_allowed": "Unknown", "custom_amenities": "Bike Room"}, [], False, ["Bike Room"]),
    ({"pets_allowed": "Yes", "custom_amenities": ""}, [], True, []),
    ({"pets_allowed": "No", "custom_amenities": "Garden"}, [], False, ["Garden"]),
    ({"pets_allowed": "Yes", "custom_amenities": "Garden, Storage, Deck"}, ["Parking"], True, ["Parking", "Garden", "Storage", "Deck"]),
    ({"pets_allowed": "No", "custom_amenities": "Roof Deck"}, ["Gym"], False, ["Gym", "Roof Deck"]),
    ({"pets_allowed": "Yes", "custom_amenities": "Locker"}, ["Pool"], True, ["Pool", "Locker"]),
    ({"pets_allowed": "Unknown", "custom_amenities": "Mailbox"}, ["Water"], False, ["Water", "Mailbox"]),
    ({"pets_allowed": "Yes", "custom_amenities": "Garden, Garden"}, ["Parking"], True, ["Parking", "Garden", "Garden"]),
    ({"pets_allowed": "No", "custom_amenities": "Access Ramp"}, ["Laundry"], False, ["Laundry", "Access Ramp"]),
]

AUTH_ROLE_CASES = [
    ({}, "owner@example.com", "high_admin"),
    ({}, "admin@example.com", "admin"),
    ({}, "renter@example.com", "renter"),
    ({}, "guest@example.com", "guest"),
    ({"guest@example.com": "admin"}, "guest@example.com", "admin"),
    ({"owner@example.com": "renter"}, "owner@example.com", "renter"),
    ({"admin@example.com": "high_admin"}, "admin@example.com", "high_admin"),
    ({"renter@example.com": "admin"}, "renter@example.com", "admin"),
    ({}, "OWNER@example.com", "high_admin"),
    ({}, "ADMIN@example.com", "admin"),
    ({}, "RENTER@example.com", "renter"),
    ({"guest@example.com": "renter"}, "GUEST@example.com", "renter"),
    ({"mixed@example.com": "high_admin"}, "mixed@example.com", "high_admin"),
    ({"mixed@example.com": "admin"}, "mixed@example.com", "admin"),
    ({"mixed@example.com": "renter"}, "mixed@example.com", "renter"),
    ({}, "unknown@another.com", "guest"),
    ({"owner@example.com": "admin"}, "owner@example.com", "admin"),
    ({"admin@example.com": "renter"}, "admin@example.com", "renter"),
    ({"renter@example.com": "high_admin"}, "renter@example.com", "high_admin"),
    ({"person@example.com": "guest"}, "person@example.com", "guest"),
]

NOTIFICATION_PARSE_CASES = [
    ("2026-03-23 18:47:42|INFO|http|GET / -> 200", "INFO", "[http] GET / -> 200"),
    ("2026-03-23 18:47:42|WARN|notify|Missing config", "WARNING", "[notify] Missing config"),
    ("2026-03-23 18:47:42|CRIT|auth|Auth crash", "CRITICAL", "[auth] Auth crash"),
    ("2026-03-23:INFO:Legacy info", "INFO", "Legacy info"),
    ("2026-03-23:WARN:Legacy warning", "WARNING", "Legacy warning"),
    ("2026-03-23:CRIT:Legacy critical", "CRITICAL", "Legacy critical"),
    ("plain line without separators", "", "plain line without separators"),
    ("2026-03-23 18:47:42|DEBUG|jobs|Refresh started", "DEBUG", "[jobs] Refresh started"),
    ("2026-03-23 18:47:42|ERROR|mail|Failed to send", "ERROR", "[mail] Failed to send"),
    ("2026-03-23 18:47:42|INFO|cache|Updated 5 records", "INFO", "[cache] Updated 5 records"),
    ("2026-03-23 18:47:42|WARN|http|Slow response", "WARNING", "[http] Slow response"),
    ("2026-03-23 18:47:42|CRIT|db|Outage", "CRITICAL", "[db] Outage"),
    ("2026-03-23:DEBUG:Legacy debug", "DEBUG", "Legacy debug"),
    ("2026-03-23:ERROR:Legacy error", "ERROR", "Legacy error"),
    ("2026-03-23 18:47:42|INFO|auth|Login ok", "INFO", "[auth] Login ok"),
    ("2026-03-23 18:47:42|INFO|notify|Sent email", "INFO", "[notify] Sent email"),
    ("2026-03-23 18:47:42|INFO|jobs|Background pass", "INFO", "[jobs] Background pass"),
    ("2026-03-23 18:47:42|WARN|jobs|Background warn", "WARNING", "[jobs] Background warn"),
    ("2026-03-23 18:47:42|CRIT|jobs|Background crit", "CRITICAL", "[jobs] Background crit"),
    ("2026-03-23 18:47:42|INFO|ui|Theme toggled", "INFO", "[ui] Theme toggled"),
]

STARTUP_CHOICE_CASES = [
    (["quiet"], "quiet"),
    (["normal"], "normal"),
    (["debug"], "debug"),
    (["LOUD", "debug"], "debug"),
    ([""], "normal"),
]

STARTUP_YES_NO_CASES = [
    ("y", True),
    ("yes", True),
    ("n", False),
    ("no", False),
    ("", True),
]

STARTUP_PORT_CASES = [
    (["5001"], 5001),
    (["1"], 1),
    (["65535"], 65535),
    (["abc", "5002"], 5002),
    (["70000", "5003"], 5003),
]

LOWERCASE_EMAILS = [
    "Admin@Example.com",
    "ADMIN@EXAMPLE.COM",
    "Mixed.Case@Example.com",
    "User+tag@Example.com",
    "user.name@Example.com",
    "First.Last@Example.com",
    "Owner@Example.com",
    "Tenant@Example.com",
    "Sample123@Example.com",
    "CaseTest@Example.com",
]


def _slug(text):
    return (
        text.replace("/", "_")
        .replace("-", "_")
        .replace(".", "_")
        .replace("@", "_")
        .replace(" ", "_")
        .replace(":", "_")
        .replace("|", "_")
        .replace(">", "_")
        .replace("<", "_")
        .replace("?", "_")
        .replace("=", "_")
        .replace("+", "_")
    ).strip("_").lower()


for path, expectations in GET_ROUTE_MATRIX:
    for role_name, role_value in ROLE_STATUSES.items():
        def _make_get_test(path=path, role_value=role_value, expected=expectations[role_name]):
            def test(self):
                response = self.request_for_role("get", path, role=role_value)
                self.assertEqual(response.status_code, expected)
            return test

        setattr(
            GeneratedRouteMatrixTestCase,
            f"test_get_{_slug(path)}_{role_name}_returns_{expectations[role_name]}",
            _make_get_test(),
        )


for path, request_kwargs, expectations in POST_ROUTE_MATRIX:
    for role_name, role_value in ROLE_STATUSES.items():
        def _make_post_test(path=path, role_value=role_value, expected=expectations[role_name], request_kwargs=request_kwargs):
            def test(self):
                response = self.request_for_role("post", path, role=role_value, **request_kwargs)
                self.assertEqual(response.status_code, expected)
            return test

        setattr(
            GeneratedRouteMatrixTestCase,
            f"test_post_{_slug(path)}_{role_name}_returns_{expectations[role_name]}",
            _make_post_test(),
        )


for role_name, expectations in NAV_EXPECTATIONS.items():
    role_value = ROLE_STATUSES[role_name]
    for label in expectations["present"]:
        def _make_nav_present(role_value=role_value, label=label):
            def test(self):
                response = self.request_for_role("get", "/for-rent", role=role_value)
                self.assertIn(label.encode("utf-8"), response.data)
            return test

        setattr(
            GeneratedRouteMatrixTestCase,
            f"test_nav_{role_name}_shows_{_slug(label)}",
            _make_nav_present(),
        )
    for label in expectations["missing"]:
        def _make_nav_missing(role_value=role_value, label=label):
            def test(self):
                response = self.request_for_role("get", "/for-rent", role=role_value)
                self.assertNotIn(label.encode("utf-8"), response.data)
            return test

        setattr(
            GeneratedRouteMatrixTestCase,
            f"test_nav_{role_name}_hides_{_slug(label)}",
            _make_nav_missing(),
        )


for path, role, snippet in PAGE_SNIPPETS:
    def _make_snippet_test(path=path, role=role, snippet=snippet):
        def test(self):
            response = self.request_for_role("get", path, role=role)
            self.assertEqual(response.status_code, 200)
            self.assertIn(snippet, response.data)
        return test

    setattr(
        GeneratedRouteMatrixTestCase,
        f"test_page_{_slug(path)}_{_slug(snippet.decode('utf-8'))}_snippet",
        _make_snippet_test(),
    )


for key in ADA_KEYS:
    for raw_value, expected in ADA_CASES:
        def _make_ada_test(key=key, raw_value=raw_value, expected=expected):
            def test(self):
                normalized = self.property_service.normalize_property({key: raw_value}, "prop-1")
                self.assertEqual(normalized["ada_accessible"], expected)
            return test

        setattr(
            GeneratedServiceMatrixTestCase,
            f"test_ada_{key}_{_slug(str(raw_value)) or 'empty'}_normalizes_to_{_slug(expected)}",
            _make_ada_test(),
        )


for index, (payload, expected) in enumerate(PETS_CASES):
    def _make_pets_test(payload=payload, expected=expected):
        def test(self):
            normalized = self.property_service.normalize_property(payload, "prop-1")
            self.assertEqual(normalized["pets_allowed"], expected)
        return test

    setattr(
        GeneratedServiceMatrixTestCase,
        f"test_pets_case_{index+1}_{_slug(expected)}",
        _make_pets_test(),
    )


for field, expected in DEFAULT_FIELD_EXPECTATIONS.items():
    def _make_default_field_test(field=field, expected=expected):
        def test(self):
            normalized = self.property_service.normalize_property({}, "prop-1")
            self.assertEqual(normalized[field], expected)
        return test

    setattr(
        GeneratedServiceMatrixTestCase,
        f"test_default_field_{field}",
        _make_default_field_test(),
    )


for index, (record, expected_list) in enumerate(SERIALIZE_CASES):
    def _make_serialize_test(record=record, expected_list=expected_list):
        def test(self):
            key = next(iter(record.keys()))
            serialized = self.property_service.serialize_properties([record])[0][key]
            self.assertCountEqual(serialized, expected_list)
        return test

    setattr(
        GeneratedServiceMatrixTestCase,
        f"test_serialize_case_{index+1}",
        _make_serialize_test(),
    )


for index, (values, amenities, pets_expected, amenity_expected) in enumerate(PAYLOAD_CASES):
    def _make_payload_test(values=values, amenities=amenities, pets_expected=pets_expected, amenity_expected=amenity_expected):
        def test(self):
            form_values = {
                "name": "Maple House",
                "address": "123 Main St",
                "rent": "1500",
                "deposit": "1500",
                "bedrooms": "2",
                "bathrooms": "1",
                "lease_length": "12 months",
                "blurb": "Blurb",
                "description": "Description",
            }
            form_values.update(values)
            payload = self.property_service.property_payload_from_form(
                DummyForm(values=form_values, lists={"amenities": amenities})
            )
            self.assertEqual(payload["pets_allowed"], pets_expected)
            self.assertEqual(payload["included_amenities"], amenity_expected)
        return test

    setattr(
        GeneratedServiceMatrixTestCase,
        f"test_property_payload_case_{index+1}",
        _make_payload_test(),
    )


for index, (stored_roles, email, expected_role) in enumerate(AUTH_ROLE_CASES):
    def _make_auth_role_test(stored_roles=stored_roles, email=email, expected_role=expected_role):
        def test(self):
            self.auth_storage.get_user_roles.return_value = stored_roles
            self.assertEqual(self.auth_service.get_user_role(email), expected_role)
        return test

    setattr(
        GeneratedServiceMatrixTestCase,
        f"test_auth_role_case_{index+1}_{_slug(expected_role)}",
        _make_auth_role_test(),
    )


for index, (line, expected_level, expected_message) in enumerate(NOTIFICATION_PARSE_CASES):
    def _make_notification_parse_test(line=line, expected_level=expected_level, expected_message=expected_message):
        def test(self):
            with patch.object(Path, "exists", return_value=True), patch.object(
                Path,
                "open",
                mock_open(read_data=line + "\n"),
            ):
                entries = self.notification_service.read_logs()
            self.assertEqual(entries[0]["level"], expected_level)
            self.assertIn(expected_message, entries[0]["message"])
        return test

    setattr(
        GeneratedServiceMatrixTestCase,
        f"test_notification_parse_case_{index+1}",
        _make_notification_parse_test(),
    )


for index, (inputs, expected) in enumerate(STARTUP_CHOICE_CASES):
    def _make_startup_choice_test(inputs=inputs, expected=expected):
        def test(self):
            iterator = iter(inputs)
            with patch("builtins.input", side_effect=lambda _prompt: next(iterator)):
                choice = website_app._prompt_choice(
                    "Choose level",
                    "normal",
                    {"quiet": "WARNING", "normal": "INFO", "debug": "DEBUG"},
                )
            self.assertEqual(choice, expected)
        return test

    setattr(
        GeneratedServiceMatrixTestCase,
        f"test_startup_choice_case_{index+1}_{_slug(expected)}",
        _make_startup_choice_test(),
    )


for index, (answer, expected) in enumerate(STARTUP_YES_NO_CASES):
    def _make_yes_no_test(answer=answer, expected=expected):
        def test(self):
            with patch("builtins.input", return_value=answer):
                value = website_app._prompt_yes_no("Enable logs", True)
            self.assertEqual(value, expected)
        return test

    setattr(
        GeneratedServiceMatrixTestCase,
        f"test_startup_yes_no_case_{index+1}_{_slug(str(answer) or 'blank')}",
        _make_yes_no_test(),
    )


for index, (answers, expected) in enumerate(STARTUP_PORT_CASES):
    def _make_port_test(answers=answers, expected=expected):
        def test(self):
            iterator = iter(answers)
            with patch("builtins.input", side_effect=lambda _prompt: next(iterator)):
                value = website_app._prompt_port(5000)
            self.assertEqual(value, expected)
        return test

    setattr(
        GeneratedServiceMatrixTestCase,
        f"test_startup_port_case_{index+1}_{expected}",
        _make_port_test(),
    )


for index, email in enumerate(LOWERCASE_EMAILS):
    def _make_lowercase_email_test(email=email):
        def test(self):
            with patch.object(self.file_storage, "get_user_roles", return_value={}), patch.object(
                self.file_storage,
                "save_json_file",
            ) as save_json_mock:
                self.file_storage.set_user_role(email, "admin")
            saved_roles = save_json_mock.call_args[0][1]
            self.assertIn(email.lower(), saved_roles)
            self.assertEqual(saved_roles[email.lower()], "admin")
        return test

    setattr(
        GeneratedServiceMatrixTestCase,
        f"test_file_storage_lowercases_email_case_{index+1}",
        _make_lowercase_email_test(),
    )


if __name__ == "__main__":
    unittest.main()
