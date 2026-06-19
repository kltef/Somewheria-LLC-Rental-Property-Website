"""Shared time helpers.

Anything that writes a wire-format timestamp (JSONL change log, ticket
records, contract metadata, lead-capture submissions, outgoing email
bodies, …) routes through :func:`utcnow_iso` so timestamps stay
consistent across the codebase. ``datetime.datetime.now()`` returns a
naive local-clock value whose textual ``isoformat()`` looks identical to
a UTC string but isn't — which silently misbuckets entries in
``AnalyticsTracker.recent_listing_activity`` when the server's local
date and UTC date straddle midnight.
"""

from __future__ import annotations

import datetime


def utcnow_iso() -> str:
    """Return current UTC time as ``YYYY-MM-DDTHH:MM:SSZ``.

    Microseconds are dropped so the wire format is compact and identical
    to what older ticket records carry — keeping the JSONL change log
    grep-friendly and making the ``ts[:7]`` month-bucket slice in
    :meth:`AnalyticsTracker.recent_listing_activity` correct.
    """
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0, tzinfo=None)
        .isoformat()
        + "Z"
    )
