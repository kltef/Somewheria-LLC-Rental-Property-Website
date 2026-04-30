import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

from somewheria_app.services.appointments import AppointmentService
from somewheria_app.services.auth import AuthService
from somewheria_app.services.notifications import NotificationService
from somewheria_app.services.properties import PropertyService
from somewheria_app.services.storage import FileStorageService


class DummyForm:
    def __init__(self, values=None, lists=None):
        self.values = values or {}
        self.lists = lists or {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def getlist(self, key):
        return list(self.lists.get(key, []))


class AuthServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.storage = Mock()
        self.config = SimpleNamespace(
            authorized_users={"renter@example.com"},
            admin_users={"admin@example.com"},
            high_admin_users={"owner@example.com"},
        )
        self.service = AuthService(self.config, self.storage)

    def test_whitelist_configured_returns_true_when_authorized_users_exist(self):
        self.assertTrue(self.service.whitelist_configured())

    def test_get_user_role_prefers_storage_role(self):
        self.storage.get_user_roles.return_value = {"user@example.com": "admin"}

        role = self.service.get_user_role("user@example.com")

        self.assertEqual(role, "admin")

    def test_get_user_role_uses_high_admin_config(self):
        self.storage.get_user_roles.return_value = {}

        role = self.service.get_user_role("owner@example.com")

        self.assertEqual(role, "high_admin")

    def test_get_user_role_uses_admin_config(self):
        self.storage.get_user_roles.return_value = {}

        role = self.service.get_user_role("admin@example.com")

        self.assertEqual(role, "admin")

    def test_get_user_role_uses_authorized_users_as_renter(self):
        self.storage.get_user_roles.return_value = {}

        role = self.service.get_user_role("renter@example.com")

        self.assertEqual(role, "renter")

    def test_get_user_role_defaults_to_guest(self):
        self.storage.get_user_roles.return_value = {}

        role = self.service.get_user_role("guest@example.com")

        self.assertEqual(role, "guest")


class FileStorageServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            registration_file=Path("registrations.json"),
            user_roles_file=Path("roles.json"),
            renter_profile_file=Path("profiles.json"),
            contracts_file=Path("contracts.json"),
        )
        self.service = FileStorageService(self.config)

    def test_load_json_file_returns_default_when_file_is_missing(self):
        with patch.object(Path, "exists", return_value=False):
            loaded = self.service.load_json_file(self.config.registration_file, [])

        self.assertEqual(loaded, [])

    def test_load_json_file_reads_existing_json(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path,
            "open",
            mock_open(read_data='{"admin@example.com": "admin"}'),
        ):
            loaded = self.service.load_json_file(self.config.user_roles_file, {})

        self.assertEqual(loaded, {"admin@example.com": "admin"})

    def test_add_pending_registration_appends_and_saves(self):
        with patch.object(self.service, "get_pending_registrations", return_value=[{"email": "keep@example.com"}]), patch.object(
            self.service,
            "save_json_file",
        ) as save_json_mock:
            self.service.add_pending_registration({"email": "new@example.com", "name": "New User"})

        save_json_mock.assert_called_once_with(
            self.config.registration_file,
            [{"email": "keep@example.com"}, {"email": "new@example.com", "name": "New User"}],
        )

    def test_remove_pending_registration_deletes_matching_entry(self):
        with patch.object(
            self.service,
            "get_pending_registrations",
            return_value=[{"email": "keep@example.com"}, {"email": "drop@example.com"}],
        ), patch.object(self.service, "save_json_file") as save_json_mock:
            self.service.remove_pending_registration("drop@example.com")

        save_json_mock.assert_called_once_with(
            self.config.registration_file,
            [{"email": "keep@example.com"}],
        )

    def test_set_user_role_lowercases_email_and_saves(self):
        with patch.object(self.service, "get_user_roles", return_value={}), patch.object(
            self.service,
            "save_json_file",
        ) as save_json_mock:
            self.service.set_user_role("Admin@Example.com", "admin")

        save_json_mock.assert_called_once_with(
            self.config.user_roles_file,
            {"admin@example.com": "admin"},
        )

    def test_delete_user_role_returns_true_when_removed(self):
        with patch.object(self.service, "get_user_roles", return_value={"admin@example.com": "admin"}), patch.object(
            self.service,
            "save_json_file",
        ) as save_json_mock:
            removed = self.service.delete_user_role("admin@example.com")

        self.assertTrue(removed)
        save_json_mock.assert_called_once_with(self.config.user_roles_file, {"admin@example.com": "revoked"})

    def test_delete_user_role_returns_false_when_missing(self):
        with patch.object(self.service, "get_user_roles", return_value={}), patch.object(
            self.service,
            "save_json_file",
        ) as save_json_mock:
            removed = self.service.delete_user_role("missing@example.com")

        self.assertFalse(removed)
        save_json_mock.assert_called_once_with(self.config.user_roles_file, {"missing@example.com": "revoked"})

    def test_save_renter_profiles_delegates_to_save_json_file(self):
        profiles = {"renter@example.com": {"name": "Jamie"}}
        with patch.object(self.service, "save_json_file") as save_json_mock:
            self.service.save_renter_profiles(profiles)

        save_json_mock.assert_called_once_with(self.config.renter_profile_file, profiles)

    def test_save_renter_contracts_delegates_to_save_json_file(self):
        contracts = {"renter@example.com": [{"property_name": "Maple House"}]}
        with patch.object(self.service, "save_json_file") as save_json_mock:
            self.service.save_renter_contracts(contracts)

        save_json_mock.assert_called_once_with(self.config.contracts_file, contracts)


class AppointmentServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(property_appointments_file=Path("appointments.txt"))
        self.service = AppointmentService(self.config)

    def test_load_returns_empty_when_file_missing(self):
        with patch.object(Path, "exists", return_value=False):
            self.assertEqual(self.service.load(), {})

    def test_save_and_load_round_trip(self):
        handle = mock_open()
        with patch.object(Path, "open", handle), patch.object(
            self.service,
            "print_check_file",
        ) as print_check_mock:
            self.service.save({"prop-1": {"2030-01-11", "2030-01-10"}})

        written = "".join(call.args[0] for call in handle().write.call_args_list)
        self.assertIn("prop-1:2030-01-10,2030-01-11", written)
        print_check_mock.assert_called_once()

    def test_load_ignores_malformed_lines(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path,
            "open",
            mock_open(read_data="prop-1:2030-01-10,2030-01-11\nmalformed\nprop-2:2030-02-01\n"),
        ):
            loaded = self.service.load()

        self.assertEqual(loaded["prop-1"], {"2030-01-10", "2030-01-11"})
        self.assertEqual(loaded["prop-2"], {"2030-02-01"})


class PropertyServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.notifications = Mock()
        self.config = SimpleNamespace(
            api_base_url="https://api.example.com",
            upload_dir=Path(tempfile.gettempdir()),
        )
        self.service = PropertyService(self.config, self.notifications)

    def test_serialize_properties_converts_sets_to_lists(self):
        serialized = self.service.serialize_properties(
            [{"id": "prop-1", "amenities": {"Parking", "Laundry"}, "status": "Active"}]
        )

        self.assertCountEqual(serialized[0]["amenities"], ["Parking", "Laundry"])

    def test_normalize_property_applies_defaults(self):
        normalized = self.service.normalize_property({"name": "Maple House"}, "prop-1")

        self.assertEqual(normalized["id"], "prop-1")
        self.assertEqual(normalized["bedrooms"], "N/A")
        self.assertEqual(normalized["pets_allowed"], "Unknown")
        self.assertEqual(normalized["ada_accessible"], "Unknown")

    def test_normalize_property_converts_boolean_flags(self):
        normalized = self.service.normalize_property(
            {"name": "Maple House", "pets_allowed": True, "ada_accessible": False},
            "prop-1",
        )

        self.assertEqual(normalized["pets_allowed"], "Yes")
        self.assertEqual(normalized["ada_accessible"], "No")

    def test_normalize_property_converts_string_accessibility_flags(self):
        normalized = self.service.normalize_property({"accessible": "yes"}, "prop-1")

        self.assertEqual(normalized["ada_accessible"], "Yes")

    def test_normalize_property_rejects_unknown_accessibility_values(self):
        normalized = self.service.normalize_property({"accessible": "maybe"}, "prop-1")

        self.assertEqual(normalized["ada_accessible"], "Unknown")

    def test_normalize_property_uses_photo_as_thumbnail_when_missing(self):
        normalized = self.service.normalize_property({"photos": ["photo-1.jpg"]}, "prop-1")

        self.assertEqual(normalized["thumbnail"], "photo-1.jpg")

    def test_property_payload_from_form_merges_custom_amenities(self):
        form = DummyForm(
            values={
                "name": "Maple House",
                "pets_allowed": "Yes",
                "custom_amenities": "Garden, Storage ",
            },
            lists={"amenities": ["Parking", "Laundry"]},
        )

        payload = self.service.property_payload_from_form(form)

        self.assertTrue(payload["pets_allowed"])
        self.assertEqual(payload["included_amenities"], ["Parking", "Laundry", "Garden", "Storage"])

    def test_delete_property_updates_cache_and_logs_change(self):
        self.service.cache = [{"id": "prop-1"}, {"id": "prop-2"}]
        response = Mock(status_code=204, text="")
        with patch("somewheria_app.services.properties.requests.delete", return_value=response) as delete_mock:
            self.service.delete_property("prop-1", "admin@example.com")

        self.assertEqual(self.service.cache, [{"id": "prop-2"}])
        delete_mock.assert_called_once()
        self.notifications.log_site_change.assert_called_once_with(
            "admin@example.com",
            "property_deleted",
            {"property_id": "prop-1"},
        )

    def test_delete_property_raises_on_remote_error(self):
        self.service.cache = [{"id": "prop-1"}]
        response = Mock(status_code=500, text="boom")
        with patch("somewheria_app.services.properties.requests.delete", return_value=response):
            with self.assertRaises(RuntimeError):
                self.service.delete_property("prop-1", "admin@example.com")

    def test_toggle_sale_updates_cache_and_status(self):
        self.service.cache = [{"id": "prop-1", "for_sale": False, "status": "Active"}]
        with patch("somewheria_app.services.properties.requests.put") as put_mock:
            self.service.toggle_sale("prop-1", "admin@example.com")

        self.assertTrue(self.service.cache[0]["for_sale"])
        self.assertEqual(self.service.cache[0]["status"], "For Sale")
        put_mock.assert_called_once()
        self.notifications.log_site_change.assert_called_once_with(
            "admin@example.com",
            "property_toggle_sale",
            {"property_id": "prop-1", "for_sale": True},
        )

    def test_toggle_sale_raises_when_property_missing(self):
        self.service.cache = []

        with self.assertRaises(KeyError):
            self.service.toggle_sale("missing", "admin@example.com")

    def test_fetch_live_property_name_returns_name(self):
        response = Mock()
        response.json.return_value = {"name": "Maple House"}
        with patch("somewheria_app.services.properties.requests.get", return_value=response):
            name = self.service.fetch_live_property_name("prop-1")

        self.assertEqual(name, "Maple House")

    def test_fetch_live_property_name_returns_none_on_error(self):
        with patch("somewheria_app.services.properties.requests.get", side_effect=RuntimeError("boom")):
            name = self.service.fetch_live_property_name("prop-1")

        self.assertIsNone(name)


class NotificationServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.analytics = Mock()
        self.config = SimpleNamespace(
            email_sender="sender@example.com",
            email_recipient="recipient@example.com",
            log_file=Path("application.log"),
            change_log_file=Path("site_changes.log"),
        )
        self.service = NotificationService(self.config, self.analytics)

    def test_send_email_returns_false_when_password_missing(self):
        with patch.object(self.service, "_email_password", return_value=""):
            result = self.service.send_email("Test Subject", "Hello world")

        self.assertFalse(result)

    def test_send_email_returns_true_on_success(self):
        smtp_instance = Mock()
        smtp_context = Mock()
        smtp_context.__enter__ = Mock(return_value=smtp_instance)
        smtp_context.__exit__ = Mock(return_value=None)

        with patch.object(self.service, "_email_password", return_value="app-pass"), patch(
            "somewheria_app.services.notifications.smtplib.SMTP", return_value=smtp_context
        ):
            result = self.service.send_email("Test Subject", "Hello world")

        self.assertTrue(result)
        smtp_instance.starttls.assert_called_once()
        smtp_instance.login.assert_called_once_with("sender@example.com", "app-pass")
        smtp_instance.send_message.assert_called_once()
        sent_message = smtp_instance.send_message.call_args.args[0]
        self.assertTrue(sent_message.is_multipart())
        html_part = sent_message.get_body(preferencelist=("html",))
        text_part = sent_message.get_body(preferencelist=("plain",))
        self.assertIsNotNone(html_part)
        self.assertIsNotNone(text_part)
        self.assertIn("Somewheria LLC", html_part.get_content())

    def test_send_email_returns_false_on_smtp_failure(self):
        smtp_instance = Mock()
        smtp_instance.send_message.side_effect = RuntimeError("smtp boom")
        smtp_context = Mock()
        smtp_context.__enter__ = Mock(return_value=smtp_instance)
        smtp_context.__exit__ = Mock(return_value=None)

        with patch.object(self.service, "_email_password", return_value="app-pass"), patch(
            "somewheria_app.services.notifications.smtplib.SMTP", return_value=smtp_context
        ):
            result = self.service.send_email("Test Subject", "Hello world")

        self.assertFalse(result)

    def test_html_email_body_formats_subject_and_lines(self):
        html_body = self.service._html_email_body(
            "Image Edited Notification",
            "The following image(s) have been edited:\nhttps://example.com/a.jpg",
        )

        self.assertIn("Image Edited Notification", html_body)
        self.assertIn("Somewheria LLC", html_body)
        self.assertIn("https://example.com/a.jpg", html_body)

    def test_log_and_notify_error_records_error_and_sends_email(self):
        with patch.object(self.service, "send_email") as send_email_mock:
            self.service.log_and_notify_error("Save Error", "Something broke")

        self.analytics.record_error.assert_called_once()
        send_email_mock.assert_called_once_with("Save Error", "Something broke")

    def test_notify_image_edit_sends_summary_email(self):
        with patch.object(self.service, "send_email") as send_email_mock:
            self.service.notify_image_edit(["https://example.com/a.jpg", "https://example.com/b.jpg"])

        send_email_mock.assert_called_once()
        self.assertEqual(send_email_mock.call_args[0][0], "Image Edited Notification")
        self.assertIn("https://example.com/a.jpg", send_email_mock.call_args[0][1])

    def test_log_site_change_writes_json_line(self):
        handle = mock_open()
        with patch.object(Path, "open", handle):
            self.service.log_site_change("admin@example.com", "property_updated", {"property_id": "prop-1"})

        written = "".join(call.args[0] for call in handle().write.call_args_list)
        self.assertIn('"user": "admin@example.com"', written)
        self.assertIn('"action": "property_updated"', written)
        self.assertIn('"property_id": "prop-1"', written)

    def test_read_logs_returns_empty_list_when_log_file_missing(self):
        with patch.object(Path, "exists", return_value=False):
            entries = self.service.read_logs()

        self.assertEqual(entries, [])

    def test_read_logs_parses_pipe_and_legacy_formats(self):
        log_text = (
            "2026-03-23 18:47:42|INFO|http|GET /admin/status -> 200 in 23.55ms\n"
            "2026-03-23:WARN:Legacy warning line\n"
        )
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path,
            "open",
            mock_open(read_data=log_text),
        ):
            entries = self.service.read_logs()

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["level"], "WARNING")
        self.assertIn("Legacy warning line", entries[0]["message"])
        self.assertEqual(entries[1]["level"], "INFO")
        self.assertIn("[http] GET /admin/status -> 200", entries[1]["message"])


if __name__ == "__main__":
    unittest.main()
