import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _csv_env(name: str) -> list[str]:
    return [item.strip().lower() for item in os.getenv(name, "").split(",") if item.strip()]


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass
class AppConfig:
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    secret_key: str = field(default_factory=lambda: os.getenv("SECRET_KEY", secrets.token_hex(32)))
    google_client_id: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_ID", ""))
    google_client_secret: str = field(default_factory=lambda: os.getenv("GOOGLE_CLIENT_SECRET", ""))
    google_redirect_uri: str = field(
        default_factory=lambda: os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:5000/google/callback")
    )
    api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "PROPERTIES_API_BASE_URL",
            "https://7pdnexz05a.execute-api.us-east-1.amazonaws.com/test",
        ).rstrip("/")
    )
    # Write endpoints (POST/PUT/DELETE) on the properties API require this
    # x-api-key; reads are public. Unset means upstream rejects writes (403).
    api_key: str = field(default_factory=lambda: os.getenv("PROPERTIES_API_KEY", ""))
    # ``int()`` alone would raise ValueError at startup on a mistyped env value
    # (``CACHE_REFRESH_INTERVAL=sixty``) and refuse to boot. ``_int_env``
    # matches the ``TRUSTED_PROXY_COUNT`` contract: blank / non-numeric /
    # negative all fall back to the default rather than crashing.
    cache_refresh_interval: int = field(
        default_factory=lambda: _int_env("CACHE_REFRESH_INTERVAL", 60)
    )
    analytics_days: int = 7
    # Gmail account the app sends from (must match EMAIL_APP_PASSWORD) and
    # the default inbox notifications land in. Env-overridable so the
    # recipient can be repointed (e.g. to the property manager) without a
    # code change.
    email_sender: str = field(
        default_factory=lambda: os.getenv("EMAIL_SENDER", "anthony.j.ekberg@gmail.com")
    )
    email_recipient: str = field(
        default_factory=lambda: os.getenv("EMAIL_RECIPIENT", "anthony@ekbergproperties.com")
    )
    console_log_level: str = field(default_factory=lambda: os.getenv("CONSOLE_LOG_LEVEL", "INFO"))
    disable_background_threads: bool = field(
        default_factory=lambda: os.getenv("DISABLE_BACKGROUND_THREADS") == "1"
    )
    use_sqlite_storage: bool = field(
        default_factory=lambda: os.getenv("USE_SQLITE_STORAGE", "0") == "1"
    )
    # Number of trusted reverse-proxy hops in front of the app. When 0
    # (default) the rate limiter and crash log use ``request.remote_addr``
    # directly and ignore client-supplied ``X-Forwarded-For``, which
    # otherwise lets an attacker rotate that header to bypass per-IP
    # throttles. Set to the number of proxies you control (e.g. 1 for a
    # single nginx in front) so Werkzeug's ProxyFix can strip exactly
    # that many entries from the right of ``X-Forwarded-For`` and expose
    # the real client IP as ``remote_addr``. A non-numeric value falls
    # back to 0 — fail closed rather than fail open.
    trusted_proxy_count: int = field(
        default_factory=lambda: _int_env("TRUSTED_PROXY_COUNT", 0)
    )
    authorized_users: list[str] = field(default_factory=lambda: _csv_env("AUTHORIZED_USERS"))
    admin_users: list[str] = field(default_factory=lambda: _csv_env("ADMIN_USERS"))
    high_admin_users: list[str] = field(default_factory=lambda: _csv_env("HIGH_ADMIN_USERS"))
    # JIRA integration (Phase 3 §6) — optional. When any of the four credential
    # values are missing, JiraClient operates as a no-op and ticket creation is
    # unaffected. The webhook secret is independent and only validates inbound
    # webhook requests at /webhooks/jira.
    jira_base_url: str = field(default_factory=lambda: os.getenv("JIRA_BASE_URL", "").rstrip("/"))
    jira_project_key: str = field(default_factory=lambda: os.getenv("JIRA_PROJECT_KEY", ""))
    jira_api_token: str = field(default_factory=lambda: os.getenv("JIRA_API_TOKEN", ""))
    jira_user_email: str = field(default_factory=lambda: os.getenv("JIRA_USER_EMAIL", ""))
    jira_webhook_secret: str = field(default_factory=lambda: os.getenv("JIRA_WEBHOOK_SECRET", ""))

    def __post_init__(self) -> None:
        self.template_dir = self.base_dir / "templates"
        self.static_dir = self.base_dir / "static"
        self.upload_dir = self.static_dir / "uploads"
        # Contract PDFs are auth-gated (renter or admin only). They MUST NOT
        # sit under ``static_dir`` — Flask serves anything in the static tree
        # at ``/static/<path>`` with no auth check, which would let anyone
        # who guesses (or has been linked to) a PDF's UUID download it,
        # bypassing the ``/contracts/<id>/download`` access control. Storing
        # them under ``base_dir / "private"`` keeps them out of the static
        # route entirely; ``contract_download`` is the sole access path.
        self.private_dir = self.base_dir / "private"
        self.contract_upload_dir = self.private_dir / "contracts"
        self.ticket_upload_dir = self.upload_dir / "tickets"
        self.log_file = self.base_dir / "application.log"
        self.change_log_file = self.base_dir / "site_changes.log"
        self.property_appointments_file = self.base_dir / "property_appointments.txt"
        self.registration_file = self.base_dir / "pending_registrations.json"
        self.user_roles_file = self.base_dir / "user_roles.json"
        self.renter_profile_file = self.base_dir / "renter_profiles.json"
        self.contracts_file = self.base_dir / "renter_contracts.json"
        self.tickets_file = self.base_dir / "tickets.json"
        self.sqlite_file = self.base_dir / "somewheria.sqlite3"
        self.lead_capture_file = self.base_dir / "pending_lead_captures.json"
        # Listing ids an admin has deactivated (hidden from the public site
        # but kept upstream). Stored locally because the upstream properties
        # table has no active/status column — deactivation is a presentation
        # concern of this site, not upstream data.
        self.hidden_listings_file = self.base_dir / "hidden_listings.json"

    def ensure_directories(self) -> None:
        self.static_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.private_dir.mkdir(parents=True, exist_ok=True)
        self.contract_upload_dir.mkdir(parents=True, exist_ok=True)
        self.ticket_upload_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_contract_pdfs()

    def _migrate_legacy_contract_pdfs(self) -> None:
        # Earlier versions stored contract PDFs under static/uploads/contracts/,
        # where Flask's static handler served them unauthenticated. Move any
        # leftover files out of that directory on startup so an in-place
        # upgrade doesn't continue to expose them.
        legacy_dir = self.upload_dir / "contracts"
        try:
            if not legacy_dir.exists() or legacy_dir.resolve() == self.contract_upload_dir.resolve():
                return
        except OSError:
            return
        try:
            entries = list(legacy_dir.iterdir())
        except OSError:
            return
        for source in entries:
            if not source.is_file():
                continue
            destination = self.contract_upload_dir / source.name
            try:
                if destination.exists():
                    source.unlink()
                else:
                    source.replace(destination)
            except OSError:
                # Best-effort: a permission error here is logged via the next
                # admin operation's failure path. Don't crash startup.
                continue
        try:
            # Remove the empty legacy directory so future startups skip the
            # migration branch entirely.
            legacy_dir.rmdir()
        except OSError:
            pass
