"""Reading, validating and changing business policy settings."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationMissing, NotFound, PolicyViolation, ValidationFailed
from app.core.timeutil import utc_now
from app.modules.audit import service as audit
from app.modules.settings.catalogue import SETTINGS, SettingDef, validate_catalogue
from app.modules.settings.models import SettingChangeHistory, SystemSetting

_MISSING = object()


def _wrap(value: Any) -> dict[str, Any]:
    """Store every value under a fixed key so scalars and containers share a column."""
    if isinstance(value, Decimal):
        # Serialise as a string: JSON floats would lose exactness, and these
        # values feed payroll arithmetic.
        return {"value": str(value)}
    if isinstance(value, (dt.time, dt.date, dt.datetime)):
        return {"value": value.isoformat()}
    return {"value": value}


# ---------------------------------------------------------------- validation


def coerce_value(value_type: str, raw: Any, *, key: str) -> Any:
    """Convert an incoming JSON value to its declared Python type.

    Rejects rather than coerces where silent conversion would hide a mistake -
    notably booleans submitted for integer settings, which Python would happily
    treat as 0/1.
    """
    if value_type == "string":
        if not isinstance(raw, str):
            raise ValidationFailed(f"Setting {key!r} expects a string value.")
        return raw

    if value_type == "boolean":
        if not isinstance(raw, bool):
            raise ValidationFailed(f"Setting {key!r} expects true or false.")
        return raw

    if value_type == "integer":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValidationFailed(f"Setting {key!r} expects a whole number.")
        return raw

    if value_type == "decimal":
        if isinstance(raw, bool):
            raise ValidationFailed(f"Setting {key!r} expects a decimal number.")
        try:
            return Decimal(str(raw))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValidationFailed(f"Setting {key!r} expects a decimal number.") from exc

    if value_type == "time":
        if not isinstance(raw, str):
            raise ValidationFailed(f"Setting {key!r} expects a time as 'HH:MM'.")
        try:
            return dt.time.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationFailed(f"Setting {key!r} expects a time as 'HH:MM'.") from exc

    if value_type == "date":
        if not isinstance(raw, str):
            raise ValidationFailed(f"Setting {key!r} expects a date as 'YYYY-MM-DD'.")
        try:
            return dt.date.fromisoformat(raw)
        except ValueError as exc:
            raise ValidationFailed(f"Setting {key!r} expects a date as 'YYYY-MM-DD'.") from exc

    if value_type == "enum":
        if not isinstance(raw, str):
            raise ValidationFailed(f"Setting {key!r} expects one of its allowed choices.")
        return raw

    if value_type == "json":
        if not isinstance(raw, (list, dict)):
            raise ValidationFailed(f"Setting {key!r} expects a JSON array or object.")
        return raw

    raise ValidationFailed(f"Setting {key!r} has an unsupported type {value_type!r}.")


def check_constraints(
    value: Any, *, value_type: str, constraints: dict[str, Any] | None, key: str
) -> None:
    """Apply the declared constraint metadata to a coerced value."""
    if not constraints:
        return

    if value_type in {"integer", "decimal"}:
        # Compare as Decimal so an int value and a decimal bound (or vice versa)
        # are still compared exactly.
        numeric = Decimal(str(value))
        minimum = constraints.get("min")
        maximum = constraints.get("max")
        if minimum is not None and numeric < Decimal(str(minimum)):
            raise ValidationFailed(f"Setting {key!r} must be at least {minimum}.")
        if maximum is not None and numeric > Decimal(str(maximum)):
            raise ValidationFailed(f"Setting {key!r} must be at most {maximum}.")

    if value_type == "enum":
        choices = constraints.get("choices") or []
        if value not in choices:
            raise ValidationFailed(
                f"Setting {key!r} must be one of: {', '.join(map(str, choices))}."
            )

    if value_type == "json" and isinstance(value, list):
        item_choices = constraints.get("item_choices")
        if item_choices is not None:
            invalid = [item for item in value if item not in item_choices]
            if invalid:
                raise ValidationFailed(
                    f"Setting {key!r} contains values that are not allowed: "
                    f"{', '.join(map(str, invalid))}."
                )
        if len(value) != len(set(map(str, value))):
            raise ValidationFailed(f"Setting {key!r} must not contain duplicate entries.")
        min_items = constraints.get("min_items")
        max_items = constraints.get("max_items")
        if min_items is not None and len(value) < min_items:
            raise ValidationFailed(f"Setting {key!r} requires at least {min_items} entries.")
        if max_items is not None and len(value) > max_items:
            raise ValidationFailed(f"Setting {key!r} allows at most {max_items} entries.")


# -------------------------------------------------------------------- seeding


def seed_settings(session: Session) -> dict[str, int]:
    """Reconcile the catalogue into the database.

    Inserts keys that do not exist and refreshes presentation metadata on those
    that do. A stored ``value`` is never overwritten: once HR has decided
    something, a code default must not quietly revert it.
    """
    validate_catalogue()

    existing = {row.key: row for row in session.scalars(select(SystemSetting)).all()}
    created = updated = 0

    for definition in SETTINGS:
        row = existing.get(definition.key)
        if row is None:
            session.add(
                SystemSetting(
                    key=definition.key,
                    category=definition.category,
                    label=definition.label,
                    description=definition.description,
                    value=_wrap(definition.default),
                    value_type=definition.value_type,
                    default_value=_wrap(definition.default),
                    constraints=definition.constraints,
                    is_editable=definition.editable,
                    is_confirmed=definition.confirmed,
                    requires_legal_review=definition.legal_review,
                )
            )
            created += 1
            continue

        changed = False
        for attr, new in (
            ("category", definition.category),
            ("label", definition.label),
            ("description", definition.description),
            ("value_type", definition.value_type),
            ("default_value", _wrap(definition.default)),
            ("constraints", definition.constraints),
            ("is_editable", definition.editable),
            ("requires_legal_review", definition.legal_review),
        ):
            if getattr(row, attr) != new:
                setattr(row, attr, new)
                changed = True
        if changed:
            updated += 1

    session.flush()
    return {"created": created, "updated": updated, "total": len(SETTINGS)}


# -------------------------------------------------------------------- reading


def get_row(session: Session, key: str) -> SystemSetting:
    row = session.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row is None:
        raise NotFound(f"Setting {key!r} is not configured. Run 'iams seed-settings'.")
    return row


def get_value(session: Session, key: str, *, default: Any = _MISSING) -> Any:
    """Typed value for a setting key.

    Returns the Python type declared in the catalogue, so callers get a
    ``Decimal`` or ``datetime.time`` rather than a raw JSON scalar.
    """
    row = session.scalar(select(SystemSetting).where(SystemSetting.key == key))
    if row is None:
        if default is not _MISSING:
            return default
        raise NotFound(f"Setting {key!r} is not configured. Run 'iams seed-settings'.")
    return coerce_value(row.value_type, row.unwrapped_value, key=key)


def require_confirmed(session: Session, key: str) -> Any:
    """Value for a setting that must have been explicitly confirmed.

    Used by code paths - payroll finalization above all - where acting on an
    unconfirmed placeholder would produce a wrong but plausible result. Raises
    :class:`ConfigurationMissing` rather than returning the placeholder.
    """
    row = get_row(session, key)
    if not row.is_confirmed:
        raise ConfigurationMissing(
            f"'{row.label}' has not been confirmed yet. An HR/Admin user must review "
            f"and confirm this setting before this operation can proceed.",
            details=[{"field": row.key, "message": "Awaiting confirmation."}],
        )
    return coerce_value(row.value_type, row.unwrapped_value, key=key)


def list_settings(
    session: Session,
    *,
    category: str | None = None,
    unconfirmed_only: bool = False,
) -> list[SystemSetting]:
    statement = select(SystemSetting)
    if category:
        statement = statement.where(SystemSetting.category == category)
    if unconfirmed_only:
        statement = statement.where(SystemSetting.is_confirmed.is_(False))
    return list(
        session.scalars(statement.order_by(SystemSetting.category, SystemSetting.key)).all()
    )


def definition_for(key: str) -> SettingDef | None:
    from app.modules.settings.catalogue import SETTINGS_BY_KEY

    return SETTINGS_BY_KEY.get(key)


# -------------------------------------------------------------------- writing


def update_setting(
    session: Session,
    key: str,
    raw_value: Any,
    *,
    context: audit.AuditContext,
    reason: str | None = None,
    confirm: bool | None = None,
    source_reference: str | None = None,
) -> SystemSetting:
    """Validate and apply a settings change, recording history and an audit entry."""
    row = get_row(session, key)

    if not row.is_editable:
        raise PolicyViolation(
            f"'{row.label}' is not editable through the API.",
            details=[{"field": key, "message": "This setting is fixed by the application."}],
        )

    value = coerce_value(row.value_type, raw_value, key=key)
    check_constraints(value, value_type=row.value_type, constraints=row.constraints, key=key)

    old_wrapped = dict(row.value) if isinstance(row.value, dict) else {"value": row.value}
    new_wrapped = _wrap(value)

    value_changed = old_wrapped != new_wrapped
    confirm_changed = confirm is not None and confirm != row.is_confirmed

    if not value_changed and not confirm_changed and source_reference is None:
        return row

    if row.requires_legal_review and confirm and not (reason or source_reference):
        raise ValidationFailed(
            f"'{row.label}' has statutory consequences. Record the authoritative source "
            f"before confirming it.",
            details=[
                {
                    "field": "source_reference",
                    "message": "Cite the circular, notification or policy document.",
                }
            ],
        )

    row.value = new_wrapped
    if confirm is not None:
        row.is_confirmed = confirm
    if source_reference is not None:
        row.source_reference = source_reference
    row.updated_by_user_id = context.actor_user_id

    session.add(
        SettingChangeHistory(
            setting_id=row.id,
            setting_key=row.key,
            old_value=old_wrapped,
            new_value=new_wrapped,
            changed_at=utc_now(),
            changed_by_user_id=context.actor_user_id,
            change_reason=reason,
        )
    )

    audit.record(
        session,
        action="settings.updated",
        context=context,
        entity_type="system_setting",
        entity_id=row.key,
        before=old_wrapped,
        after=new_wrapped,
        extra={
            "reason": reason,
            "confirmed": row.is_confirmed,
            "source_reference": source_reference,
        },
    )

    session.flush()
    return row


def configuration_health(session: Session) -> dict[str, Any]:
    """Summary of outstanding configuration decisions.

    Surfaced on the HR/Admin dashboard and by ``iams config-status`` so that
    unconfirmed values are visible rather than quietly in effect.
    """
    rows = list_settings(session)
    unconfirmed = [r for r in rows if not r.is_confirmed]
    legal = [r for r in rows if r.requires_legal_review and not r.is_confirmed]
    return {
        "total": len(rows),
        "confirmed": len(rows) - len(unconfirmed),
        "unconfirmed": len(unconfirmed),
        "awaiting_legal_verification": len(legal),
        "unconfirmed_keys": [r.key for r in unconfirmed],
        "legal_review_keys": [r.key for r in legal],
    }
