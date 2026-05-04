import copy
import datetime
import importlib
import os
import unittest
from io import BytesIO
from unittest.mock import patch


os.environ["DISABLE_BACKGROUND_THREADS"] = "1"

website_app = importlib.import_module("website_app")


class ExpandedRouteCoverageTestCase(unittest.TestCase):
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

    def tearDown(self):
        with self.services.properties.cache_lock:
            self.services.properties.cache = self.original_cache
        self.services.config.google_client_id = self.original_google_client_id
        self.services.config.google_client_secret = self.original_google_client_secret

    def login_as(self, role="renter", email=None, name="Test User"):
        email = email or f"{role}@example.com"
        with self.client.session_transaction() as session:
            session["user"] = {
                "id": f"{role}-id",
                "email": email,
                "name": name,
                "role": role,
            }

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
        }
        property_data.update(overrides)
        with self.services.properties.cache_lock:
            self.services.properties.cache = [property_data]
        return property_data

    def test_login_page_loads(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Login", response.data)

    def test_login_post_redirects_to_manage_listings(self):
        response = self.client.post("/login", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/manage-listings", response.headers["Location"])

    def test_login_redirects_when_already_authenticated(self):
        self.login_as("renter")

        response = self.client.get("/login", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/manage-listings", response.headers["Location"])

    def test_logout_clears_session(self):
        self.login_as("renter")

        response = self.client.get("/logout", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/", response.headers["Location"])
        with self.client.session_transaction() as session:
            self.assertNotIn("user", session)

    def test_auth_status_returns_unauthenticated_payload(self):
        response = self.client.get("/auth/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"authenticated": False, "user": None})

    def test_auth_status_returns_authenticated_payload(self):
        self.login_as("renter", email="renter@example.com", name="Renter User")

        response = self.client.get("/auth/status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["user"]["email"], "renter@example.com")
        self.assertEqual(payload["user"]["name"], "Renter User")

    def test_for_rent_page_loads(self):
        self.seed_property()

        with patch.object(self.services.properties, "refresh_cache"):
            response = self.client.get("/for-rent")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Maple House", response.data)

    def test_manage_listings_loads_when_logged_in(self):
        self.login_as("admin", email="admin@example.com")
        self.seed_property()

        response = self.client.get("/manage-listings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Manage Listings", response.data)
        self.assertIn(b"123 Main St", response.data)

    def test_for_rent_json_returns_cached_properties(self):
        self.seed_property(included_amenities={"Parking", "Laundry"})

        with patch.object(self.services.properties, "refresh_cache"):
            response = self.client.get("/for-rent.json")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["name"], "Maple House")
        self.assertCountEqual(payload[0]["included_amenities"], ["Parking", "Laundry"])

    def test_for_rent_refresh_uses_anonymous_actor_when_logged_out(self):
        with patch.object(self.services.properties, "trigger_background_refresh") as trigger_mock:
            response = self.client.get("/for-rent-refresh.json")

        self.assertEqual(response.status_code, 200)
        trigger_mock.assert_called_once_with("anonymous")

    def test_for_rent_refresh_uses_logged_in_email(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "trigger_background_refresh") as trigger_mock:
            response = self.client.get("/for-rent-refresh.json")

        self.assertEqual(response.status_code, 200)
        trigger_mock.assert_called_once_with("admin@example.com")

    def test_property_details_renders_existing_property(self):
        self.seed_property()
        with patch.object(self.services.appointments, "load", return_value={"prop-1": {"2030-01-10"}}):
            response = self.client.get("/property/prop-1")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Maple House", response.data)
        self.assertIn(b"ADA Accessible", response.data)
        self.assertIn(b"Yes", response.data)

    def test_property_details_returns_404_when_property_missing(self):
        response = self.client.get("/property/missing-prop")

        self.assertEqual(response.status_code, 404)
        self.assertIn(b"Property not found", response.data)

    def test_schedule_appointment_rejects_invalid_date(self):
        response = self.client.post(
            "/property/prop-1/schedule",
            json={
                "name": "Alex",
                "date": "not-a-date",
                "contact_method": "email",
                "contact_info": "alex@example.com",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Invalid date.")

    def test_schedule_appointment_rejects_past_date(self):
        past_date = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

        response = self.client.post(
            "/property/prop-1/schedule",
            json={
                "name": "Alex",
                "date": past_date,
                "contact_method": "email",
                "contact_info": "alex@example.com",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "Date cannot be in the past.")

    def test_schedule_appointment_returns_404_when_property_is_missing(self):
        future_date = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        with patch.object(self.services.properties, "fetch_live_property_name", return_value=None):
            response = self.client.post(
                "/property/missing-prop/schedule",
                json={
                    "name": "Alex",
                    "date": future_date,
                    "contact_method": "email",
                    "contact_info": "alex@example.com",
                },
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Property not found.")

    def test_schedule_appointment_success_sends_email(self):
        future_date = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
        with patch.object(self.services.properties, "fetch_live_property_name", return_value="Maple House"), patch.object(
            self.services.notifications,
            "send_email",
        ) as send_email_mock:
            response = self.client.post(
                "/property/prop-1/schedule",
                json={
                    "name": "Alex",
                    "date": future_date,
                    "contact_method": "email",
                    "contact_info": "alex@example.com",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True})
        send_email_mock.assert_called_once()
        self.assertIn("Viewing Appointment Request", send_email_mock.call_args[0][0])
        self.assertIn("Maple House", send_email_mock.call_args[0][1])

    def test_about_page_loads(self):
        response = self.client.get("/about")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"About", response.data)

    def test_contact_page_loads(self):
        response = self.client.get("/contact")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Contact", response.data)

    def test_logs_page_loads(self):
        self.login_as("high_admin")
        with patch.object(self.services.notifications, "read_logs", return_value=[]):
            response = self.client.get("/logs")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Logger", response.data)

    def test_report_issue_form_loads(self):
        response = self.client.get("/report-issue")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Report", response.data)

    def test_report_issue_complete_loads_confirmation_page(self):
        response = self.client.get("/report-issue-complete")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Report", response.data)

    def test_report_issue_requires_name_and_description(self):
        response = self.client.post("/report-issue", data={"name": "", "description": ""})

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Name and description are required fields.", response.data)

    def test_report_issue_sends_email_and_renders_confirmation(self):
        with patch.object(self.services.notifications, "send_email") as send_email_mock:
            response = self.client.post(
                "/report-issue",
                data={"name": "Jamie", "description": "Broken contact form"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Jamie", response.data)
        send_email_mock.assert_called_once()
        self.assertIn("User Reported Issue", send_email_mock.call_args[0][0])

    def test_register_page_loads(self):
        response = self.client.get("/register")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Register", response.data)

    def test_register_requires_name_and_email(self):
        response = self.client.post("/register", data={"name": "", "email": ""})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Name and a valid email are required.", response.data)

    def test_register_saves_pending_registration_and_sends_email(self):
        with patch.object(self.services.storage, "get_pending_registrations", return_value=[]), patch.object(
            self.services.storage,
            "add_pending_registration",
        ) as add_pending_mock, patch.object(
            self.services.notifications,
            "send_email",
        ) as send_email_mock:
            response = self.client.post(
                "/register",
                data={"name": "Jamie", "email": "jamie@example.com", "reason": "Need access"},
            )

        self.assertEqual(response.status_code, 200)
        add_pending_mock.assert_called_once_with(
            {"name": "Jamie", "email": "jamie@example.com", "reason": "Need access"}
        )
        send_email_mock.assert_called_once()

    def test_admin_registrations_page_loads_for_admin(self):
        self.login_as("admin")
        with patch.object(self.services.storage, "get_pending_registrations", return_value=[]):
            response = self.client.get("/admin/registrations")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Pending", response.data)

    def test_admin_registrations_requires_email_on_post(self):
        self.login_as("admin")
        with patch.object(self.services.storage, "get_pending_registrations", return_value=[]), patch.object(
            self.services.storage,
            "set_user_role",
        ) as set_role_mock, patch.object(self.services.storage, "remove_pending_registration") as remove_pending_mock, patch.object(
            self.services.notifications,
            "send_email",
        ) as send_email_mock:
            response = self.client.post("/admin/registrations", data={"action": "approve", "email": ""})

        self.assertEqual(response.status_code, 200)
        set_role_mock.assert_not_called()
        remove_pending_mock.assert_not_called()
        send_email_mock.assert_not_called()

    def test_admin_registrations_rejects_invalid_action(self):
        self.login_as("admin")
        with patch.object(
            self.services.storage,
            "get_pending_registrations",
            return_value=[{"email": "pending@example.com", "name": "Pending"}],
        ), patch.object(self.services.storage, "set_user_role") as set_role_mock, patch.object(
            self.services.storage,
            "remove_pending_registration",
        ) as remove_pending_mock, patch.object(self.services.notifications, "send_email") as send_email_mock:
            response = self.client.post(
                "/admin/registrations",
                data={"action": "wat", "email": "pending@example.com"},
            )

        self.assertEqual(response.status_code, 200)
        set_role_mock.assert_not_called()
        remove_pending_mock.assert_not_called()
        send_email_mock.assert_not_called()

    def test_admin_registrations_approve_calls_storage_and_email(self):
        self.login_as("admin")
        with patch.object(
            self.services.storage,
            "get_pending_registrations",
            side_effect=[
                [{"email": "pending@example.com", "name": "Pending"}],
                [],
            ],
        ), patch.object(self.services.storage, "set_user_role") as set_role_mock, patch.object(
            self.services.storage,
            "remove_pending_registration",
        ) as remove_pending_mock, patch.object(self.services.notifications, "send_email") as send_email_mock:
            response = self.client.post(
                "/admin/registrations",
                data={"action": "approve", "email": "pending@example.com"},
            )

        self.assertEqual(response.status_code, 200)
        set_role_mock.assert_called_once_with("pending@example.com", "renter")
        remove_pending_mock.assert_called_once_with("pending@example.com")
        send_email_mock.assert_called_once()

    def test_admin_registrations_reject_calls_storage_and_email(self):
        self.login_as("admin")
        with patch.object(
            self.services.storage,
            "get_pending_registrations",
            side_effect=[
                [{"email": "pending@example.com", "name": "Pending"}],
                [],
            ],
        ), patch.object(self.services.storage, "remove_pending_registration") as remove_pending_mock, patch.object(
            self.services.notifications,
            "send_email",
        ) as send_email_mock:
            response = self.client.post(
                "/admin/registrations",
                data={"action": "reject", "email": "pending@example.com"},
            )

        self.assertEqual(response.status_code, 200)
        remove_pending_mock.assert_called_once_with("pending@example.com")
        send_email_mock.assert_called_once()

    def test_admin_users_page_loads(self):
        self.login_as("admin")
        with patch.object(self.services.storage, "get_user_roles", return_value={"admin@example.com": "admin"}):
            response = self.client.get("/admin/users")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"User Management", response.data)

    def test_admin_users_requires_email_on_post(self):
        self.login_as("admin")
        with patch.object(self.services.storage, "get_user_roles", return_value={}):
            response = self.client.post("/admin/users", data={"email": "", "role": "renter", "action": "update"})

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No email provided.", response.data)

    def test_admin_users_delete_missing_user_shows_error(self):
        self.login_as("admin")
        with patch.object(self.services.storage, "delete_user_role", return_value=False), patch.object(
            self.services.storage,
            "get_user_roles",
            return_value={},
        ):
            response = self.client.post(
                "/admin/users",
                data={"email": "missing@example.com", "action": "delete"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"User not found.", response.data)

    def test_admin_users_update_role_succeeds(self):
        self.login_as("admin")
        with patch.object(self.services.storage, "set_user_role") as set_user_role_mock, patch.object(
            self.services.storage,
            "get_user_roles",
            return_value={"user@example.com": "renter"},
        ):
            response = self.client.post(
                "/admin/users",
                data={"email": "user@example.com", "role": "renter", "action": "update"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"updated to renter", response.data)
        set_user_role_mock.assert_called_once_with("user@example.com", "renter")

    def test_admin_dashboard_forbids_standard_admin(self):
        self.login_as("admin")

        response = self.client.get("/admin/dashboard")

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Front Door Locked", response.data)

    def test_admin_dashboard_loads_for_high_admin(self):
        self.login_as("high_admin", email="owner@example.com")
        with patch.object(self.services.analytics, "dashboard_data", return_value=({"visits": 10}, {"labels": []})), patch.object(
            self.services.storage,
            "get_user_roles",
            return_value={"owner@example.com": "high_admin"},
        ):
            response = self.client.get("/admin/dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Admin Dashboard", response.data)

    def test_admin_dashboard_adds_user_for_high_admin(self):
        self.login_as("high_admin", email="owner@example.com")
        with patch.object(self.services.analytics, "dashboard_data", return_value=({"visits": 10}, {"labels": []})), patch.object(
            self.services.storage,
            "get_user_roles",
            side_effect=[{}, {"new@example.com": "admin"}],
        ), patch.object(self.services.storage, "set_user_role") as set_user_role_mock, patch.object(
            self.services.notifications,
            "log_site_change",
        ) as log_site_change_mock:
            response = self.client.post(
                "/admin/dashboard",
                data={"action": "add", "email": "new@example.com", "role": "admin"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"added as admin", response.data)
        set_user_role_mock.assert_called_once_with("new@example.com", "admin")
        log_site_change_mock.assert_called_once()

    def test_renter_dashboard_loads_for_renter(self):
        self.login_as("renter", email="renter@example.com")
        with patch.object(
            self.services.storage,
            "get_renter_contracts",
            return_value={"renter@example.com": [{"property_name": "Maple House"}]},
        ):
            response = self.client.get("/renter-dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Maple House", response.data)

    def test_renter_profile_loads_existing_profile(self):
        self.login_as("renter", email="renter@example.com")
        with patch.object(
            self.services.storage,
            "get_renter_profiles",
            return_value={"renter@example.com": {"name": "Jamie", "contact": "555-0100"}},
        ):
            response = self.client.get("/renter/profile")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Jamie", response.data)

    def test_renter_profile_post_saves_profile(self):
        self.login_as("renter", email="renter@example.com")
        with patch.object(self.services.storage, "get_renter_profiles", return_value={}), patch.object(
            self.services.storage,
            "save_renter_profiles",
        ) as save_profiles_mock:
            response = self.client.post(
                "/renter/profile",
                data={"name": "Jamie", "contact": "555-0100"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Profile updated.", response.data)
        # The profile now carries a ticket-email preference; an unchecked box
        # submits nothing, which the route interprets as False.
        save_profiles_mock.assert_called_once_with(
            {"renter@example.com": {"name": "Jamie", "contact": "555-0100", "email_status_updates": False}}
        )

    def test_renter_profile_post_honors_email_status_updates_checkbox(self):
        self.login_as("renter", email="renter@example.com")
        with patch.object(self.services.storage, "get_renter_profiles", return_value={}), patch.object(
            self.services.storage,
            "save_renter_profiles",
        ) as save_profiles_mock:
            response = self.client.post(
                "/renter/profile",
                data={"name": "Jamie", "contact": "555-0100", "email_status_updates": "1"},
            )

        self.assertEqual(response.status_code, 200)
        save_profiles_mock.assert_called_once_with(
            {"renter@example.com": {"name": "Jamie", "contact": "555-0100", "email_status_updates": True}}
        )

    def test_admin_contracts_add_requires_all_fields(self):
        self.login_as("admin")
        with patch.object(self.services.storage, "get_renter_contracts", return_value={}):
            response = self.client.post(
                "/admin/contracts",
                data={"action": "add", "renter_email": "", "property_name": "", "start_date": "", "end_date": ""},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"All fields are required.", response.data)

    def test_admin_contracts_add_successfully_saves(self):
        self.login_as("admin")
        with patch.object(self.services.storage, "get_renter_contracts", return_value={}), patch.object(
            self.services.storage,
            "save_renter_contracts",
        ) as save_contracts_mock:
            response = self.client.post(
                "/admin/contracts",
                data={
                    "action": "add",
                    "renter_email": "renter@example.com",
                    "property_name": "Maple House",
                    "start_date": "2030-01-01",
                    "end_date": "2030-12-31",
                    "status": "Active",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Contract added for renter@example.com.", response.data)
        save_contracts_mock.assert_called_once()

    def test_admin_contracts_delete_rejects_invalid_index(self):
        self.login_as("admin")
        with patch.object(
            self.services.storage,
            "get_renter_contracts",
            return_value={"renter@example.com": [{"property_name": "Maple House"}]},
        ):
            response = self.client.post(
                "/admin/contracts",
                data={"action": "delete", "renter_email": "renter@example.com", "contract_index": "abc"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Invalid contract index.", response.data)

    def test_admin_contracts_delete_successfully_saves(self):
        self.login_as("admin")
        contracts = {"renter@example.com": [{"property_name": "Maple House"}]}
        with patch.object(self.services.storage, "get_renter_contracts", return_value=contracts), patch.object(
            self.services.storage,
            "save_renter_contracts",
        ) as save_contracts_mock:
            response = self.client.post(
                "/admin/contracts",
                data={"action": "delete", "renter_email": "renter@example.com", "contract_index": "0"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Contract removed for renter@example.com.", response.data)
        save_contracts_mock.assert_called_once_with({})

    def test_analytics_dashboard_forbids_standard_admin(self):
        self.login_as("admin")

        response = self.client.get("/admin/analytics")

        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Front Door Locked", response.data)

    def test_analytics_dashboard_loads_for_high_admin(self):
        self.login_as("high_admin")
        with patch.object(self.services.analytics, "dashboard_data", return_value=({"visits": 10}, {"labels": []})):
            response = self.client.get("/admin/analytics")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Site Analytics", response.data)

    def test_add_listing_loads_for_admin(self):
        self.login_as("admin")

        response = self.client.get("/add-listing")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Edit your listing", response.data)

    def test_edit_listing_returns_404_when_missing(self):
        self.login_as("admin")

        response = self.client.get("/edit-listing/missing")

        self.assertEqual(response.status_code, 404)
        self.assertIn(b"Property not found", response.data)

    def test_edit_listing_loads_existing_property_for_admin(self):
        self.login_as("admin")
        self.seed_property()

        response = self.client.get("/edit-listing/prop-1")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"123 Main St", response.data)

    def test_save_edit_creates_new_property_and_redirects(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "create_property") as create_property_mock:
            response = self.client.post("/save-edit/new", data={"name": "Maple House"}, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/manage-listings", response.headers["Location"])
        create_property_mock.assert_called_once()

    def test_save_edit_updates_existing_property_and_redirects(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "update_property") as update_property_mock:
            response = self.client.post("/save-edit/prop-1", data={"name": "Maple House"}, follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/manage-listings", response.headers["Location"])
        update_property_mock.assert_called_once()

    def test_save_edit_returns_404_when_property_update_is_missing(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "update_property", side_effect=KeyError("Property not found")):
            response = self.client.post("/save-edit/prop-1", data={"name": "Maple House"})

        self.assertEqual(response.status_code, 404)
        self.assertIn(b"Property not found", response.data)

    def test_delete_listing_redirects_when_successful(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "delete_property") as delete_property_mock:
            response = self.client.post("/delete-listing/prop-1", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/manage-listings", response.headers["Location"])
        delete_property_mock.assert_called_once_with("prop-1", "admin@example.com")

    def test_upload_image_requires_file_part(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.notifications, "log_and_notify_error") as notify_mock:
            response = self.client.post("/upload-image/prop-1", data={}, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])
        notify_mock.assert_called_once()

    def test_upload_image_requires_selected_filename(self):
        self.login_as("admin", email="admin@example.com")
        data = {"file": (BytesIO(b""), "")}
        with patch.object(self.services.notifications, "log_and_notify_error") as notify_mock:
            response = self.client.post("/upload-image/prop-1", data=data, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["success"])
        notify_mock.assert_called_once()

    def test_image_edit_notify_success(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.notifications, "notify_image_edit") as notify_mock:
            response = self.client.post("/image-edit-notify", json={"images": ["https://example.com/a.jpg"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["message"], "Notification sent.")
        notify_mock.assert_called_once_with(["(See admin console for details.)"])

    def test_image_edit_notify_handles_failure(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(
            self.services.notifications,
            "notify_image_edit",
            side_effect=RuntimeError("boom"),
        ), patch.object(self.services.notifications, "log_and_notify_error") as log_error_mock:
            response = self.client.post("/image-edit-notify", json={"images": ["https://example.com/a.jpg"]})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["message"], "Failed to send notification.")
        log_error_mock.assert_called_once()

    def test_toggle_sale_returns_404_for_missing_property(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "toggle_sale", side_effect=KeyError("Property not found")):
            response = self.client.post("/toggle-sale/prop-1")

        self.assertEqual(response.status_code, 404)
        self.assertIn(b"Property not found", response.data)

    def test_toggle_sale_redirects_when_successful(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "toggle_sale") as toggle_sale_mock:
            response = self.client.post("/toggle-sale/prop-1", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/manage-listings", response.headers["Location"])
        toggle_sale_mock.assert_called_once_with("prop-1", "admin@example.com")

    def test_google_callback_shows_oauth_error_screen_when_not_configured(self):
        self.services.config.google_client_id = ""
        self.services.config.google_client_secret = ""

        response = self.client.get("/google/callback")

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Google Sign-In", response.data)

    def test_google_login_redirects_when_oauth_is_configured(self):
        self.services.config.google_client_id = "client-id"
        self.services.config.google_client_secret = "client-secret"
        flow_mock = type("FlowMock", (), {})()
        flow_mock.redirect_uri = None
        flow_mock.authorization_url = lambda **kwargs: ("https://accounts.google.com/o/oauth2/auth?mock=1", "state-123")

        with patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow_mock):
            response = self.client.get("/google/login", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("https://accounts.google.com/o/oauth2/auth?mock=1", response.headers["Location"])
        with self.client.session_transaction() as session:
            self.assertEqual(session["oauth_state"], "state-123")

    def test_offline_page_loads(self):
        response = self.client.get("/offline")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Offline", response.data)

    def test_manifest_json_is_served(self):
        response = self.client.get("/manifest.json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/manifest+json", response.content_type)

    def test_service_worker_is_served_with_no_cache_header(self):
        response = self.client.get("/service-worker.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Cache-Control"), "no-cache")


if __name__ == "__main__":
    unittest.main()
