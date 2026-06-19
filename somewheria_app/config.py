import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _csv_env(name: str) -> list[str]:
    return [item.strip().lower() for item in os.getenv(name, "").split(",") if item.strip()]


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
    cache_refresh_interval: int = field(default_factory=lambda: int(os.getenv("CACHE_REFRESH_INTERVAL", "60")))
    analytics_days: int = 7
    email_sender: str = "anthony.j.ekberg@gmail.com"
    email_recipient: str = "anthony@ekbergproperties.com"
    console_log_level: str = field(default_factory=lambda: os.getenv("CONSOLE_LOG_LEVEL", "INFO"))
    disable_background_threads: bool = field(
        default_factory=lambda: os.getenv("DISABLE_BACKGROUND_THREADS") == "1"
    )
    use_sqlite_storage: bool = field(
        default_factory=lambda: os.getenv("USE_SQLITE_STORAGE", "0") == "1"
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
    # Whether to send a Priority field on maintenance-ticket issues. Default on,
    # but some JIRA projects don't expose Priority on the create screen, which
    # makes the create call 400. Set JIRA_SET_PRIORITY=0 in that case.
    jira_set_priority: bool = field(
        default_factory=lambda: os.getenv("JIRA_SET_PRIORITY", "1") == "1"
    )

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
