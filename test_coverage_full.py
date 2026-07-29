import base64
import copy
import importlib
import io
import os
import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, mock_open, patch

import requests
from flask import Flask, Response, abort
from PIL import Image

from somewheria_app import create_app
from somewheria_app.services.analytics import AnalyticsTracker
from somewheria_app.services.appointments import AppointmentService
from somewheria_app.services.auth import AuthService, auth_status_payload, renter_required
from somewheria_app.services.console import set_console_log_level
from somewheria_app.services.notifications import NotificationService
from somewheria_app.services.properties import PropertyService, UploadValidationError
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
        for filename in ("prop-1_abc123.png", "prop-1_bad.png", "prop-1_assoc.png", "prop-1_httperr.png"):
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

    def test_refresh_cache_coalesces_within_window(self):
        # Two refreshes back-to-back should only fan out once. The second
        # caller sees the recent timestamp and skips its own fanout — that's
        # the cost reduction /for-rent and /for-rent.json depend on when a
        # burst of visitors land on the same page in parallel.
        latest = [{"id": "prop-1"}]
        with patch.object(
            self.service, "fetch_all_properties", return_value=latest
        ) as fetch_mock:
            self.service.refresh_cache()
            self.service.refresh_cache()

        self.assertEqual(fetch_mock.call_count, 1)
        self.assertEqual(self.service.cache, latest)

    def test_refresh_cache_refetches_after_window(self):
        latest = [{"id": "prop-1"}]
        with patch.object(
            self.service, "fetch_all_properties", return_value=latest
        ) as fetch_mock:
            self.service.refresh_cache()
            # Force the coalesce window to lapse without sleeping.
            self.service._last_refresh_monotonic -= (
                self.service.REFRESH_COALESCE_SECONDS + 1.0
            )
            self.service.refresh_cache()

        self.assertEqual(fetch_mock.call_count, 2)

    def test_refresh_cache_failure_still_coalesces_followers(self):
        # An upstream outage records the attempt time too, so a queued
        # follower doesn't immediately re-hit a dead endpoint. The route
        # handler's existing UpstreamUnavailable fallback serves the cache
        # for both leader and follower.
        from somewheria_app.services.properties import UpstreamUnavailable

        self.service.cache = [{"id": "prop-1", "name": "Maple"}]
        with patch.object(
            self.service,
            "fetch_all_properties",
            side_effect=UpstreamUnavailable("upstream down"),
        ) as fetch_mock:
            with self.assertRaises(UpstreamUnavailable):
                self.service.refresh_cache()
            # Follower arriving within the coalesce window returns silently
            # rather than re-attempting the failed fetch.
            self.service.refresh_cache()

        self.assertEqual(fetch_mock.call_count, 1)
        self.assertEqual(self.service.cache, [{"id": "prop-1", "name": "Maple"}])

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

    def test_get_property_name_returns_matching_name(self):
        self.service.cache = [
            {"id": "prop-1", "name": "Maple"},
            {"id": "prop-2", "name": "Oak"},
        ]

        self.assertEqual(self.service.get_property_name("prop-2"), "Oak")

    def test_get_property_name_returns_none_when_missing(self):
        self.service.cache = [{"id": "prop-1", "name": "Maple"}]

        self.assertIsNone(self.service.get_property_name("nope"))

    def test_get_property_name_returns_none_when_name_is_not_a_string(self):
        # Upstream can hand back ``null`` (or a misshaped value) for the name
        # field; return None so callers don't render the literal "None".
        self.service.cache = [{"id": "prop-1", "name": None}]

        self.assertIsNone(self.service.get_property_name("prop-1"))

    def test_get_property_name_does_not_deep_copy_the_record(self):
        # The whole point of this method vs. get_property() is that it must
        # avoid the deep-copy of the full record (photos payload can be
        # tens of MB). Patch deepcopy to explode if anyone reaches for it.
        self.service.cache = [{"id": "prop-1", "name": "Maple"}]
        with patch(
            "somewheria_app.services.properties.copy.deepcopy",
            side_effect=AssertionError("get_property_name must not deep-copy"),
        ):
            self.assertEqual(self.service.get_property_name("prop-1"), "Maple")

    def test_trigger_background_refresh_starts_daemon_thread(self):
        thread_mock = Mock()
        with patch("somewheria_app.services.properties.threading.Thread", return_value=thread_mock) as thread_ctor:
            self.service.trigger_background_refresh("admin@example.com")

        self.assertEqual(thread_ctor.call_args.kwargs["args"], ("admin@example.com",))
        thread_mock.start.assert_called_once()

    def test_trigger_background_refresh_coalesces_concurrent_callers(self):
        # /for-rent-refresh.json accepts up to 6 GETs per minute per IP and
        # admin mutations also call this path, so a burst of callers used
        # to each spawn their own daemon thread doing a full 8-worker
        # upstream fanout. Coalesce so at most one is in flight at a time —
        # the running thread's cache update covers everyone arriving while
        # it works.
        thread_mock = Mock()
        with patch(
            "somewheria_app.services.properties.threading.Thread",
            return_value=thread_mock,
        ) as thread_ctor:
            self.service.trigger_background_refresh("admin@example.com")
            self.service.trigger_background_refresh("user@example.com")
            self.service.trigger_background_refresh("anonymous")

        thread_ctor.assert_called_once()
        thread_mock.start.assert_called_once()

    def test_refresh_with_change_log_updates_coalesce_timestamp(self):
        # Sharing ``_last_refresh_monotonic`` with refresh_cache lets a
        # synchronous /for-rent hit landing within the coalesce window
        # skip its own fanout after a background refresh just populated
        # the cache. Before the shared timestamp every admin mutation
        # silently forced a second upstream fetch on the next page load.
        latest = [{"id": "prop-1", "name": "New"}]
        with patch.object(
            self.service, "fetch_all_properties", return_value=latest
        ) as fetch_mock:
            self.service._refresh_with_change_log("admin@example.com")
            self.service.refresh_cache()

        fetch_mock.assert_called_once()
        self.assertEqual(self.service.cache, latest)

    def test_trigger_background_refresh_re_arms_after_worker_completes(self):
        # The in-flight guard must reset whether the worker succeeded or
        # raised, otherwise a single upstream blip would wedge the
        # background-refresh path forever (every subsequent caller would
        # see the flag still set and silently skip).
        thread_mock = Mock()
        with patch(
            "somewheria_app.services.properties.threading.Thread",
            return_value=thread_mock,
        ):
            self.service.trigger_background_refresh("admin@example.com")
        self.assertTrue(self.service._background_refresh_active)

        with patch.object(
            self.service, "fetch_all_properties", side_effect=RuntimeError("boom")
        ):
            self.service._refresh_with_change_log("admin@example.com")
        self.assertFalse(self.service._background_refresh_active)

        with patch(
            "somewheria_app.services.properties.threading.Thread",
            return_value=thread_mock,
        ) as thread_ctor:
            self.service.trigger_background_refresh("admin@example.com")
        thread_ctor.assert_called_once()

    def test_refresh_with_change_log_returns_when_snapshot_is_unchanged(self):
        self.service.cache = [{"id": "prop-1", "name": "Old"}]
        with patch.object(self.service, "fetch_all_properties", return_value=[{"id": "prop-1", "name": "Old"}]), patch.object(
            self.service.logger,
            "info",
        ) as info_mock:
            self.service._refresh_with_change_log("admin@example.com")

        self.notifications.log_site_change.assert_not_called()
        info_mock.assert_called_once()

    def test_refresh_with_change_log_treats_dict_key_order_as_equal(self):
        # The "no-change" short-circuit compares the live cache to the
        # upstream fetch with Python ``==`` rather than serializing both
        # sides to canonical JSON. Dict equality in Python 3 is already
        # order-independent, so a property whose fields come back in a
        # different key order from upstream must NOT register as a change
        # and trigger a phantom ``properties_cache_updated`` audit entry.
        self.service.cache = [{"id": "prop-1", "name": "Maple", "rent": "2000"}]
        latest = [{"rent": "2000", "id": "prop-1", "name": "Maple"}]
        with patch.object(self.service, "fetch_all_properties", return_value=latest):
            self.service._refresh_with_change_log("admin@example.com")

        self.notifications.log_site_change.assert_not_called()

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

    def test_refresh_with_change_log_coalesces_when_cache_recently_refreshed(self):
        # A ``/for-rent`` synchronous refresh just populated the cache; an
        # admin-triggered ``_refresh_with_change_log`` arriving within the
        # coalesce window should skip its upstream fanout rather than
        # double-fetching (and silently dropping a redundant change-log
        # diff). Without this, every admin mutation could spawn a duplicate
        # fanout on top of a still-fresh cache.
        self.service.cache = [{"id": "prop-1", "name": "Maple"}]
        with patch.object(
            self.service, "fetch_all_properties", return_value=[{"id": "prop-1", "name": "Maple"}]
        ) as fetch_mock:
            self.service.refresh_cache()
            fetch_mock.reset_mock()
            self.service._refresh_with_change_log("admin@example.com")

        fetch_mock.assert_not_called()

    def test_refresh_with_change_log_updates_last_refresh_monotonic(self):
        # After a successful admin-triggered refresh, the very next ``/for-rent``
        # hit must reuse the cache instead of immediately re-firing the
        # upstream fanout. Before this fix, ``_refresh_with_change_log``
        # forgot to stamp ``_last_refresh_monotonic`` and the coalesce
        # window never engaged for that path.
        self.service.cache = [{"id": "prop-1", "name": "Old"}]
        with patch.object(
            self.service,
            "fetch_all_properties",
            return_value=[{"id": "prop-1", "name": "New"}],
        ) as fetch_mock:
            self.service._refresh_with_change_log("admin@example.com")
            # Immediately afterward, a ``/for-rent`` hit calls refresh_cache()
            # — it should coalesce against the timestamp we just set.
            self.service.refresh_cache()

        self.assertEqual(fetch_mock.call_count, 1)

    def test_refresh_with_change_log_serializes_concurrent_triggers(self):
        # Multiple ``trigger_background_refresh`` calls firing in parallel
        # (e.g. several anonymous hits on ``/for-rent-refresh.json`` or a
        # burst of admin mutations) must serialize on ``_refresh_lock`` and
        # coalesce so they don't all fan out upstream simultaneously.
        import threading

        self.service.cache = [{"id": "prop-1", "name": "Old"}]
        ready = threading.Event()
        proceed = threading.Event()

        def slow_fetch():
            ready.set()
            proceed.wait(timeout=2)
            return [{"id": "prop-1", "name": "New"}]

        with patch.object(self.service, "fetch_all_properties", side_effect=slow_fetch) as fetch_mock:
            t1 = threading.Thread(target=self.service._refresh_with_change_log, args=("admin@example.com",))
            t1.start()
            self.assertTrue(ready.wait(timeout=2))
            t2 = threading.Thread(target=self.service._refresh_with_change_log, args=("admin2@example.com",))
            t2.start()
            proceed.set()
            t1.join(timeout=2)
            t2.join(timeout=2)

        # Only one fanout: the second trigger arrived after the first had
        # already stamped ``_last_refresh_monotonic`` and so coalesced.
        self.assertEqual(fetch_mock.call_count, 1)

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

    def test_build_change_log_does_not_mutate_its_inputs(self):
        # ``_refresh_with_change_log`` hands the live cache directly to
        # ``_build_change_log`` (no defensive deep-copy) so it can avoid
        # copying tens of MB of base64 photo data on every admin-triggered
        # refresh. That optimization is only safe if the diff routine is
        # strictly read-only on its inputs -- lock that contract in a test
        # so a future change can't silently regress the assumption and
        # corrupt the live cache mid-swap.
        current = [
            {"id": "prop-1", "name": "Old", "photos": ["data:image/jpeg;base64,AAA="]},
            {"id": "prop-2", "rent": "1000"},
        ]
        latest = [
            {"id": "prop-1", "name": "New", "photos": ["data:image/jpeg;base64,BBB="]},
            {"id": "prop-3", "rent": "1200"},
        ]
        # Deep-copy so ``assertEqual`` compares against the pre-call shape
        # even if the routine mutated something in place.
        current_before = copy.deepcopy(current)
        latest_before = copy.deepcopy(latest)

        self.service._build_change_log(current, latest)

        self.assertEqual(current, current_before)
        self.assertEqual(latest, latest_before)

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

    def test_fetch_property_ids_dedupes_duplicate_upstream_returns(self):
        # Belt-and-braces at the upstream boundary: if the ID-listing Lambda
        # ever returns the same id twice (transient replication, upstream
        # bug), a plain pass-through would fan out per-property fetches
        # twice AND leave two copies of the property in the cache — the
        # ``/for-rent`` page then shows duplicate cards and
        # ``get_property()`` only ever surfaces the first match. First-seen
        # order is preserved so tests / logs stay deterministic.
        response = Mock()
        response.json.return_value = {
            "property_ids": ["prop-1", "prop-2", "prop-1", "prop-3", "prop-2"]
        }

        with patch("somewheria_app.services.properties.requests.get", return_value=response):
            property_ids = self.service._fetch_property_ids()

        self.assertEqual(property_ids, ["prop-1", "prop-2", "prop-3"])

    def test_fetch_property_ids_raises_upstream_unavailable_on_failure(self):
        # A network/HTTP failure must surface as ``UpstreamUnavailable`` so
        # ``refresh_cache`` can leave the existing cache in place. Returning
        # an empty list here would silently clobber the listings during a
        # transient upstream outage.
        from somewheria_app.services.properties import UpstreamUnavailable

        with patch(
            "somewheria_app.services.properties.requests.get",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(UpstreamUnavailable):
                self.service._fetch_property_ids()

    def test_refresh_cache_preserves_existing_cache_on_upstream_failure(self):
        # The current cache must NOT be blanked when the upstream property
        # API is temporarily unreachable. ``refresh_cache`` propagates the
        # ``UpstreamUnavailable`` so the route's try/except can fall back
        # to serving whatever's already cached.
        from somewheria_app.services.properties import UpstreamUnavailable

        self.service.cache = [{"id": "prop-1", "name": "Maple"}]
        with patch.object(
            self.service,
            "_fetch_property_ids",
            side_effect=UpstreamUnavailable("upstream down"),
        ):
            with self.assertRaises(UpstreamUnavailable):
                self.service.refresh_cache()

        self.assertEqual(self.service.cache, [{"id": "prop-1", "name": "Maple"}])

    def test_refresh_with_change_log_preserves_cache_on_upstream_failure(self):
        # ``_refresh_with_change_log`` is invoked from a daemon thread for
        # admin-triggered refreshes; an upstream blip there must also leave
        # the cache (and the change log) untouched rather than recording a
        # spurious "everything was deleted" delta.
        from somewheria_app.services.properties import UpstreamUnavailable

        self.service.cache = [{"id": "prop-1", "name": "Maple"}]
        with patch.object(
            self.service,
            "fetch_all_properties",
            side_effect=UpstreamUnavailable("upstream down"),
        ):
            self.service._refresh_with_change_log("admin@example.com")

        self.assertEqual(self.service.cache, [{"id": "prop-1", "name": "Maple"}])
        self.notifications.log_site_change.assert_not_called()

    def test_refresh_with_change_log_records_success_health(self):
        # ``get_cache_health`` powers /admin/status. Before this fix, an
        # admin-triggered refresh (property_created / property_updated /
        # image_added / …) never touched ``_last_success_at`` /
        # ``_last_refresh_ok`` / ``_last_refresh_seconds``, so the status
        # page showed "never refreshed yet" until a public /for-rent hit
        # exercised ``refresh_cache`` — misleading during an admin-only
        # burst of activity.
        self.service.cache = [{"id": "prop-1", "name": "Old"}]
        with patch.object(
            self.service,
            "fetch_all_properties",
            return_value=[{"id": "prop-1", "name": "New"}],
        ):
            self.service._refresh_with_change_log("admin@example.com")

        health = self.service.get_cache_health()
        self.assertIs(health["last_attempt_ok"], True)
        self.assertIsNone(health["last_error"])
        self.assertIsNotNone(health["last_success_at"])
        self.assertIsNotNone(health["last_refresh_seconds"])

    def test_refresh_with_change_log_records_success_even_when_unchanged(self):
        # The "no-op, cache identical" path also successfully reached upstream —
        # it just happened to find no diff. The status page must still see
        # this as a successful refresh, otherwise a steady-state site with
        # no property changes but frequent admin activity would drift the
        # displayed health toward "stale" purely because nothing changed.
        self.service.cache = [{"id": "prop-1", "name": "Maple"}]
        with patch.object(
            self.service,
            "fetch_all_properties",
            return_value=[{"id": "prop-1", "name": "Maple"}],
        ):
            self.service._refresh_with_change_log("admin@example.com")

        health = self.service.get_cache_health()
        self.assertIs(health["last_attempt_ok"], True)
        self.assertIsNone(health["last_error"])
        self.assertIsNotNone(health["last_success_at"])

    def test_refresh_with_change_log_records_failure_health(self):
        # A failed admin-triggered refresh must surface the error to
        # /admin/status, mirroring refresh_cache's error path. Without this,
        # an ongoing upstream outage during admin activity would keep the
        # status page pinned to the last public /for-rent success.
        self.service._last_refresh_ok = True
        self.service._last_refresh_error = None
        with patch.object(
            self.service,
            "fetch_all_properties",
            side_effect=RuntimeError("upstream 502"),
        ):
            self.service._refresh_with_change_log("admin@example.com")

        health = self.service.get_cache_health()
        self.assertIs(health["last_attempt_ok"], False)
        self.assertIn("upstream 502", health["last_error"])
        self.assertIsNotNone(health["last_refresh_seconds"])

    def test_refresh_cache_records_consistent_timestamps(self):
        # ``_last_refresh_monotonic`` and ``_last_refresh_seconds`` are both
        # written in the ``finally`` block. The old code called
        # ``time.monotonic()`` twice, so the "when did we finish?" timestamp
        # and the "how long did it take?" duration derived from two different
        # clock samples — a subtle inconsistency for the follower-coalesce
        # comparison and the admin-status "creeping toward 29s" check. Pin the
        # invariant: the recorded end-time must equal start-time plus duration.
        # Sequence: [coalesce-check, started, completed].
        coalesce_check_at = 100.0
        started_at = 100.5
        finished_at = 106.0
        with patch.object(
            self.service, "fetch_all_properties", return_value=[]
        ), patch(
            "somewheria_app.services.properties.time.monotonic",
            side_effect=[coalesce_check_at, started_at, finished_at],
        ):
            self.service.refresh_cache()

        self.assertEqual(self.service._last_refresh_monotonic, finished_at)
        self.assertEqual(
            self.service._last_refresh_seconds, finished_at - started_at
        )

    def test_refresh_with_change_log_records_consistent_timestamps(self):
        # Same invariant as refresh_cache but for the admin-triggered path,
        # which had the same duplicate-monotonic pattern in its finally.
        # Sequence: [coalesce-check, started, completed].
        coalesce_check_at = 200.0
        started_at = 200.25
        finished_at = 208.5
        self.service.cache = [{"id": "prop-1", "name": "Old"}]
        with patch.object(
            self.service,
            "fetch_all_properties",
            return_value=[{"id": "prop-1", "name": "New"}],
        ), patch(
            "somewheria_app.services.properties.time.monotonic",
            side_effect=[coalesce_check_at, started_at, finished_at],
        ):
            self.service._refresh_with_change_log("admin@example.com")

        self.assertEqual(self.service._last_refresh_monotonic, finished_at)
        self.assertEqual(
            self.service._last_refresh_seconds, finished_at - started_at
        )

    def test_fetch_all_properties_preserves_cache_when_every_record_fetch_fails(self):
        # IDs listing succeeds, but every per-property details fetch fails
        # (transient 5xx on the details endpoint). Without the guard,
        # fetch_all_properties would return [] and refresh_cache would blank
        # the listings; with the guard it raises UpstreamUnavailable so the
        # existing cache survives the outage.
        from somewheria_app.services.properties import UpstreamUnavailable

        self.service.cache = [{"id": "prop-1", "name": "Maple"}]
        with patch.object(
            self.service,
            "_fetch_property_ids",
            return_value=["prop-1", "prop-2", "prop-3"],
        ), patch.object(
            self.service,
            "fetch_property_record",
            return_value=None,
        ):
            with self.assertRaises(UpstreamUnavailable):
                self.service.fetch_all_properties()
            with self.assertRaises(UpstreamUnavailable):
                self.service.refresh_cache()

        # Cache untouched -- /for-rent's try/except falls back to it.
        self.assertEqual(self.service.cache, [{"id": "prop-1", "name": "Maple"}])

    def test_fetch_all_properties_returns_partial_results_when_some_records_fetch(self):
        # Mixed success/failure should NOT raise — return the successful
        # records (matching pre-existing partial-failure behavior) and let
        # refresh_cache overwrite. This keeps the new guard narrow: it only
        # fires when *every* per-property fetch failed.
        good = {"id": "prop-1", "name": "Maple"}
        with patch.object(
            self.service,
            "_fetch_property_ids",
            return_value=["prop-1", "prop-2"],
        ), patch.object(
            self.service,
            "fetch_property_record",
            side_effect=[good, None],
        ):
            self.assertEqual(self.service.fetch_all_properties(), [good])

    def test_fetch_all_properties_empty_id_listing_does_not_raise(self):
        # Upstream legitimately reporting zero properties is not an outage.
        # An empty ID list must propagate as an empty result so refresh_cache
        # can clear the cache when properties are actually all removed.
        with patch.object(self.service, "_fetch_property_ids", return_value=[]):
            self.assertEqual(self.service.fetch_all_properties(), [])

    def test_fetch_property_ids_rejects_non_dict_payload(self):
        # A misbehaving upstream that returns a top-level list/string must not
        # silently iterate as characters or unrelated keys downstream.
        for bogus in (["prop-1", "prop-2"], "prop-1", 42, None):
            response = Mock()
            response.json.return_value = bogus
            with patch("somewheria_app.services.properties.requests.get", return_value=response):
                self.assertEqual(self.service._fetch_property_ids(), [])

    def test_fetch_property_ids_rejects_non_list_property_ids_field(self):
        response = Mock()
        response.json.return_value = {"property_ids": "prop-1"}

        with patch("somewheria_app.services.properties.requests.get", return_value=response):
            self.assertEqual(self.service._fetch_property_ids(), [])

    def test_fetch_property_ids_filters_out_non_string_entries(self):
        response = Mock()
        response.json.return_value = {"property_ids": ["prop-1", 42, None, "prop-2"]}

        with patch("somewheria_app.services.properties.requests.get", return_value=response):
            self.assertEqual(self.service._fetch_property_ids(), ["prop-1", "prop-2"])

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
        ):
            property_info = self.service.fetch_property_record("prop-1")

        self.assertEqual(property_info["id"], "prop-1")
        # Photos are kept as S3 URLs; with no thumbnail from upstream the first
        # photo URL is used as the thumbnail.
        self.assertEqual(
            property_info["photos"],
            ["https://example.com/a.jpg", "https://example.com/b.jpg"],
        )
        self.assertEqual(property_info["thumbnail"], "https://example.com/a.jpg")

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
        payload = buffer.getvalue()
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Length": str(len(payload))}
        response.raise_for_status.return_value = None
        response.iter_content.return_value = iter([payload])

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

    def test_create_property_returns_empty_id_when_response_is_not_a_dict(self):
        # Upstream sometimes returns a JSON list, scalar, or null body; the old
        # ``response.json().get(...)`` chain would raise AttributeError and 500
        # the admin POST. The route should still succeed with an empty new_id.
        form = SimpleNamespace(
            get=lambda key, default="": default,
            getlist=lambda key: [],
        )
        response = Mock()
        response.json.return_value = []  # list, not dict

        with patch.object(self.service, "property_payload_from_form", return_value={"address": "123 Main"}), patch(
            "somewheria_app.services.properties.requests.post",
            return_value=response,
        ), patch.object(self.service, "trigger_background_refresh"):
            new_id = self.service.create_property(form, "admin@example.com")

        self.assertEqual(new_id, "")
        self.notifications.log_site_change.assert_called_once()

    def test_create_property_returns_empty_id_when_response_body_is_invalid_json(self):
        form = SimpleNamespace(
            get=lambda key, default="": default,
            getlist=lambda key: [],
        )
        response = Mock()
        response.json.side_effect = ValueError("no json body")

        with patch.object(self.service, "property_payload_from_form", return_value={"address": "123 Main"}), patch(
            "somewheria_app.services.properties.requests.post",
            return_value=response,
        ), patch.object(self.service, "trigger_background_refresh"):
            new_id = self.service.create_property(form, "admin@example.com")

        self.assertEqual(new_id, "")

    def test_create_property_parses_response_json_only_once(self):
        # Guard against regressing into the double-parse pattern that wasted
        # CPU and made the code fragile to non-cached response shapes.
        form = SimpleNamespace(
            get=lambda key, default="": default,
            getlist=lambda key: [],
        )
        response = Mock()
        response.json.return_value = {"id": "prop-99"}

        with patch.object(self.service, "property_payload_from_form", return_value={"address": "123 Main"}), patch(
            "somewheria_app.services.properties.requests.post",
            return_value=response,
        ), patch.object(self.service, "trigger_background_refresh"):
            new_id = self.service.create_property(form, "admin@example.com")

        self.assertEqual(new_id, "prop-99")
        self.assertEqual(response.json.call_count, 1)

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
            stream = io.BytesIO(file_payload)

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
            ["/static/uploads/prop-1_abc123.png"]
        )
        self.notifications.log_site_change.assert_called_once()
        trigger_mock.assert_called_once_with("admin@example.com")

    def test_upload_image_raises_for_invalid_image_content(self):
        class UploadedFile:
            filename = "bad.png"
            stream = io.BytesIO(b"not-an-image")

        with self.assertRaises(UploadValidationError):
            self.service.upload_image("prop-1", UploadedFile(), "https://example.com", "admin@example.com")

        self.notifications.log_and_notify_error.assert_not_called()

    def test_upload_image_rejects_oversized_dimensions(self):
        from somewheria_app.services.properties import MAX_IMAGE_DIMENSION

        file_bytes = io.BytesIO()
        # Tiny on disk but past the per-side dimension cap.
        Image.new("RGB", (MAX_IMAGE_DIMENSION + 1, 10), color="white").save(
            file_bytes, format="PNG"
        )

        class UploadedFile:
            filename = "huge.png"
            stream = io.BytesIO(file_bytes.getvalue())

        with self.assertRaises(UploadValidationError) as ctx:
            self.service.upload_image(
                "prop-1", UploadedFile(), "https://example.com", "admin@example.com"
            )
        self.assertIn(str(MAX_IMAGE_DIMENSION), str(ctx.exception))
        # Nothing should have been written to disk for a rejected upload.
        for entry in self.upload_dir.iterdir():
            self.assertFalse(entry.name.startswith("prop-1_"))

    def test_letterbox_rejects_extreme_aspect_ratio(self):
        from somewheria_app.services.properties import MAX_IMAGE_DIMENSION

        # A tall, narrow input within the per-side dimension cap whose 16:9
        # letterbox would amplify pixel count past MAX_IMAGE_PIXELS. Using a
        # mock keeps the test from actually allocating that buffer.
        image = Mock()
        image.size = (100, MAX_IMAGE_DIMENSION)
        with self.assertRaises(UploadValidationError):
            self.service.letterbox_to_16_9(image)

    def test_get_base64_image_from_url_rejects_oversized_dimensions(self):
        from somewheria_app.services.properties import MAX_IMAGE_DIMENSION

        buffer = io.BytesIO()
        Image.new("RGB", (MAX_IMAGE_DIMENSION + 1, 10), color="white").save(
            buffer, format="PNG"
        )
        payload = buffer.getvalue()
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Length": str(len(payload))}
        response.raise_for_status.return_value = None
        response.iter_content.return_value = iter([payload])

        with patch(
            "somewheria_app.services.properties.requests.get", return_value=response
        ), patch.object(self.service.logger, "warning") as warning_mock:
            encoded = self.service.get_base64_image_from_url(
                "https://example.com/huge.png"
            )

        self.assertIsNone(encoded)
        warning_mock.assert_called_once()

    def test_get_base64_image_downscales_large_landscape_instead_of_dropping(self):
        # Regression: a full-res landscape photo (within the per-side cap) used
        # to letterbox past MAX_IMAGE_PIXELS and get dropped. It must now be
        # downscaled and returned as a data URI instead.
        buffer = io.BytesIO()
        Image.new("RGB", (6000, 4000), color="white").save(buffer, format="JPEG")
        payload = buffer.getvalue()
        response = MagicMock()
        response.__enter__.return_value = response
        response.headers = {"Content-Length": str(len(payload))}
        response.raise_for_status.return_value = None
        response.iter_content.return_value = iter([payload])

        with patch("somewheria_app.services.properties.requests.get", return_value=response):
            encoded = self.service.get_base64_image_from_url("https://example.com/big.jpg")

        self.assertIsNotNone(encoded)
        self.assertTrue(encoded.startswith("data:image/jpeg;base64,"))

    def test_upload_image_logs_association_failure(self):
        file_bytes = io.BytesIO()
        Image.new("RGB", (16, 9), color="yellow").save(file_bytes, format="PNG")
        file_payload = file_bytes.getvalue()

        class UploadedFile:
            filename = "photo.png"
            stream = io.BytesIO(file_payload)

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

    def test_upload_image_logs_association_http_error(self):
        # A 4xx/5xx upstream response used to be silently ignored because the
        # POST didn't call ``raise_for_status``. The local file is saved but
        # upstream never learns about the URL, so the next refresh blanks it
        # and the file becomes orphaned — make sure the failure surfaces.
        file_bytes = io.BytesIO()
        Image.new("RGB", (16, 9), color="yellow").save(file_bytes, format="PNG")
        file_payload = file_bytes.getvalue()

        class UploadedFile:
            filename = "photo.png"
            stream = io.BytesIO(file_payload)

        error_response = Mock()
        error_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        with patch("somewheria_app.services.properties.secrets.token_hex", return_value="httperr"), patch(
            "somewheria_app.services.properties.url_for",
            return_value="/static/uploads/prop-1_httperr.png",
        ), patch(
            "somewheria_app.services.properties.requests.post",
            return_value=error_response,
        ), patch.object(self.service.logger, "warning") as warning_mock, patch.object(
            self.service,
            "trigger_background_refresh",
        ):
            self.service.upload_image("prop-1", UploadedFile(), "https://example.com", "admin@example.com")

        warning_mock.assert_called_once()
        message = warning_mock.call_args.args[0]
        self.assertIn("Failed to upload image", message)


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
    # A realistic browser UA so analytics count these as human visits (an
    # empty/bot UA is now filtered out).
    BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"

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
        with self.app.test_request_context("/hello", headers={"User-Agent": self.BROWSER_UA}):
            from flask import session

            session["user"] = {"email": "user@example.com"}
            self.analytics.before_request()

        self.assertEqual(sum(self.analytics.site_visits.values()), 1)
        self.assertEqual(len(next(iter(self.analytics.unique_users.values()))), 1)

    def test_repeat_page_views_count_as_one_visit(self):
        # Browsing several pages within the session window is ONE visit,
        # not one per page view.
        for _ in range(5):
            with self.app.test_request_context("/hello", headers={"User-Agent": self.BROWSER_UA}):
                from flask import session

                session["user"] = {"email": "user@example.com"}
                self.analytics.before_request()

        self.assertEqual(sum(self.analytics.site_visits.values()), 1)

    def test_distinct_visitors_count_as_separate_visits(self):
        for email in ("a@example.com", "b@example.com"):
            with self.app.test_request_context("/hello", headers={"User-Agent": self.BROWSER_UA}):
                from flask import session

                session["user"] = {"email": email}
                self.analytics.before_request()

        self.assertEqual(sum(self.analytics.site_visits.values()), 2)

    def test_visit_counts_again_after_session_gap(self):
        from somewheria_app.services.analytics import VISIT_SESSION_GAP_SECONDS

        with self.app.test_request_context("/hello", headers={"User-Agent": self.BROWSER_UA}):
            from flask import session

            session["user"] = {"email": "user@example.com"}
            with patch("somewheria_app.services.analytics.time.monotonic", return_value=1000.0):
                self.analytics.before_request()
            # Same visitor returning after the session window: a new visit.
            with patch(
                "somewheria_app.services.analytics.time.monotonic",
                return_value=1000.0 + VISIT_SESSION_GAP_SECONDS,
            ):
                self.analytics.before_request()

        self.assertEqual(sum(self.analytics.site_visits.values()), 2)

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
        with patch.dict(os.environ, {"DISABLE_BACKGROUND_THREADS": "0"}, clear=False):
            app = create_app()

        self.assertFalse(app.config["DISABLE_BACKGROUND_THREADS"])

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
        # Unhandled exceptions are caught by the crash handler and return 503
        self.assertEqual(client.get("/force-500").status_code, 503)
        self.assertEqual(client.get("/force-502").status_code, 502)
        self.assertEqual(client.get("/force-503").status_code, 503)
        self.assertEqual(client.get("/force-504").status_code, 504)

    def test_crash_handler_dedupes_dynamic_route_crashes_by_endpoint(self):
        # Regression: previously fingerprinted by raw URL path, so every
        # /thing/<param> value crashed to a distinct fingerprint and the
        # 10-min cooldown never fired — risking an email storm. Fingerprint
        # is now keyed on the Flask endpoint, which is stable across
        # parameter values.
        import threading
        import time as time_module

        with patch.dict(os.environ, {"DISABLE_BACKGROUND_THREADS": "1"}, clear=False):
            app = create_app()
        app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

        @app.route("/thing/<param>")
        def thing_view(param):
            raise RuntimeError(f"boom-{param}")

        services = app.extensions["somewheria_services"]
        send_calls: list[tuple[str, str]] = []
        send_event = threading.Event()

        def fake_send(subject, body, **kwargs):
            send_calls.append((subject, body))
            send_event.set()
            return True

        services.notifications.send_email = fake_send

        client = app.test_client()
        for value in ("aaa", "bbb", "ccc"):
            self.assertEqual(client.get(f"/thing/{value}").status_code, 503)

        # The worker that calls send_email is spawned on a daemon thread.
        # Wait briefly for at least one to fire; the assertion below verifies
        # that exactly one fired (the others were suppressed by fingerprint).
        send_event.wait(timeout=2.0)
        time_module.sleep(0.1)  # give any racing extra threads a chance to land
        self.assertEqual(len(send_calls), 1)

    def test_before_request_skips_static_endpoint(self):
        with self.app.test_client() as client:
            client.get("/static/missing.css")

        self.assertEqual(sum(self.analytics.site_visits.values()), 0)

    def test_before_request_skips_bot_user_agents(self):
        # Real page (matched endpoint) but an automated client — not a visit.
        for headers in (
            {"User-Agent": "Mozilla/5.0 (l9scan/2.0; +https://leakix.net)"},
            {"User-Agent": "Mozilla/5.0 (compatible; GPTBot/1.4; +https://openai.com/gptbot)"},
            {"User-Agent": "python-requests/2.31.0"},
            {},  # no User-Agent header at all
        ):
            with self.app.test_request_context("/hello", headers=headers):
                self.analytics.before_request()

        self.assertEqual(sum(self.analytics.site_visits.values()), 0)
        self.assertEqual(sum(len(s) for s in self.analytics.unique_users.values()), 0)

    def test_before_request_skips_unmatched_route_probes(self):
        # Scanner probing a path with no route (would 404) — not a visit,
        # even with a browser-looking User-Agent.
        for path in ("/.env", "/wp-login.php"):
            with self.app.test_request_context(path, headers={"User-Agent": self.BROWSER_UA}):
                self.analytics.before_request()

        self.assertEqual(sum(self.analytics.site_visits.values()), 0)

    def test_before_request_counts_real_browser_on_real_page(self):
        with self.app.test_request_context("/hello", headers={"User-Agent": self.BROWSER_UA}):
            self.analytics.before_request()

        self.assertEqual(sum(self.analytics.site_visits.values()), 1)

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

    def _set_oauth_state(self, state="test-state"):
        with self.client.session_transaction() as sess:
            sess["oauth_state"] = state

    def test_google_callback_rejects_unapproved_external_email(self):
        # A random Gmail with no assigned role and not in any list is denied.
        self.configure_google()
        self._set_oauth_state()
        self.services.config.authorized_users = []
        flow = self.make_flow()
        with patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow), patch(
            "somewheria_app.routes.auth_routes.id_token.verify_oauth2_token",
            return_value={"email": "stranger@gmail.com"},
        ), patch.object(self.services.auth, "get_user_role", return_value="guest"):
            response = self.client.get("/google/callback?state=test-state")

        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Access denied", response.data)

    def test_google_callback_allows_approved_external_email(self):
        # An approved applicant (Gmail/Outlook) whose role was set to "renter"
        # through the registration flow must be able to sign in.
        self.configure_google()
        self._set_oauth_state()
        self.services.config.authorized_users = []
        flow = self.make_flow()
        with patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow), patch(
            "somewheria_app.routes.auth_routes.id_token.verify_oauth2_token",
            return_value={"sub": "9", "email": "approved@gmail.com", "name": "Approved"},
        ), patch.object(self.services.auth, "get_user_role", return_value="renter"), patch.object(
            self.services.auth, "login_user", return_value={"email": "approved@gmail.com"}
        ) as login_mock, patch.object(self.services.analytics, "record_login"):
            response = self.client.get("/google/callback?state=test-state", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        login_mock.assert_called_once()

    def test_google_callback_rejects_unauthorized_company_user(self):
        self.configure_google()
        self._set_oauth_state()
        self.services.config.authorized_users = ["allowed@ekbergproperties.com"]
        flow = self.make_flow()
        with patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow), patch(
            "somewheria_app.routes.auth_routes.id_token.verify_oauth2_token",
            return_value={"email": "blocked@ekbergproperties.com"},
        ), patch.object(self.services.notifications, "log_and_notify_error") as notify_mock:
            response = self.client.get("/google/callback?state=test-state")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Access denied", response.data)
        notify_mock.assert_called_once()

    def test_google_callback_logs_user_in_when_authorized(self):
        self.configure_google()
        self._set_oauth_state()
        self.services.config.authorized_users = []
        flow = self.make_flow()
        with patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow), patch(
            "somewheria_app.routes.auth_routes.id_token.verify_oauth2_token",
            return_value={"sub": "123", "email": "user@ekbergproperties.com", "name": "User"},
        ), patch.object(self.services.auth, "get_user_role", return_value="renter"), patch.object(
            self.services.auth, "login_user", return_value={"email": "user@ekbergproperties.com"}
        ) as login_mock, patch.object(
            self.services.analytics,
            "record_login",
        ) as record_login_mock:
            response = self.client.get("/google/callback?state=test-state", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        login_mock.assert_called_once()
        record_login_mock.assert_called_once_with("user@ekbergproperties.com")

    def test_google_callback_handles_flow_failure(self):
        self.configure_google()
        self._set_oauth_state()
        flow = self.make_flow(fetch_side_effect=RuntimeError("boom"))
        with patch("somewheria_app.routes.auth_routes.Flow.from_client_config", return_value=flow), patch.object(
            self.services.notifications,
            "log_and_notify_error",
        ) as notify_mock:
            response = self.client.get("/google/callback?state=test-state")

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
        self.assertIn(b"Failed to save changes", response.data)
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
        # The delete branch decides success from the caller's *effective* role
        # (via auth.get_user_role), not from delete_user_role's return value,
        # so the delete_ok path needs an existing storage entry to be visible
        # and the delete_missing path needs the target to resolve to guest.
        with patch.object(self.services.analytics, "dashboard_data", return_value=({}, {})), patch.object(
            self.services.storage,
            "get_user_roles",
            return_value={"existing@example.com": "admin", "user@example.com": "renter"},
        ), patch.object(self.services.storage, "delete_user_role", return_value=True), patch.object(
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

        self.assertIn(b"Deactivated", delete_ok.data)
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
        # target_role must resolve to something above "guest" for the delete
        # branch to reach storage — the route short-circuits guest targets to
        # "User not found." without touching user_roles.
        with patch.object(self.services.storage, "delete_user_role", return_value=True), patch.object(
            self.services.storage,
            "get_user_roles",
            side_effect=[{"user@example.com": "renter"}, {"user@example.com": "renter"}, {"user@example.com": "renter"}],
        ):
            delete_response = self.client.post(
                "/admin/users",
                data={"email": "user@example.com", "action": "delete"},
            )

        with patch.object(self.services.storage, "get_user_roles", side_effect=[{}, {}, {}]), patch.object(
            self.services.storage,
            "set_user_role",
        ) as set_role_mock:
            invalid_response = self.client.post(
                "/admin/users",
                data={"email": "user@example.com", "action": "update", "role": "bad-role"},
            )

        self.assertIn(b"Deactivated", delete_response.data)
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
        self.assertIn(b"Operation failed", response.data)
        notify_mock.assert_called_once()

    def test_toggle_sale_returns_500_on_generic_error(self):
        self.login_as("admin", email="admin@example.com")
        with patch.object(self.services.properties, "toggle_sale", side_effect=RuntimeError("boom")), patch.object(
            self.services.notifications,
            "log_and_notify_error",
        ) as notify_mock:
            response = self.client.post("/toggle-sale/prop-1")

        self.assertEqual(response.status_code, 500)
        self.assertIn(b"Operation failed", response.data)
        notify_mock.assert_called_once()


class CoverageStartupExecutionTestCase(unittest.TestCase):
    def test_start_cache_refresh_thread_calls_service(self):
        with patch.object(
            website_app.app.extensions["somewheria_services"].properties,
            "start_background_refresh",
        ) as refresh_mock:
            website_app.start_cache_refresh_thread()

        refresh_mock.assert_not_called()

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

        # Strip PORT/HOST/LOG_LEVEL so the non-interactive defaults branch
        # returns the in-code defaults rather than whatever the surrounding
        # env happens to set.
        env_overrides = {
            k: v
            for k, v in os.environ.items()
            if k not in {"PORT", "HOST", "LOG_LEVEL"}
        }
        with patch.dict(os.environ, env_overrides, clear=True), patch(
            "somewheria_app.create_app", return_value=fake_app
        ), patch(
            "somewheria_app.services.console.get_console_logger",
            return_value=fake_logger,
        ), patch("somewheria_app.services.console.set_console_log_level") as set_level_mock, patch(
            "sys.stdin.isatty",
            return_value=False,
        ), patch(
            "sys.stdout.isatty",
            return_value=False,
        ), patch.dict(os.environ, {"PORT": "5000"}):
            runpy.run_module("website_app", run_name="__main__")

        set_level_mock.assert_called_once_with("INFO")
        fake_services.properties.refresh_cache.assert_called_once()
        fake_services.appointments.print_check_file.assert_called_once()
        fake_app.run.assert_called_once_with("0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    unittest.main()
