import os
import tempfile
import threading

from .console import get_console_logger


class AppointmentService:
    def __init__(self, config) -> None:
        self.config = config
        self.logger = get_console_logger("appointments")
        # Serialize reads/writes against the appointments file so concurrent
        # save() calls cannot interleave and corrupt the line-oriented data.
        self._lock = threading.Lock()

    def print_check_file(self, path, purpose: str) -> None:
        abs_path = path.resolve()
        status = "exists" if abs_path.exists() else "does NOT exist"
        self.logger.info("%s: %s (%s)", purpose, abs_path, status)

    def load(self) -> dict[str, set[str]]:
        appointments: dict[str, set[str]] = {}
        abs_path = self.config.property_appointments_file.resolve()
        self.logger.info("Loading appointments from %s", abs_path)
        if not self.config.property_appointments_file.exists():
            self.logger.info("Appointments file does not exist yet: %s", abs_path)
            return appointments
        with self._lock:
            with self.config.property_appointments_file.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        property_id, dates = line.split(":", 1)
                        appointments[property_id.strip()] = {item for item in dates.split(",") if item}
                    except Exception:
                        continue
        return appointments

    def save(self, appointments: dict[str, set[str]]) -> None:
        # Atomic write: render the full payload to a sibling temp file, fsync,
        # then os.replace() over the destination. A crash mid-write leaves the
        # original file intact instead of a half-written, truncated one.
        path = self.config.property_appointments_file
        abs_path = path.resolve()
        self.logger.info("Saving %s appointment sets to %s", len(appointments), abs_path)
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    for property_id, date_set in appointments.items():
                        print(f"{property_id}:{','.join(sorted(date_set))}", file=handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, path)
            except Exception:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        self.print_check_file(self.config.property_appointments_file, "Appointments saved")
