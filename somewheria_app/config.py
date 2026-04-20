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
    authorized_users: list[str] = field(default_factory=lambda: _csv_env("AUTHORIZED_USERS"))
    admin_users: list[str] = field(default_factory=lambda: _csv_env("ADMIN_USERS"))
    high_admin_users: list[str] = field(default_factory=lambda: _csv_env("HIGH_ADMIN_USERS"))

    def __post_init__(self) -> None:
        self.template_dir = self.base_dir / "templates"
        self.static_dir = self.base_dir / "static"
        self.upload_dir = self.static_dir / "uploads"
        self.log_file = self.base_dir / "application.log"
        self.change_log_file = self.base_dir / "site_changes.log"
        self.property_appointments_file = self.static_dir / "property_appointments.txt"
        self.registration_file = self.base_dir / "pending_registrations.json"
        self.user_roles_file = self.base_dir / "user_roles.json"
        self.renter_profile_file = self.base_dir / "renter_profiles.json"
        self.contracts_file = self.base_dir / "renter_contracts.json"

    def ensure_directories(self) -> None:
        self.static_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
