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
            # ``errors="replace"`` so a corrupted / partially-written /
            # externally-modified appointments file with invalid UTF-8 bytes
            # (a stray binary byte from a disk error, an aborted write) can't
            # raise ``UnicodeDecodeError`` inside the read loop and take out
            # ``/property/<uuid>`` and ``/property/<uuid>/schedule-appointment``
            # via the crash handler's empty 503. Any byte the codec can't
            # decode is substituted with U+FFFD, so the corrupted date token
            # simply never matches a legitimate ISO date on the double-booking
            # check — no crash, no user-facing 503. Matches the same defensive
            # shape ``notifications.read_logs`` and ``analytics.recent_listing_activity``
            # already apply to their log-file reads.
            with path.open("r", encoding="utf-8", errors="replace") as handle:
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
                    # Strip whitespace around each date and drop empty tokens
                    # so a hand-edited / externally-corrupted file with
                    # ``prop-1:2030-01-10, 2030-01-11`` doesn't seed the
                    # in-memory set with ``" 2030-01-11"`` (leading space).
                    # book() compares the raw date string against the set for
                    # its double-booking check; a whitespace-padded token
                    # never matches the clean value the request handler
                    # passes, so the second booking would slip through and
                    # both entries — the padded original and the new clean
                    # one — would sit on the same property/date.
                    parsed_dates = {token.strip() for token in dates.split(",")}
                    parsed_dates.discard("")
                    # Merge duplicate property_id lines instead of letting the
                    # later line clobber the earlier one. The service itself
                    # only ever writes a single line per property, so in
                    # normal operation this is a no-op — but a hand-edited /
                    # partially-appended file with two ``prop-1:`` lines
                    # would otherwise silently drop every date on the first
                    # line, and the next save() would rewrite the file with
                    # only the survivors. Same defensive shape the recent
                    # storage / analytics guards apply to malformed rows.
                    existing = appointments.get(property_id)
                    if existing is None:
                        appointments[property_id] = parsed_dates
                    else:
                        existing.update(parsed_dates)
        return appointments

    def save(self, appointments: dict[str, set[str]]) -> bool:
        # Atomic write: render the full payload to a sibling temp file, fsync,
        # then os.replace() over the destination. A crash mid-write leaves the
        # original file intact instead of a half-written, truncated one.
        #
        # DEBUG log level: save() runs on every booking, and successful writes
        # are already observable via os.replace() completing (and via the
        # ``ticket_created`` / notification email fired by the caller).
        #
        # Returns True when the payload is durably on disk, False when the
        # write failed (ENOSPC / EACCES / fsync error / a crash inside
        # os.replace, or a parent-mkdir / mkstemp failure before the inner
        # try block is entered). The pre-fix shape re-raised every exception
        # straight through ``book()`` and the unauthenticated
        # ``/property/<uuid>/schedule`` POST to the crash handler's empty
        # 503 — the user got a blank response, every save failure fanned
        # out an admin crash email per 10-minute fingerprint, and the
        # booking was NOT persisted but the in-memory ``appointments`` dict
        # held a bogus entry until the next load(). Returning a bool lets
        # ``book()`` surface the failure like the sibling silent-save fixes
        # (``save_json_file`` PR #201, ``save_renter_profiles`` / ``_contracts``
        # PR #203, ``set_user_role`` PR #209, ``set_listing_hidden`` PR #213,
        # ``TicketService._save`` PR #202) rather than crashing the request.
        path = self.config.property_appointments_file
        self.logger.debug("Saving %s appointment sets to %s", len(appointments), path.resolve())
        try:
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
            return True
        except Exception as exc:
            self.logger.error("Failed to save appointments to %s: %s", path, exc)
            return False

    def book(self, property_id: str, iso_date: str) -> bool:
        # Returns True when the booking was newly recorded, False when the
        # date was already taken for that property OR the save failed. Load+
        # save run under the same lock so a second caller cannot squeeze in
        # between and produce a double-booking on the same property/date.
        #
        # Collapsing the save-failure case into the same ``False`` the
        # already-booked path returns is intentional: the sole caller
        # (``schedule_appointment`` in ``routes/public_routes.py``) renders
        # 409 "already booked" on False and skips the "Viewing Appointment
        # Request" admin email. On a save failure that's a slightly
        # misleading user-facing message, but strictly better than the
        # pre-fix empty-503 crash handler path (which also fanned out an
        # admin crash email per 10-minute fingerprint), AND the admin email
        # is correctly suppressed — a "booking requested" notification for
        # a booking that never persisted would otherwise reach the inbox
        # and land on the admin's calendar as a ghost appointment. The
        # failure itself is already logged by ``save()`` for triage.
        with self._lock:
            appointments = self.load()
            booked = appointments.setdefault(property_id, set())
            if iso_date in booked:
                return False
            booked.add(iso_date)
            if not self.save(appointments):
                return False
            return True
