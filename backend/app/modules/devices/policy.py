"""Pure device-data interpretation. No I/O, no database.

Attendance machines disagree about almost everything: field names, timestamp
formats, whether a punch says which direction it was, whether events carry an
identifier at all. This module turns that variety into one shape, and it is
pure so every awkward real-world case can be tested exactly.

The rules here are the ones most likely to be wrong in a live integration, so
each is stated explicitly rather than left to a format-guessing library:

* **Timestamps are parsed against a declared format**, not sniffed. A device
  sending ``03/04/2026`` must be configured as day-first or month-first; the
  system will not guess, because guessing silently misdates attendance.
* **Naive timestamps are anchored to a declared timezone**, never the server's.
* **Future-dated events are rejected** beyond a tolerance. Device clocks drift,
  and a punch stamped two hours ahead would credit time not yet worked.
* **Direction is inferred only when the device does not report it**, and the
  inference is stated in the data so it is visible rather than invisible.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

Direction = Literal["in", "out"]

#: Timestamp formats an operator may select. Deliberately explicit - there is no
#: "try them all" option, because ``03/04/2026`` is a different day under two of
#: these and the wrong choice is invisible until a month-end report.
TIMESTAMP_FORMATS: dict[str, str] = {
    "iso8601": "",  # handled separately
    "epoch_seconds": "",  # handled separately
    "epoch_millis": "",  # handled separately
    "ymd_hms": "%Y-%m-%d %H:%M:%S",
    "ymd_hm": "%Y-%m-%d %H:%M",
    "dmy_hms": "%d-%m-%Y %H:%M:%S",
    "dmy_hms_slash": "%d/%m/%Y %H:%M:%S",
    "dmy_hm_slash": "%d/%m/%Y %H:%M",
    "mdy_hms_slash": "%m/%d/%Y %H:%M:%S",
    "ymd_t_hms": "%Y-%m-%dT%H:%M:%S",
    "ymd_compact": "%Y%m%d%H%M%S",
}

MAX_PAYLOAD_DEPTH = 8


class DeviceDataError(ValueError):
    """A device event could not be interpreted. Carries a field for diagnosis."""

    def __init__(self, message: str, *, field_name: str | None = None) -> None:
        self.field_name = field_name
        super().__init__(message)


# ---------------------------------------------------------------------------
# Field mapping
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """Which keys in a device payload mean what.

    Paths are dotted, so a nested payload such as
    ``{"data": {"user": {"id": 47}}}`` is reachable as ``data.user.id`` without
    the adapter needing to know the device's shape in code.
    """

    employee_ref: str = "employee_id"
    timestamp: str = "timestamp"
    direction: str | None = None
    event_id: str | None = None

    timestamp_format: str = "iso8601"
    timestamp_timezone: str | None = None
    """IANA zone for naive timestamps. None means "use the device's zone"."""

    direction_map: dict[str, str] = field(default_factory=dict)
    """Device value to ``in``/``out``, e.g. ``{"0": "in", "1": "out"}``."""

    ignore_when: dict[str, Any] = field(default_factory=dict)
    """
    Field/value pairs that mark an event as not an attendance punch - a device
    heartbeat, a door-held-open alarm, an enrolment record. Matching events are
    stored and marked ``ignored`` rather than failing the whole sync.
    """

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> FieldMapping:
        mapping = config.get("field_mapping") or {}
        if not isinstance(mapping, dict):
            raise DeviceDataError("field_mapping must be an object.", field_name="field_mapping")

        timestamp_format = str(mapping.get("timestamp_format", "iso8601"))
        if timestamp_format not in TIMESTAMP_FORMATS:
            raise DeviceDataError(
                f"Unknown timestamp_format {timestamp_format!r}. Choose one of: "
                + ", ".join(sorted(TIMESTAMP_FORMATS)),
                field_name="timestamp_format",
            )

        direction_map = mapping.get("direction_map") or {}
        if not isinstance(direction_map, dict):
            raise DeviceDataError("direction_map must be an object.", field_name="direction_map")
        normalised_map: dict[str, str] = {}
        for key, value in direction_map.items():
            if value not in ("in", "out"):
                raise DeviceDataError(
                    f"direction_map values must be 'in' or 'out', got {value!r}.",
                    field_name="direction_map",
                )
            normalised_map[str(key).strip().lower()] = value

        return cls(
            employee_ref=str(mapping.get("employee_ref", "employee_id")),
            timestamp=str(mapping.get("timestamp", "timestamp")),
            direction=(str(mapping["direction"]) if mapping.get("direction") is not None else None),
            event_id=(str(mapping["event_id"]) if mapping.get("event_id") is not None else None),
            timestamp_format=timestamp_format,
            timestamp_timezone=(
                str(mapping["timestamp_timezone"]) if mapping.get("timestamp_timezone") else None
            ),
            direction_map=normalised_map,
            ignore_when=dict(mapping.get("ignore_when") or {}),
        )


def read_path(payload: Any, path: str) -> Any:
    """Read a dotted path out of a nested payload.

    Returns ``None`` for a missing key rather than raising, so one absent
    optional field does not fail an otherwise good event. Numeric segments index
    into lists, which some devices use for repeated records.
    """
    current = payload
    for depth, segment in enumerate(path.split(".")):
        if depth > MAX_PAYLOAD_DEPTH:
            return None
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def parse_timestamp(
    raw: Any,
    *,
    fmt: str,
    tz,
) -> datetime:
    """Parse a device timestamp into an aware UTC instant.

    ``tz`` is the ``ZoneInfo`` to anchor a naive value to; it is passed in
    rather than resolved here so this stays free of configuration lookups.

    An offset-bearing value is honoured as given. A naive value is interpreted
    in ``tz`` - **never** in the server's timezone, which would misdate every
    punch whenever the server and the device are in different zones.
    """
    if raw is None or raw == "":
        raise DeviceDataError("The event has no timestamp.", field_name="timestamp")

    if fmt in {"epoch_seconds", "epoch_millis"}:
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise DeviceDataError(
                f"Expected a numeric epoch timestamp, got {raw!r}.",
                field_name="timestamp",
            ) from exc
        seconds = number / 1000 if fmt == "epoch_millis" else number
        # Epoch values are unambiguously UTC by definition, so no zone is applied.
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise DeviceDataError(
                f"Epoch timestamp {raw!r} is out of range.", field_name="timestamp"
            ) from exc

    text = str(raw).strip()

    if fmt == "iso8601":
        # Accept a trailing Z, which fromisoformat historically rejected.
        candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise DeviceDataError(
                f"Could not read {text!r} as an ISO 8601 timestamp.",
                field_name="timestamp",
            ) from exc
    else:
        pattern = TIMESTAMP_FORMATS[fmt]
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError as exc:
            raise DeviceDataError(
                f"Could not read {text!r} using format {fmt!r} ({pattern}).",
                field_name="timestamp",
            ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(UTC)


def check_not_too_far_ahead(moment: datetime, *, now: datetime, tolerance_seconds: int) -> None:
    """Reject an event stamped implausibly far in the future.

    Device clocks drift, and some reset to an epoch default after a power cut.
    Accepting a punch from two hours hence would credit an employee with time
    they have not worked, so it is refused and surfaced for reconciliation
    rather than quietly stored.
    """
    drift = (moment - now).total_seconds()
    if drift > tolerance_seconds:
        raise DeviceDataError(
            f"Event timestamp {moment.isoformat()} is {int(drift)}s in the future, "
            f"beyond the {tolerance_seconds}s tolerance. Check the device clock.",
            field_name="timestamp",
        )


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------


def resolve_direction(
    raw_value: Any,
    *,
    mode: str,
    direction_map: dict[str, str],
    previous_direction: Direction | None,
) -> tuple[Direction, bool]:
    """Work out whether a punch was an entry or an exit.

    Returns ``(direction, was_inferred)``. The flag matters: an inferred
    direction is a guess the pipeline made, and a reconciliation screen should
    be able to show which punches were guessed.

    Modes:

    ``explicit``
        The device says. An unrecognised value is an error, not a guess - a
        device reporting a direction code nobody mapped is a configuration
        problem that should be fixed, not papered over.
    ``alternate``
        The device reports only that a punch happened. Alternate from the
        previous punch for that employee on that day; the first is an entry.
        This is the common case for simple readers.
    ``always_in`` / ``always_out``
        A single-purpose reader - an entry turnstile, an exit gate.
    """
    if mode == "always_in":
        return "in", False
    if mode == "always_out":
        return "out", False

    if mode == "explicit":
        if raw_value is None or str(raw_value).strip() == "":
            raise DeviceDataError(
                "The device is configured to report direction explicitly, but this event has none.",
                field_name="direction",
            )
        key = str(raw_value).strip().lower()
        if key in direction_map:
            return direction_map[key], False  # type: ignore[return-value]
        if key in ("in", "i", "entry", "checkin", "check_in", "0"):
            return "in", False
        if key in ("out", "o", "exit", "checkout", "check_out", "1"):
            return "out", False
        raise DeviceDataError(
            f"Unrecognised direction value {raw_value!r}. Add it to direction_map.",
            field_name="direction",
        )

    if mode == "alternate":
        # A device may still volunteer a direction; honour it when it does,
        # because real information beats inference.
        if raw_value is not None and str(raw_value).strip() != "":
            key = str(raw_value).strip().lower()
            if key in direction_map:
                return direction_map[key], False  # type: ignore[return-value]
        return ("out" if previous_direction == "in" else "in"), True

    raise DeviceDataError(f"Unknown direction mode {mode!r}.", field_name="direction_mode")


# ---------------------------------------------------------------------------
# Event identity
# ---------------------------------------------------------------------------


def derive_event_id(
    *,
    device_code: str,
    employee_ref: str,
    occurred_at: datetime,
    direction: str | None,
    raw_index: int | None = None,
) -> str:
    """Build a stable identifier for an event the device did not number.

    Many devices report punches with no identifier at all. Deduplication still
    has to work, so the id is derived from the content - the same punch
    re-delivered produces the same id and is recognised as a duplicate.

    Deliberately **excludes** the receive time and the sync run, which would
    differ between deliveries of the same punch and defeat the whole point.
    Includes ``raw_index`` only when a single payload legitimately contains two
    identical punches, which some batch formats do.
    """
    parts = [
        device_code,
        employee_ref,
        occurred_at.astimezone(UTC).isoformat(timespec="seconds"),
        direction or "",
    ]
    if raw_index is not None:
        parts.append(str(raw_index))
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"derived-{digest[:40]}"


# ---------------------------------------------------------------------------
# Webhook authentication
# ---------------------------------------------------------------------------


def verify_signature(
    *,
    body: bytes,
    provided_signature: str | None,
    secret: str,
) -> None:
    """Verify an HMAC-SHA256 signature over the raw request body.

    A webhook endpoint cannot require a bearer token - a device will not perform
    a sign-in - so the signature is the authentication. It must be computed over
    the **raw bytes**, before any JSON parsing, or an attacker can alter
    semantically-equivalent content without breaking the signature.

    Compared with :func:`hmac.compare_digest` so the comparison does not leak
    the expected value a byte at a time.
    """
    if not provided_signature:
        raise DeviceDataError("The request has no signature header.", field_name="signature")

    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    # Accept a "sha256=" prefix, which several conventions use.
    candidate = provided_signature.strip()
    if "=" in candidate:
        algorithm, _, value = candidate.partition("=")
        if algorithm.strip().lower() not in {"sha256", "hmac-sha256"}:
            raise DeviceDataError(
                f"Unsupported signature algorithm {algorithm!r}; expected sha256.",
                field_name="signature",
            )
        candidate = value.strip()

    if not hmac.compare_digest(expected, candidate.lower()):
        raise DeviceDataError("The signature does not match.", field_name="signature")


def check_source_ip(client_ip: str | None, allowed: list[str] | None) -> None:
    """Enforce an optional source-address allowlist.

    Defence in depth only. A source address can be spoofed and is rewritten by
    proxies, so this is never the sole control - the HMAC signature is.
    """
    if not allowed:
        return
    if client_ip is None:
        raise DeviceDataError(
            "The request has no determinable source address, and this device has an allowlist.",
            field_name="client_ip",
        )
    if client_ip not in allowed:
        raise DeviceDataError(
            f"Source address {client_ip} is not in this device's allowlist.",
            field_name="client_ip",
        )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NormalisedEvent:
    """One device event reduced to the shape the pipeline ingests."""

    external_event_id: str
    external_employee_ref: str
    occurred_at: datetime
    direction: Direction
    direction_was_inferred: bool
    timezone_used: str
    raw_payload: dict[str, Any]
    raw_timestamp: str | None
    raw_direction: str | None


@dataclass(frozen=True, slots=True)
class RejectedEvent:
    """An event that could not be interpreted, kept with its reason.

    Rejections are returned rather than raised so one malformed row does not
    abandon the rest of a batch. They are still stored, so nothing a device
    sent is lost.
    """

    raw_payload: dict[str, Any]
    reason: str
    field_name: str | None
    external_event_id: str | None = None
    external_employee_ref: str | None = None
    raw_timestamp: str | None = None
    is_ignored: bool = False


def should_ignore(payload: dict[str, Any], ignore_when: dict[str, Any]) -> bool:
    """Whether this payload is something other than an attendance punch."""
    if not ignore_when:
        return False
    for path, expected in ignore_when.items():
        actual = read_path(payload, path)
        if isinstance(expected, list):
            if actual in expected or str(actual) in [str(item) for item in expected]:
                return True
        elif actual == expected or str(actual) == str(expected):
            return True
    return False


def normalise_batch(
    payloads: list[dict[str, Any]],
    *,
    device_code: str,
    mapping: FieldMapping,
    direction_mode: str,
    tz,
    timezone_name: str,
    now: datetime,
    drift_tolerance_seconds: int,
    previous_direction_by_ref: dict[str, Direction] | None = None,
) -> tuple[list[NormalisedEvent], list[RejectedEvent]]:
    """Turn a batch of raw payloads into normalised events plus rejections.

    Events are sorted by their parsed timestamp before direction inference,
    because ``alternate`` mode depends on order and devices do not reliably
    deliver in chronological order - a buffered batch after an outage often
    arrives newest-first.

    ``previous_direction_by_ref`` seeds the inference with what the database
    already knows about each employee's last punch, so a batch boundary does
    not reset the alternation and turn an exit into an entry.
    """
    accepted: list[NormalisedEvent] = []
    rejected: list[RejectedEvent] = []
    last_direction: dict[str, Direction] = dict(previous_direction_by_ref or {})

    # First pass: extract and parse, without deciding direction.
    staged: list[tuple[dict[str, Any], str, datetime, Any, str | None]] = []

    for payload in payloads:
        if not isinstance(payload, dict):
            rejected.append(
                RejectedEvent(
                    raw_payload={"value": repr(payload)},
                    reason="Event is not an object.",
                    field_name=None,
                )
            )
            continue

        raw_timestamp_value = read_path(payload, mapping.timestamp)
        raw_timestamp = str(raw_timestamp_value) if raw_timestamp_value is not None else None

        if should_ignore(payload, mapping.ignore_when):
            rejected.append(
                RejectedEvent(
                    raw_payload=payload,
                    reason="Matched an ignore rule; not an attendance punch.",
                    field_name=None,
                    external_event_id=(
                        str(read_path(payload, mapping.event_id)) if mapping.event_id else None
                    ),
                    external_employee_ref=(
                        str(read_path(payload, mapping.employee_ref))
                        if read_path(payload, mapping.employee_ref) is not None
                        else None
                    ),
                    raw_timestamp=raw_timestamp,
                    is_ignored=True,
                )
            )
            continue

        employee_ref_value = read_path(payload, mapping.employee_ref)
        if employee_ref_value is None or str(employee_ref_value).strip() == "":
            rejected.append(
                RejectedEvent(
                    raw_payload=payload,
                    reason=(
                        f"No employee reference at {mapping.employee_ref!r}. Check the "
                        "field mapping."
                    ),
                    field_name="employee_ref",
                    raw_timestamp=raw_timestamp,
                )
            )
            continue
        employee_ref = str(employee_ref_value).strip()

        try:
            occurred_at = parse_timestamp(raw_timestamp_value, fmt=mapping.timestamp_format, tz=tz)
            check_not_too_far_ahead(occurred_at, now=now, tolerance_seconds=drift_tolerance_seconds)
        except DeviceDataError as error:
            rejected.append(
                RejectedEvent(
                    raw_payload=payload,
                    reason=str(error),
                    field_name=error.field_name,
                    external_employee_ref=employee_ref,
                    raw_timestamp=raw_timestamp,
                    external_event_id=(
                        str(read_path(payload, mapping.event_id)) if mapping.event_id else None
                    ),
                )
            )
            continue

        raw_direction_value = read_path(payload, mapping.direction) if mapping.direction else None
        staged.append((payload, employee_ref, occurred_at, raw_direction_value, raw_timestamp))

    # Second pass: in chronological order, so alternation is correct.
    staged.sort(key=lambda item: item[2])

    for payload, employee_ref, occurred_at, raw_direction_value, raw_timestamp in staged:
        try:
            direction, inferred = resolve_direction(
                raw_direction_value,
                mode=direction_mode,
                direction_map=mapping.direction_map,
                previous_direction=last_direction.get(employee_ref),
            )
        except DeviceDataError as error:
            rejected.append(
                RejectedEvent(
                    raw_payload=payload,
                    reason=str(error),
                    field_name=error.field_name,
                    external_employee_ref=employee_ref,
                    raw_timestamp=raw_timestamp,
                )
            )
            continue

        last_direction[employee_ref] = direction

        event_id_value = read_path(payload, mapping.event_id) if mapping.event_id else None
        external_event_id = (
            str(event_id_value).strip()
            if event_id_value is not None and str(event_id_value).strip()
            else derive_event_id(
                device_code=device_code,
                employee_ref=employee_ref,
                occurred_at=occurred_at,
                # Only a device-reported direction contributes to the id. An
                # inferred one would change if the batch were reprocessed in a
                # different order, breaking deduplication.
                direction=None if inferred else direction,
            )
        )

        accepted.append(
            NormalisedEvent(
                external_event_id=external_event_id[:160],
                external_employee_ref=employee_ref[:80],
                occurred_at=occurred_at,
                direction=direction,
                direction_was_inferred=inferred,
                timezone_used=timezone_name,
                raw_payload=payload,
                raw_timestamp=raw_timestamp[:80] if raw_timestamp else None,
                raw_direction=(
                    str(raw_direction_value)[:40] if raw_direction_value is not None else None
                ),
            )
        )

    return accepted, rejected


# ---------------------------------------------------------------------------
# Cursors
# ---------------------------------------------------------------------------

_SAFE_CURSOR = re.compile(r"^[A-Za-z0-9:_.+-]{1,200}$")


def validate_cursor(cursor: str | None) -> str | None:
    """Sanity-check an adapter-supplied cursor before storing it.

    The cursor is opaque to the pipeline but is interpolated into the next
    request by some adapters, so it is constrained to a safe character set
    rather than trusted because it came from the device.
    """
    if cursor is None:
        return None
    text = str(cursor).strip()
    if not text:
        return None
    if not _SAFE_CURSOR.match(text):
        raise DeviceDataError(
            "The device returned a cursor containing unexpected characters; it was not stored.",
            field_name="cursor",
        )
    return text


def next_cursor_from_events(events: list[NormalisedEvent], *, previous: str | None) -> str | None:
    """Advance a timestamp cursor to the newest event seen.

    Never moves backwards: a device that re-delivers an old batch must not cause
    the cursor to regress and re-fetch everything after it.
    """
    if not events:
        return previous
    newest = max(event.occurred_at for event in events)
    candidate = newest.astimezone(UTC).isoformat(timespec="seconds")
    if previous and previous >= candidate:
        return previous
    return candidate


def sync_idempotency_key(*, device_code: str, trigger: str, window: str) -> str:
    """Deterministic key for a sync run.

    A retried scheduled run computes the same key and fails on the unique
    constraint rather than re-fetching and re-applying. Manual and webhook
    triggers include a distinguishing window, since an operator may legitimately
    run two syncs a minute apart.
    """
    return f"{device_code}:{trigger}:{window}"[:200]


def device_is_quiet(
    *, last_seen_at: datetime | None, now: datetime, expected_interval_minutes: int
) -> bool:
    """Whether a device has stopped reporting for longer than expected.

    This is the failure that otherwise goes unnoticed: a device that stops
    sending produces no error, just a slow accumulation of missing attendance
    that surfaces at month end. A device that has never reported is not yet
    "quiet" - it is unproven, which is a different problem.
    """
    if last_seen_at is None:
        return False
    return now - last_seen_at > timedelta(minutes=expected_interval_minutes)
