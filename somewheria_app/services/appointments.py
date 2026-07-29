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
        # Reentrant so book() can hold the lock across a load+save round-trip
        # without deadlocking on its own nested load()/save() calls.
        self._lock = threading.RLock()

    def print_check_file(self, path, purpose: str) -> None:
        abs_path = path.resolve()
        status = "exists" if abs_path.exists() else "does NOT exist"
        self.logger.info("%s: %s (%s)", purpose, abs_path, status)

    def load(self) -> dict[str, set[str]]:
        # ``property_details`` calls this on every page view (including bot
        # crawlers), so keep it at DEBUG — routine reads at INFO fill the
        # 10 MB rotating application.log with duplicate lines and shorten
        # the useful log retention window. Mutations (save/book) still log
        # at INFO because they're rare and worth the audit trail.
        appointments: dict[str, set[str]] = {}
        path = self.config.property_appointments_file
        if not path.exists():
            self.logger.debug("Appointments file does not exist yet: %s", path)
            return appointments
        self.logger.debug("Loading appointments from %s", path)
        with self._lock:
            with path.open("r", encoding="utf-8") as handle:
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
                        line = f"{property_id}:{','.join(sorted(date_set))}\n"
                        handle.write(line)
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

    def book(self, property_id: str, iso_date: str) -> bool:
        # Returns True when the booking was newly recorded, False when the
        # date was already taken for that property. Load+save run under the
        # same lock so a second caller cannot squeeze in between and produce
        # a double-booking on the same property/date.
        with self._lock:
            appointments = self.load()
            booked = appointments.setdefault(property_id, set())
            if iso_date in booked:
                return False
            booked.add(iso_date)
            self.save(appointments)
            return True
