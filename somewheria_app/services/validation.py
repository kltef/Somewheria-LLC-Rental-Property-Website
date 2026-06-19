"""Lightweight input validators shared across services and routes.

Kept deliberately small: just the rules we want to enforce at the
public boundary (registration form, lead capture, ticket email
recipients). Anything more involved should live with the service it
belongs to.
"""

from __future__ import annotations

import re


# Practical email regex. Not the full RFC 5321 grammar — that's both
# too permissive (allows quoted-strings and IP literals nobody actually
# uses) and surprisingly hard to express — but covers the everyday
# "local@domain.tld" shape we want to accept and rejects the obvious
# junk the previous "@ in value" check let through ("@", "a@", "@b",
# "a@b", "a b@c.com"). RFC 5321 caps the total length at 254 bytes.
_EMAIL_REGEX = re.compile(
    r"^[A-Za-z0-9._%+\-!#$&'*/=?^`{|}~]+"
    r"@"
    r"[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?)+$"
)


def is_valid_email(value) -> bool:
    """Return True when ``value`` is a plausible deliverable email address.

    Rejects non-strings, anything outside the RFC 5321 length bounds,
    values containing whitespace, and values that don't fit the
    standard ``local@domain.tld`` shape. The previous check (just
    ``"@" in value``) accepted ``"@"`` and ``"a@b"`` — exactly the
    sort of junk that pollutes the lead-capture and pending-registration
    JSON files and triggers wasted SMTP attempts.
    """
    if not isinstance(value, str):
        return False
    if len(value) < 3 or len(value) > 254:
        return False
    return _EMAIL_REGEX.match(value) is not None
