"""Tests for device payload interpretation.

These are the cases that go wrong in a live integration: a device that sends
day-first dates, one that reports no direction at all, one whose clock has
drifted, one that re-delivers its buffer after an outage. Each is pure logic, so
each can be pinned down exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.modules.devices.policy import (
    TIMESTAMP_FORMATS,
    DeviceDataError,
    FieldMapping,
    check_not_too_far_ahead,
    check_source_ip,
    derive_event_id,
    device_is_quiet,
    next_cursor_from_events,
    normalise_batch,
    parse_timestamp,
    read_path,
    resolve_direction,
    should_ignore,
    sync_idempotency_key,
    validate_cursor,
    verify_signature,
)

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------- field mapping


def test_field_mapping_defaults_are_usable_without_configuration() -> None:
    mapping = FieldMapping.from_config({})
    assert mapping.employee_ref == "employee_id"
    assert mapping.timestamp == "timestamp"
    assert mapping.timestamp_format == "iso8601"
    assert mapping.direction is None


def test_unknown_timestamp_format_is_refused_with_the_available_options() -> None:
    with pytest.raises(DeviceDataError) as error:
        FieldMapping.from_config({"field_mapping": {"timestamp_format": "guess"}})
    assert error.value.field_name == "timestamp_format"
    assert "ymd_hms" in str(error.value)


def test_direction_map_values_must_be_in_or_out() -> None:
    with pytest.raises(DeviceDataError) as error:
        FieldMapping.from_config({"field_mapping": {"direction_map": {"0": "entry"}}})
    assert error.value.field_name == "direction_map"


def test_direction_map_keys_are_matched_case_insensitively() -> None:
    mapping = FieldMapping.from_config(
        {"field_mapping": {"direction_map": {"IN ": "in", "Out": "out"}}}
    )
    assert mapping.direction_map == {"in": "in", "out": "out"}


def test_read_path_walks_nested_payloads_and_lists() -> None:
    payload = {"data": {"records": [{"user": {"id": 47}}]}}
    assert read_path(payload, "data.records.0.user.id") == 47


def test_read_path_returns_none_for_a_missing_key_rather_than_raising() -> None:
    assert read_path({"a": 1}, "b.c.d") is None


def test_read_path_stops_at_the_depth_limit() -> None:
    # A deeply nested payload must not be able to drive unbounded traversal.
    deep = "a." * 20 + "a"
    assert read_path({"a": {"a": {"a": 1}}}, deep) is None


# -------------------------------------------------------------------- parsing


def test_day_first_and_month_first_are_different_days_and_both_are_honoured() -> None:
    """The reason formats are declared, not guessed.

    ``03/04/2026`` is 3 April under day-first and 4 March under month-first. A
    library that sniffs would pick one silently and misdate a month of
    attendance.
    """
    day_first = parse_timestamp("03/04/2026 09:30:00", fmt="dmy_hms_slash", tz=IST)
    month_first = parse_timestamp("03/04/2026 09:30:00", fmt="mdy_hms_slash", tz=IST)

    assert day_first.astimezone(IST).date().isoformat() == "2026-04-03"
    assert month_first.astimezone(IST).date().isoformat() == "2026-03-04"


def test_a_naive_timestamp_is_anchored_to_the_device_zone_not_the_server() -> None:
    parsed = parse_timestamp("2026-11-03 09:30:00", fmt="ymd_hms", tz=IST)
    # 09:30 IST is 04:00 UTC. If the server's zone had been used this would differ.
    assert parsed == datetime(2026, 11, 3, 4, 0, tzinfo=UTC)


def test_an_offset_bearing_timestamp_is_honoured_as_given() -> None:
    parsed = parse_timestamp("2026-11-03T09:30:00+05:30", fmt="iso8601", tz=ZoneInfo("UTC"))
    assert parsed == datetime(2026, 11, 3, 4, 0, tzinfo=UTC)


def test_a_trailing_z_is_accepted() -> None:
    assert parse_timestamp("2026-11-03T04:00:00Z", fmt="iso8601", tz=IST) == datetime(
        2026, 11, 3, 4, 0, tzinfo=UTC
    )


def test_epoch_values_are_utc_and_ignore_the_device_zone() -> None:
    seconds = parse_timestamp(1762142400, fmt="epoch_seconds", tz=IST)
    millis = parse_timestamp(1762142400000, fmt="epoch_millis", tz=IST)
    assert seconds == millis
    assert seconds.tzinfo is UTC


def test_an_unparseable_timestamp_names_the_format_it_was_tried_against() -> None:
    with pytest.raises(DeviceDataError) as error:
        parse_timestamp("not-a-date", fmt="ymd_hms", tz=IST)
    assert "ymd_hms" in str(error.value)
    assert error.value.field_name == "timestamp"


def test_a_missing_timestamp_is_an_error_not_a_default() -> None:
    for empty in (None, ""):
        with pytest.raises(DeviceDataError):
            parse_timestamp(empty, fmt="iso8601", tz=IST)


def test_every_declared_format_parses_its_own_shape() -> None:
    """No format may be listed in the catalogue without working."""
    samples = {
        "ymd_hms": "2026-11-03 09:30:00",
        "ymd_hm": "2026-11-03 09:30",
        "dmy_hms": "03-11-2026 09:30:00",
        "dmy_hms_slash": "03/11/2026 09:30:00",
        "dmy_hm_slash": "03/11/2026 09:30",
        "mdy_hms_slash": "11/03/2026 09:30:00",
        "ymd_t_hms": "2026-11-03T09:30:00",
        "ymd_compact": "20261103093000",
        "iso8601": "2026-11-03T09:30:00",
        "epoch_seconds": 1762163400,
        "epoch_millis": 1762163400000,
    }
    assert set(samples) == set(TIMESTAMP_FORMATS)
    for fmt, value in samples.items():
        assert parse_timestamp(value, fmt=fmt, tz=IST).tzinfo is UTC


# ------------------------------------------------------------------ clock drift


def test_a_punch_from_the_future_is_rejected_beyond_tolerance() -> None:
    """A drifted device clock must not credit unworked time."""
    with pytest.raises(DeviceDataError) as error:
        check_not_too_far_ahead(NOW + timedelta(hours=2), now=NOW, tolerance_seconds=300)
    assert "future" in str(error.value)
    assert "device clock" in str(error.value)


def test_small_drift_within_tolerance_is_accepted() -> None:
    check_not_too_far_ahead(NOW + timedelta(seconds=120), now=NOW, tolerance_seconds=300)


def test_a_past_timestamp_is_never_rejected_as_drift() -> None:
    # A device re-sending a week-old buffer is normal and must not be refused.
    check_not_too_far_ahead(NOW - timedelta(days=7), now=NOW, tolerance_seconds=300)


# -------------------------------------------------------------------- direction


def test_explicit_mode_refuses_an_unmapped_value_instead_of_guessing() -> None:
    with pytest.raises(DeviceDataError) as error:
        resolve_direction("7", mode="explicit", direction_map={}, previous_direction=None)
    assert "direction_map" in str(error.value)


def test_explicit_mode_refuses_a_missing_value() -> None:
    with pytest.raises(DeviceDataError):
        resolve_direction(None, mode="explicit", direction_map={}, previous_direction=None)


def test_explicit_mode_uses_the_configured_map() -> None:
    assert resolve_direction(
        "1", mode="explicit", direction_map={"1": "in", "2": "out"}, previous_direction=None
    ) == ("in", False)


def test_alternate_mode_starts_with_an_entry_then_alternates() -> None:
    first, inferred_first = resolve_direction(
        None, mode="alternate", direction_map={}, previous_direction=None
    )
    second, inferred_second = resolve_direction(
        None, mode="alternate", direction_map={}, previous_direction="in"
    )
    assert (first, second) == ("in", "out")
    assert inferred_first and inferred_second


def test_alternate_mode_prefers_a_direction_the_device_actually_reported() -> None:
    """Real information beats inference."""
    direction, inferred = resolve_direction(
        "exit", mode="alternate", direction_map={"exit": "out"}, previous_direction=None
    )
    assert direction == "out"
    assert inferred is False


def test_single_purpose_readers_report_one_direction_without_inferring() -> None:
    assert resolve_direction(None, mode="always_in", direction_map={}, previous_direction="in") == (
        "in",
        False,
    )
    assert resolve_direction(
        None, mode="always_out", direction_map={}, previous_direction="out"
    ) == ("out", False)


def test_an_unknown_direction_mode_is_refused() -> None:
    with pytest.raises(DeviceDataError):
        resolve_direction(None, mode="telepathy", direction_map={}, previous_direction=None)


# ------------------------------------------------------------------- event ids


def test_a_derived_id_is_stable_across_redeliveries() -> None:
    """Deduplication rests on this: the same punch must derive the same id."""
    first = derive_event_id(device_code="GATE", employee_ref="47", occurred_at=NOW, direction="in")
    second = derive_event_id(device_code="GATE", employee_ref="47", occurred_at=NOW, direction="in")
    assert first == second


def test_derived_ids_differ_by_device_employee_and_time() -> None:
    base = {"device_code": "GATE", "employee_ref": "47", "occurred_at": NOW, "direction": "in"}
    ids = {
        derive_event_id(**base),
        derive_event_id(**{**base, "device_code": "DOOR"}),
        derive_event_id(**{**base, "employee_ref": "48"}),
        derive_event_id(**{**base, "occurred_at": NOW + timedelta(minutes=1)}),
    }
    assert len(ids) == 4


# ------------------------------------------------------------------ signatures


def test_a_valid_signature_over_the_raw_body_passes() -> None:
    import hashlib
    import hmac

    body = b'{"user_id":"47","punch_time":"2026-11-03 09:30:00"}'
    secret = "a-long-random-webhook-secret"
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    verify_signature(body=body, provided_signature=digest, secret=secret)
    verify_signature(body=body, provided_signature=f"sha256={digest}", secret=secret)
    verify_signature(body=body, provided_signature=digest.upper(), secret=secret)


def test_a_body_altered_after_signing_is_rejected() -> None:
    import hashlib
    import hmac

    secret = "a-long-random-webhook-secret"
    signed = b'{"user_id":"47"}'
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()

    # Semantically identical JSON, different bytes. Verifying after parsing
    # would accept this; verifying the raw body does not.
    with pytest.raises(DeviceDataError):
        verify_signature(body=b'{ "user_id" : "47" }', provided_signature=digest, secret=secret)


def test_a_missing_signature_header_is_rejected() -> None:
    with pytest.raises(DeviceDataError) as error:
        verify_signature(body=b"{}", provided_signature=None, secret="s" * 20)
    assert error.value.field_name == "signature"


def test_an_unsupported_signature_algorithm_is_refused() -> None:
    with pytest.raises(DeviceDataError) as error:
        verify_signature(body=b"{}", provided_signature="md5=abc", secret="s" * 20)
    assert "sha256" in str(error.value)


def test_source_ip_allowlist_permits_and_refuses() -> None:
    check_source_ip("203.0.113.7", ["203.0.113.7"])
    check_source_ip("anything", None)  # no allowlist configured
    with pytest.raises(DeviceDataError):
        check_source_ip("203.0.113.8", ["203.0.113.7"])
    with pytest.raises(DeviceDataError):
        check_source_ip(None, ["203.0.113.7"])


# ----------------------------------------------------------------- ignore rules


def test_ignore_rules_match_a_value_or_any_of_a_list() -> None:
    assert should_ignore({"type": "heartbeat"}, {"type": "heartbeat"})
    assert should_ignore({"type": "alarm"}, {"type": ["heartbeat", "alarm"]})
    assert not should_ignore({"type": "punch"}, {"type": ["heartbeat"]})
    assert not should_ignore({"type": "punch"}, {})


def test_ignore_rules_compare_across_types() -> None:
    # A device sending 9 as a number must match a rule written as "9".
    assert should_ignore({"code": 9}, {"code": "9"})


# ----------------------------------------------------------------- normalisation


def _mapping(**overrides) -> FieldMapping:
    config = {
        "field_mapping": {
            "employee_ref": "user_id",
            "timestamp": "punch_time",
            "timestamp_format": "ymd_hms",
            **overrides,
        }
    }
    return FieldMapping.from_config(config)


def _normalise(payloads, *, mode="alternate", mapping=None, previous=None, drift=300):
    return normalise_batch(
        payloads,
        device_code="GATE",
        mapping=mapping or _mapping(),
        direction_mode=mode,
        tz=IST,
        timezone_name="Asia/Kolkata",
        now=NOW + timedelta(days=1),
        drift_tolerance_seconds=drift,
        previous_direction_by_ref=previous,
    )


def test_a_batch_delivered_out_of_order_still_alternates_chronologically() -> None:
    """A buffered device often delivers newest-first after an outage.

    Without sorting before inference, the first punch processed would be stamped
    as an entry and the day would read backwards.
    """
    accepted, rejected = _normalise(
        [
            {"user_id": "47", "punch_time": "2026-11-03 18:34:00"},
            {"user_id": "47", "punch_time": "2026-11-03 09:32:00"},
        ]
    )
    assert not rejected
    assert [(event.raw_timestamp, event.direction) for event in accepted] == [
        ("2026-11-03 09:32:00", "in"),
        ("2026-11-03 18:34:00", "out"),
    ]


def test_alternation_continues_from_what_is_already_stored() -> None:
    """A batch boundary must not turn an exit into an entry."""
    accepted, _ = _normalise(
        [{"user_id": "47", "punch_time": "2026-11-03 18:34:00"}],
        previous={"47": "in"},
    )
    assert accepted[0].direction == "out"


def test_alternation_is_tracked_per_employee() -> None:
    accepted, _ = _normalise(
        [
            {"user_id": "47", "punch_time": "2026-11-03 09:32:00"},
            {"user_id": "48", "punch_time": "2026-11-03 09:33:00"},
            {"user_id": "47", "punch_time": "2026-11-03 18:34:00"},
        ]
    )
    by_ref: dict[str, list[str]] = {}
    for event in accepted:
        by_ref.setdefault(event.external_employee_ref, []).append(event.direction)
    assert by_ref == {"47": ["in", "out"], "48": ["in"]}


def test_one_bad_row_does_not_discard_the_rest_of_the_batch() -> None:
    accepted, rejected = _normalise(
        [
            {"user_id": "47", "punch_time": "2026-11-03 09:32:00"},
            {"user_id": "48", "punch_time": "rubbish"},
            {"user_id": "49", "punch_time": "2026-11-03 09:34:00"},
        ]
    )
    assert len(accepted) == 2
    assert len(rejected) == 1
    assert rejected[0].external_employee_ref == "48"
    # The payload is kept, so the row can be shown to an operator and retried.
    assert rejected[0].raw_payload["punch_time"] == "rubbish"


def test_a_row_with_no_employee_reference_names_the_mapping_as_the_suspect() -> None:
    _accepted, rejected = _normalise([{"punch_time": "2026-11-03 09:32:00"}])
    assert rejected[0].field_name == "employee_ref"
    assert "field mapping" in rejected[0].reason


def test_an_ignored_event_is_separated_from_a_failed_one() -> None:
    """A heartbeat is not a failure, and must not look like one."""
    mapping = _mapping(ignore_when={"type": "heartbeat"})
    _accepted, rejected = _normalise(
        [{"user_id": "47", "punch_time": "2026-11-03 09:32:00", "type": "heartbeat"}],
        mapping=mapping,
    )
    assert len(rejected) == 1
    assert rejected[0].is_ignored is True


def test_a_non_object_event_is_rejected_without_crashing_the_batch() -> None:
    accepted, rejected = _normalise(
        ["not-an-object", {"user_id": "47", "punch_time": "2026-11-03 09:32:00"}]
    )
    assert len(accepted) == 1
    assert len(rejected) == 1


def test_a_device_supplied_event_id_is_preferred_over_a_derived_one() -> None:
    mapping = _mapping(event_id="id")
    accepted, _ = _normalise(
        [{"id": "abc-123", "user_id": "47", "punch_time": "2026-11-03 09:32:00"}],
        mapping=mapping,
    )
    assert accepted[0].external_event_id == "abc-123"


def test_an_inferred_direction_does_not_enter_the_derived_id() -> None:
    """Otherwise reprocessing in a different order would break deduplication.

    The same punch, delivered once alone and once after an earlier punch, infers
    different directions - but it is the same punch and must derive the same id.
    """
    alone, _ = _normalise([{"user_id": "47", "punch_time": "2026-11-03 18:34:00"}])
    after, _ = _normalise(
        [
            {"user_id": "47", "punch_time": "2026-11-03 09:32:00"},
            {"user_id": "47", "punch_time": "2026-11-03 18:34:00"},
        ]
    )
    later = next(event for event in after if event.raw_timestamp == "2026-11-03 18:34:00")
    assert alone[0].direction == "in"
    assert later.direction == "out"
    assert alone[0].external_event_id == later.external_event_id


def test_the_timezone_used_is_recorded_on_every_event() -> None:
    accepted, _ = _normalise([{"user_id": "47", "punch_time": "2026-11-03 09:32:00"}])
    assert accepted[0].timezone_used == "Asia/Kolkata"


def test_a_future_dated_row_is_rejected_and_reported_with_its_reference() -> None:
    _accepted, rejected = _normalise([{"user_id": "47", "punch_time": "2030-01-01 09:00:00"}])
    assert rejected[0].field_name == "timestamp"
    assert rejected[0].external_employee_ref == "47"


def test_explicit_mode_rejects_rows_whose_direction_is_unmapped() -> None:
    mapping = _mapping(direction="in_out")
    accepted, rejected = _normalise(
        [
            {"user_id": "47", "punch_time": "2026-11-03 09:32:00", "in_out": "in"},
            {"user_id": "47", "punch_time": "2026-11-03 18:34:00", "in_out": "99"},
        ],
        mode="explicit",
        mapping=mapping,
    )
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0].field_name == "direction"


def test_an_empty_batch_produces_nothing_rather_than_failing() -> None:
    accepted, rejected = _normalise([])
    assert accepted == [] and rejected == []


# ---------------------------------------------------------------------- cursors


def test_a_cursor_with_unexpected_characters_is_refused() -> None:
    """The cursor is interpolated into the next request, so it is not trusted."""
    with pytest.raises(DeviceDataError):
        validate_cursor("abc'; DROP TABLE devices; --")
    with pytest.raises(DeviceDataError):
        validate_cursor("../../etc/passwd")


def test_a_reasonable_cursor_passes_and_blank_becomes_none() -> None:
    assert validate_cursor("2026-11-03T09:30:00+00:00") == "2026-11-03T09:30:00+00:00"
    assert validate_cursor("   ") is None
    assert validate_cursor(None) is None


def test_an_over_long_cursor_is_refused() -> None:
    with pytest.raises(DeviceDataError):
        validate_cursor("a" * 250)


def test_a_timestamp_cursor_never_moves_backwards() -> None:
    """A device re-delivering an old batch must not cause a full re-fetch."""
    accepted, _ = _normalise([{"user_id": "47", "punch_time": "2026-01-01 09:00:00"}])
    previous = "2026-11-03T00:00:00+00:00"
    assert next_cursor_from_events(accepted, previous=previous) == previous


def test_a_cursor_advances_to_the_newest_event_seen() -> None:
    accepted, _ = _normalise(
        [
            {"user_id": "47", "punch_time": "2026-11-03 09:32:00"},
            {"user_id": "47", "punch_time": "2026-11-03 18:34:00"},
        ]
    )
    advanced = next_cursor_from_events(accepted, previous=None)
    assert advanced == "2026-11-03T13:04:00+00:00"  # 18:34 IST


def test_an_empty_batch_leaves_the_cursor_untouched() -> None:
    assert next_cursor_from_events([], previous="keep-me") == "keep-me"


# ------------------------------------------------------------------ idempotency


def test_the_same_device_trigger_and_window_produce_the_same_key() -> None:
    first = sync_idempotency_key(device_code="GATE", trigger="scheduled", window="202611031200")
    second = sync_idempotency_key(device_code="GATE", trigger="scheduled", window="202611031200")
    assert first == second


def test_a_different_window_produces_a_different_key() -> None:
    assert sync_idempotency_key(
        device_code="GATE", trigger="scheduled", window="202611031200"
    ) != sync_idempotency_key(device_code="GATE", trigger="scheduled", window="202611031230")


# ------------------------------------------------------------------- quiet check


def test_a_device_past_its_expected_interval_is_quiet() -> None:
    assert device_is_quiet(
        last_seen_at=NOW - timedelta(hours=30), now=NOW, expected_interval_minutes=1440
    )


def test_a_recently_seen_device_is_not_quiet() -> None:
    assert not device_is_quiet(
        last_seen_at=NOW - timedelta(minutes=5), now=NOW, expected_interval_minutes=1440
    )


def test_a_device_that_has_never_reported_is_not_called_quiet() -> None:
    """It is unproven, which is a different problem and a different message."""
    assert not device_is_quiet(last_seen_at=None, now=NOW, expected_interval_minutes=1440)
