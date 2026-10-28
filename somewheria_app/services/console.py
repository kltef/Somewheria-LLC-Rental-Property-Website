import json
import logging
import sys
import time
from logging.handlers import RotatingFileHandler


LOGGER_NAME = "somewheria.console"

# Rotation policy for the on-disk log. Caps total footprint at ~50 MB
# (10 MB current + 4 rotated backups) so application.log can't grow without
# bound and slowly fill the instance disk.
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 4

try:  # Flask is always present at runtime; guard only so the module can be
    from flask import g, has_request_context  # imported in isolation (tooling).
except Exception:  # pragma: no cover
    g = None

    def has_request_context() -> bool:
        return False


class RequestContextFilter(logging.Filter):
    """Stamp every record with the current request's id (or '-' outside one).

    Attached to the logger itself so all existing log calls automatically
    carry the id without touching a single call site — that's what lets you
    follow one request across the several lines it emits.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = "-"
        if has_request_context():
            request_id = getattr(g, "request_id", "-") or "-"
        record.request_id = request_id
        if not hasattr(record, "component"):
            record.component = "app"
        return True


class ConsoleFormatter(logging.Formatter):
    """Human-readable line for the terminal / systemd journal."""

    def format(self, record):
        timestamp = self.formatTime(record, "%H:%M:%S")
        component = getattr(record, "component", "app")
        request_id = getattr(record, "request_id", "-")
        rid = f" [{request_id}]" if request_id and request_id != "-" else ""
        return f"[{timestamp}] {record.levelname:<7} {component}{rid}: {record.getMessage()}"


class JsonFormatter(logging.Formatter):
    """One JSON object per line — machine-parseable for search / shipping.

    Consumed by the /logs viewer (notifications.read_logs) and, once logs are
    centralized, by tools like CloudWatch Logs Insights or jq.

    Timestamps emit in UTC with a ``Z`` suffix, matching the wire format every
    other on-disk timestamp uses (``utcnow_iso()`` for the JSONL change log,
    tickets' ``created_at`` / ``updated_at``, contracts' ``created_at``, and
    lead captures). ``logging.Formatter.formatTime`` otherwise defaults to
    server-local time, so an operator correlating the ``/logs`` viewer
    against ``site_changes.log`` or a ticket's ``created_at`` sees entries
    drift by the server's local offset — and across a DST transition by a
    shifting offset, double-stamping or missing an hour. The ``Z`` suffix
    is also what the first-7-char ``YYYY-MM`` slice in
    ``AnalyticsTracker.recent_listing_activity`` relies on upstream, so
    a future "logs by month" bucket can reuse the same pattern.
    """

    # Switch the base class's internal strftime to UTC. The timestamps are
    # written to the shared ``application.log`` file read by the /logs viewer;
    # keeping them aligned with ``utcnow_iso()`` means operators don't have
    # to mentally offset by the server's local-timezone delta.
    converter = time.gmtime

    def format(self, record):
        entry = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "component": getattr(record, "component", "app"),
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _has_console_handler(logger: logging.Logger) -> bool:
    # A plain stream handler to stdout — deliberately excludes FileHandler /
    # RotatingFileHandler, which are StreamHandler subclasses.
    return any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in logger.handlers
    )


def setup_console_logger(level: str = "INFO", log_file=None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    # Idempotent: create_app may run more than once in a process (tests), so
    # only attach the filter/handlers when they're not already present.
    if not any(isinstance(f, RequestContextFilter) for f in logger.filters):
        logger.addFilter(RequestContextFilter())

    if not _has_console_handler(logger):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(ConsoleFormatter())
        logger.addHandler(handler)

    if log_file is not None:
        resolved = str(log_file)
        has_file_handler = any(
            isinstance(handler, logging.FileHandler)
            and getattr(handler, "baseFilename", "") == resolved
            for handler in logger.handlers
        )
        if not has_file_handler:
            file_handler = RotatingFileHandler(
                resolved,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(JsonFormatter())
            logger.addHandler(file_handler)

    return logger


def set_console_log_level(level: str) -> None:
    logging.getLogger(LOGGER_NAME).setLevel(getattr(logging, level.upper(), logging.INFO))


def get_console_logger(component: str = "app") -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logging.getLogger(LOGGER_NAME), {"component": component})
