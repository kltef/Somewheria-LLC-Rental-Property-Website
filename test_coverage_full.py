import base64
import importlib
import io
import os
import runpy
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, mock_open, patch

from flask import Flask, Response, abort
from PIL import Image

from somewheria_app import create_app
from somewheria_app.services.analytics import AnalyticsTracker
from somewheria_app.services.appointments import AppointmentService
from somewheria_app.services.auth import AuthService, auth_status_payload, renter_required
from somewheria_app.services.console import set_console_log_level
from somewheria_app.services.notifications import NotificationService
from somewheria_app.services.properties import PropertyService
from somewheria_app.services.storage import FileStorageService


os.environ["DISABLE_BACKGROUND_THREADS"] = "1"

website_app = importlib.import_module("website_app")


class CoveragePropertyServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.notifications = Mock()
        self.upload_dir = Path(os.getcwd()) / "static" / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.config = SimpleNamespace(
            api_base_url="https://api.example.com",
            upload_dir=self.upload_dir,
            cache_refresh_interval=5,
        )
        self.service = PropertyService(self.config, self.notifications)

    def tearDown(self):
        for filename in ("prop-1_abc123.png", "prop-1_bad.png", "prop-1_assoc.png"):
            file_path = self.upload_dir / filename
            if file_path.exists():
                file_path.unlink()

    def test_start_background_refresh_starts_thread_once(self):
        thread_mock = Mock()
        thread_mock.is_alive.return_value = False

        with patch("somewheria_app.services.properties.threading.Thread", return_value=thread_mock) as thread_ctor:
            self.service.start_background_refresh()

        thread_ctor.assert_called_once()
        thread_mock.start.assert_called_once()
        self.assertIs(self.service.refresh_thread, thread_mock)

    def test_start_background_refresh_skips_when_existing_thread_is_alive(self):
        thread_mock = Mock()
        thread_mock.is_alive.return_value = True
        self.service.refresh_thread = thread_mock

        with patch("somewheria_app.services.properties.threading.Thread") as thread_ctor:
            self.service.start_background_refresh()

        thread_ctor.assert_not_called()

    def test_periodic_refresh_runs_refresh_then_sleeps(self):
        with patch.object(self.service, "refresh_cache") as refresh_cache_mock, patch(
            "somewheria_app.services.properties.time.sleep",
            side_effect=StopIteration,
        ) as sleep_mock:
            with self.assertRaises(StopIteration):
                self.service._periodic_refresh()

        refresh_cache_mock.assert_called_once()
        sleep_mock.assert_called_once_with(5)

    def test_periodic_refresh_logs_failures(self):
        with patch.object(self.service, "refresh_cache", side_effect=RuntimeError("boom")), patch(
            "somewheria_app.services.properties.time.sleep",
            side_effect=StopIteration,
        ), patch.object(self.service.logger, "error") as error_mock:
            with self.assertRaises(StopIteration):
                self.service._periodic_refresh()

        error_mock.assert_called_once()

    def test_refresh_cache_updates_cache_and_logs(self):
        latest = [{"id": "prop-1"}]
        with patch.object(self.service, "fetch_all_properties", return_value=latest), patch.object(
            self.service.logger,
            "info",
        ) as info_mock:
            self.service.refresh_cache()

        self.assertEqual(self.service.cache, latest)
        info_mock.assert_called_once()

    def test_get_cached_properties_returns_copy(self):
        self.service.cache = [{"id": "prop-1", "nested": {"a": 1}}]

        cached = self.service.get_cached_properties()
        cached[0]["nested"]["a"] = 99

        self.assertEqual(self.service.cache[0]["nested"]["a"], 1)

    def test_get_property_returns_copy_for_match(self):
        self.service.cache = [{"id": "prop-1", "name": "Maple"}]

        property_info = self.service.get_property("prop-1")
        property_info["name"] = "Changed"

        self.assertEqual(self.service.cache[0]["name"], "Maple")

    def test_get_property_returns_none_when_missing(self):
        self.service.cache = [{"id": "prop-1"}]

        self.assertIsNone(self.service.get_property("missing"))

    def test_trigger_background_refresh_starts_daemon_thread(self):
        thread_mock = Mock()
        with patch("somewheria_app.services.properties.threading.Thread", return_value=thread_mock) as thread_ctor:
            self.service.trigger_background_refresh("admin@example.com")

        self.assertEqual(thread_ctor.call_args.kwargs["args"], ("admin@example.com",))
        thread_mock.start.assert_called_once()

    def test_refresh_with_change_log_returns_when_snapshot_is_unchanged(self):
        self.service.cache = [{"id": "prop-1", "name": "Old"}]
        with patch.object(self.service, "fetch_all_properties", return_value=[{"id": "prop-1", "name": "Old"}]), patch.object(
            self.service.logger,
            "info",
        ) as info_mock:
            self.service._refresh_with_change_log("admin@example.com")

        self.notifications.log_site_change.assert_not_called()
        info_mock.assert_called_once()

    def test_refresh_with_change_log_updates_cache_and_logs_change(self):
        self.service.cache = [{"id": "prop-1", "name": "Old"}]
        latest = [{"id": "prop-1", "name": "New"}]

        with patch.object(self.service, "fetch_all_properties", return_value=latest):
            self.service._refresh_with_change_log("admin@example.com")

        self.assertEqual(self.service.cache, latest)
        self.notifications.log_site_change.assert_called_once()

    def test_refresh_with_change_log_logs_exception(self):
        with patch.object(self.service, "fetch_all_properties", side_effect=RuntimeError("boom")), patch.object(
            self.service.logger,
            "error",
        ) as error_mock:
            self.service._refresh_with_change_log("admin@example.com")

        error_mock.assert_called_once()

    def test_build_change_log_reports_added_removed_and_changed_items(self):
        change_log = self.service._build_change_log(
            [{"id": "prop-1", "name": "Old"}, {"id": "prop-2", "rent": "1000"}],
            [{"id": "prop-1", "name": "New"}, {"id": "prop-3", "rent": "1200"}],
        )

        self.assertEqual(change_log["added_ids"], ["prop-3"])
        self.assertEqual(change_log["removed_ids"], ["prop-2"])
        self.assertEqual(change_log["old_count"], 2)
        self.assertEqual(change_log["new_count"], 2)
        self.assertEqual(change_log["changed"][0]["id"], "prop-1")

    def test_fetch_all_properties_filters_out_missing_records(self):
        with patch.object(self.service, "_fetch_property_ids", return_value=["prop-1", "prop-2"]), patch.object(
            self.service,
            "fetch_property_record",
            side_effect=[{"id": "prop-1"}, None],
        ):
            properties = self.service.fetch_all_properties()

        self.assertEqual(properties, [{"id": "prop-1"}])

    def test_fetch_property_ids_returns_ids_on_success(self):
        response = Mock()
        response.json.return_value = {"property_ids": ["prop-1", "prop-2"]}

        with patch("somewheria_app.services.properties.requests.get", return_value=response):
            property_ids = self.service._fetch_property_ids()

        self.assertEqual(property_ids, ["prop-1", "prop-2"])
        response.raise_for_status.assert_called_once()

    def test_fetch_property_ids_returns_empty_list_on_failure(self):
        with patch("somewheria_app.services.properties.requests.get", side_effect=RuntimeError("boom")):
            property_ids = self.service._fetch_property_ids()

        self.assertEqual(property_ids, [])

    def test_fetch_property_record_builds_normalized_payload(self):
        details_response = Mock()
        details_response.json.return_value = {"name": "Maple House"}
        photo_response = Mock()
        photo_response.json.return_value = ["https://example.com/a.jpg", "https://example.com/b.jpg"]
        thumb_response = Mock()
        thumb_response.json.return_value = ""

        with patch(
            "somewheria_app.services.properties.requests.get",
            side_effect=[details_response, photo_response, thumb_response],
        ), patch.object(
            self.service,
            "get_base64_image_from_url",
            side_effect=["encoded-a", None],
        ):
            property_info = self.service.fetch_property_record("prop-1")

        self.assertEqual(property_info["id"], "prop-1")
        self.assertEqual(property_info["photos"], ["encoded-a"])
        self.assertEqual(property_info["thumbnail"], "encoded-a")

    def test_fetch_property_record_returns_none_on_failure(self):
        with patch("somewheria_app.services.properties.requests.get", side_effect=RuntimeError("boom")), patch.object(
            self.service.logger,
            "warning",
        ) as warning_mock:
            property_info = self.service.fetch_property_record("prop-1")

        self.assertIsNone(property_info)
        warning_mock.assert_called_once()

    def test_safe_json_returns_response_payload(self):
        response = Mock()
        response.json.return_value = {"ok": True}
        with patch("somewheria_app.services.properties.requests.get", return_value=response):
            payload = self.service._safe_json("https://example.com/data", [])

        self.assertEqual(payload, {"ok": True})

    def test_safe_json_returns_default_on_failure(self):
        with patch("somewheria_app.services.properties.requests.get", side_effect=RuntimeError("boom")):
            payload = self.service._safe_json("https://example.com/data", [])

        self.assertEqual(payload, [])

    def test_letterbox_returns_original_image_when_ratio_matches(self):
        image = Image.new("RGB", (1600, 900), color="red")

        result = self.service.letterbox_to_16_9(image)

        self.assertIs(result, image)

    def test_letterbox_returns_original_image_when_height_is_zero(self):
        image = Mock()
        image.size = (100, 0)

        result = self.service.letterbox_to_16_9(image)

        self.assertIs(result, image)

    def test_letterbox_adds_padding_for_tall_image(self):
        image = Image.new("RGB", (900, 900), color="blue")

        result = self.service.letterbox_to_16_9(image)

        self.assertEqual(result.size, (1600, 900))

    def test_letterbox_adds_padding_for_wide_image(self):
        image = Image.new("RGB", (2000, 900), color="orange")

        result = self.service.letterbox_to_16_9(image)

        self.assertEqual(result.size, (2000, 1125))

    def test_get_base64_image_from_url_returns_data_url(self):
        image = Image.new("RGB", (16, 9), color="green")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        response = Mock(content=buffer.getvalue())

        with patch("somewheria_app.services.properties.requests.get", return_value=response):
            encoded = self.service.get_base64_image_from_url("https://example.com/image.png")

        self.assertTrue(encoded.startswith("data:image/jpeg;base64,"))
        base64.b64decode(encoded.split(",", 1)[1])

    def test_get_base64_image_from_url_returns_none_on_failure(self):
        with patch("somewheria_app.services.properties.requests.get", side_effect=RuntimeError("boom")), patch.object(
            self.service.logger,
            "warning",
        ) as warning_mock:
            encoded = self.service.get_base64_image_from_url("https://example.com/image.png")

        self.assertIsNone(encoded)
        warning_mock.assert_called_once()

    def test_create_property_posts_payload_and_returns_id(self):
        form = SimpleNamespace(
            get=lambda key, default="": {"name": "Maple House", "address": "123 Main St"}.get(key, default),
            getlist=lambda key: [],
        )
        response = Mock()
        response.json.return_value = {"property_id": "prop-77"}

        with patch.object(self.service, "property_payload_from_form", return_value={"address": "123 Main St"}), patch(
            "somewheria_app.services.properties.requests.post",
            return_value=response,
        ), patch.object(self.service, "trigger_background_refresh") as trigger_mock:
            new_id = self.service.create_property(form, "admin@example.com")

        self.assertEqual(new_id, "prop-77")
        self.notifications.log_site_change.assert_called_once()
        trigger_mock.assert_called_once_with("admin@example.com")

    def test_update_property_updates_remote_api_and_triggers_refresh(self):
        current = {
            "id": "prop-1",
            "name": "Maple",
            "address": "123 Main",
            "rent": "1000",
            "deposit": "1000",
            "bedrooms": "2",
            "bathrooms": "1",
            "lease_length": "12 months",
            "pets_allowed": "No",
            "blurb": "Old",
            "description": "Old description",
        }
        form = SimpleNamespace(
            get=lambda key, default=None: {
                "name": "Updated Maple",
                "pets_allowed": "Yes",
                "custom_amenities": "Garden, Storage",
                "blurb": "New",
            }.get(key, default),
            getlist=lambda key: ["Parking"] if key == "amenities" else [],
        )

        with patch.object(self.service, "get_property", return_value=current), patch(
            "somewheria_app.services.properties.requests.put"
        ) as put_mock, patch.object(self.service, "trigger_background_refresh") as trigger_mock:
            self.service.update_property("prop-1", form, "admin@example.com")

        self.assertTrue(put_mock.call_args.kwargs["json"]["pets_allowed"])
        self.assertEqual(put_mock.call_args.kwargs["json"]["included_amenities"], ["Parking", "Garden", "Storage"])
        self.notifications.log_site_change.assert_called_once()
        trigger_mock.assert_called_once_with("admin@example.com")

    def test_update_property_raises_when_property_is_missing(self):
        form = SimpleNamespace(get=lambda _key, default=None: default, getlist=lambda _key: [])

        with patch.object(self.service, "get_property", return_value=None):
            with self.assertRaises(KeyError):
                self.service.update_property("missing", form, "admin@example.com")

    def test_upload_image_processes_and_associates_file(self):
        upload_dir = self.upload_dir
        file_bytes = io.BytesIO()
        Image.new("RGB", (16, 9), color="purple").save(file_bytes, format="PNG")
        file_payload = file_bytes.getvalue()

        class UploadedFile:
            filename = "photo.png"

            def save(self_inner, path):
                path.write_bytes(file_payload)

        with patch("somewheria_app.services.properties.secrets.token_hex", return_value="abc123"), patch(
            "somewheria_app.services.properties.url_for",
            return_value="/static/uploads/prop-1_abc123.png",
        ), patch("somewheria_app.services.properties.requests.post") as post_mock, patch.object(
            self.service,
            "trigger_background_refresh",
        ) as trigger_mock:
            relative_url = self.service.upload_image("prop-1", UploadedFile(), "https://example.com/", "admin@example.com")

        expected_path = upload_dir / "prop-1_abc123.png"
        self.assertTrue(expected_path.exists())
        self.assertEqual(relative_url, "/static/uploads/prop-1_abc123.png")
        post_mock.assert_called_once()
        self.notifications.notify_image_edit.assert_called_once_with(
            ["https://example.com/static/uploads/prop-1_abc123.png"]
        )
        self.notifications.log_site_change.assert_called_once()
        trigger_mock.assert_called_once_with("admin@example.com")

    def test_upload_image_logs_processing_error_but_continues(self):
        class UploadedFile:
            filename = "bad.png"

            def save(self_inner, path):
                path.write_bytes(b"not-an-image")

        with patch("somewheria_app.services.properties.secrets.token_hex", return_value="bad"), patch(
            "somewheria_app.services.properties.url_for",
            return_value="/static/uploads/prop-1_bad.png",
        ), patch("somewheria_app.services.properties.requests.post"), patch.object(
            self.service,
            "trigger_background_refresh",
        ):
            relative_url = self.service.upload_image("prop-1", UploadedFile(), "https://example.com", "admin@example.com")

        self.assertEqual(relative_url, "/static/uploads/prop-1_bad.png")
        self.notifications.log_and_notify_error.assert_called_once()

    def test_upload_image_logs_association_failure(self):
        file_bytes = io.BytesIO()
        Image.new("RGB", (16, 9), color="yellow").save(file_bytes, format="PNG")
        file_payload = file_bytes.getvalue()

        class UploadedFile:
            filename = "photo.png"

            def save(self_inner, path):
                path.write_bytes(file_payload)

        with patch("somewheria_app.services.properties.secrets.token_hex", return_value="assoc"), patch(
            "somewheria_app.services.properties.url_for",
            return_value="/static/uploads/prop-1_assoc.png",
        ), patch(
            "somewheria_app.services.properties.requests.post",
            side_effect=RuntimeError("boom"),
        ), patch.object(self.service.logger, "warning") as warning_mock, patch.object(
            self.service,
            "trigger_background_refresh",
        ):
            self.service.upload_image("prop-1", UploadedFile(), "https://example.com", "admin@example.com")

        warning_mock.assert_called_once()


