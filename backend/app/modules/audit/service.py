"""Audit log writer.

All audit writes go through :func:`record` so that redaction is applied in one
place. Callers never construct :class:`AuditLog` directly.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from sqlalchemy.orm import Session

from app.core.timeutil import utc_now
from app.modules.audit.models import AuditLog

logger = logging.getLogger(__name__)

#: Keys whose values must never be persisted to the audit trail, matched
#: case-insensitively as substrings. The brief forbids logging passwords, tokens
#: and bank details; this is the enforcement point.
SENSITIVE_KEY_FRAGMENTS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "api_key",
        "apikey",
        "private_key",
        "account_number",
        "bank_account",
        "ifsc",
        "aadhaar",
        "aadhar",
        "pan_number",
        "cvv",
        "otp",
        "signature",
    }
)

REDACTED: Final = "[redacted]"

_MAX_DEPTH: Final = 6


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in SENSITIVE_KEY_FRAGMENTS)


def redact_sensitive(value: Any, *, _depth: int = 0) -> Any:
    """Recursively replace sensitive values with a placeholder.

    Redacts by *key name*, which is a deliberate trade-off: it cannot catch a
    secret stored under an innocuous key, but it is predictable and cheap, and it
    covers the realistic case of a serialised model being handed to the audit
    writer.
    """
    if _depth > _MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            key: (REDACTED if _is_sensitive(str(key)) else redact_sensitive(val, _depth=_depth + 1))
            for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item, _depth=_depth + 1) for item in value]
    return value


class AuditContext:
    """Who is acting and from where. Assembled once per request by a dependency."""

    __slots__ = ("actor_user_id", "actor_label", "ip_address", "user_agent", "request_id")

    def __init__(
        self,
        *,
        actor_user_id: int | None = None,
        actor_label: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        request_id: str | None = None,
    ) -> None:
        self.actor_user_id = actor_user_id
        self.actor_label = actor_label
        self.ip_address = ip_address
        self.user_agent = user_agent
        self.request_id = request_id


def record(
    session: Session,
    *,
    action: str,
    context: AuditContext | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    before: Any = None,
    after: Any = None,
    extra: dict[str, Any] | None = None,
    succeeded: bool = True,
) -> AuditLog:
    """Append an audit entry to the current transaction.

    The entry shares the caller's transaction on purpose: if the operation rolls
    back, its audit row disappears with it, so the trail never claims a change
    that did not happen. Events that must survive a rollback - a failed sign-in,
    for instance - are committed by their own caller in a separate transaction.
    """
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        actor_user_id=context.actor_user_id if context else None,
        actor_label=context.actor_label if context else None,
        actor_ip=context.ip_address if context else None,
        actor_user_agent=(context.user_agent[:255] if context and context.user_agent else None),
        request_id=context.request_id if context else None,
        before_value=redact_sensitive(before) if before is not None else None,
        after_value=redact_sensitive(after) if after is not None else None,
        context=redact_sensitive(extra) if extra else None,
        succeeded=succeeded,
        created_at=utc_now(),
    )
    session.add(entry)
    return entry
