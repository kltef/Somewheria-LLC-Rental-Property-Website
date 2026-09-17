"""Inbound webhook endpoints — Phase 3 §6.

Currently exposes one route, ``POST /webhooks/jira``, which JIRA's
automation rules call when an issue transitions. We update the local
ticket's status to mirror the change.

These endpoints are intentionally CSRF-exempt (they're authenticated by
a shared secret in a header, not by session+token). The exemption is
declared in ``services.security.CSRF_EXEMPT_ENDPOINTS``.
"""

from __future__ import annotations

import secrets

from flask import jsonify, request

from ..services.console import get_console_logger
from ..services.registry import get_services


_logger = get_console_logger("webhooks")


def _extract_jira_status(payload: dict) -> str:
    """Pull the new status name out of a JIRA webhook payload.

    JIRA emits several shapes; the two we care about:
      * issue_updated/transitioned: ``issue.fields.status.name``
      * automation/manual: top-level ``status`` for testability

    ``isinstance(..., str)`` on the nested ``name`` value — not ``str(...)``
    coercion — matches the top-level fallback's contract. A hand-crafted
    (or replay-corrupted) payload that stores a dict/list/number under
    ``status.name`` would otherwise be coerced to a Python repr string
    (``"{'foo': 'bar'}"``), which flows into ``map_jira_status`` as a
    never-matching junk string and into the webhook's response as
    ``ignored_status``. Returning ``""`` instead lets the webhook fall
    through to the same "ignored" path without echoing the garbage back.
    """
    if not isinstance(payload, dict):
        return ""
    issue = payload.get("issue") or {}
    fields = (issue.get("fields") or {}) if isinstance(issue, dict) else {}
    status = (fields.get("status") or {}) if isinstance(fields, dict) else {}
    if isinstance(status, dict):
        name = status.get("name")
        if isinstance(name, str) and name:
            return name
    # Fallback for hand-rolled / test payloads.
    if isinstance(payload.get("status"), str):
        return payload["status"]
    return ""


def _extract_jira_key(payload: dict) -> str:
    """Pull the issue key out of a JIRA webhook payload.

    See ``_extract_jira_status``: the nested ``issue.key`` path is
    isinstance-checked so a non-string value (dict/list/number from a
    malformed payload) can't be ``str(...)``-coerced into a junk key that
    ``find_by_jira_key``'s linear scan would then miss with a misleading
    404 whose ``jira_key`` echoes the Python repr back to the caller.
    """
    if not isinstance(payload, dict):
        return ""
    issue = payload.get("issue") or {}
    if isinstance(issue, dict):
        key = issue.get("key")
        if isinstance(key, str) and key:
            return key
    if isinstance(payload.get("key"), str):
        return payload["key"]
    return ""


def jira_webhook():
    services = get_services()
    expected = (services.config.jira_webhook_secret or "").strip()
    submitted = (request.headers.get("X-JIRA-Webhook-Secret") or "").strip()

    # 401 covers both "we never configured a secret" and "wrong secret".
    # We never want this endpoint to be open if the operator forgot to set
    # JIRA_WEBHOOK_SECRET — fail closed.
    if not expected or not submitted or not secrets.compare_digest(expected, submitted):
        _logger.warning("Rejected JIRA webhook: bad or missing secret")
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    jira_key = _extract_jira_key(payload)
    jira_status = _extract_jira_status(payload)
    if not jira_key:
        return jsonify({"error": "missing issue key"}), 400

    ticket = services.tickets.find_by_jira_key(jira_key)
    if not ticket:
        return jsonify({"error": "ticket not found", "jira_key": jira_key}), 404

    updated = services.jira.transition_local_ticket(
        services.tickets, ticket["id"], jira_status, actor_email="jira-webhook"
    )
    if updated is None:
        # Status didn't map to anything we track; ack so JIRA doesn't retry.
        return jsonify({"ok": True, "ignored_status": jira_status}), 200
    return jsonify({"ok": True, "ticket_id": ticket["id"], "status": updated.get("status")}), 200


def register_webhook_routes(app) -> None:
    app.add_url_rule(
        "/webhooks/jira",
        endpoint="jira_webhook",
        view_func=jira_webhook,
        methods=["POST"],
    )