class CoverageInfrastructureTestCase(unittest.TestCase):
    def test_file_storage_logs_load_errors_and_returns_default(self):
        service = FileStorageService(
            SimpleNamespace(
                registration_file=Path("registrations.json"),
                user_roles_file=Path("roles.json"),
                renter_profile_file=Path("profiles.json"),
                contracts_file=Path("contracts.json"),
            )
        )
        path_mock = Mock()
        path_mock.exists.return_value = True
        path_mock.open.side_effect = RuntimeError("boom")

        with patch.object(service.logger, "error") as error_mock:
            loaded = service.load_json_file(path_mock, {"fallback": True})

        self.assertEqual(loaded, {"fallback": True})
        error_mock.assert_called_once()

    def test_file_storage_logs_save_errors(self):
        service = FileStorageService(
            SimpleNamespace(
                registration_file=Path("registrations.json"),
                user_roles_file=Path("roles.json"),
                renter_profile_file=Path("profiles.json"),
                contracts_file=Path("contracts.json"),
            )
        )
        path_mock = Mock()
        path_mock.open.side_effect = RuntimeError("boom")

        with patch.object(service.logger, "error") as error_mock:
            service.save_json_file(path_mock, {"ok": True})

        error_mock.assert_called_once()

    def test_file_storage_profile_and_contract_loaders_delegate(self):
        service = FileStorageService(
            SimpleNamespace(
                registration_file=Path("registrations.json"),
                user_roles_file=Path("roles.json"),
                renter_profile_file=Path("profiles.json"),
                contracts_file=Path("contracts.json"),
            )
        )
        with patch.object(service, "load_json_file", side_effect=[{"renter": {}}, {"contract": []}]):
            self.assertEqual(service.get_renter_profiles(), {"renter": {}})
            self.assertEqual(service.get_renter_contracts(), {"contract": []})

    def test_appointment_print_check_file_logs_path_status(self):
        service = AppointmentService(SimpleNamespace(property_appointments_file=Path("appointments.txt")))
        with patch.object(service.logger, "info") as info_mock:
            service.print_check_file(Path("missing.txt"), "Missing file check")

        info_mock.assert_called_once()

    def test_appointment_load_skips_blank_lines(self):
        service = AppointmentService(SimpleNamespace(property_appointments_file=Path("appointments.txt")))
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path,
            "open",
            mock_open(read_data="\nprop-1:2030-01-10\n"),
        ):
            loaded = service.load()

        self.assertEqual(loaded["prop-1"], {"2030-01-10"})

    def test_auth_service_login_user_stores_session_payload(self):
        app = Flask(__name__)
        app.secret_key = "test"
        storage = Mock()
        storage.get_user_roles.return_value = {}
        service = AuthService(
            SimpleNamespace(
                authorized_users=[],
                admin_users=[],
                high_admin_users=[],
            ),
            storage,
        )

        with app.test_request_context("/"):
            user = service.login_user({"sub": "123", "email": "user@example.com", "name": "User"})

        self.assertEqual(user["role"], "guest")
        self.assertEqual(user["email"], "user@example.com")

    def test_auth_status_payload_returns_false_when_logged_out(self):
        app = Flask(__name__)
        with app.test_request_context("/"), patch("somewheria_app.services.auth.is_logged_in", return_value=False):
            response = auth_status_payload()

        self.assertFalse(response.get_json()["authenticated"])

    def test_auth_status_payload_returns_user_payload_when_logged_in(self):
        app = Flask(__name__)
        user = {"id": "1", "email": "user@example.com", "name": "User", "picture": "pic"}
        with app.test_request_context("/"), patch("somewheria_app.services.auth.is_logged_in", return_value=True), patch(
            "somewheria_app.services.auth.get_current_user",
            return_value=user,
        ):
            response = auth_status_payload()

        self.assertTrue(response.get_json()["authenticated"])
        self.assertEqual(response.get_json()["user"]["email"], "user@example.com")

    def test_renter_required_forbids_guest_role(self):
        app = Flask(__name__)
        app.secret_key = "test"

        @app.route("/protected")
        @renter_required
        def protected():
            return "ok"

        with app.test_request_context("/protected"), patch(
            "somewheria_app.services.auth.get_services",
            return_value=SimpleNamespace(
                auth=SimpleNamespace(
                    is_logged_in=lambda: True,
                    current_user=lambda: {"role": "guest"},
                )
            ),
        ):
            with self.assertRaises(Exception):
                protected()

    def test_notification_server_url_uses_hostname_lookup(self):
        service = NotificationService(
            SimpleNamespace(
                email_sender="sender@example.com",
                email_recipient="recipient@example.com",
                log_file=Path("application.log"),
                change_log_file=Path("site_changes.log"),
            ),
            Mock(),
        )
        with patch("somewheria_app.services.notifications.socket.gethostname", return_value="host"), patch(
            "somewheria_app.services.notifications.socket.gethostbyname",
            return_value="10.0.0.5",
        ):
            server_url = service._server_url()

        self.assertEqual(server_url, "http://10.0.0.5:5000")

    def test_notification_log_site_change_logs_write_errors(self):
        service = NotificationService(
            SimpleNamespace(
                email_sender="sender@example.com",
                email_recipient="recipient@example.com",
                log_file=Path("application.log"),
                change_log_file=Path("site_changes.log"),
            ),
            Mock(),
        )
        path_mock = Mock()
        path_mock.open.side_effect = RuntimeError("boom")
        service.config.change_log_file = path_mock

        with patch.object(service.console, "error") as error_mock:
            service.log_site_change("admin@example.com", "update", {"id": "prop-1"})

        error_mock.assert_called_once()

    def test_read_logs_handles_crit_and_unstructured_lines(self):
        service = NotificationService(
            SimpleNamespace(
                email_sender="sender@example.com",
                email_recipient="recipient@example.com",
                log_file=Path("application.log"),
                change_log_file=Path("site_changes.log"),
            ),
            Mock(),
        )
        log_text = "\nraw unstructured line\n2026-01-01:CRIT:Critical issue\n"
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path,
            "open",
            mock_open(read_data=log_text),
        ):
            entries = service.read_logs()

        self.assertEqual(entries[0]["level"], "CRITICAL")
        self.assertEqual(entries[1]["message"], "raw unstructured line")

    def test_read_logs_handles_malformed_pipe_line(self):
        service = NotificationService(
            SimpleNamespace(
                email_sender="sender@example.com",
                email_recipient="recipient@example.com",
                log_file=Path("application.log"),
                change_log_file=Path("site_changes.log"),
            ),
            Mock(),
        )
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path,
            "open",
            mock_open(read_data="broken|pipe\n"),
        ):
            entries = service.read_logs()

        self.assertEqual(entries[0]["message"], "broken|pipe")

    def test_set_console_log_level_updates_logger(self):
        set_console_log_level("debug")
        self.assertEqual(importlib.import_module("logging").getLogger("somewheria.console").level, 10)


