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
        # Called on every /property/<uuid> page render, so the routine
        # "loading ..." / "not created yet" traces sit at DEBUG — otherwise a
        # single visit dominates application.log with no-signal messages.
        # Startup / booking flows still get an INFO trail via
        # print_check_file() and NotificationService.log_site_change().
        appointments: dict[str, set[str]] = {}
        path = self.config.property_appointments_file
        # Hold the lock across the existence check and open() so a concurrent
        # save() (which creates the file atomically via os.replace) cannot
        # create-then-be-observed-as-missing between the two calls. This is a
        # thin race in practice — save() is rare — but keeps the read side
        # observation consistent with the write side.
        with self._lock:
            if not path.exists():
                self.logger.debug("Appointments file does not exist yet: %s", path.resolve())
                return appointments
            self.logger.debug("Loading appointments from %s", path.resolve())
            with path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        property_id, dates = line.split(":", 1)
                    except ValueError:
                        continue
                    property_id = property_id.strip()
                    # Skip lines that stored an empty property id — treating
                    # them as real entries would pollute the returned map with
                    # a bogus "" key that gets re-written on the next save().
                    if not property_id:
                        continue
                    appointments[property_id] = {item for item in dates.split(",") if item}
        return appointments

    def save(self, appointments: dict[str, set[str]]) -> None:
        # Atomic write: render the full payload to a sibling temp file, fsync,
        # then os.replace() over the destination. A crash mid-write leaves the
        # original file intact instead of a half-written, truncated one.
        #
        # DEBUG log level: save() runs on every booking, and successful writes
        # are already observable via os.replace() completing (and via the
        # ``ticket_created`` / notification email fired by the caller).
        path = self.config.property_appointments_file
        self.logger.debug("Saving %s appointment sets to %s", len(appointments), path.resolve())
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
