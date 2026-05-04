import json
import os
import tempfile
import threading

from .console import get_console_logger


class FileStorageService:
    def __init__(self, config) -> None:
        self.config = config
        self.file_lock = threading.Lock()
        self.logger = get_console_logger("storage")

    def load_json_file(self, path, default, *, expected_type: type | tuple[type, ...] | None = None):
        try:
            with self.file_lock:
                if path.exists():
                    with path.open("r", encoding="utf-8") as handle:
                        loaded = json.load(handle)
                    # If the caller declared an expected shape, fall back to
                    # the default when the file's contents don't match. Without
                    # this, a corrupted or hand-edited file holding a dict
                    # where a list is expected would crash callers that do
                    # ``.append`` or list-iteration on the return value.
                    if expected_type is not None and not isinstance(loaded, expected_type):
                        self.logger.warning(
                            "Unexpected JSON shape in %s (got %s); using default",
                            path,
                            type(loaded).__name__,
                        )
                        return default
                    return loaded
        except Exception as exc:
            self.logger.error("Failed to load %s: %s", path, exc)
        return default

    def save_json_file(self, path, data) -> None:
        try:
            with self.file_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(
                    prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump(data, handle, indent=2)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp_name, path)
                except Exception:
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
                    raise
        except Exception as exc:
            self.logger.error("Failed to save %s: %s", path, exc)

    def get_pending_registrations(self) -> list[dict]:
        return self.load_json_file(self.config.registration_file, [], expected_type=list)

    def add_pending_registration(self, registration: dict) -> None:
        registrations = self.get_pending_registrations()
        registrations.append(registration)
        self.save_json_file(self.config.registration_file, registrations)

    def remove_pending_registration(self, email: str) -> None:
        registrations = [
            item for item in self.get_pending_registrations() if item.get("email", "").lower() != email.lower()
        ]
        self.save_json_file(self.config.registration_file, registrations)

    def get_user_roles(self) -> dict:
        return self.load_json_file(self.config.user_roles_file, {}, expected_type=dict)

    def set_user_role(self, email: str, role: str) -> None:
        roles = self.get_user_roles()
        roles[email.lower()] = role
        self.save_json_file(self.config.user_roles_file, roles)

    def delete_user_role(self, email: str) -> bool:
        email = email.lower()
        roles = self.get_user_roles()
        previous = roles.get(email)
        # Store a tombstone ("revoked") instead of removing the key outright
        # so that env-var fallbacks in AuthService.get_user_role cannot
        # silently restore a deleted user's access on their next login.
        roles[email] = "revoked"
        self.save_json_file(self.config.user_roles_file, roles)
        return previous is not None and previous != "revoked"

    def get_renter_profiles(self) -> dict:
        return self.load_json_file(self.config.renter_profile_file, {}, expected_type=dict)

    def save_renter_profiles(self, profiles: dict) -> None:
        self.save_json_file(self.config.renter_profile_file, profiles)

    def get_renter_contracts(self) -> dict:
        return self.load_json_file(self.config.contracts_file, {}, expected_type=dict)

    def save_renter_contracts(self, contracts: dict) -> None:
        self.save_json_file(self.config.contracts_file, contracts)