class CoverageAnalyticsAndFactoryTestCase(unittest.TestCase):
    def setUp(self):
        self.analytics = AnalyticsTracker(3)
        self.app = Flask(__name__, static_folder="static")
        self.app.secret_key = "test"
        self.app.config["SHOW_REQUEST_LOGS"] = True

        @self.app.route("/hello")
        def hello():
            return "hello"

        self.app.before_request(self.analytics.before_request)
        self.app.after_request(self.analytics.after_request)

    def test_before_request_tracks_visits_and_unique_users(self):
        with self.app.test_request_context("/hello"):
            from flask import session

            session["user"] = {"email": "user@example.com"}
            self.analytics.before_request()

        self.assertEqual(sum(self.analytics.site_visits.values()), 1)
        self.assertEqual(len(next(iter(self.analytics.unique_users.values()))), 1)

    def test_after_request_logs_duration(self):
        with self.app.test_request_context("/hello"):
            from flask import g

            g.start_time = 0
            response = Response("ok", status=200)
            with patch("somewheria_app.services.analytics.time.time", return_value=0.05), patch.object(
                self.analytics.logger,
                "info",
            ) as info_mock:
                self.analytics.after_request(response)

        info_mock.assert_called_once()

    def test_after_request_logs_warning_when_logging_fails(self):
        with self.app.test_request_context("/hello"):
            from flask import g

            g.start_time = 0
            response = Response("ok", status=200)
            with patch("somewheria_app.services.analytics.time.time", return_value=1.0), patch.object(
                self.analytics.logger,
                "info",
                side_effect=RuntimeError("boom"),
            ), patch.object(self.analytics.logger, "warning") as warning_mock:
                self.analytics.after_request(response)

        warning_mock.assert_called_once()

    def test_after_request_skips_logging_when_disabled(self):
        self.app.config["SHOW_REQUEST_LOGS"] = False
        with self.app.test_request_context("/hello"):
            from flask import g

            g.start_time = 0
            response = Response("ok", status=200)
            with patch.object(self.analytics.logger, "info") as info_mock:
                self.analytics.after_request(response)

        info_mock.assert_not_called()

    def test_record_login_record_error_and_dashboard_data(self):
        self.analytics.record_login("user@example.com")
        self.analytics.record_error()

        metrics, chart_data = self.analytics.dashboard_data(7)

        self.assertEqual(metrics["properties_listed"], 7)
        self.assertEqual(len(chart_data["days"]), 3)
        self.assertEqual(len(chart_data["unique_users"]), 3)

    def test_create_app_skips_background_thread_when_disabled(self):
        with patch.dict(os.environ, {"DISABLE_BACKGROUND_THREADS": "1"}, clear=False), patch(
            "somewheria_app.services.properties.PropertyService.start_background_refresh"
        ) as refresh_mock:
            app = create_app()

        self.assertTrue(app.config["DISABLE_BACKGROUND_THREADS"])
        refresh_mock.assert_not_called()

    def test_create_app_starts_background_thread_when_enabled(self):
        with patch.dict(os.environ, {"DISABLE_BACKGROUND_THREADS": "0"}, clear=False), patch(
            "somewheria_app.services.properties.PropertyService.start_background_refresh"
        ) as refresh_mock:
            app = create_app()

        self.assertFalse(app.config["DISABLE_BACKGROUND_THREADS"])
        refresh_mock.assert_called_once()

    def test_error_handlers_render_401_500_502_503_and_504_pages(self):
        with patch.dict(os.environ, {"DISABLE_BACKGROUND_THREADS": "1"}, clear=False):
            app = create_app()
        app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

        @app.route("/force-401")
        def force_401():
            abort(401)

        @app.route("/force-500")
        def force_500():
            raise RuntimeError("boom")

        @app.route("/force-502")
        def force_502():
            abort(502)

        @app.route("/force-503")
        def force_503():
            abort(503)

        @app.route("/force-504")
        def force_504():
            abort(504)

        client = app.test_client()
        self.assertEqual(client.get("/force-401").status_code, 401)
        self.assertEqual(client.get("/force-500").status_code, 500)
        self.assertEqual(client.get("/force-502").status_code, 502)
        self.assertEqual(client.get("/force-503").status_code, 503)
        self.assertEqual(client.get("/force-504").status_code, 504)

    def test_before_request_skips_static_endpoint(self):
        with self.app.test_client() as client:
            client.get("/static/missing.css")

        self.assertEqual(sum(self.analytics.site_visits.values()), 0)

    def test_normalize_property_resets_non_list_photos(self):
        notifications = Mock()
        service = PropertyService(
            SimpleNamespace(api_base_url="https://api.example.com", upload_dir=Path("."), cache_refresh_interval=5),
            notifications,
        )

        normalized = service.normalize_property({"photos": "not-a-list"}, "prop-1")

        self.assertEqual(normalized["photos"], [])


class CoverageRouteBranchTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        website_app.app.config.update(TESTING=True)

    def setUp(self):
        self.app = website_app.app
        self.client = self.app.test_client()
        self.services = self.app.extensions["somewheria_services"]
        self.original_google_client_id = self.services.config.google_client_id
        self.original_google_client_secret = self.services.config.google_client_secret
        self.original_authorized_users = list(self.services.config.authorized_users)

    def tearDown(self):
        self.services.config.google_client_id = self.original_google_client_id
        self.services.config.google_client_secret = self.original_google_client_secret
        self.services.config.authorized_users = self.original_authorized_users

    def login_as(self, role, email=None):
        with self.client.session_transaction() as session:
            session["user"] = {
                "id": f"{role}-id",
                "email": email or f"{role}@example.com",
                "name": role.title(),
                "role": role,
            }

    def configure_google(self):
        self.services.config.google_client_id = "client-id"
        self.services.config.google_client_secret = "client-secret"

    def make_flow(self, fetch_side_effect=None):
        flow = Mock()
        flow.credentials = SimpleNamespace(id_token="token")
        flow.fetch_token.side_effect = fetch_side_effect
        flow.redirect_uri = None
        return flow

    def test_manifest_webmanifest_route_loads(self):
        response = self.client.get("/manifest.webmanifest")

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/manifest+json", response.content_type)

    def test_google_callback_rejects_non_company_email(self):
        self.configure_google()
        flow = self.make_flow()
        with patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow), patch(
            "somewheria_app.routes.auth_routes.id_token.verify_oauth2_token",
            return_value={"email": "user@gmail.com"},
        ):
            response = self.client.get("/google/callback")

        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Only ekbergproperties.com accounts are allowed.", response.data)

    def test_google_callback_rejects_unauthorized_company_user(self):
        self.configure_google()
        self.services.config.authorized_users = ["allowed@ekbergproperties.com"]
        flow = self.make_flow()
        with patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow), patch(
            "somewheria_app.routes.auth_routes.id_token.verify_oauth2_token",
            return_value={"email": "blocked@ekbergproperties.com"},
        ), patch.object(self.services.notifications, "log_and_notify_error") as notify_mock:
            response = self.client.get("/google/callback")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Access denied", response.data)
        notify_mock.assert_called_once()

    def test_google_callback_logs_user_in_when_authorized(self):
        self.configure_google()
        self.services.config.authorized_users = []
        flow = self.make_flow()
        with patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow), patch(
            "somewheria_app.routes.auth_routes.id_token.verify_oauth2_token",
            return_value={"sub": "123", "email": "user@ekbergproperties.com", "name": "User"},
        ), patch.object(self.services.auth, "login_user", return_value={"email": "user@ekbergproperties.com"}) as login_mock, patch.object(
            self.services.analytics,
            "record_login",
        ) as record_login_mock:
            response = self.client.get("/google/callback", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        login_mock.assert_called_once()
        record_login_mock.assert_called_once_with("user@ekbergproperties.com")

    def test_google_callback_handles_flow_failure(self):
        self.configure_google()
        flow = self.make_flow(fetch_side_effect=RuntimeError("boom"))
        with patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow), patch.object(
            self.services.notifications,
            "log_and_notify_error",
        ) as notify_mock:
            response = self.client.get("/google/callback")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Authentication failed", response.data)
        notify_mock.assert_called_once()

    def test_save_edit_returns_500_on_generic_error(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "update_property", side_effect=RuntimeError("boom")), patch.object(
            self.services.notifications,
            "log_and_notify_error",
        ) as notify_mock:
            response = self.client.post("/save-edit/prop-1", data={"name": "Maple"})

        self.assertEqual(response.status_code, 500)
        self.assertIn(b"boom", response.data)
        notify_mock.assert_called_once()

    def test_upload_image_success_returns_json(self):
        self.login_as("admin", email="admin@example.com")
        data = {"file": (io.BytesIO(b"image"), "photo.png")}
        with patch.object(self.services.properties, "upload_image", return_value="/static/uploads/photo.png") as upload_mock:
            response = self.client.post("/upload-image/prop-1", data=data, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        upload_mock.assert_called_once()

    def test_admin_dashboard_handles_branchy_post_paths(self):
        self.login_as("high_admin", email="owner@example.com")
        with patch.object(self.services.analytics, "dashboard_data", return_value=({}, {})), patch.object(
            self.services.storage,
            "get_user_roles",
            return_value={"existing@example.com": "admin"},
        ), patch.object(self.services.storage, "delete_user_role", side_effect=[True, False]), patch.object(
            self.services.storage,
            "set_user_role",
        ) as set_role_mock, patch.object(self.services.notifications, "log_site_change") as change_mock:
            delete_ok = self.client.post("/admin/dashboard", data={"action": "delete", "email": "user@example.com"})
            delete_missing = self.client.post(
                "/admin/dashboard",
                data={"action": "delete", "email": "missing@example.com"},
            )
            invalid_update = self.client.post(
                "/admin/dashboard",
                data={"action": "update", "email": "user@example.com", "role": "bad-role"},
            )
            existing_add = self.client.post(
                "/admin/dashboard",
                data={"action": "add", "email": "existing@example.com", "role": "admin"},
            )
            invalid_add = self.client.post(
                "/admin/dashboard",
                data={"action": "add", "email": "new@example.com", "role": "bad-role"},
            )
            missing_email = self.client.post("/admin/dashboard", data={"action": "add", "email": "", "role": "admin"})

        self.assertIn(b"removed", delete_ok.data)
        self.assertIn(b"User not found.", delete_missing.data)
        self.assertIn(b"Invalid role.", invalid_update.data)
        self.assertIn(b"User already exists.", existing_add.data)
        self.assertIn(b"Invalid role.", invalid_add.data)
        self.assertIn(b"No email provided.", missing_email.data)
        set_role_mock.assert_not_called()
        change_mock.assert_called_once()

    def test_admin_dashboard_updates_role_for_high_admin(self):
        self.login_as("high_admin", email="owner@example.com")
        with patch.object(self.services.analytics, "dashboard_data", return_value=({}, {})), patch.object(
            self.services.storage,
            "get_user_roles",
            return_value={},
        ), patch.object(self.services.storage, "set_user_role") as set_role_mock, patch.object(
            self.services.notifications,
            "log_site_change",
        ) as change_mock:
            response = self.client.post(
                "/admin/dashboard",
                data={"action": "update", "email": "user@example.com", "role": "admin"},
            )

        self.assertIn(b"updated to admin", response.data)
        set_role_mock.assert_called_once_with("user@example.com", "admin")
        change_mock.assert_called_once()

    def test_admin_users_delete_success_and_invalid_role(self):
        self.login_as("admin")
        with patch.object(self.services.storage, "delete_user_role", return_value=True), patch.object(
            self.services.storage,
            "get_user_roles",
            side_effect=[{}, {}],
        ):
            delete_response = self.client.post(
                "/admin/users",
                data={"email": "user@example.com", "action": "delete"},
            )

        with patch.object(self.services.storage, "get_user_roles", side_effect=[{}, {}]), patch.object(
            self.services.storage,
            "set_user_role",
        ) as set_role_mock:
            invalid_response = self.client.post(
                "/admin/users",
                data={"email": "user@example.com", "action": "update", "role": "bad-role"},
            )

        self.assertIn(b"removed", delete_response.data)
        self.assertIn(b"Invalid role.", invalid_response.data)
        set_role_mock.assert_not_called()

    def test_admin_contracts_handles_missing_fields_and_missing_contract(self):
        self.login_as("admin")
        with patch.object(
            self.services.storage,
            "get_renter_contracts",
            return_value={"renter@example.com": [{"property_name": "Maple"}]},
        ):
            missing_fields = self.client.post(
                "/admin/contracts",
                data={"action": "delete", "renter_email": "", "contract_index": "0"},
            )
            missing_contract = self.client.post(
                "/admin/contracts",
                data={"action": "delete", "renter_email": "renter@example.com", "contract_index": "5"},
            )

        self.assertIn(b"Missing required fields.", missing_fields.data)
        self.assertIn(b"Contract not found.", missing_contract.data)

    def test_delete_listing_returns_500_when_service_fails(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "delete_property", side_effect=RuntimeError("boom")), patch.object(
            self.services.notifications,
            "log_and_notify_error",
        ) as notify_mock:
            response = self.client.post("/delete-listing/prop-1")

        self.assertEqual(response.status_code, 500)
        self.assertIn(b"boom", response.data)
        notify_mock.assert_called_once()

    def test_toggle_sale_returns_500_on_generic_error(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "toggle_sale", side_effect=RuntimeError("boom")), patch.object(
            self.services.notifications,
            "log_and_notify_error",
        ) as notify_mock:
            response = self.client.post("/toggle-sale/prop-1")

        self.assertEqual(response.status_code, 500)
        self.assertIn(b"boom", response.data)
        notify_mock.assert_called_once()


class CoverageStartupExecutionTestCase(unittest.TestCase):
    def test_start_cache_refresh_thread_calls_service(self):
        with patch.object(
            website_app.app.extensions["somewheria_services"].properties,
            "start_background_refresh",
        ) as refresh_mock:
            website_app.start_cache_refresh_thread()

        refresh_mock.assert_called_once()

    def test_print_check_file_delegates_to_appointment_service(self):
        with patch.object(
            website_app.app.extensions["somewheria_services"].appointments,
            "print_check_file",
        ) as print_mock:
            website_app.print_check_file(Path("appointments.txt"), "Startup")

        print_mock.assert_called_once_with(Path("appointments.txt"), "Startup")

    def test_prompt_choice_uses_default_on_blank(self):
        with patch("builtins.input", return_value=""):
            choice = website_app._prompt_choice("Choose level", "normal", {"normal": "INFO"})

        self.assertEqual(choice, "normal")

    def test_prompt_yes_no_accepts_explicit_no(self):
        with patch("builtins.input", return_value="no"):
            self.assertFalse(website_app._prompt_yes_no("Show logs", True))

    def test_prompt_yes_no_retries_on_invalid_answer(self):
        answers = iter(["maybe", "yes"])
        with patch("builtins.input", side_effect=lambda _prompt: next(answers)):
            self.assertTrue(website_app._prompt_yes_no("Show logs", False))

    def test_prompt_port_uses_default_on_blank(self):
        with patch("builtins.input", return_value=""):
            self.assertEqual(website_app._prompt_port(5000), 5000)

    def test_main_block_runs_default_startup_path(self):
        fake_services = SimpleNamespace(
            config=SimpleNamespace(property_appointments_file=Path("appointments.txt")),
            properties=SimpleNamespace(refresh_cache=Mock(), start_background_refresh=Mock()),
            appointments=SimpleNamespace(print_check_file=Mock()),
        )
        fake_app = SimpleNamespace(
            extensions={"somewheria_services": fake_services},
            config={},
            run=Mock(),
        )
        fake_logger = Mock()

        with patch("somewheria_app.create_app", return_value=fake_app), patch(
            "somewheria_app.services.console.get_console_logger",
            return_value=fake_logger,
        ), patch("somewheria_app.services.console.set_console_log_level") as set_level_mock, patch(
            "sys.stdin.isatty",
            return_value=False,
        ), patch(
            "sys.stdout.isatty",
            return_value=False,
        ):
            runpy.run_module("website_app", run_name="__main__")

        set_level_mock.assert_called_once_with("INFO")
        fake_services.properties.refresh_cache.assert_called_once()
        fake_services.appointments.print_check_file.assert_called_once()
        fake_app.run.assert_called_once_with("0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    unittest.main()
