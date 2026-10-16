"""Attendance and shift endpoint tests against a real database.

Covers the parts the pure engine cannot: the immutability trigger, the overlap
exclusion constraint, idempotent recomputation, the regularization approval
chain, and the authorization boundaries around all of it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.modules.attendance.models import (
    AttendanceDay,
    AttendanceEvent,
)
from app.modules.auth.models import User
from app.modules.shifts.models import ShiftAssignment
from tests.conftest import auth_header

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def day_shift(client: TestClient, admin_user: User) -> dict:
    response = client.post(
        "/api/v1/shifts",
        json={
            "code": "GEN",
            "name": "General shift",
            "start_time": "09:30",
            "end_time": "18:30",
            "break_minutes": 60,
        },
        headers=auth_header(client, "hradmin"),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def night_shift(client: TestClient, admin_user: User) -> dict:
    response = client.post(
        "/api/v1/shifts",
        json={
            "code": "NIGHT",
            "name": "Night shift",
            "start_time": "22:00",
            "end_time": "06:00",
            "break_minutes": 45,
        },
        headers=auth_header(client, "hradmin"),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def linked_employee(
    client: TestClient,
    session: Session,
    admin_user: User,
    employee_user: User,
    org_fixtures: dict[str, int],
) -> dict:
    """An employee with a sign-in account, so self-service punching works."""
    headers = auth_header(client, "hradmin")
    created = client.post(
        "/api/v1/employees",
        json={
            "employee_code": "ITC0001",
            "first_name": "Ravi",
            "last_name": "Sharma",
            "employment_type": "full_time",
            "date_of_joining": "2026-01-05",
            **org_fixtures,
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    employee = created.json()
    client.post(
        f"/api/v1/employees/{employee['id']}/link-account",
        json={"user_id": employee_user.id},
        headers=headers,
    )
    return employee


def assign(
    client: TestClient,
    headers: dict,
    *,
    employee_id: int,
    shift_id: int,
    effective_from: str = "2026-01-05",
    effective_to: str | None = None,
) -> dict:
    payload = {
        "employee_id": employee_id,
        "shift_id": shift_id,
        "effective_from": effective_from,
    }
    if effective_to:
        payload["effective_to"] = effective_to
    response = client.post("/api/v1/shifts/assignments", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------


class TestShifts:
    def test_crosses_midnight_is_derived(
        self, client: TestClient, admin_user: User, night_shift: dict
    ) -> None:
        assert night_shift["crosses_midnight"] is True
        assert night_shift["scheduled_minutes"] == 435, "480 span minus 45 break"

    def test_day_shift_does_not_cross_midnight(self, day_shift: dict) -> None:
        assert day_shift["crosses_midnight"] is False
        assert day_shift["scheduled_minutes"] == 480

    def test_equal_start_and_end_is_refused(self, client: TestClient, admin_user: User) -> None:
        """Its span would be either zero or 24 hours; refuse rather than guess."""
        response = client.post(
            "/api/v1/shifts",
            json={
                "code": "BAD",
                "name": "Ambiguous",
                "start_time": "09:00",
                "end_time": "09:00",
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_half_day_above_full_day_is_refused(self, client: TestClient, admin_user: User) -> None:
        response = client.post(
            "/api/v1/shifts",
            json={
                "code": "BAD2",
                "name": "Inverted thresholds",
                "start_time": "09:00",
                "end_time": "18:00",
                "min_minutes_full_day": 240,
                "min_minutes_half_day": 480,
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_employees_cannot_create_shifts(self, client: TestClient, employee_user: User) -> None:
        response = client.post(
            "/api/v1/shifts",
            json={
                "code": "X",
                "name": "X",
                "start_time": "09:00",
                "end_time": "18:00",
            },
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403

    def test_employees_may_read_shifts(
        self, client: TestClient, employee_user: User, day_shift: dict
    ) -> None:
        """Employees need to see their own shift timings."""
        response = client.get("/api/v1/shifts", headers=auth_header(client, "e.employee"))
        assert response.status_code == 200
        assert [row["code"] for row in response.json()] == ["GEN"]


class TestShiftAssignments:
    def test_assign_and_list(
        self, client: TestClient, admin_user: User, day_shift: dict, linked_employee: dict
    ) -> None:
        headers = auth_header(client, "hradmin")
        assign(client, headers, employee_id=linked_employee["id"], shift_id=day_shift["id"])
        listed = client.get(f"/api/v1/shifts/assignments/{linked_employee['id']}", headers=headers)
        assert listed.status_code == 200
        assert listed.json()[0]["shift_code"] == "GEN"
        assert listed.json()[0]["is_current"] is True

    def test_overlapping_assignment_is_refused(
        self,
        client: TestClient,
        admin_user: User,
        day_shift: dict,
        night_shift: dict,
        linked_employee: dict,
    ) -> None:
        """Two shifts on one day would make that day's calculation ambiguous."""
        headers = auth_header(client, "hradmin")
        assign(client, headers, employee_id=linked_employee["id"], shift_id=day_shift["id"])
        clash = client.post(
            "/api/v1/shifts/assignments",
            json={
                "employee_id": linked_employee["id"],
                "shift_id": night_shift["id"],
                "effective_from": "2026-06-01",
            },
            headers=headers,
        )
        assert clash.status_code == 409
        assert "overlap" in clash.json()["error"]["message"].lower()

    def test_adjacent_assignments_are_allowed(
        self,
        client: TestClient,
        admin_user: User,
        day_shift: dict,
        night_shift: dict,
        linked_employee: dict,
    ) -> None:
        """The legitimate handover: close one, start the next the following day."""
        headers = auth_header(client, "hradmin")
        assign(
            client,
            headers,
            employee_id=linked_employee["id"],
            shift_id=day_shift["id"],
            effective_from="2026-01-05",
            effective_to="2026-06-30",
        )
        assign(
            client,
            headers,
            employee_id=linked_employee["id"],
            shift_id=night_shift["id"],
            effective_from="2026-07-01",
        )
        listed = client.get(
            f"/api/v1/shifts/assignments/{linked_employee['id']}", headers=headers
        ).json()
        assert len(listed) == 2

    def test_database_blocks_an_overlap_the_service_missed(
        self,
        session: Session,
        admin_user: User,
        day_shift: dict,
        night_shift: dict,
        linked_employee: dict,
    ) -> None:
        """The exclusion constraint is the real guarantee.

        Two concurrent requests can each pass the service's overlap check and
        then both commit; only the database can stop that.
        """
        for shift_id in (day_shift["id"], night_shift["id"]):
            session.add(
                ShiftAssignment(
                    employee_id=linked_employee["id"],
                    shift_id=shift_id,
                    effective_from=date(2026, 3, 1),
                    effective_to=None,
                )
            )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_assignment_before_joining_is_refused(
        self, client: TestClient, admin_user: User, day_shift: dict, linked_employee: dict
    ) -> None:
        response = client.post(
            "/api/v1/shifts/assignments",
            json={
                "employee_id": linked_employee["id"],
                "shift_id": day_shift["id"],
                "effective_from": "2025-01-01",
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422
        assert "joined" in response.json()["error"]["message"]

    def test_closing_then_reassigning(
        self,
        client: TestClient,
        admin_user: User,
        day_shift: dict,
        night_shift: dict,
        linked_employee: dict,
    ) -> None:
        headers = auth_header(client, "hradmin")
        first = assign(client, headers, employee_id=linked_employee["id"], shift_id=day_shift["id"])
        closed = client.post(
            f"/api/v1/shifts/assignments/{first['id']}/close",
            json={"effective_to": "2026-06-30"},
            headers=headers,
        )
        assert closed.status_code == 200
        assign(
            client,
            headers,
            employee_id=linked_employee["id"],
            shift_id=night_shift["id"],
            effective_from="2026-07-01",
        )


class TestWorkCalendars:
    def test_create_pattern_and_calendar(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        pattern = client.post(
            "/api/v1/shifts/weekly-off-patterns",
            json={"code": "MONFRI", "name": "Monday to Friday", "off_days": ["sat", "sun"]},
            headers=headers,
        )
        assert pattern.status_code == 201
        assert pattern.json()["off_days"] == ["sat", "sun"]

        calendar = client.post(
            "/api/v1/shifts/calendars",
            json={
                "code": "IN",
                "name": "India calendar",
                "weekly_off_pattern_id": pattern.json()["id"],
                "is_default": True,
            },
            headers=headers,
        )
        assert calendar.status_code == 201
        assert calendar.json()["is_default"] is True

    def test_all_days_off_is_refused(self, client: TestClient, admin_user: User) -> None:
        response = client.post(
            "/api/v1/shifts/weekly-off-patterns",
            json={
                "code": "NEVER",
                "name": "Never working",
                "off_days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_only_one_default_calendar(self, client: TestClient, admin_user: User) -> None:
        """Two defaults would make calendar resolution ambiguous."""
        headers = auth_header(client, "hradmin")
        pattern = client.post(
            "/api/v1/shifts/weekly-off-patterns",
            json={"code": "P1", "name": "P1", "off_days": ["sun"]},
            headers=headers,
        ).json()

        first = client.post(
            "/api/v1/shifts/calendars",
            json={
                "code": "C1",
                "name": "C1",
                "weekly_off_pattern_id": pattern["id"],
                "is_default": True,
            },
            headers=headers,
        ).json()
        client.post(
            "/api/v1/shifts/calendars",
            json={
                "code": "C2",
                "name": "C2",
                "weekly_off_pattern_id": pattern["id"],
                "is_default": True,
            },
            headers=headers,
        )

        calendars = client.get("/api/v1/shifts/calendars", headers=headers).json()
        defaults = [row for row in calendars if row["is_default"]]
        assert len(defaults) == 1
        assert defaults[0]["code"] == "C2", "the newer one takes over"
        assert first["code"] == "C1"

    def test_day_type_resolution_over_a_range(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        pattern = client.post(
            "/api/v1/shifts/weekly-off-patterns",
            json={"code": "MF", "name": "Mon-Fri", "off_days": ["sat", "sun"]},
            headers=headers,
        ).json()
        calendar = client.post(
            "/api/v1/shifts/calendars",
            json={"code": "MAIN", "name": "Main", "weekly_off_pattern_id": pattern["id"]},
            headers=headers,
        ).json()

        client.post(
            f"/api/v1/shifts/calendars/{calendar['id']}/exceptions",
            json={
                "exception_date": "2026-11-04",
                "exception_type": "non_working",
                "reason": "Company closure",
            },
            headers=headers,
        )
        client.post(
            f"/api/v1/shifts/calendars/{calendar['id']}/exceptions",
            json={
                "exception_date": "2026-11-07",
                "exception_type": "working",
                "reason": "Working Saturday to compensate",
            },
            headers=headers,
        )

        resolved = client.get(
            f"/api/v1/shifts/calendars/{calendar['id']}/day-types",
            params={"start": "2026-11-02", "end": "2026-11-08"},
            headers=headers,
        ).json()
        by_date = {row["work_date"]: row for row in resolved}

        assert by_date["2026-11-02"]["day_type"] == "working"
        assert by_date["2026-11-04"]["day_type"] == "holiday"
        assert by_date["2026-11-04"]["source"] == "calendar_exception"
        assert by_date["2026-11-04"]["reason"] == "Company closure"
        assert by_date["2026-11-07"]["day_type"] == "working", "working Saturday wins"
        assert by_date["2026-11-08"]["day_type"] == "weekly_off"

    def test_duplicate_exception_date_is_refused(
        self, client: TestClient, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        pattern = client.post(
            "/api/v1/shifts/weekly-off-patterns",
            json={"code": "P", "name": "P", "off_days": ["sun"]},
            headers=headers,
        ).json()
        calendar = client.post(
            "/api/v1/shifts/calendars",
            json={"code": "C", "name": "C", "weekly_off_pattern_id": pattern["id"]},
            headers=headers,
        ).json()
        payload = {
            "exception_date": "2026-12-25",
            "exception_type": "non_working",
            "reason": "Holiday",
        }
        first = client.post(
            f"/api/v1/shifts/calendars/{calendar['id']}/exceptions",
            json=payload,
            headers=headers,
        )
        assert first.status_code == 201
        clash = client.post(
            f"/api/v1/shifts/calendars/{calendar['id']}/exceptions",
            json=payload,
            headers=headers,
        )
        assert clash.status_code == 409


# ---------------------------------------------------------------------------
# Punching
# ---------------------------------------------------------------------------


class TestPunching:
    def test_punch_in_then_out(
        self, client: TestClient, admin_user: User, day_shift: dict, linked_employee: dict
    ) -> None:
        assign(
            client,
            auth_header(client, "hradmin"),
            employee_id=linked_employee["id"],
            shift_id=day_shift["id"],
        )
        headers = auth_header(client, "e.employee")

        state = client.get("/api/v1/attendance/state", headers=headers).json()
        assert state["is_punched_in"] is False
        assert state["next_punch"] == "in"
        assert state["has_shift_assigned"] is True
        assert state["shift_code"] == "GEN"

        punched_in = client.post(
            "/api/v1/attendance/punch", json={"punch_type": "auto"}, headers=headers
        )
        assert punched_in.status_code == 200, punched_in.text
        assert punched_in.json()["recorded"] is True
        assert punched_in.json()["event_type"] == "in"

        after = client.get("/api/v1/attendance/state", headers=headers).json()
        assert after["is_punched_in"] is True
        assert after["next_punch"] == "out"

        punched_out = client.post(
            "/api/v1/attendance/punch", json={"punch_type": "auto"}, headers=headers
        )
        assert punched_out.json()["event_type"] == "out"
        assert punched_out.json()["day"]["worked_minutes"] >= 0

    def test_punch_requires_a_linked_employee_record(
        self, client: TestClient, employee_user: User, seeded: None
    ) -> None:
        response = client.post(
            "/api/v1/attendance/punch",
            json={"punch_type": "auto"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 409
        assert "not linked" in response.json()["error"]["message"].lower()

    def test_duplicate_punch_is_not_an_error(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        """A double-tapped button must not produce a failure to interpret."""
        headers = auth_header(client, "e.employee")
        client.post("/api/v1/attendance/punch", json={"punch_type": "in"}, headers=headers)
        again = client.post("/api/v1/attendance/punch", json={"punch_type": "in"}, headers=headers)
        assert again.status_code == 200
        assert again.json()["recorded"] is False
        assert "already recorded" in again.json()["message"]

    def test_punch_out_without_being_in_is_refused(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        response = client.post(
            "/api/v1/attendance/punch",
            json={"punch_type": "out"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422
        assert "not currently punched in" in response.json()["error"]["message"]

    def test_punch_body_has_no_timestamp_field(self, client: TestClient) -> None:
        """A client-supplied time would let an employee fake attendance."""
        schema = client.get("/api/openapi.json").json()
        body = schema["components"]["schemas"]["PunchRequest"]["properties"]
        assert set(body) == {"punch_type"}

    def test_a_supplied_timestamp_is_rejected(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        response = client.post(
            "/api/v1/attendance/punch",
            json={"punch_type": "in", "occurred_at": "2020-01-01T09:00:00Z"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422, "extra='forbid' refuses it outright"

    def test_punch_disabled_by_setting(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        client.put(
            "/api/v1/settings/attendance.web_punch_enabled",
            json={"value": False, "reason": "Testing"},
            headers=auth_header(client, "hradmin"),
        )
        response = client.post(
            "/api/v1/attendance/punch",
            json={"punch_type": "auto"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422
        assert "disabled" in response.json()["error"]["message"]

    def test_punch_works_without_a_shift_assignment(
        self, client: TestClient, linked_employee: dict, admin_user: User
    ) -> None:
        """Company defaults are used, and the day is flagged as unassigned."""
        headers = auth_header(client, "e.employee")
        state = client.get("/api/v1/attendance/state", headers=headers).json()
        assert state["has_shift_assigned"] is False

        response = client.post(
            "/api/v1/attendance/punch", json={"punch_type": "auto"}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["day"]["shift_id"] is None

    def test_punch_records_the_timezone_used(
        self,
        client: TestClient,
        session: Session,
        day_shift: dict,
        linked_employee: dict,
        admin_user: User,
    ) -> None:
        """Recorded so the attribution can be re-explained after a transfer."""
        client.post(
            "/api/v1/attendance/punch",
            json={"punch_type": "in"},
            headers=auth_header(client, "e.employee"),
        )
        event = session.scalar(select(AttendanceEvent))
        assert event is not None
        assert event.timezone_used == "Asia/Kolkata"
        assert event.source == "web"
        assert event.client_ip is not None


# ---------------------------------------------------------------------------
# Immutability and recomputation
# ---------------------------------------------------------------------------


class TestEventImmutability:
    def test_core_fields_cannot_be_updated(
        self,
        client: TestClient,
        session: Session,
        day_shift: dict,
        linked_employee: dict,
        admin_user: User,
    ) -> None:
        """The database refuses it, not just the application.

        An UPDATE would change history silently and make a past figure
        unexplainable.
        """
        client.post(
            "/api/v1/attendance/punch",
            json={"punch_type": "in"},
            headers=auth_header(client, "e.employee"),
        )
        with pytest.raises(DBAPIError):
            session.execute(
                text("UPDATE attendance_events SET occurred_at = now() + interval '1 hour'")
            )
        session.rollback()

    def test_superseding_is_permitted(
        self,
        client: TestClient,
        session: Session,
        day_shift: dict,
        linked_employee: dict,
        admin_user: User,
    ) -> None:
        """The trigger allows the bookkeeping columns, which is how a
        correction is expressed without destroying the original."""
        client.post(
            "/api/v1/attendance/punch",
            json={"punch_type": "in"},
            headers=auth_header(client, "e.employee"),
        )
        session.execute(text("UPDATE attendance_events SET is_superseded = true, reason = 'test'"))
        session.commit()
        event = session.scalar(select(AttendanceEvent))
        assert event is not None and event.is_superseded is True

    def test_superseded_events_stop_counting(
        self,
        client: TestClient,
        session: Session,
        day_shift: dict,
        linked_employee: dict,
        admin_user: User,
    ) -> None:
        headers = auth_header(client, "hradmin")
        today = date.today().isoformat()

        for event_type, clock in (("in", "09:30:00"), ("out", "18:30:00")):
            client.post(
                "/api/v1/attendance/events",
                json={
                    "employee_id": linked_employee["id"],
                    "event_type": event_type,
                    "local_datetime": f"{today}T{clock}",
                    "reason": "Backfill",
                },
                headers=headers,
            )

        day = session.scalar(select(AttendanceDay))
        assert day is not None and day.worked_minutes == 540

        out_event = session.scalar(
            select(AttendanceEvent).where(AttendanceEvent.event_type == "out")
        )
        assert out_event is not None
        response = client.post(
            f"/api/v1/attendance/events/{out_event.id}/supersede",
            json={"reason": "Device reported a spurious punch"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["has_missing_punch"] is True
        assert response.json()["worked_minutes"] == 0

        # The row is still there, which is the point.
        session.expire_all()
        assert session.scalar(select(func.count()).select_from(AttendanceEvent)) == 2


class TestRecomputation:
    def test_recompute_is_idempotent(
        self,
        client: TestClient,
        session: Session,
        day_shift: dict,
        linked_employee: dict,
        admin_user: User,
    ) -> None:
        """It runs after every punch and every correction, so it must be."""
        headers = auth_header(client, "hradmin")
        today = date.today().isoformat()
        for event_type, clock in (("in", "09:30:00"), ("out", "18:30:00")):
            client.post(
                "/api/v1/attendance/events",
                json={
                    "employee_id": linked_employee["id"],
                    "event_type": event_type,
                    "local_datetime": f"{today}T{clock}",
                    "reason": "Backfill",
                },
                headers=headers,
            )

        first = client.post(
            "/api/v1/attendance/recompute",
            json={"employee_ids": [linked_employee["id"]], "start": today, "end": today},
            headers=headers,
        )
        assert first.status_code == 200

        before = session.scalar(
            select(AttendanceDay).where(AttendanceDay.work_date == date.today())
        )
        assert before is not None
        snapshot = (before.status, before.worked_minutes, before.net_minutes)

        for _ in range(3):
            client.post(
                "/api/v1/attendance/recompute",
                json={
                    "employee_ids": [linked_employee["id"]],
                    "start": today,
                    "end": today,
                },
                headers=headers,
            )

        session.expire_all()
        after = session.scalar(select(AttendanceDay).where(AttendanceDay.work_date == date.today()))
        assert after is not None
        assert (after.status, after.worked_minutes, after.net_minutes) == snapshot
        assert session.scalar(select(func.count()).select_from(AttendanceDay)) == 1

    def test_sessions_are_replaced_not_duplicated(
        self,
        client: TestClient,
        session: Session,
        day_shift: dict,
        linked_employee: dict,
        admin_user: User,
    ) -> None:
        from app.modules.attendance.models import AttendanceSession

        headers = auth_header(client, "hradmin")
        today = date.today().isoformat()
        for event_type, clock in (("in", "09:30:00"), ("out", "18:30:00")):
            client.post(
                "/api/v1/attendance/events",
                json={
                    "employee_id": linked_employee["id"],
                    "event_type": event_type,
                    "local_datetime": f"{today}T{clock}",
                    "reason": "Backfill",
                },
                headers=headers,
            )
        for _ in range(3):
            client.post(
                "/api/v1/attendance/recompute",
                json={
                    "employee_ids": [linked_employee["id"]],
                    "start": today,
                    "end": today,
                },
                headers=headers,
            )
        session.expire_all()
        assert session.scalar(select(func.count()).select_from(AttendanceSession)) == 1

    def test_recompute_respects_the_lock_window(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        """A policy change must not silently rewrite old attendance."""
        headers = auth_header(client, "hradmin")
        old = (date.today() - timedelta(days=200)).isoformat()
        response = client.post(
            "/api/v1/attendance/recompute",
            json={
                "employee_ids": [linked_employee["id"]],
                "start": old,
                "end": old,
                "respect_lock": True,
            },
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["days_computed"] == 0
        assert body["skipped_locked"] == 1
        assert body["lock_cutoff"] is not None

    def test_lock_can_be_waived_deliberately(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        old = (date.today() - timedelta(days=200)).isoformat()
        response = client.post(
            "/api/v1/attendance/recompute",
            json={
                "employee_ids": [linked_employee["id"]],
                "start": old,
                "end": old,
                "respect_lock": False,
            },
            headers=headers,
        )
        assert response.json()["days_computed"] == 1
        assert response.json()["skipped_locked"] == 0

    def test_days_before_joining_are_skipped(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        """No row at all, which is distinct from a row saying absent."""
        headers = auth_header(client, "hradmin")
        response = client.post(
            "/api/v1/attendance/recompute",
            json={
                "employee_ids": [linked_employee["id"]],
                "start": "2026-01-01",
                "end": "2026-01-06",
                "respect_lock": False,
            },
            headers=headers,
        )
        body = response.json()
        assert body["skipped_not_employed"] == 4, "joined on the 5th"
        assert body["days_computed"] == 2

    def test_manual_override_survives_recomputation(
        self,
        client: TestClient,
        session: Session,
        day_shift: dict,
        linked_employee: dict,
        admin_user: User,
    ) -> None:
        """HR's explicit decision must not be quietly undone."""
        headers = auth_header(client, "hradmin")
        today = date.today().isoformat()

        client.post(
            "/api/v1/attendance/override",
            json={
                "employee_id": linked_employee["id"],
                "work_date": today,
                "status": "present",
                "payable_day_fraction": "1.00",
                "reason": "Off-site client visit, no badge reader",
            },
            headers=headers,
        )

        recomputed = client.post(
            "/api/v1/attendance/recompute",
            json={"employee_ids": [linked_employee["id"]], "start": today, "end": today},
            headers=headers,
        )
        assert recomputed.json()["preserved_manual_overrides"] == 1

        session.expire_all()
        day = session.scalar(select(AttendanceDay))
        assert day is not None
        assert day.status == "present"
        assert day.is_manual_override is True

    def test_override_requires_a_reason(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        response = client.post(
            "/api/v1/attendance/override",
            json={
                "employee_id": linked_employee["id"],
                "work_date": date.today().isoformat(),
                "status": "present",
                "payable_day_fraction": "1.00",
                "reason": "",
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_inverted_range_is_refused(self, client: TestClient, admin_user: User) -> None:
        response = client.post(
            "/api/v1/attendance/recompute",
            json={"start": "2026-11-30", "end": "2026-11-01"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_employees_cannot_recompute(self, client: TestClient, employee_user: User) -> None:
        response = client.post(
            "/api/v1/attendance/recompute",
            json={"start": "2026-11-01", "end": "2026-11-02"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403


class TestManualEvents:
    def test_manual_event_requires_a_reason(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        response = client.post(
            "/api/v1/attendance/events",
            json={
                "employee_id": linked_employee["id"],
                "event_type": "in",
                "local_datetime": f"{date.today().isoformat()}T09:30:00",
                "reason": "",
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_offset_bearing_timestamp_is_refused(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        """HR enters local wall-clock time; the zone is applied server-side."""
        response = client.post(
            "/api/v1/attendance/events",
            json={
                "employee_id": linked_employee["id"],
                "event_type": "in",
                "local_datetime": f"{date.today().isoformat()}T09:30:00+05:30",
                "reason": "Backfill",
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_local_time_is_anchored_to_the_work_location_zone(
        self,
        client: TestClient,
        session: Session,
        day_shift: dict,
        linked_employee: dict,
        admin_user: User,
    ) -> None:
        today = date.today()
        client.post(
            "/api/v1/attendance/events",
            json={
                "employee_id": linked_employee["id"],
                "event_type": "in",
                "local_datetime": f"{today.isoformat()}T09:30:00",
                "reason": "Backfill",
            },
            headers=auth_header(client, "hradmin"),
        )
        event = session.scalar(select(AttendanceEvent))
        assert event is not None
        # 09:30 IST is 04:00 UTC.
        assert event.occurred_at.astimezone(tz=None).utcoffset() is not None
        assert event.occurred_at.hour == 4 and event.occurred_at.minute == 0

    def test_employees_cannot_add_events_for_others(
        self, client: TestClient, employee_user: User, linked_employee: dict
    ) -> None:
        response = client.post(
            "/api/v1/attendance/events",
            json={
                "employee_id": linked_employee["id"],
                "event_type": "in",
                "local_datetime": f"{date.today().isoformat()}T09:30:00",
                "reason": "Trying it on",
            },
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Regularization
# ---------------------------------------------------------------------------


class TestRegularization:
    @pytest.fixture
    def manager_and_report(
        self,
        client: TestClient,
        session: Session,
        admin_user: User,
        employee_user: User,
        org_fixtures: dict[str, int],
        day_shift: dict,
    ) -> dict:
        """A manager with a linked account, and a report with one too."""
        headers = auth_header(client, "hradmin")

        manager = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0100",
                "first_name": "Meera",
                "last_name": "Iyer",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-05",
                **org_fixtures,
            },
            headers=headers,
        ).json()
        client.post(
            f"/api/v1/employees/{manager['id']}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )

        report_account = client.post(
            "/api/v1/auth/users",
            json={"username": "r.report", "role_codes": ["employee"]},
            headers=headers,
        ).json()
        report = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0101",
                "first_name": "Suresh",
                "last_name": "Rao",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-05",
                "reporting_manager_id": manager["id"],
                **org_fixtures,
            },
            headers=headers,
        ).json()
        client.post(
            f"/api/v1/employees/{report['id']}/link-account",
            json={"user_id": report_account["id"]},
            headers=headers,
        )

        # The report changes their temporary password so they can sign in.
        tokens = client.post(
            "/api/v1/auth/login",
            json={
                "identifier": "r.report",
                "password": report_account["temporary_password"],
            },
        ).json()
        client.post(
            "/api/v1/auth/me/change-password",
            json={
                "current_password": report_account["temporary_password"],
                "new_password": "ReportPass!2026",
            },
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

        assign(client, headers, employee_id=report["id"], shift_id=day_shift["id"])
        return {"manager": manager, "report": report}

    def report_headers(self, client: TestClient) -> dict:
        return auth_header(client, "r.report", "ReportPass!2026")

    def test_full_approval_chain(
        self, client: TestClient, session: Session, manager_and_report: dict
    ) -> None:
        """Employee submits, manager reviews, HR decides - the confirmed chain."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        created = client.post(
            "/api/v1/attendance/regularizations",
            json={
                "work_date": yesterday,
                "local_in": f"{yesterday}T09:30:00",
                "local_out": f"{yesterday}T18:30:00",
                "reason": "Forgot to punch; worked a full day",
            },
            headers=self.report_headers(client),
        )
        assert created.status_code == 201, created.text
        request_id = created.json()["id"]
        assert created.json()["status"] == "pending_manager"
        assert created.json()["current_stage"] == "reporting_manager"

        # The manager sees it in their queue.
        manager_headers = auth_header(client, "e.employee")
        pending = client.get(
            "/api/v1/attendance/regularizations/pending", headers=manager_headers
        ).json()
        assert [row["id"] for row in pending] == [request_id]

        # Manager approval advances; it does not apply the change.
        approved = client.post(
            f"/api/v1/attendance/regularizations/{request_id}/decision",
            json={"approve": True, "comment": "Confirmed, he was here"},
            headers=manager_headers,
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "pending_hr"
        assert approved.json()["manager_decision"] == "approved"

        session.expire_all()
        assert (
            session.scalar(
                select(func.count())
                .select_from(AttendanceEvent)
                .where(AttendanceEvent.source == "regularization")
            )
            == 0
        ), "nothing applied until HR decides"

        # HR gives the final decision, which applies it.
        final = client.post(
            f"/api/v1/attendance/regularizations/{request_id}/decision",
            json={"approve": True},
            headers=auth_header(client, "hradmin"),
        )
        assert final.json()["status"] == "approved"
        assert final.json()["current_stage"] == "complete"

        session.expire_all()
        events = session.scalars(
            select(AttendanceEvent).where(AttendanceEvent.source == "regularization")
        ).all()
        assert len(events) == 2, "approval inserts events rather than editing figures"

        day = session.scalar(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == manager_and_report["report"]["id"]
            )
        )
        assert day is not None
        assert day.worked_minutes == 540
        assert day.status == "present"

    def test_manager_rejection_ends_it(self, client: TestClient, manager_and_report: dict) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        created = client.post(
            "/api/v1/attendance/regularizations",
            json={
                "work_date": yesterday,
                "local_in": f"{yesterday}T09:30:00",
                "reason": "Please correct",
            },
            headers=self.report_headers(client),
        ).json()

        rejected = client.post(
            f"/api/v1/attendance/regularizations/{created['id']}/decision",
            json={"approve": False, "comment": "He was not in the office"},
            headers=auth_header(client, "e.employee"),
        )
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["current_stage"] == "complete"

    def test_cannot_decide_your_own_request(
        self, client: TestClient, manager_and_report: dict
    ) -> None:
        """Even with the permission, and even for an HR user."""
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        created = client.post(
            "/api/v1/attendance/regularizations",
            json={
                "work_date": yesterday,
                "local_in": f"{yesterday}T09:30:00",
                "reason": "Mine",
            },
            headers=self.report_headers(client),
        ).json()

        response = client.post(
            f"/api/v1/attendance/regularizations/{created['id']}/decision",
            json={"approve": True},
            headers=self.report_headers(client),
        )
        assert response.status_code == 403
        assert "your own" in response.json()["error"]["message"]

    def test_a_manager_cannot_decide_for_someone_elses_report(
        self,
        client: TestClient,
        manager_and_report: dict,
        org_fixtures: dict[str, int],
    ) -> None:
        """Holding the permission is not the same as being *their* manager."""
        headers = auth_header(client, "hradmin")
        outsider_account = client.post(
            "/api/v1/auth/users",
            json={"username": "o.outsider", "role_codes": ["employee"]},
            headers=headers,
        ).json()
        outsider = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0200",
                "first_name": "Outsider",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-05",
                **org_fixtures,
            },
            headers=headers,
        ).json()
        client.post(
            f"/api/v1/employees/{outsider['id']}/link-account",
            json={"user_id": outsider_account["id"]},
            headers=headers,
        )
        tokens = client.post(
            "/api/v1/auth/login",
            json={
                "identifier": "o.outsider",
                "password": outsider_account["temporary_password"],
            },
        ).json()
        client.post(
            "/api/v1/auth/me/change-password",
            json={
                "current_password": outsider_account["temporary_password"],
                "new_password": "Outsider!2026",
            },
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        created = client.post(
            "/api/v1/attendance/regularizations",
            json={
                "work_date": yesterday,
                "local_in": f"{yesterday}T09:30:00",
                "reason": "Mine",
            },
            headers=self.report_headers(client),
        ).json()

        response = client.post(
            f"/api/v1/attendance/regularizations/{created['id']}/decision",
            json={"approve": True},
            headers=auth_header(client, "o.outsider", "Outsider!2026"),
        )
        assert response.status_code == 403
        assert "direct reports" in response.json()["error"]["message"]

    def test_future_date_is_refused(self, client: TestClient, manager_and_report: dict) -> None:
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        response = client.post(
            "/api/v1/attendance/regularizations",
            json={
                "work_date": tomorrow,
                "local_in": f"{tomorrow}T09:30:00",
                "reason": "Future",
            },
            headers=self.report_headers(client),
        )
        assert response.status_code == 422
        assert "future" in response.json()["error"]["message"]

    def test_outside_the_window_is_refused(
        self, client: TestClient, manager_and_report: dict
    ) -> None:
        old = (date.today() - timedelta(days=120)).isoformat()
        response = client.post(
            "/api/v1/attendance/regularizations",
            json={"work_date": old, "local_in": f"{old}T09:30:00", "reason": "Old"},
            headers=self.report_headers(client),
        )
        assert response.status_code == 422
        assert "within" in response.json()["error"]["message"]

    def test_one_open_request_per_date(self, client: TestClient, manager_and_report: dict) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        payload = {
            "work_date": yesterday,
            "local_in": f"{yesterday}T09:30:00",
            "reason": "First",
        }
        assert (
            client.post(
                "/api/v1/attendance/regularizations",
                json=payload,
                headers=self.report_headers(client),
            ).status_code
            == 201
        )
        clash = client.post(
            "/api/v1/attendance/regularizations",
            json={**payload, "reason": "Second"},
            headers=self.report_headers(client),
        )
        assert clash.status_code == 409

    def test_withdrawal_frees_the_date(self, client: TestClient, manager_and_report: dict) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        payload = {
            "work_date": yesterday,
            "local_in": f"{yesterday}T09:30:00",
            "reason": "First",
        }
        created = client.post(
            "/api/v1/attendance/regularizations",
            json=payload,
            headers=self.report_headers(client),
        ).json()
        cancelled = client.post(
            f"/api/v1/attendance/regularizations/{created['id']}/cancel",
            headers=self.report_headers(client),
        )
        assert cancelled.json()["status"] == "cancelled"

        again = client.post(
            "/api/v1/attendance/regularizations",
            json={**payload, "reason": "Second attempt"},
            headers=self.report_headers(client),
        )
        assert again.status_code == 201

    def test_deciding_twice_is_refused(self, client: TestClient, manager_and_report: dict) -> None:
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        created = client.post(
            "/api/v1/attendance/regularizations",
            json={
                "work_date": yesterday,
                "local_in": f"{yesterday}T09:30:00",
                "reason": "Once",
            },
            headers=self.report_headers(client),
        ).json()
        client.post(
            f"/api/v1/attendance/regularizations/{created['id']}/decision",
            json={"approve": False},
            headers=auth_header(client, "e.employee"),
        )
        again = client.post(
            f"/api/v1/attendance/regularizations/{created['id']}/decision",
            json={"approve": True},
            headers=auth_header(client, "hradmin"),
        )
        assert again.status_code == 409

    def test_no_manager_escalates_straight_to_hr(
        self,
        client: TestClient,
        session: Session,
        admin_user: User,
        employee_user: User,
        org_fixtures: dict[str, int],
        day_shift: dict,
    ) -> None:
        """Otherwise the request would sit with nobody able to act on it."""
        headers = auth_header(client, "hradmin")
        employee = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0300",
                "first_name": "Lone",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-05",
                **org_fixtures,
            },
            headers=headers,
        ).json()
        client.post(
            f"/api/v1/employees/{employee['id']}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )

        yesterday = (date.today() - timedelta(days=1)).isoformat()
        created = client.post(
            "/api/v1/attendance/regularizations",
            json={
                "work_date": yesterday,
                "local_in": f"{yesterday}T09:30:00",
                "reason": "No manager assigned",
            },
            headers=auth_header(client, "e.employee"),
        )
        assert created.status_code == 201
        assert created.json()["status"] == "pending_hr"
        assert created.json()["current_stage"] == "hr_admin"
        assert created.json()["escalated_from_missing_manager"] is True

    def test_request_records_the_status_it_is_changing(
        self, client: TestClient, manager_and_report: dict
    ) -> None:
        """So a reviewer can see what is being changed and from what."""
        headers = auth_header(client, "hradmin")
        yesterday = date.today() - timedelta(days=1)
        client.post(
            "/api/v1/attendance/recompute",
            json={
                "employee_ids": [manager_and_report["report"]["id"]],
                "start": yesterday.isoformat(),
                "end": yesterday.isoformat(),
            },
            headers=headers,
        )
        created = client.post(
            "/api/v1/attendance/regularizations",
            json={
                "work_date": yesterday.isoformat(),
                "local_in": f"{yesterday.isoformat()}T09:30:00",
                "local_out": f"{yesterday.isoformat()}T18:30:00",
                "reason": "Please correct",
            },
            headers=self.report_headers(client),
        ).json()
        assert created["status_before"] in {
            "absent",
            "weekly_off",
            "holiday",
        }


# ---------------------------------------------------------------------------
# Reads and authorization
# ---------------------------------------------------------------------------


class TestReadsAndScoping:
    def test_summary_sums_payable_fractions(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        today = date.today()
        for event_type, clock in (("in", "09:30:00"), ("out", "18:30:00")):
            client.post(
                "/api/v1/attendance/events",
                json={
                    "employee_id": linked_employee["id"],
                    "event_type": event_type,
                    "local_datetime": f"{today.isoformat()}T{clock}",
                    "reason": "Backfill",
                },
                headers=headers,
            )

        summary = client.get(
            "/api/v1/attendance/me/summary",
            params={"start": today.isoformat(), "end": today.isoformat()},
            headers=auth_header(client, "e.employee"),
        )
        assert summary.status_code == 200
        body = summary.json()
        assert body["days_recorded"] == 1
        assert body["payable_days"] == "1.00", "exact decimal, as a string"
        assert body["worked_minutes"] == 540

    def test_employee_cannot_read_another_employees_attendance(
        self,
        client: TestClient,
        admin_user: User,
        employee_user: User,
        day_shift: dict,
        linked_employee: dict,
        org_fixtures: dict[str, int],
    ) -> None:
        other = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0999",
                "first_name": "Other",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-05",
                **org_fixtures,
            },
            headers=auth_header(client, "hradmin"),
        ).json()

        response = client.get(
            f"/api/v1/attendance/employees/{other['id']}",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 404, "404 not 403, so ids cannot be enumerated"

    def test_employee_cannot_read_raw_events(
        self, client: TestClient, employee_user: User, linked_employee: dict
    ) -> None:
        response = client.get(
            f"/api/v1/attendance/employees/{linked_employee['id']}/events",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403

    def test_employee_cannot_read_the_company_overview(
        self, client: TestClient, employee_user: User
    ) -> None:
        response = client.get("/api/v1/attendance/today", headers=auth_header(client, "e.employee"))
        assert response.status_code == 403

    def test_today_overview_separates_absent_from_not_computed(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        """Collapsing the two would misreport absence on the dashboard."""
        response = client.get("/api/v1/attendance/today", headers=auth_header(client, "hradmin"))
        assert response.status_code == 200
        body = response.json()
        assert body["active_employees"] >= 1
        assert body["absent"] == 0
        assert body["not_yet_computed"] >= 1

    def test_anomalies_queue_lists_missing_punches(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        today = date.today().isoformat()
        client.post(
            "/api/v1/attendance/events",
            json={
                "employee_id": linked_employee["id"],
                "event_type": "in",
                "local_datetime": f"{today}T09:30:00",
                "reason": "Backfill with no matching out",
            },
            headers=headers,
        )
        response = client.get("/api/v1/attendance/anomalies", headers=headers)
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["has_missing_punch"] is True
        assert response.json()[0]["anomalies"]

    def test_range_cap_is_enforced(
        self, client: TestClient, day_shift: dict, linked_employee: dict, admin_user: User
    ) -> None:
        response = client.get(
            "/api/v1/attendance/me",
            params={"start": "2020-01-01", "end": "2026-12-31"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422


class TestAuditTrail:
    def test_punches_and_corrections_are_audited(
        self,
        client: TestClient,
        session: Session,
        day_shift: dict,
        linked_employee: dict,
        admin_user: User,
    ) -> None:
        from app.modules.audit.models import AuditLog

        client.post(
            "/api/v1/attendance/punch",
            json={"punch_type": "in"},
            headers=auth_header(client, "e.employee"),
        )
        client.post(
            "/api/v1/attendance/override",
            json={
                "employee_id": linked_employee["id"],
                "work_date": date.today().isoformat(),
                "status": "present",
                "payable_day_fraction": "1.00",
                "reason": "Client site",
            },
            headers=auth_header(client, "hradmin"),
        )

        actions = {action for (action,) in session.execute(select(AuditLog.action)).all()}
        assert "attendance.punched_in" in actions
        assert "attendance.day_overridden" in actions
        assert "shift.created" in actions
        assert "shift.assigned" in actions or "employee.created" in actions

    def test_override_audit_records_before_and_after(
        self,
        client: TestClient,
        session: Session,
        day_shift: dict,
        linked_employee: dict,
        admin_user: User,
    ) -> None:
        from app.modules.audit.models import AuditLog

        client.post(
            "/api/v1/attendance/override",
            json={
                "employee_id": linked_employee["id"],
                "work_date": date.today().isoformat(),
                "status": "half_day",
                "payable_day_fraction": "0.50",
                "reason": "Left early for a medical appointment",
            },
            headers=auth_header(client, "hradmin"),
        )
        entry = session.scalar(
            select(AuditLog).where(AuditLog.action == "attendance.day_overridden")
        )
        assert entry is not None
        assert entry.after_value["status"] == "half_day"
        assert entry.before_value is not None
        assert "medical" in entry.context["reason"]
