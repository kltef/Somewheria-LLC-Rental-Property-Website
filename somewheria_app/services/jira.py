"""JIRA integration for maintenance tickets — Phase 3 §6.

This is a SCAFFOLD. We do not yet have JIRA credentials, so no real HTTP
requests are made to atlassian.net even when the env vars are populated.
``create_issue`` returns a fake key so the rest of the wiring (storage,
webhook lookup, template link) can be exercised end-to-end.

Pattern matches the email/Zillow stubs elsewhere in the codebase: missing
credentials -> log a warning at construction time and operate as a no-op.

When real credentials land, the only thing that needs to change is the
body of ``create_issue`` (swap the stubbed return for a real
``requests.post`` to ``{base}/rest/api/3/issue``). Field mapping and the
webhook reverse-mapping should not need to change.
"""

from __future__ import annotations

from typing import Optional

from .console import get_console_logger


# JIRA priority names map cleanly to our four-step ladder. Anything we
# don't recognise falls back to "Medium" so a malformed ticket still
# creates an issue.
_PRIORITY_MAP = {
    "low": "Low",
    "normal": "Medium",
    "high": "High",
    "urgent": "Highest",
}

# Reverse map for webhook callbacks. JIRA workflows can have many status
# names; we only react to the four canonical ones. Anything else is
# returned as ``None`` so the webhook handler can ignore it without
# corrupting local state.
_STATUS_REVERSE_MAP = {
    "open": "open",
    "in progress": "in_progress",
    "done": "resolved",
    "closed": "closed",
}


class JiraClient:
    def __init__(self, config, notifications) -> None:
        self.config = config
        self.notifications = notifications
        self.logger = get_console_logger("jira")
        # Resolved at construction time so a missing-credential warning fires
        # once at boot rather than on every ticket creation.
        self.base_url = (getattr(config, "jira_base_url", "") or "").rstrip("/")
        self.project_key = getattr(config, "jira_project_key", "") or ""
        self.api_token = getattr(config, "jira_api_token", "") or ""
        self.user_email = getattr(config, "jira_user_email", "") or ""

        if not self.is_configured():
            self.logger.warning(
                "JIRA credentials incomplete (need JIRA_BASE_URL, JIRA_PROJECT_KEY, "
                "JIRA_API_TOKEN, JIRA_USER_EMAIL); JiraClient will no-op."
            )

    def is_configured(self) -> bool:
        return bool(self.base_url and self.project_key and self.api_token and self.user_email)

    # ------------------------------------------------------------------ create

    def create_issue(self, ticket: dict) -> Optional[str]:
        """Map a local ticket -> JIRA issue and return the new issue key.

        Returns ``None`` when not configured. Returns a stub key when
        configured (no real HTTP call yet — credentials pending).
        """
        if not self.is_configured():
            return None

        title = (ticket.get("title") or "").strip()
        description = (ticket.get("description") or "").strip()
        property_name = (ticket.get("property_name") or "(not specified)").strip()
        submitter = (ticket.get("submitted_by") or "anonymous").strip()
        priority_name = _PRIORITY_MAP.get(
            (ticket.get("priority") or "normal").lower(), "Medium"
        )
        category = (ticket.get("category") or "other").lower()

        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": title,
                "description": f"{description}\n\nProperty: {property_name}\nSubmitter: {submitter}",
                "priority": {"name": priority_name},
                "labels": [category],
                "issuetype": {"name": "Task"},
            }
        }

        # No real HTTP — credentials still pending. Log enough that the ops
        # team can see exactly what *would* be sent once the toggle flips.
        self.logger.info(
            "would create JIRA issue in project=%s priority=%s labels=%s summary=%r",
            self.project_key, priority_name, payload["fields"]["labels"], title,
        )
        return "STUB-1"

    def create_site_report(
        self,
        name: str,
        description: str,
        *,
        page_url: str = "",
        user_agent: str = "",
    ) -> Optional[str]:
        """Map a public *site-bug* report -> a JIRA Bug issue; return the key.

        This is the website's own bug-report intake (``/report-issue``), as
        opposed to ``create_issue`` which mirrors property *maintenance*
        tickets. Reports come from an unauthenticated public form, so they're
        labelled ``public-reported`` — by convention these should land in a
        triage/backlog status in JIRA for a human to confirm before they mix
        in with planned work, not straight into an active sprint.

        Returns ``None`` when not configured. Returns a stub key when
        configured (no real HTTP call yet — same scaffold state as
        ``create_issue``; see the module docstring).
        """
        if not self.is_configured():
            return None

        name = (name or "anonymous").strip()
        description = (description or "").strip()
        # First non-empty line becomes the summary so the board is scannable.
        first_line = next(
            (line for line in description.splitlines() if line.strip()),
            "Site issue report",
        )
        summary = f"[Site Bug] {first_line[:80]}"

        meta = [
            f"Reported by: {name}",
            "Source: public site /report-issue form (unverified reporter)",
        ]
        if page_url:
            meta.append(f"Page: {page_url}")
        if user_agent:
            meta.append(f"User-Agent: {user_agent}")

        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": summary,
                "description": "\n".join(meta) + "\n\n" + (description or "(no description)"),
                "labels": ["public-reported", "site-bug"],
                "issuetype": {"name": "Bug"},
            }
        }

        # No real HTTP — credentials still pending. Log what WOULD be sent so
        # the flow is observable end-to-end (mirrors create_issue).
        self.logger.info(
            "would create JIRA Bug in project=%s labels=%s summary=%r",
            self.project_key, payload["fields"]["labels"], summary,
        )
        return "STUB-1"

    # --------------------------------------------------------------- webhook

    @staticmethod
    def map_jira_status(jira_status: str) -> Optional[str]:
        """Translate a JIRA status name to one of ALLOWED_STATUSES, or None."""
        if not jira_status:
            return None
        return _STATUS_REVERSE_MAP.get(jira_status.strip().lower())

    def transition_local_ticket(
        self,
        tickets_service,
        ticket_id: str,
        jira_status: str,
        actor_email: str = "jira-webhook",
    ) -> Optional[dict]:
        """Apply a JIRA status change to a local ticket via TicketService.

        Returns the updated ticket, or ``None`` if the status couldn't be
        mapped or the ticket couldn't be found.
        """
        local_status = self.map_jira_status(jira_status)
        if not local_status:
            self.logger.info("Ignoring unmapped JIRA status %r for ticket %s", jira_status, ticket_id)
            return None
        return tickets_service.update_ticket(
            ticket_id, {"status": local_status}, actor_email
        )
