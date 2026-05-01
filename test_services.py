import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, mock_open, patch

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
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.appointments_path = Path(self.tmpdir.name) / "appointments.txt"
        self.config = SimpleNamespace(property_appointments_file=self.appointments_path)
        self.service = AppointmentService(self.config)

    def test_load_returns_empty_when_file_missing(self):
        self.assertEqual(self.service.load(), {})

    def test_save_and_load_round_trip(self):
        self.service.save({"prop-1": {"2030-01-11", "2030-01-10"}, "prop-2": {"2030-02-01"}})
        loaded = self.service.load()
        self.assertEqual(loaded["prop-1"], {"2030-01-10", "2030-01-11"})
        self.assertEqual(loaded["prop-2"], {"2030-02-01"})
        # Dates should be persisted in sorted order on disk.
        on_disk = self.appointments_path.read_text(encoding="utf-8")
        self.assertIn("prop-1:2030-01-10,2030-01-11", on_disk)

    def test_save_is_atomic_no_temp_files_left_behind(self):
        self.service.save({"prop-1": {"2030-01-10"}})
        leftovers = [p for p in self.appointments_path.parent.iterdir() if p.name != "appointments.txt"]
        self.assertEqual(leftovers, [], f"Unexpected temp files: {leftovers}")

    def test_save_failure_preserves_existing_file(self):
        # First save establishes the original contents we want preserved.
        self.service.save({"prop-1": {"2030-01-10"}})
        original = self.appointments_path.read_text(encoding="utf-8")

        # Force the os.replace step to fail; the original file must survive
        # untouched and no leftover temp file may remain.
        with patch("somewheria_app.services.appointments.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.save({"prop-1": {"2099-12-31"}})

        self.assertEqual(self.appointments_path.read_text(encoding="utf-8"), original)
        leftovers = [p for p in self.appointments_path.parent.iterdir() if p.name != "appointments.txt"]
        self.assertEqual(leftovers, [], f"Temp file leaked after failed save: {leftovers}")

    def test_load_ignores_malformed_lines(self):
        self.appointments_path.write_text(
            "prop-1:2030-01-10,2030-01-11\nmalformed\nprop-2:2030-02-01\n",
            encoding="utf-8",
        )
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

    def test_fetch_property_record_returns_none_on_http_error(self):
        # Upstream returns valid JSON but with a 5xx status code. Without
        # raise_for_status() the error body would be passed through as a
        # property record.
        from requests import HTTPError

        details_response = Mock()
        details_response.raise_for_status.side_effect = HTTPError("500")
        details_response.json.return_value = {"error": "boom"}

        with patch(
            "somewheria_app.services.properties.requests.get",
            return_value=details_response,
        ):
            self.assertIsNone(self.service.fetch_property_record("prop-1"))

    def test_fetch_property_record_skips_non_dict_payload(self):
        details_response = Mock()
        details_response.raise_for_status.return_value = None
        details_response.json.return_value = ["not", "a", "dict"]

        with patch(
            "somewheria_app.services.properties.requests.get",
            return_value=details_response,
        ):
            self.assertIsNone(self.service.fetch_property_record("prop-1"))

    def test_get_base64_image_from_url_rejects_oversize_content_length(self):
        from somewheria_app.services.properties import MAX_IMAGE_BYTES

        oversize = MAX_IMAGE_BYTES + 1
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Length": str(oversize)}
        response.raise_for_status.return_value = None
        response.iter_content.return_value = iter([])

        with patch("somewheria_app.services.properties.requests.get", return_value=response):
            self.assertIsNone(self.service.get_base64_image_from_url("https://example.com/big.jpg"))

    def test_get_base64_image_from_url_rejects_oversize_streamed(self):
        from somewheria_app.services.properties import MAX_IMAGE_BYTES

        # No Content-Length header (chunked / unknown size); the cap must be
        # enforced while iterating chunks.
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.raise_for_status.return_value = None
        response.iter_content.return_value = iter([b"x" * (MAX_IMAGE_BYTES + 1)])

        with patch("somewheria_app.services.properties.requests.get", return_value=response):
            self.assertIsNone(self.service.get_base64_image_from_url("https://example.com/big.jpg"))


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


class PropertyWritePathTestCase(unittest.TestCase):
    def setUp(self):
        self.notifications = Mock()
        self.config = SimpleNamespace(
            api_base_url="https://api.example.com",
            upload_dir=Path(tempfile.gettempdir()),
        )
        self.service = PropertyService(self.config, self.notifications)

    def test_property_payload_from_form_includes_sqft(self):
        form = DummyForm(values={"name": "Maple", "sqft": "1200"})

        payload = self.service.property_payload_from_form(form)

        self.assertEqual(payload["sqft"], "1200")

    def test_update_property_forwards_sqft_to_upstream(self):
        current = {
            "id": "prop-1",
            "name": "Maple",
            "address": "123 Main",
            "rent": "1000",
            "deposit": "1000",
            "sqft": "900",
            "bedrooms": "2",
            "bathrooms": "1",
            "lease_length": "12 months",
            "pets_allowed": "No",
            "blurb": "Old",
            "description": "Old description",
        }
        form = DummyForm(values={"sqft": "1500"})

        with patch.object(self.service, "get_property", return_value=current), patch(
            "somewheria_app.services.properties.requests.put"
        ) as put_mock, patch.object(self.service, "trigger_background_refresh"):
            self.service.update_property("prop-1", form, "admin@example.com")

        self.assertEqual(put_mock.call_args.kwargs["json"]["sqft"], "1500")

    def test_update_property_raises_when_upstream_rejects(self):
        current = {"id": "prop-1", "name": "Maple"}
        form = DummyForm(values={"name": "Updated"})
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError("upstream 500")

        with patch.object(self.service, "get_property", return_value=current), patch(
            "somewheria_app.services.properties.requests.put",
            return_value=response,
        ), patch.object(self.service, "trigger_background_refresh") as trigger_mock:
            with self.assertRaises(RuntimeError):
                self.service.update_property("prop-1", form, "admin@example.com")

        # The upstream rejected, so we must NOT log the change or kick a refresh.
        self.notifications.log_site_change.assert_not_called()
        trigger_mock.assert_not_called()

    def test_toggle_sale_does_not_update_cache_when_upstream_fails(self):
        self.service.cache = [{"id": "prop-1", "for_sale": False, "status": "Active"}]
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError("upstream 500")

        with patch(
            "somewheria_app.services.properties.requests.put",
            return_value=response,
        ):
            with self.assertRaises(RuntimeError):
                self.service.toggle_sale("prop-1", "admin@example.com")

        self.assertFalse(self.service.cache[0]["for_sale"])
        self.assertEqual(self.service.cache[0]["status"], "Active")
        self.notifications.log_site_change.assert_not_called()

    def test_safe_json_returns_default_on_http_error_status(self):
        response = Mock()
        response.raise_for_status.side_effect = RuntimeError("404")
        with patch(
            "somewheria_app.services.properties.requests.get",
            return_value=response,
        ):
            payload = self.service._safe_json("https://example.com/data", ["fallback"])

        self.assertEqual(payload, ["fallback"])


class AnalyticsPruningTestCase(unittest.TestCase):
    def test_prune_drops_buckets_outside_window(self):
        from somewheria_app.services.analytics import AnalyticsTracker

        tracker = AnalyticsTracker(analytics_days=3)
        tracker.site_visits["2024-01-01"] = 5  # well outside the 3-day window
        tracker.unique_users["2024-01-01"] = {"old@example.com"}
        tracker.logins["2024-01-01"] = 1
        tracker.errors["2024-01-01"] = 2

        # Today happens to be 2026-05-01 in this test environment but the
        # prune logic uses whatever string we pass in, so this is independent
        # of wall-clock time.
        tracker._prune_old_buckets("2030-01-10")

        self.assertNotIn("2024-01-01", tracker.site_visits)
        self.assertNotIn("2024-01-01", tracker.unique_users)
        self.assertNotIn("2024-01-01", tracker.logins)
        self.assertNotIn("2024-01-01", tracker.errors)

    def test_prune_keeps_recent_days(self):
        from somewheria_app.services.analytics import AnalyticsTracker

        tracker = AnalyticsTracker(analytics_days=7)
        tracker.site_visits["2030-01-08"] = 4  # 2 days before "today"
        tracker.site_visits["2030-01-10"] = 1  # the test "today"

        tracker._prune_old_buckets("2030-01-10")

        self.assertEqual(tracker.site_visits["2030-01-08"], 4)
        self.assertEqual(tracker.site_visits["2030-01-10"], 1)


if __name__ == "__main__":
    unittest.main()
