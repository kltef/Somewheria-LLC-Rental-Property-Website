"""Zillow publish-only sync.

This module is a SCAFFOLD. The Zillow integration is currently blocked on:

- Confirmation of which integration path is available to us (Rental Manager
  API vs the legacy RSS-style listings feed).
- Production credentials (`ZILLOW_API_BASE_URL`, `ZILLOW_API_TOKEN`,
  `ZILLOW_FEED_KEY`).

Until those decisions land, ``ZillowPublisher`` accepts publish requests,
validates that credentials are present, and either no-ops with a warning (if
unconfigured) or logs a "would publish" stub (if configured). No real HTTP
calls are made anywhere in this module.

The retry queue and admin-status hooks are real, so when the upstream client
is plugged in, only the inner ``_perform_publish`` call needs to grow into a
real HTTP request.

The site is the source of truth — Zillow sync failures must NEVER block an
admin operation. Callers wrap enqueues in try/except defensively.
"""

import os
import threading
import time
from collections import deque
from typing import Optional

from .console import get_console_logger


# Backoff schedule for the retry worker. Exponential with base 4: 1s, 4s, 16s.
# Three attempts total (initial try + 2 retries handled here as 3 attempts).
_RETRY_BACKOFF_SECONDS = (1, 4, 16)
_MAX_ATTEMPTS = 3

# Cap on how many recent error records we keep on the publisher for the
# admin-status page. The publisher is in-memory and process-local; this is a
# rolling window, not a durable record.
_RECENT_ERROR_LIMIT = 20


class ZillowPublisher:
    """Publish-only Zillow sync with an in-memory retry queue.

    All methods are non-raising at the boundary: if credentials are missing,
    the call logs a warning and returns. If the (stubbed) upstream call fails,
    the retry worker handles backoff in a background daemon thread so admin
    operations return immediately.
    """

    def __init__(self, config, notifications) -> None:
        self.config = config
        self.notifications = notifications
        self.logger = get_console_logger("zillow")

        # Counters and rolling error window for the admin-status page. Guarded
        # by ``_stats_lock`` because the retry worker mutates them off-thread.
        self._stats_lock = threading.Lock()
        self.success_count = 0
        self.failure_count = 0
        self.last_success_at: Optional[float] = None
        self.last_error_at: Optional[float] = None
        self.recent_errors: deque = deque(maxlen=_RECENT_ERROR_LIMIT)

    # ------------------------------------------------------------------ public API

    def publish_create(self, property_data: dict) -> None:
        property_id = (property_data or {}).get("id", "")
        self._enqueue("create", property_id, {"property": property_data})

    def publish_update(self, property_data: dict) -> None:
        property_id = (property_data or {}).get("id", "")
        self._enqueue("update", property_id, {"property": property_data})

    def publish_delete(self, property_id: str) -> None:
        self._enqueue("delete", property_id, {})

    def publish_for_sale_toggle(self, property_id: str, for_sale: bool) -> None:
        self._enqueue("for_sale_toggle", property_id, {"for_sale": bool(for_sale)})

    # ------------------------------------------------------------------ status

    def credentials_configured(self) -> bool:
        """Return True iff all three Zillow env vars are present."""
        base, token, feed = self._read_credentials()
        return bool(base and token and feed)

    def status_snapshot(self) -> dict:
        """Snapshot used by the admin status page. Process-local; resets on restart."""
        with self._stats_lock:
            return {
                "configured": self.credentials_configured(),
                "success_count": self.success_count,
                "failure_count": self.failure_count,
                "last_success_at": self.last_success_at,
                "last_error_at": self.last_error_at,
                "recent_errors": list(self.recent_errors),
            }

    # ------------------------------------------------------------------ internals

    def _read_credentials(self) -> tuple[str, str, str]:
        return (
            os.getenv("ZILLOW_API_BASE_URL", "").strip(),
            os.getenv("ZILLOW_API_TOKEN", "").strip(),
            os.getenv("ZILLOW_FEED_KEY", "").strip(),
        )

    def _enqueue(self, action: str, property_id: str, payload: dict) -> None:
        """Spin up a daemon worker that retries the (stubbed) publish call."""
        # Mirror the EMAIL_APP_PASSWORD handling in NotificationService: if
        # credentials aren't set, log once and return without raising. We do
        # NOT spawn a worker — there is nothing to retry.
        if not self.credentials_configured():
            self.logger.warning(
                "Zillow credentials are not configured (ZILLOW_API_BASE_URL / "
                "ZILLOW_API_TOKEN / ZILLOW_FEED_KEY); skipping %s for %s",
                action,
                property_id or "<unknown>",
            )
            return

        worker = threading.Thread(
            target=self._retry_worker,
            args=(action, property_id, payload),
            daemon=True,
            name=f"zillow-publish-{action}-{property_id or 'na'}",
        )
        worker.start()

    def _retry_worker(self, action: str, property_id: str, payload: dict) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                self._perform_publish(action, property_id, payload)
                self._record_success(action, property_id)
                return
            except Exception as exc:  # pragma: no cover - stub never raises today
                last_exc = exc
                self.logger.warning(
                    "Zillow %s for %s failed on attempt %s/%s: %s",
                    action,
                    property_id,
                    attempt + 1,
                    _MAX_ATTEMPTS,
                    exc,
                )
                # Sleep BEFORE the next attempt so the backoff actually waits.
                # On the final attempt we fall through to failure handling
                # without sleeping.
                if attempt + 1 < _MAX_ATTEMPTS:
                    time.sleep(_RETRY_BACKOFF_SECONDS[attempt])

        # All attempts exhausted. Surface to the admin via email + record
        # for the status page.
        message = (
            f"Zillow publish failed after {_MAX_ATTEMPTS} attempts. "
            f"action={action} property_id={property_id} error={last_exc}"
        )
        self._record_failure(action, property_id, str(last_exc) if last_exc else "unknown")
        try:
            self.notifications.log_and_notify_error("Zillow Sync Failure", message)
        except Exception as notify_exc:  # pragma: no cover - best-effort
            self.logger.error("Could not notify admin of Zillow failure: %s", notify_exc)

    def _perform_publish(self, action: str, property_id: str, payload: dict) -> None:
        """STUB. Replace with real HTTP once the integration path is chosen.

        Today this just logs. It does NOT make any network calls. Callers /
        the retry worker treat any exception raised here as a failure and
        will back off accordingly.
        """
        # Re-check credentials at perform time. The env can theoretically
        # change between enqueue and perform; the retry worker should not
        # silently start sending requests with partial credentials.
        if not self.credentials_configured():
            raise RuntimeError("Zillow credentials disappeared between enqueue and publish")

        self.logger.info("would publish to Zillow: %s %s", action, property_id or "<unknown>")
        # Keep payload reference alive for future client implementation; right
        # now it is intentionally unused.
        _ = payload

    def _record_success(self, action: str, property_id: str) -> None:
        with self._stats_lock:
            self.success_count += 1
            self.last_success_at = time.time()
        self.logger.info("Zillow publish succeeded: %s %s", action, property_id)

    def _record_failure(self, action: str, property_id: str, error: str) -> None:
        with self._stats_lock:
            self.failure_count += 1
            self.last_error_at = time.time()
            self.recent_errors.append(
                {
                    "timestamp": time.time(),
                    "action": action,
                    "property_id": property_id,
                    "error": error,
                }
            )
