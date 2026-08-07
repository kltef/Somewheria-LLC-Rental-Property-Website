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
        # date was already taken for that property (or the inputs were
        # rejected). Load+save run under the same lock so a second caller
        # cannot squeeze in between and produce a double-booking on the
        # same property/date.
        #
        # Reject inputs the on-disk format cannot round-trip. The
        # appointments file is line-oriented ``property_id:date1,date2``;
        # the load side splits on the first ``:``, filters empty property
        # ids, and drops empty dates from the comma-separated set. A caller
        # bypassing the route's upstream validation with an empty value or
        # one containing ``:`` / ``,`` / a newline would otherwise get
        # ``True`` back from a booking whose next ``load()`` silently drops
        # the row, so a repeat "already booked" caller cannot happen and a
        # cascade of ghost True's stacks up in the file. Rejecting here
        # matches ``PROPERTY_ID_PATTERN`` (colon and comma are already
        # forbidden by the route) and turns a silent corruption into a
        # visible ``False`` at the boundary.
        if not isinstance(property_id, str) or not property_id.strip():
            return False
        if not isinstance(iso_date, str) or not iso_date.strip():
            return False
        if any(bad in property_id for bad in (":", ",", "\n", "\r")):
            return False
        if any(bad in iso_date for bad in (":", ",", "\n", "\r")):
            return False
        with self._lock:
            appointments = self.load()
            booked = appointments.setdefault(property_id, set())
            if iso_date in booked:
                return False
            booked.add(iso_date)
            self.save(appointments)
            return True
