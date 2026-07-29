"""Repair-ticket service.

Tickets are stored as a JSON list via FileStorageService. Each ticket is a
dict with stable keys; see ``_blank_ticket`` for the canonical shape.

The service is intentionally small and synchronous — it mirrors the
``appointments`` / ``storage`` style used elsewhere in the app.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
import uuid
from io import BytesIO
from typing import Iterable

from PIL import Image

from .console import get_console_logger
from .properties import (
    ALLOWED_IMAGE_EXTENSIONS,
    MAX_IMAGE_BYTES,
    UploadValidationError,
    _ensure_safe_image_dimensions,
)
from .timeutil import utcnow_iso
from .validation import is_valid_email


MAX_TICKET_PHOTOS = 5


ALLOWED_STATUSES: tuple[str, ...] = (
    "open",
    "in_progress",
    "awaiting_parts",
    "resolved",
    "closed",
)

ALLOWED_PRIORITIES: tuple[str, ...] = ("low", "normal", "high", "urgent")

ALLOWED_CATEGORIES: tuple[str, ...] = (
    "plumbing",
    "electrical",
    "appliance",
    "hvac",
    "structural",
    "pest",
    "other",
)

# Active statuses count as "open" for dashboard summaries.
OPEN_STATUSES: frozenset[str] = frozenset({"open", "in_progress", "awaiting_parts"})


MAX_TITLE_LEN = 120
MAX_DESCRIPTION_LEN = 4000
MAX_NOTE_LEN = 2000
MAX_CONTACT_LEN = 200
MAX_NAME_LEN = 120


# Thin alias for backwards compatibility — call sites inside this module
# already use ``_now_iso``; the implementation lives in services.timeutil
# so notifications/log entries and contract/lead timestamps stay on the
# same UTC wire format.
_now_iso = utcnow_iso


class TicketService:
    def __init__(self, config, storage, notifications, jira=None) -> None:
        self.config = config
        self.storage = storage
        self.notifications = notifications
        # ``jira`` is optional so existing tests that construct TicketService
        # directly (without a JiraClient) continue to pass.
        self.jira = jira
        self.logger = get_console_logger("tickets")
        # Retry backoffs (seconds). 1s/4s/16s = max ~21s of retry per ticket,
        # entirely off the request thread so JIRA being slow never blocks the
        # ticket-creation response.
        self._jira_backoffs = (1, 4, 16)

    # ------------------------------------------------------------------ I/O

    def _load(self) -> list[dict]:
        data = self.storage.load_json_file(self.config.tickets_file, [])
        return data if isinstance(data, list) else []

    def _save(self, tickets: list[dict]) -> None:
        self.storage.save_json_file(self.config.tickets_file, tickets)

    # ---------------------------------------------------------------- query

    def list_tickets(
        self,
        *,
        submitter: str | None = None,
        statuses: Iterable[str] | None = None,
    ) -> list[dict]:
        tickets = self._load()
        if submitter:
            submitter_lc = submitter.lower()
            tickets = [t for t in tickets if (t.get("submitted_by") or "").lower() == submitter_lc]
        if statuses:
            status_set = {s for s in statuses if s in ALLOWED_STATUSES}
            if status_set:
                tickets = [t for t in tickets if t.get("status") in status_set]
        # Most recently updated first.
        tickets.sort(key=lambda t: t.get("updated_at") or t.get("created_at") or "", reverse=True)
        return tickets

    def get_ticket(self, ticket_id: str) -> dict | None:
        if not ticket_id:
            return None
        for ticket in self._load():
            if ticket.get("id") == ticket_id:
                return ticket
        return None

    def summary(self) -> dict:
        tickets = self._load()
        total = len(tickets)
        open_count = sum(1 for t in tickets if t.get("status") in OPEN_STATUSES)
        urgent_count = sum(
            1 for t in tickets
            if t.get("status") in OPEN_STATUSES and t.get("priority") == "urgent"
        )
        return {"total": total, "open": open_count, "urgent": urgent_count}

    def status_counts(self) -> dict:
        """Return ``{status: count}`` for every ALLOWED_STATUSES bucket.

        Always returns a key per allowed status (zero-filled) so the chart
        on the admin dashboard renders consistent slices regardless of
        whether any tickets currently sit in a given status.
        """
        tickets = self._load()
        counts = {status: 0 for status in ALLOWED_STATUSES}
        for ticket in tickets:
            status = ticket.get("status")
            if status in counts:
                counts[status] += 1
        return counts

    # --------------------------------------------------------------- mutate

    def create_ticket(self, payload: dict, submitter_email: str) -> dict:
        title = (payload.get("title") or "").strip()[:MAX_TITLE_LEN]
        description = (payload.get("description") or "").strip()[:MAX_DESCRIPTION_LEN]
        if not title or not description:
            raise ValueError("Title and description are required.")

        category = (payload.get("category") or "other").strip().lower()
        if category not in ALLOWED_CATEGORIES:
            category = "other"

        priority = (payload.get("priority") or "normal").strip().lower()
        if priority not in ALLOWED_PRIORITIES:
            priority = "normal"

        submitter_name = (payload.get("submitter_name") or "").strip()[:MAX_NAME_LEN]
        contact = (payload.get("contact") or "").strip()[:MAX_CONTACT_LEN]
        property_id = (payload.get("property_id") or "").strip()[:64]
        property_name = (payload.get("property_name") or "").strip()[:MAX_NAME_LEN]
        # Opt-in to email updates. Default to True when the submitter has an
        # email we can actually reach; otherwise default to False.
        email_updates = bool(payload.get("email_updates")) if "email_updates" in payload else bool(submitter_email)

        now = _now_iso()
        ticket = {
            "id": uuid.uuid4().hex,
            "created_at": now,
            "updated_at": now,
            "submitted_by": (submitter_email or "").strip().lower()[:254],
            "submitter_name": submitter_name,
            "contact": contact,
            "property_id": property_id,
            "property_name": property_name,
            "category": category,
            "priority": priority,
            "title": title,
            "description": description,
            "status": "open",
            "assigned_to": "",
            "email_updates": email_updates,
            "notes": [],
        }

        # Hold the storage lock across load+append+save so a second
        # ``create_ticket`` racing this one cannot load the same pre-append
        # list, then save back over our just-saved row — a plain load/save
        # pair on the file backend silently loses one ticket per collision.
        with self.storage.atomic():
            tickets = self._load()
            tickets.append(ticket)
            self._save(tickets)

        # Notify the internal admin inbox. Dispatched off the request thread
        # so a slow/unreachable SMTP relay can't stall the caller for up to
        # ``SMTP_TIMEOUT_SECONDS`` (30s) — matching how the JIRA mirror
        # already runs in a daemon thread below.
        self._enqueue_email(
            f"New Repair Ticket: {title}",
            (
                f"Ticket ID: {ticket['id']}\n"
                f"Submitted by: {submitter_name or submitter_email or 'anonymous'}\n"
                f"Contact: {contact or '(none)'}\n"
                f"Property: {property_name or '(not specified)'}\n"
                f"Category: {category}\n"
                f"Priority: {priority}\n\n"
                f"{description}"
            ),
        )

        # Confirmation email to the renter so they know we got it.
        self._maybe_email_submitter(
            ticket,
            subject=f"We received your repair ticket: {title}",
            body=(
                f"Hi {submitter_name or 'there'},\n\n"
                f"Thanks for reporting this — we've received your repair ticket.\n\n"
                f"Reference: {ticket['id'][:8]}\n"
                f"Severity: {priority}\n"
                f"Category: {category}\n"
                f"{('Property: ' + property_name) if property_name else ''}\n\n"
                f"What you submitted:\n{description}\n\n"
                f"You'll get email updates as the ticket progresses. "
                f"You can turn those off anytime from the ticket page."
            ),
        )

        try:
            self.notifications.log_site_change(
                submitter_email or "anonymous",
                "ticket_created",
                {"ticket_id": ticket["id"], "priority": priority, "category": category},
            )
        except Exception:
            pass

        # Fire JIRA mirror creation off the request thread. JIRA being down
        # (timeouts, 5xx, auth errors) MUST NOT block ticket creation —
        # ``_enqueue_jira_create`` returns immediately.
        if self.jira is not None:
            self._enqueue_jira_create(ticket["id"])

        return ticket

    # ------------------------------------------------------- email dispatch

    def _enqueue_email(self, subject: str, body: str, to: str | None = None) -> None:
        """Send an email off the request thread.

        SMTP has a 30 s socket timeout, so a slow Gmail relay could otherwise
        stall the caller by that much per send — a ticket-creation request
        that fires an internal notice plus a submitter confirmation could
        block for up to a minute. All email sends from this service are
        fire-and-forget (callers don't rely on the return value; delivery
        failures are already logged inside ``NotificationService.send_email``),
        so dispatching from a daemon thread preserves semantics while
        removing the latency from the request path.

        Honours ``DISABLE_BACKGROUND_THREADS=1`` so test runs stay
        synchronous and can assert against mocked ``send_email`` calls.
        """
        if os.getenv("DISABLE_BACKGROUND_THREADS") == "1":
            try:
                self.notifications.send_email(subject, body, to=to)
            except Exception as exc:
                self.logger.warning("Inline email send failed for %r: %s", subject, exc)
            return

        def _worker() -> None:
            try:
                self.notifications.send_email(subject, body, to=to)
            except Exception as exc:
                self.logger.warning("Background email send failed for %r: %s", subject, exc)

        threading.Thread(
            target=_worker,
            name="ticket-email",
            daemon=True,
        ).start()

    # --------------------------------------------------------------- JIRA

    def _enqueue_jira_create(self, ticket_id: str) -> None:
        """Create a JIRA mirror in a daemon thread with bounded retries.

        Honours the same DISABLE_BACKGROUND_THREADS env hatch the rest of
        the app uses so test runs stay synchronous and predictable.
        """
        if os.getenv("DISABLE_BACKGROUND_THREADS") == "1":
            # Run inline so tests can assert against jira_key directly. Still
            # wrapped in try/except so a failing JIRA never raises into the
            # caller (matching production semantics).
            try:
                self._attempt_jira_create_once(ticket_id)
            except Exception as exc:
                self.logger.warning("Inline JIRA create failed for %s: %s", ticket_id, exc)
            return

        thread = threading.Thread(
            target=self._jira_create_with_retries,
            args=(ticket_id,),
            name=f"jira-create-{ticket_id[:8]}",
            daemon=True,
        )
        thread.start()

    def _jira_create_with_retries(self, ticket_id: str) -> None:
        last_exc: Exception | None = None
        for attempt, backoff in enumerate(self._jira_backoffs, start=1):
            try:
                key = self._attempt_jira_create_once(ticket_id)
                if key:
                    return  # success path; nothing more to do
                # is_configured() returned False -> no point retrying.
                return
            except Exception as exc:
                last_exc = exc
                self.logger.warning(
                    "JIRA create attempt %d/%d failed for %s: %s",
                    attempt, len(self._jira_backoffs), ticket_id, exc,
                )
                if attempt < len(self._jira_backoffs):
                    time.sleep(backoff)
        self.logger.error(
            "Giving up creating JIRA mirror for %s after %d attempts: %s",
            ticket_id, len(self._jira_backoffs), last_exc,
        )

    def _attempt_jira_create_once(self, ticket_id: str) -> str | None:
        """One JIRA create attempt. Persists ``jira_key`` on success."""
        ticket = self.get_ticket(ticket_id)
        if not ticket:
            return None
        key = self.jira.create_issue(ticket)
        if not key:
            return None
        # Persist the JIRA key onto the ticket so the admin UI can deep-link
        # and the webhook can map JIRA events back to the local ticket.
        with self.storage.atomic():
            tickets = self._load()
            for stored in tickets:
                if stored.get("id") == ticket_id:
                    stored["jira_key"] = key
                    stored["updated_at"] = _now_iso()
                    self._save(tickets)
                    break
        return key

    def find_by_jira_key(self, jira_key: str) -> dict | None:
        if not jira_key:
            return None
        for ticket in self._load():
            if ticket.get("jira_key") == jira_key:
                return ticket
        return None

    def set_email_updates(self, ticket_id: str, enabled: bool, actor_email: str) -> dict | None:
        # Skip the write and change-log entry when the value already matches.
        # A no-op POST otherwise bumps ``updated_at`` (jumping the ticket to
        # the top of the "recently updated" list for nothing) and appends a
        # spurious ``ticket_email_updates_toggled`` row that inflates the
        # site-change history with noise a reviewer has to skip past.
        # Same shape ``update_ticket`` already uses: only persist when
        # something actually changed.
        target: dict | None = None
        changed = False
        with self.storage.atomic():
            tickets = self._load()
            for ticket in tickets:
                if ticket.get("id") != ticket_id:
                    continue
                target = ticket
                new_value = bool(enabled)
                if ticket.get("email_updates") != new_value:
                    ticket["email_updates"] = new_value
                    ticket["updated_at"] = _now_iso()
                    self._save(tickets)
                    changed = True
                break
        if target is None:
            return None
        if changed:
            try:
                self.notifications.log_site_change(
                    actor_email or "unknown",
                    "ticket_email_updates_toggled",
                    {"ticket_id": ticket_id, "enabled": bool(enabled)},
                )
            except Exception:
                pass
        return target

    # Send the email when ``email_updates`` is on AND we actually have a
    # reachable submitter address. The NotificationService itself will no-op
    # gracefully when no password is configured, so this is safe in tests.
    def _maybe_email_submitter(self, ticket: dict, *, subject: str, body: str) -> None:
        if not ticket.get("email_updates"):
            return
        recipient = (ticket.get("submitted_by") or "").strip()
        if not is_valid_email(recipient):
            return
        # Fire-and-forget: dispatched off the request thread so a slow SMTP
        # relay can't stall the caller. See ``_enqueue_email``.
        self._enqueue_email(subject, body, to=recipient)

    def update_ticket(
        self,
        ticket_id: str,
        updates: dict,
        actor_email: str,
    ) -> dict | None:
        # Hold the storage lock only across load+modify+save. Side-effect
        # notifications (email + change log) run outside the lock so a slow
        # SMTP send can't stall other ticket operations.
        target: dict | None = None
        changed: dict = {}
        with self.storage.atomic():
            tickets = self._load()
            for ticket in tickets:
                if ticket.get("id") != ticket_id:
                    continue
                target = ticket

                if "status" in updates:
                    status = (updates.get("status") or "").strip().lower()
                    if status in ALLOWED_STATUSES and status != ticket.get("status"):
                        ticket["status"] = status
                        changed["status"] = status

                if "priority" in updates:
                    priority = (updates.get("priority") or "").strip().lower()
                    if priority in ALLOWED_PRIORITIES and priority != ticket.get("priority"):
                        ticket["priority"] = priority
                        changed["priority"] = priority

                if "assigned_to" in updates:
                    assignee = (updates.get("assigned_to") or "").strip().lower()[:254]
                    if assignee != ticket.get("assigned_to"):
                        ticket["assigned_to"] = assignee
                        changed["assigned_to"] = assignee

                if changed:
                    ticket["updated_at"] = _now_iso()
                    self._save(tickets)
                break

        if target is None:
            return None
        if not changed:
            return target

        # Email the submitter a summary of what changed.
        ticket = target
        friendly_bits = []
        if "status" in changed:
            friendly_bits.append(f"Status: {changed['status'].replace('_', ' ')}")
        if "priority" in changed:
            friendly_bits.append(f"Severity: {changed['priority']}")
        if "assigned_to" in changed:
            friendly_bits.append(
                f"Assigned to: {changed['assigned_to'] or '(unassigned)'}"
            )
        self._maybe_email_submitter(
            ticket,
            subject=f"Repair ticket update: {ticket.get('title', '')}",
            body=(
                f"Hi {ticket.get('submitter_name') or 'there'},\n\n"
                f"There's an update on your repair ticket (reference {ticket_id[:8]}):\n\n"
                + "\n".join(friendly_bits)
                + "\n\nYou can view the full ticket in the Somewheria portal."
            ),
        )

        try:
            self.notifications.log_site_change(
                actor_email or "unknown",
                "ticket_updated",
                {"ticket_id": ticket_id, **changed},
            )
        except Exception:
            pass
        return ticket

    def add_photo(self, ticket_id: str, uploaded_file, actor_email: str) -> dict | None:
        """Validate ``uploaded_file`` (same checks as listing-photo uploads:
        size cap, allowed extension, decompression-bomb dimensions) and write
        it under ``static/uploads/tickets/<ticket_id>/<random>.<ext>``.

        Raises UploadValidationError on any validation failure (caller maps
        to a user-facing message). Returns the updated ticket on success or
        None when the ticket id doesn't exist.
        """
        if not uploaded_file or not getattr(uploaded_file, "filename", ""):
            raise UploadValidationError("No file selected.")

        original_name = uploaded_file.filename
        if "." not in original_name:
            raise UploadValidationError("Unsupported file name.")
        ext = original_name.rsplit(".", 1)[1].lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            raise UploadValidationError("Unsupported image type.")

        raw = uploaded_file.stream.read(MAX_IMAGE_BYTES + 1)
        if not raw:
            raise UploadValidationError("Empty upload.")
        if len(raw) > MAX_IMAGE_BYTES:
            raise UploadValidationError("Image exceeds maximum allowed size.")
        try:
            with Image.open(BytesIO(raw)) as probe:
                _ensure_safe_image_dimensions(probe)
                probe.verify()
        except UploadValidationError:
            raise
        except Exception as exc:
            raise UploadValidationError("File is not a valid image.") from exc

        # Hold the storage lock across the load → limit check → binary
        # save → tickets save sequence so two concurrent uploads to the
        # same ticket cannot both pass the MAX_TICKET_PHOTOS check on a
        # stale count, nor race each other's tickets.json save (which on
        # the file backend silently drops one upload's metadata).
        relative_url: str | None = None
        target_snapshot: dict | None = None
        with self.storage.atomic():
            tickets = self._load()
            target = None
            for ticket in tickets:
                if ticket.get("id") == ticket_id:
                    target = ticket
                    break
            if target is None:
                return None

            existing_photos = target.get("photos") or []
            if not isinstance(existing_photos, list):
                existing_photos = []
            if len(existing_photos) >= MAX_TICKET_PHOTOS:
                raise UploadValidationError(
                    f"Each ticket is limited to {MAX_TICKET_PHOTOS} photos."
                )

            # ticket_id is a uuid4 hex string from create_ticket(); guard anyway
            # so a hand-edited tickets.json can't smuggle path separators.
            safe_ticket_id = "".join(c for c in ticket_id if c.isalnum())[:64]
            if not safe_ticket_id:
                raise UploadValidationError("Invalid ticket id.")

            ticket_dir = (self.config.ticket_upload_dir / safe_ticket_id).resolve()
            upload_root = self.config.ticket_upload_dir.resolve()
            try:
                ticket_dir.relative_to(upload_root)
            except ValueError as exc:
                raise UploadValidationError("Invalid destination path.") from exc

            new_filename = f"{secrets.token_hex(8)}.{ext}"
            save_path = (ticket_dir / new_filename).resolve()
            try:
                save_path.relative_to(upload_root)
            except ValueError as exc:
                raise UploadValidationError("Invalid destination path.") from exc

            ok = self.storage.save_binary_file(save_path, raw)
            if not ok:
                raise UploadValidationError("Failed to save photo.")

            # Persist a relative URL so templates can render it as <img src=...>.
            relative_url = f"/static/uploads/tickets/{safe_ticket_id}/{new_filename}"
            existing_photos.append({"url": relative_url, "uploaded_at": _now_iso()})
            target["photos"] = existing_photos
            target["updated_at"] = _now_iso()
            try:
                self._save(tickets)
            except Exception as exc:
                # The photo is on disk but the ticket record never picked it up.
                # Leaving it would orphan the file under static/uploads/tickets/
                # forever — best-effort delete so the upload dir doesn't grow
                # unboundedly on repeated save failures.
                try:
                    self.storage.delete_file(save_path)
                except Exception:
                    self.logger.warning(
                        "Failed to clean up orphaned ticket photo %s", save_path
                    )
                self.logger.error("Failed to persist ticket photos: %s", exc)
                raise UploadValidationError("Failed to record photo on ticket.") from exc
            target_snapshot = target

        try:
            self.notifications.log_site_change(
                (actor_email or "unknown").lower(),
                "ticket_photo_added",
                {"ticket_id": ticket_id, "url": relative_url},
            )
        except Exception:
            pass
        return target_snapshot

    def add_note(self, ticket_id: str, text: str, actor_email: str) -> dict | None:
        text = (text or "").strip()[:MAX_NOTE_LEN]
        if not text:
            raise ValueError("Note cannot be empty.")
        # Hold the lock across load+append+save so two concurrent notes on
        # the same ticket can't both load the pre-append note list and
        # then race each other's saves, dropping one of the notes.
        target: dict | None = None
        actor = (actor_email or "unknown").lower()
        with self.storage.atomic():
            tickets = self._load()
            for ticket in tickets:
                if ticket.get("id") != ticket_id:
                    continue
                ticket.setdefault("notes", []).append({
                    "at": _now_iso(),
                    "by": actor,
                    "text": text,
                })
                ticket["updated_at"] = _now_iso()
                self._save(tickets)
                target = ticket
                break
        if target is None:
            return None

        ticket = target
        submitter = (ticket.get("submitted_by") or "").lower()
        if actor != submitter:
            # Note from someone other than the submitter (typically admin);
            # send a heads-up email if the submitter opted in.
            self._maybe_email_submitter(
                ticket,
                subject=f"New note on your repair ticket: {ticket.get('title', '')}",
                body=(
                    f"Hi {ticket.get('submitter_name') or 'there'},\n\n"
                    f"{actor} added a note to your repair ticket (reference {ticket_id[:8]}):\n\n"
                    f"{text}\n\n"
                    f"Reply from the ticket page in the Somewheria portal."
                ),
            )
        else:
            # Note from the submitter — notify the internal inbox so staff
            # see a renter reply even if they're not watching the dashboard.
            # Dispatched off the request thread; see ``_enqueue_email``.
            self._enqueue_email(
                f"Renter note on ticket: {ticket.get('title', '')}",
                (
                    f"Ticket: {ticket_id[:8]}\n"
                    f"From: {ticket.get('submitter_name') or submitter or 'renter'}\n\n"
                    f"{text}"
                ),
            )

        try:
            self.notifications.log_site_change(
                actor,
                "ticket_note_added",
                {"ticket_id": ticket_id},
            )
        except Exception:
            pass
        return ticket
