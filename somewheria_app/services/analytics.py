import collections
import datetime
import json
import threading
import time

from flask import current_app, g, request, session

from .console import get_console_logger


class AnalyticsTracker:
    def __init__(self, analytics_days: int, config=None) -> None:
        self.analytics_days = analytics_days
        self.config = config
        self.logger = get_console_logger("http")
        self.site_visits = collections.defaultdict(int)
        self.unique_users = collections.defaultdict(set)
        self.logins = collections.defaultdict(int)
        self.errors = collections.defaultdict(int)
        # Serializes bucket mutations across concurrent requests. Without it,
        # two threads crossing a day boundary can race inside
        # ``_prune_old_buckets`` -- one snapshot iterates ``bucket.keys()``
        # while the other deletes from the same dict, raising RuntimeError
        # ("dictionary changed size during iteration") or KeyError.
        self._lock = threading.Lock()

    def _prune_old_buckets(self, today: str) -> None:
        # Keep only the rolling window of ``analytics_days`` so the counters
        # don't grow unbounded over the lifetime of the process. Caller must
        # already hold ``self._lock`` -- this is invariant inside the class
        # so we don't reacquire (avoiding a needless re-entrant pattern).
        try:
            cutoff = (
                datetime.date.fromisoformat(today)
                - datetime.timedelta(days=max(0, self.analytics_days))
            ).isoformat()
        except ValueError:
            return
        for bucket in (self.site_visits, self.unique_users, self.logins, self.errors):
            for day in [d for d in list(bucket) if d < cutoff]:
                bucket.pop(day, None)

    def before_request(self) -> None:
        g.start_time = time.time()
        if request.endpoint == "static":
            return
        today = datetime.date.today().isoformat()
        user = session.get("user") or {}
        visitor = user.get("email") or request.remote_addr or "anonymous"
        with self._lock:
            self._prune_old_buckets(today)
            self.site_visits[today] += 1
            self.unique_users[today].add(visitor)

    def after_request(self, response):
        try:
            if current_app.config.get("SHOW_REQUEST_LOGS", True) and hasattr(g, "start_time"):
                elapsed_time = time.time() - g.start_time
                duration = f"{elapsed_time * 1000:.2f}ms" if elapsed_time < 0.1 else f"{elapsed_time:.2f}s"
                self.logger.info(
                    "%s %s -> %s in %s",
                    request.method,
                    request.path,
                    response.status_code,
                    duration,
                )
        except Exception as exc:
            self.logger.warning("Failed to record request timing: %s", exc)
        return response

    def record_login(self, user_identifier: str) -> None:
        today = datetime.date.today().isoformat()
        with self._lock:
            self._prune_old_buckets(today)
            self.logins[today] += 1
            self.unique_users[today].add(user_identifier)

    def record_error(self) -> None:
        today = datetime.date.today().isoformat()
        with self._lock:
            self._prune_old_buckets(today)
            self.errors[today] += 1

    def dashboard_data(self, property_count: int) -> tuple[dict, dict]:
        today = datetime.date.today().isoformat()
        days = [
            (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()
            for offset in range(self.analytics_days - 1, -1, -1)
        ]
        # Snapshot under the lock so the dashboard sees a consistent view even
        # if a request handler is mutating buckets concurrently.
        with self._lock:
            metrics = {
                "site_visits": self.site_visits[today],
                "unique_users": len(self.unique_users[today]),
                "properties_listed": property_count,
                "logins_today": self.logins[today],
                "errors_last_24h": self.errors[today],
            }
            chart_data = {
                "days": days,
                "visits": [self.site_visits.get(day, 0) for day in days],
                "logins": [self.logins.get(day, 0) for day in days],
                "errors": [self.errors.get(day, 0) for day in days],
                "unique_users": [len(self.unique_users.get(day, set())) for day in days],
            }
        return metrics, chart_data

    def recent_listing_activity(self, months: int = 12) -> dict:
        """Aggregate property created/deleted counts over the last ``months``.

        Reads ``site_changes.log`` (a JSONL file) using a bounded deque so a
        long-running process with a large change log doesn't load the entire
        history into memory. Mirrors the bounded-tail pattern used by
        ``NotificationService.read_logs``.

        Returns ``{"months": [...], "created": [...], "deleted": [...]}``
        where ``months`` is a list of ``YYYY-MM`` labels in chronological
        order ending with the current month.
        """
        months = max(1, int(months))
        # Labels are computed from UTC "today" because entries in
        # site_changes.log are written with utcnow_iso() and bucketed by the
        # UTC ``YYYY-MM`` prefix. Using ``date.today()`` (local) here would
        # silently drop the first hours of a new UTC month whenever the
        # server's local date lags UTC (or the last hours of a UTC month
        # when local leads UTC) -- the entries land in a month label that
        # isn't in the window yet.
        today = datetime.datetime.now(datetime.timezone.utc).date()
        labels: list[str] = []
        # Build labels in chronological order ending on the current month.
        year, month = today.year, today.month
        for _ in range(months):
            labels.append(f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        labels.reverse()
        label_set = set(labels)
        created: dict[str, int] = {label: 0 for label in labels}
        deleted: dict[str, int] = {label: 0 for label in labels}

        change_log = getattr(self.config, "change_log_file", None) if self.config else None
        if change_log and change_log.exists():
            # Cap memory: the log is JSONL and append-only. Keep only the
            # last 50k lines in memory at a time -- sufficient for any
            # realistic 12-month window without unbounded growth.
            tail: collections.deque[str] = collections.deque(maxlen=50000)
            try:
                with change_log.open("r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        line = line.strip()
                        if line:
                            tail.append(line)
            except OSError as exc:
                self.logger.warning("Could not read site_changes.log: %s", exc)
                tail.clear()
            for raw in tail:
                try:
                    entry = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                action = entry.get("action")
                if action not in ("property_created", "property_deleted"):
                    continue
                ts = entry.get("timestamp") or ""
                # Timestamps are ISO 8601 from datetime.isoformat(); the
                # YYYY-MM prefix is the first 7 chars and avoids parsing.
                key = ts[:7]
                if key not in label_set:
                    continue
                if action == "property_created":
                    created[key] += 1
                else:
                    deleted[key] += 1

        return {
            "months": labels,
            "created": [created[label] for label in labels],
            "deleted": [deleted[label] for label in labels],
        }
