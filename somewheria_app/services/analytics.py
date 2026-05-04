import collections
import datetime
import time

from flask import current_app, g, request, session

from .console import get_console_logger


class AnalyticsTracker:
    def __init__(self, analytics_days: int) -> None:
        self.analytics_days = analytics_days
        self.logger = get_console_logger("http")
        self.site_visits = collections.defaultdict(int)
        self.unique_users = collections.defaultdict(set)
        self.logins = collections.defaultdict(int)
        self.errors = collections.defaultdict(int)

    def _prune_old_buckets(self, today: str) -> None:
        # Keep only the rolling window of ``analytics_days`` so the counters
        # don't grow unbounded over the lifetime of the process.
        try:
            cutoff = (
                datetime.date.fromisoformat(today)
                - datetime.timedelta(days=max(0, self.analytics_days))
            ).isoformat()
        except ValueError:
            return
        for bucket in (self.site_visits, self.unique_users, self.logins, self.errors):
            for day in [d for d in bucket.keys() if d < cutoff]:
                del bucket[day]

    def before_request(self) -> None:
        g.start_time = time.time()
        if request.endpoint == "static":
            return
        today = datetime.date.today().isoformat()
        self._prune_old_buckets(today)
        self.site_visits[today] += 1
        user = session.get("user") or {}
        visitor = user.get("email") or request.remote_addr or "anonymous"
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
        self._prune_old_buckets(today)
        self.logins[today] += 1
        self.unique_users[today].add(user_identifier)

    def record_error(self) -> None:
        today = datetime.date.today().isoformat()
        self._prune_old_buckets(today)
        self.errors[today] += 1

    def dashboard_data(self, property_count: int) -> tuple[dict, dict]:
        today = datetime.date.today().isoformat()
        metrics = {
            "site_visits": self.site_visits[today],
            "unique_users": len(self.unique_users[today]),
            "properties_listed": property_count,
            "logins_today": self.logins[today],
            "errors_last_24h": self.errors[today],
        }
        days = [
            (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()
            for offset in range(self.analytics_days - 1, -1, -1)
        ]
        chart_data = {
            "days": days,
            "visits": [self.site_visits.get(day, 0) for day in days],
            "logins": [self.logins.get(day, 0) for day in days],
            "errors": [self.errors.get(day, 0) for day in days],
            "unique_users": [len(self.unique_users.get(day, set())) for day in days],
        }
        return metrics, chart_data
