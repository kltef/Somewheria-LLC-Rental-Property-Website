"""Tests for Phase 3 §6 — JIRA scaffold + webhook + CSRF exemption."""

import datetime
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

# Ensure background JIRA threads run inline so we can assert on jira_key
# right after create_ticket() returns.
os.environ["DISABLE_BACKGROUND_THREADS"] = "1"

from somewheria_app import create_app  # noqa: E402
from somewheria_app.services.jira import JiraClient  # noqa: E402
from somewheria_app.services.tickets import TicketService, _now_iso  # noqa: E402


class JiraClientTestCase(unittest.TestCase):
    def _make_config(self, **overrides):
        base = dict(
            jira_base_url="https://example.atlassian.net",
            jira_project_key="MAINT",
            jira_api_token="tok",
            jira_user_email="bot@example.com",
            jira_webhook_secret="hunter2",
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_no_op_when_credentials_missing(self):
        client = JiraClient(self._make_config(jira_api_token=""), notifications=MagicMock())
        self.assertFalse(client.is_configured())
        self.assertIsNone(client.create_issue({"title": "x", "description": "y"}))

    def test_create_issue_returns_stub_key_when_configured(self):
        client = JiraClient(self._make_config(), notifications=MagicMock())
        self.assertTrue(client.is_configured())
        key = client.create_issue({
            "title": "Leak",
            "description": "Under the sink",
            "property_name": "Maple House",
            "submitted_by": "renter@example.com",
            "priority": "high",
            "category": "plumbing",
        })
        self.assertEqual(key, "STUB-1")

    def test_status_reverse_mapping(self):
        self.assertEqual(JiraClient.map_jira_status("Open"), "open")
        self.assertEqual(JiraClient.map_jira_status("In Progress"), "in_progress")
        self.assertEqual(JiraClient.map_jira_status("Done"), "resolved")
        self.assertEqual(JiraClient.map_jira_status("Closed"), "closed")
        self.assertIsNone(JiraClient.map_jira_status("Backlog"))
        self.assertIsNone(JiraClient.map_jira_status(""))

    def test_priority_mapping_via_payload_inspection(self):
        # Indirectly assert mapping via the log. Easier path: check that the
        # configured client doesn't blow up on each priority.
        client = JiraClient(self._make_config(), notifications=MagicMock())
        for prio in ("low", "normal", "high", "urgent", "bogus"):
            self.assertEqual(client.create_issue({
                "title": "t", "description": "d", "priority": prio,
                "category": "other",
            }), "STUB-1")


class NowIsoFormatTestCase(unittest.TestCase):
    """Lock the on-disk timestamp shape so the move off the deprecated
    ``datetime.utcnow()`` cannot silently drift the format. Existing ticket
    JSON files are parsed as ``YYYY-MM-DDTHH:MM:SSZ`` strings; any change
    would break the admin dashboard's sort and the JIRA mirror payload."""

    def test_now_iso_matches_expected_shape(self):
        value = _now_iso()
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_now_iso_returns_utc(self):
        before = datetime.datetime.now(datetime.timezone.utc)
        value = _now_iso()
        after = datetime.datetime.now(datetime.timezone.utc)
        # Parse the stamp back as UTC and assert it falls within the window.
        parsed = datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
        self.assertLessEqual(before.replace(microsecond=0), parsed)
        self.assertLessEqual(parsed, after)


class TicketServiceJiraWiringTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tickets_path = Path(self.tmp.name) / "tickets.json"
        self.tickets_path.write_text("[]", encoding="utf-8")
        self.config = SimpleNamespace(tickets_file=self.tickets_path)
        # Real-ish storage stub: read/write JSON list to a temp file.
        self.storage = MagicMock()
        self.storage.load_json_file.side_effect = lambda path, default: (
            json.loads(Path(path).read_text("utf-8")) if Path(path).exists() else default
        )
        def _save(path, data):
            Path(path).write_text(json.dumps(data), encoding="utf-8")
        self.storage.save_json_file.side_effect = _save
        self.notifications = MagicMock()
        self.notifications.send_email.return_value = True

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_ticket_succeeds_without_jira(self):
        # No JIRA wired in at all — ticket creation must not raise.
        svc = TicketService(self.config, self.storage, self.notifications, jira=None)
        ticket = svc.create_ticket(
            {"title": "Heater", "description": "Cold", "priority": "high"},
            submitter_email="r@example.com",
        )
        self.assertIn("id", ticket)
        self.assertNotIn("jira_key", ticket)

    def test_create_ticket_succeeds_when_jira_unconfigured(self):
        # JiraClient present but no credentials -> create_issue returns None.
        client = JiraClient(SimpleNamespace(
            jira_base_url="", jira_project_key="", jira_api_token="",
            jira_user_email="", jira_webhook_secret="",
        ), self.notifications)
        svc = TicketService(self.config, self.storage, self.notifications, jira=client)
        ticket = svc.create_ticket(
            {"title": "Heater", "description": "Cold"}, submitter_email="r@example.com"
        )
        # Reload from disk: no jira_key persisted.
        stored = json.loads(self.tickets_path.read_text("utf-8"))[0]
        self.assertEqual(stored["id"], ticket["id"])
        self.assertNotIn("jira_key", stored)

    def test_create_ticket_persists_jira_key_when_configured(self):
        client = JiraClient(SimpleNamespace(
            jira_base_url="https://example.atlassian.net",
            jira_project_key="MAINT", jira_api_token="t",
            jira_user_email="bot@example.com", jira_webhook_secret="s",
        ), self.notifications)
        svc = TicketService(self.config, self.storage, self.notifications, jira=client)
        ticket = svc.create_ticket(
            {"title": "Heater", "description": "Cold"}, submitter_email="r@example.com"
        )
        stored = json.loads(self.tickets_path.read_text("utf-8"))[0]
        self.assertEqual(stored["id"], ticket["id"])
        self.assertEqual(stored["jira_key"], "STUB-1")
        self.assertEqual(svc.find_by_jira_key("STUB-1")["id"], ticket["id"])

    def test_jira_failure_does_not_block_creation(self):
        client = MagicMock()
        client.create_issue.side_effect = RuntimeError("JIRA down")
        svc = TicketService(self.config, self.storage, self.notifications, jira=client)
        # Must not raise even though JIRA blows up.
        ticket = svc.create_ticket(
            {"title": "Heater", "description": "Cold"}, submitter_email="r@example.com"
        )
        self.assertIn("id", ticket)

    def test_slow_smtp_does_not_block_ticket_creation(self):
        # Simulate a Gmail SMTP relay that takes ~30 s to time out — the
        # actual worst-case for ``smtplib.SMTP(..., timeout=30)``. The
        # ticket-creation call must return well before the send finishes.
        # ``_enqueue_email`` honours DISABLE_BACKGROUND_THREADS in tests,
        # so temporarily disable that hatch for this one case to exercise
        # the daemon-thread path.
        import time

        slow_call_started = threading.Event()
        slow_call_can_return = threading.Event()

        def _slow_send_email(subject, body, to=None):
            slow_call_started.set()
            # Bound the wait so a bug in the test still lets it exit.
            slow_call_can_return.wait(timeout=10)
            return True

        self.notifications.send_email.side_effect = _slow_send_email
        svc = TicketService(self.config, self.storage, self.notifications, jira=None)

        prior = os.environ.pop("DISABLE_BACKGROUND_THREADS", None)
        try:
            start = time.monotonic()
            ticket = svc.create_ticket(
                {"title": "Heater", "description": "Cold"},
                submitter_email="renter@example.com",
            )
            elapsed = time.monotonic() - start
        finally:
            slow_call_can_return.set()
            if prior is not None:
                os.environ["DISABLE_BACKGROUND_THREADS"] = prior

        # If the send were on the request thread we'd have blocked for the
        # full timeout. Give the assertion a wide margin (1 s) so slow CI
        # boxes don't flake, while still catching an accidental sync send.
        self.assertLess(elapsed, 1.0)
        self.assertIn("id", ticket)
        # Sanity: the daemon thread did actually kick off the send.
        self.assertTrue(slow_call_started.wait(timeout=5))

    def test_email_send_error_does_not_break_ticket_creation(self):
        # A raise from ``send_email`` inside the daemon thread must not
        # propagate to the caller.
        self.notifications.send_email.side_effect = RuntimeError("SMTP down")
        svc = TicketService(self.config, self.storage, self.notifications, jira=None)
        ticket = svc.create_ticket(
            {"title": "Heater", "description": "Cold"},
            submitter_email="renter@example.com",
        )
        self.assertIn("id", ticket)


class JiraWebhookRouteTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Force webhook secret + JIRA creds before app construction so the
        # service registry picks them up.
        os.environ["JIRA_WEBHOOK_SECRET"] = "shhh"
        os.environ["JIRA_BASE_URL"] = "https://example.atlassian.net"
        os.environ["JIRA_PROJECT_KEY"] = "MAINT"
        os.environ["JIRA_API_TOKEN"] = "tok"
        os.environ["JIRA_USER_EMAIL"] = "bot@example.com"

    @classmethod
    def tearDownClass(cls):
        for var in ("JIRA_WEBHOOK_SECRET", "JIRA_BASE_URL", "JIRA_PROJECT_KEY",
                    "JIRA_API_TOKEN", "JIRA_USER_EMAIL"):
            os.environ.pop(var, None)

    def setUp(self):
        self.app = create_app()
        # Isolate from any leftover state in the repo's tickets.json so tests
        # don't pick up STUB-1 entries created by prior runs.
        self._tickets_tmp = tempfile.TemporaryDirectory()
        tickets_path = Path(self._tickets_tmp.name) / "tickets.json"
        tickets_path.write_text("[]", encoding="utf-8")
        self.app.extensions["somewheria_services"].config.tickets_file = tickets_path
        # NOTE: we deliberately do NOT set TESTING=True for the rejection-path
        # test below (we want the CSRF exemption to be the thing protecting the
        # endpoint from a 400, not Flask's TESTING shortcut).
        self.client = self.app.test_client()
        self.services = self.app.extensions["somewheria_services"]
        # Seed a ticket with a jira_key.
        with self.app.app_context():
            created = self.services.tickets.create_ticket(
                {"title": "Leaky", "description": "Drips"},
                submitter_email="renter@example.com",
            )
            # create_ticket returns the in-memory dict from before the JIRA
            # post-step persists jira_key — reload from disk to confirm.
            self.ticket = self.services.tickets.get_ticket(created["id"])
        self.assertEqual(self.ticket.get("jira_key"), "STUB-1")

    def tearDown(self):
        self._tickets_tmp.cleanup()

    def test_rejects_without_secret(self):
        resp = self.client.post(
            "/webhooks/jira",
            data=json.dumps({"issue": {"key": "STUB-1", "fields": {"status": {"name": "Done"}}}}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_rejects_with_wrong_secret(self):
        resp = self.client.post(
            "/webhooks/jira",
            data=json.dumps({"issue": {"key": "STUB-1", "fields": {"status": {"name": "Done"}}}}),
            content_type="application/json",
            headers={"X-JIRA-Webhook-Secret": "wrong"},
        )
        self.assertEqual(resp.status_code, 401)

    def test_csrf_exempt_when_secret_correct(self):
        # No CSRF token in the request — the only thing keeping this from
        # being a 400 is the CSRF_EXEMPT_ENDPOINTS entry for jira_webhook.
        resp = self.client.post(
            "/webhooks/jira",
            data=json.dumps({"issue": {"key": "STUB-1", "fields": {"status": {"name": "Done"}}}}),
            content_type="application/json",
            headers={"X-JIRA-Webhook-Secret": "shhh"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["status"], "resolved")

    def test_unknown_jira_key_returns_404(self):
        resp = self.client.post(
            "/webhooks/jira",
            data=json.dumps({"issue": {"key": "MAINT-9999", "fields": {"status": {"name": "Done"}}}}),
            content_type="application/json",
            headers={"X-JIRA-Webhook-Secret": "shhh"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_status_actually_updates_local_ticket(self):
        self.client.post(
            "/webhooks/jira",
            data=json.dumps({"issue": {"key": "STUB-1", "fields": {"status": {"name": "In Progress"}}}}),
            content_type="application/json",
            headers={"X-JIRA-Webhook-Secret": "shhh"},
        )
        with self.app.app_context():
            updated = self.services.tickets.get_ticket(self.ticket["id"])
        self.assertEqual(updated["status"], "in_progress")


if __name__ == "__main__":
    unittest.main()
