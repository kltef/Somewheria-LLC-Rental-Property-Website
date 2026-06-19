"""JIRA integration for maintenance tickets + public site-bug reports.

Both issue-creating methods (``create_issue`` for property maintenance
tickets, ``create_site_report`` for the public ``/report-issue`` form) build
a ``fields`` payload and hand it to ``_post_issue``, which makes a real HTTP
call to the JIRA Cloud REST API.

The client is a no-op until all four credentials are present (matches the
email/Zillow stubs elsewhere): missing credentials -> warn once at
construction and ``is_configured()`` returns False, so callers skip the post
entirely. Once ``JIRA_BASE_URL``/``JIRA_PROJECT_KEY``/``JIRA_API_TOKEN``/
``JIRA_USER_EMAIL`` are set, issues are created for real.

Implementation notes:
* Uses the **v2** REST endpoint (``/rest/api/2/issue``) because it accepts a
  plain-text ``description`` string. The v3 endpoint requires Atlassian
  Document Format (a nested JSON doc) instead.
* Auth is HTTP Basic with the account email + API token — the JIRA **Cloud**
  scheme. Self-hosted Server/Data Center would use a Bearer PAT instead.
* The webhook reverse-mapping (``map_jira_status``) is unaffected.
"""

from __future__ import annotations

from typing import Optional

import requests

from .console import get_console_logger


# JIRA Cloud create-issue endpoint. v2 (not v3) so ``description`` can be a
# plain string rather than Atlassian Document Format.
_CREATE_ISSUE_PATH = "/rest/api/2/issue"

# Per-request timeout (seconds). Kept short so a slow JIRA can't tie up a
# worker; the maintenance path retries around this, and the public-report
# path runs it off the request thread.
_HTTP_TIMEOUT = 10


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
        # Whether to attach a Priority field to maintenance issues. Off-by-env
        # for projects whose create screen has no Priority field (would 400).
        self.set_priority = bool(getattr(config, "jira_set_priority", True))

        if not self.is_configured():
            self.logger.warning(
                "JIRA credentials incomplete (need JIRA_BASE_URL, JIRA_PROJECT_KEY, "
                "JIRA_API_TOKEN, JIRA_USER_EMAIL); JiraClient will no-op."
            )

    def is_configured(self) -> bool:
        return bool(self.base_url and self.project_key and self.api_token and self.user_email)

    # ------------------------------------------------------------------ create

    def _post_issue(self, fields: dict) -> Optional[str]:
        """POST a built ``fields`` dict to JIRA and return the new issue key.

        Raises ``requests.RequestException`` on transport or non-2xx HTTP
        error (after logging JIRA's error body) so the caller's retry/fallback
        path runs. Assumes ``is_configured()`` — callers guard before building
        the payload.
        """
        url = f"{self.base_url}{_CREATE_ISSUE_PATH}"
        try:
            resp = requests.post(
                url,
                auth=(self.user_email, self.api_token),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json={"fields": fields},
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            # JIRA returns a JSON error body (e.g. unknown issuetype, field not
            # on screen) — surface it so misconfiguration is diagnosable.
            body = getattr(getattr(exc, "response", None), "text", "") or ""
            self.logger.error("JIRA create failed (%s): %s %s", url, exc, body[:500])
            raise
        key = (resp.json() or {}).get("key")
        self.logger.info("Created JIRA issue %s in project %s", key, self.project_key)
        return key

    def create_issue(self, ticket: dict) -> Optional[str]:
        """Map a local maintenance ticket -> JIRA issue; return the issue key.

        Returns ``None`` when not configured. Raises on JIRA HTTP failure so
        the TicketService retry loop can react.
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

        fields = {
            "project": {"key": self.project_key},
            "summary": title,
            "description": f"{description}\n\nProperty: {property_name}\nSubmitter: {submitter}",
            "labels": [category],
            "issuetype": {"name": "Task"},
        }
        # Priority is opt-out: omitted when the project's create screen lacks
        # the field (JIRA_SET_PRIORITY=0) to avoid a 400.
        if self.set_priority:
            fields["priority"] = {"name": priority_name}

        return self._post_issue(fields)

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

        Returns ``None`` when not configured. Raises on JIRA HTTP failure so
        the caller can fall back (the public route runs this off-thread and
        emails the admin regardless).
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

        fields = {
            "project": {"key": self.project_key},
            "summary": summary,
            "description": "\n".join(meta) + "\n\n" + (description or "(no description)"),
            "labels": ["public-reported", "site-bug"],
            "issuetype": {"name": "Bug"},
        }
        return self._post_issue(fields)

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
