"""Repair-ticket service.

Tickets are stored as a JSON list via FileStorageService. Each ticket is a
dict with stable keys; see ``_blank_ticket`` for the canonical shape.

The service is intentionally small and synchronous — it mirrors the
``appointments`` / ``storage`` style used elsewhere in the app.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Iterable

from .console import get_console_logger


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


def _now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class TicketService:
    def __init__(self, config, storage, notifications) -> None:
        self.config = config
        self.storage = storage
        self.notifications = notifications
        self.logger = get_console_logger("tickets")

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

        tickets = self._load()
        tickets.append(ticket)
        self._save(tickets)

        try:
            # Notify the internal admin inbox.
            self.notifications.send_email(
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
        except Exception as exc:  # notifications must not block ticket creation
            self.logger.warning("Failed to send ticket email: %s", exc)

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
        return ticket

    def set_email_updates(self, ticket_id: str, enabled: bool, actor_email: str) -> dict | None:
        tickets = self._load()
        for ticket in tickets:
            if ticket.get("id") != ticket_id:
                continue
            ticket["email_updates"] = bool(enabled)
            ticket["updated_at"] = _now_iso()
            self._save(tickets)
            try:
                self.notifications.log_site_change(
                    actor_email or "unknown",
                    "ticket_email_updates_toggled",
                    {"ticket_id": ticket_id, "enabled": bool(enabled)},
                )
            except Exception:
                pass
            return ticket
        return None

    # Send the email when ``email_updates`` is on AND we actually have a
    # reachable submitter address. The NotificationService itself will no-op
    # gracefully when no password is configured, so this is safe in tests.
    def _maybe_email_submitter(self, ticket: dict, *, subject: str, body: str) -> None:
        if not ticket.get("email_updates"):
            return
        recipient = (ticket.get("submitted_by") or "").strip()
        if not recipient or "@" not in recipient:
            return
        try:
            self.notifications.send_email(subject, body, to=recipient)
        except Exception as exc:
            self.logger.warning("Failed to email submitter for ticket %s: %s", ticket.get("id"), exc)

    def update_ticket(
        self,
        ticket_id: str,
        updates: dict,
        actor_email: str,
    ) -> dict | None:
        tickets = self._load()
        for ticket in tickets:
            if ticket.get("id") != ticket_id:
                continue
            changed: dict = {}

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

            if not changed:
                return ticket

            ticket["updated_at"] = _now_iso()
            self._save(tickets)

            # Email the submitter a summary of what changed.
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
        return None

    def add_note(self, ticket_id: str, text: str, actor_email: str) -> dict | None:
        text = (text or "").strip()[:MAX_NOTE_LEN]
        if not text:
            raise ValueError("Note cannot be empty.")
        tickets = self._load()
        for ticket in tickets:
            if ticket.get("id") != ticket_id:
                continue
            actor = (actor_email or "unknown").lower()
            ticket.setdefault("notes", []).append({
                "at": _now_iso(),
                "by": actor,
                "text": text,
            })
            ticket["updated_at"] = _now_iso()
            self._save(tickets)

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
                try:
                    self.notifications.send_email(
                        f"Renter note on ticket: {ticket.get('title', '')}",
                        (
                            f"Ticket: {ticket_id[:8]}\n"
                            f"From: {ticket.get('submitter_name') or submitter or 'renter'}\n\n"
                            f"{text}"
                        ),
                    )
                except Exception as exc:
                    self.logger.warning("Failed to forward renter note: %s", exc)

            try:
                self.notifications.log_site_change(
                    actor,
                    "ticket_note_added",
                    {"ticket_id": ticket_id},
                )
            except Exception:
                pass
            return ticket
        return None
