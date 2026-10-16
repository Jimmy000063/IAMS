"""Time and timezone helpers.

Rules this system follows everywhere:

1. Every stored instant is timezone-aware UTC (``TIMESTAMPTZ`` in PostgreSQL).
2. Nothing trusts a client-supplied clock. Punch timestamps come from
   :func:`utc_now`, never from the browser.
3. A calendar date (``work_date``, ``leave_date``, holiday dates) is always
   derived in a *named* local timezone - the employee's work-location timezone -
   because "which day was that punch on" is a local-time question.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import get_settings


def utc_now() -> datetime:
    """The authoritative server timestamp. The only sanctioned source of "now"."""
    return datetime.now(UTC)


def validate_timezone_name(name: str | None) -> str:
    """Strictly validate an IANA timezone name, with **no fallback**.

    Separate from :func:`resolve_timezone` on purpose. Resolution is forgiving -
    it falls back to the configured default so a missing value cannot crash an
    attendance calculation. Validation must not be forgiving: accepting
    ``"IST"`` or ``"GMT+5:30"`` at write time and silently using the default
    instead would misdate every punch at that work location, and the mistake
    would surface only in a month-end report.
    """
    candidate = (name or "").strip()
    if not candidate:
        raise ValueError("A timezone name is required.")
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"{candidate!r} is not a known IANA timezone name. Use a form such as 'Asia/Kolkata'."
        ) from exc
    return candidate


def resolve_timezone(name: str | None) -> ZoneInfo:
    """Resolve an IANA timezone name, falling back to the configured default.

    Raises :class:`ValueError` when the configured default is itself invalid,
    because silently guessing a timezone would corrupt attendance dates.
    """
    candidates = [name, get_settings().default_timezone]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except ZoneInfoNotFoundError:
            continue
    raise ValueError(
        f"Could not resolve timezone {name!r} or the configured default "
        f"{get_settings().default_timezone!r}. Is the 'tzdata' package installed?"
    )


def to_local(moment: datetime, tz_name: str | None) -> datetime:
    """Convert an aware UTC instant into a named local timezone."""
    if moment.tzinfo is None:
        raise ValueError("Naive datetime passed to to_local(); all instants must be aware.")
    return moment.astimezone(resolve_timezone(tz_name))


def local_date(moment: datetime, tz_name: str | None) -> date:
    """The calendar date of ``moment`` as observed in ``tz_name``."""
    return to_local(moment, tz_name).date()


def local_datetime_to_utc(local_dt: datetime, tz_name: str | None) -> datetime:
    """Interpret a naive local datetime in ``tz_name`` and return aware UTC.

    Used when HR enters a correction such as "in at 09:15 on 3 March" - that is a
    local wall-clock value which must be anchored to a timezone before storage.
    """
    if local_dt.tzinfo is not None:
        raise ValueError("local_datetime_to_utc() expects a naive local datetime.")
    return local_dt.replace(tzinfo=resolve_timezone(tz_name)).astimezone(UTC)


def combine_local(day: date, clock: time, tz_name: str | None) -> datetime:
    """Combine a local date and wall-clock time into an aware UTC instant."""
    return local_datetime_to_utc(datetime.combine(day, clock), tz_name)
