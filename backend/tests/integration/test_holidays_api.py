"""Holiday calendar tests against a real database and the real HTTP stack.

The point that matters most is the integration: a published holiday has to make
a date non-working for **attendance and leave both**, through the one shared
resolver. A test that only checked the holiday table would miss the whole
purpose of the feature.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.attendance.models import AttendanceDay
from app.modules.auth.models import User
from tests.conftest import auth_header

pytestmark = pytest.mark.integration

YEAR = 2026
# A Wednesday, comfortably inside every window, with no weekend interaction.
WED = date(2026, 11, 18)
THU = date(2026, 11, 19)
FRI = date(2026, 11, 20)


@pytest.fixture
def hr(client: TestClient, admin_user: User) -> dict:
    return auth_header(client, "hradmin")


@pytest.fixture
def employee(
    client: TestClient, hr: dict, employee_user: User, org_fixtures: dict[str, int]
) -> dict:
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
        headers=hr,
    )
    assert created.status_code == 201, created.text
    record = created.json()
    link = client.post(
        f"/api/v1/employees/{record['id']}/link-account",
        json={"user_id": employee_user.id},
        headers=hr,
    )
    assert link.status_code == 200, link.text
    return record


@pytest.fixture
def day_shift(client: TestClient, hr: dict) -> dict:
    response = client.post(
        "/api/v1/shifts",
        json={
            "code": "GEN",
            "name": "General shift",
            "start_time": "09:30",
            "end_time": "18:30",
            "break_minutes": 60,
        },
        headers=hr,
    )
    assert response.status_code == 201, response.text
    return response.json()


def make_calendar(client: TestClient, hr: dict, **overrides) -> dict:
    body = {"code": "IN", "name": f"India {YEAR}", "year": YEAR, **overrides}
    response = client.post("/api/v1/holidays/calendars", json=body, headers=hr)
    assert response.status_code == 201, response.text
    return response.json()


def add_holiday(client: TestClient, hr: dict, calendar_id: int, **overrides) -> dict:
    body = {"holiday_date": WED.isoformat(), "name": "Test holiday", **overrides}
    response = client.post(
        f"/api/v1/holidays/calendars/{calendar_id}/holidays", json=body, headers=hr
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestCalendars:
    def test_no_holidays_are_seeded(self, client: TestClient, hr: dict) -> None:
        """Dates vary by year, state and company; an invented one would mark a
        wrong non-working day for everybody on the calendar."""
        response = client.get("/api/v1/holidays/calendars", headers=hr)
        assert response.status_code == 200
        assert response.json() == []

    def test_a_duplicate_code_for_the_same_year_is_refused(
        self, client: TestClient, hr: dict
    ) -> None:
        make_calendar(client, hr)
        response = client.post(
            "/api/v1/holidays/calendars",
            json={"code": "in", "name": "Clash", "year": YEAR},
            headers=hr,
        )
        assert response.status_code == 409, response.text

    def test_the_same_code_in_a_different_year_is_allowed(
        self, client: TestClient, hr: dict
    ) -> None:
        make_calendar(client, hr)
        response = client.post(
            "/api/v1/holidays/calendars",
            json={"code": "IN", "name": "India 2027", "year": 2027},
            headers=hr,
        )
        assert response.status_code == 201, response.text

    def test_a_date_outside_the_calendars_year_is_refused(
        self, client: TestClient, hr: dict
    ) -> None:
        calendar = make_calendar(client, hr)
        response = client.post(
            f"/api/v1/holidays/calendars/{calendar['id']}/holidays",
            json={"holiday_date": "2027-01-26", "name": "Wrong year"},
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "not in 2026" in response.text

    def test_two_entries_for_one_date_are_refused(self, client: TestClient, hr: dict) -> None:
        """Two rows would make "is this an optional holiday" ambiguous."""
        calendar = make_calendar(client, hr)
        add_holiday(client, hr, calendar["id"])
        response = client.post(
            f"/api/v1/holidays/calendars/{calendar['id']}/holidays",
            json={"holiday_date": WED.isoformat(), "name": "Another"},
            headers=hr,
        )
        assert response.status_code == 409, response.text

    def test_an_empty_calendar_cannot_be_published(self, client: TestClient, hr: dict) -> None:
        calendar = make_calendar(client, hr)
        response = client.post(f"/api/v1/holidays/calendars/{calendar['id']}/publish", headers=hr)
        assert response.status_code == 422, response.text
        assert "no holidays" in response.text

    def test_publishing_is_separate_from_editing(self, client: TestClient, hr: dict) -> None:
        """A draft list affects nothing until published, so it can be checked."""
        calendar = make_calendar(client, hr)
        add_holiday(client, hr, calendar["id"])

        before = client.get(f"/api/v1/holidays/calendars/{calendar['id']}", headers=hr).json()
        assert before["is_published"] is False
        assert before["holiday_count"] == 1

        published = client.post(f"/api/v1/holidays/calendars/{calendar['id']}/publish", headers=hr)
        assert published.status_code == 200, published.text
        assert published.json()["is_published"] is True
        assert published.json()["published_at"] is not None

    def test_publishing_twice_is_refused(self, client: TestClient, hr: dict) -> None:
        calendar = make_calendar(client, hr)
        add_holiday(client, hr, calendar["id"])
        client.post(f"/api/v1/holidays/calendars/{calendar['id']}/publish", headers=hr)
        again = client.post(f"/api/v1/holidays/calendars/{calendar['id']}/publish", headers=hr)
        assert again.status_code == 409, again.text

    def test_withdrawing_a_calendar_needs_a_reason(self, client: TestClient, hr: dict) -> None:
        """It turns non-working days back into working days for everyone."""
        calendar = make_calendar(client, hr)
        add_holiday(client, hr, calendar["id"])
        client.post(f"/api/v1/holidays/calendars/{calendar['id']}/publish", headers=hr)

        refused = client.post(
            f"/api/v1/holidays/calendars/{calendar['id']}/unpublish",
            json={"reason": ""},
            headers=hr,
        )
        assert refused.status_code == 422, refused.text

        allowed = client.post(
            f"/api/v1/holidays/calendars/{calendar['id']}/unpublish",
            json={"reason": "Published the wrong year's list."},
            headers=hr,
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["is_published"] is False


class TestImport:
    def test_a_list_imports_once(self, client: TestClient, hr: dict) -> None:
        calendar = make_calendar(client, hr)
        rows = {
            "rows": [
                {"holiday_date": WED.isoformat(), "name": "Holiday one"},
                {"holiday_date": THU.isoformat(), "name": "Holiday two"},
            ]
        }
        first = client.post(
            f"/api/v1/holidays/calendars/{calendar['id']}/import", json=rows, headers=hr
        )
        assert first.status_code == 200, first.text
        assert first.json()["created"] == 2
        assert first.json()["already_imported"] is False

        second = client.post(
            f"/api/v1/holidays/calendars/{calendar['id']}/import", json=rows, headers=hr
        )
        assert second.json()["already_imported"] is True
        assert second.json()["created"] == 0

    def test_one_bad_row_does_not_reject_the_whole_list(self, client: TestClient, hr: dict) -> None:
        calendar = make_calendar(client, hr)
        response = client.post(
            f"/api/v1/holidays/calendars/{calendar['id']}/import",
            json={
                "rows": [
                    {"holiday_date": WED.isoformat(), "name": "Good"},
                    {"holiday_date": "2027-01-26", "name": "Wrong year"},
                ]
            },
            headers=hr,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["created"] == 1
        assert body["rejected"] == 1
        assert "not in 2026" in body["problems"][0]["message"]

    def test_re_importing_a_changed_list_updates_the_existing_dates(
        self, client: TestClient, hr: dict
    ) -> None:
        calendar = make_calendar(client, hr)
        client.post(
            f"/api/v1/holidays/calendars/{calendar['id']}/import",
            json={"rows": [{"holiday_date": WED.isoformat(), "name": "Original"}]},
            headers=hr,
        )
        response = client.post(
            f"/api/v1/holidays/calendars/{calendar['id']}/import",
            json={"rows": [{"holiday_date": WED.isoformat(), "name": "Corrected"}]},
            headers=hr,
        )
        assert response.json()["updated"] == 1

        holidays = client.get(
            f"/api/v1/holidays/calendars/{calendar['id']}/holidays", headers=hr
        ).json()
        assert holidays[0]["name"] == "Corrected"


class TestAttendanceIntegration:
    def test_a_published_holiday_makes_the_day_non_working_in_attendance(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        day_shift: dict,
    ) -> None:
        """The whole purpose: one resolver, so attendance sees it with no change."""
        client.post(
            "/api/v1/shifts/assignments",
            json={
                "employee_id": employee["id"],
                "shift_id": day_shift["id"],
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        calendar = make_calendar(client, hr)
        add_holiday(client, hr, calendar["id"], name="Company day")
        client.post(f"/api/v1/holidays/calendars/{calendar['id']}/publish", headers=hr)

        recomputed = client.post(
            "/api/v1/attendance/recompute",
            json={
                "employee_ids": [employee["id"]],
                "start": WED.isoformat(),
                "end": WED.isoformat(),
            },
            headers=hr,
        )
        assert recomputed.status_code == 200, recomputed.text

        day = session.scalars(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == employee["id"],
                AttendanceDay.work_date == WED,
            )
        ).one()
        assert day.day_type == "holiday"
        assert day.status == "holiday"

    def test_an_unpublished_holiday_changes_nothing(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        day_shift: dict,
    ) -> None:
        client.post(
            "/api/v1/shifts/assignments",
            json={
                "employee_id": employee["id"],
                "shift_id": day_shift["id"],
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        calendar = make_calendar(client, hr)
        add_holiday(client, hr, calendar["id"])
        # Deliberately not published.

        client.post(
            "/api/v1/attendance/recompute",
            json={
                "employee_ids": [employee["id"]],
                "start": WED.isoformat(),
                "end": WED.isoformat(),
            },
            headers=hr,
        )
        day = session.scalars(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == employee["id"],
                AttendanceDay.work_date == WED,
            )
        ).one_or_none()
        assert day is None or day.day_type == "working"

    def test_a_working_exception_still_beats_a_holiday(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        day_shift: dict,
    ) -> None:
        """The M3 precedence rule has to survive holidays being added."""
        client.post(
            "/api/v1/shifts/assignments",
            json={
                "employee_id": employee["id"],
                "shift_id": day_shift["id"],
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        pattern = client.post(
            "/api/v1/shifts/weekly-off-patterns",
            json={"code": "MF", "name": "Mon-Fri", "off_days": ["sat", "sun"]},
            headers=hr,
        )
        assert pattern.status_code == 201, pattern.text
        work_calendar = client.post(
            "/api/v1/shifts/calendars",
            json={
                "code": "MAIN",
                "name": "Main",
                "weekly_off_pattern_id": pattern.json()["id"],
                "is_default": True,
            },
            headers=hr,
        )
        assert work_calendar.status_code == 201, work_calendar.text
        client.post(
            f"/api/v1/shifts/calendars/{work_calendar.json()['id']}/exceptions",
            json={
                "exception_date": WED.isoformat(),
                "exception_type": "working",
                "reason": "Compensatory working day",
            },
            headers=hr,
        )

        calendar = make_calendar(client, hr)
        add_holiday(client, hr, calendar["id"])
        client.post(f"/api/v1/holidays/calendars/{calendar['id']}/publish", headers=hr)

        client.post(
            "/api/v1/attendance/recompute",
            json={
                "employee_ids": [employee["id"]],
                "start": WED.isoformat(),
                "end": WED.isoformat(),
            },
            headers=hr,
        )
        day = session.scalars(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == employee["id"],
                AttendanceDay.work_date == WED,
            )
        ).one()
        assert day.day_type == "working", "a working exception beats a holiday"

    def test_a_scoped_calendar_applies_only_to_that_location(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        org_fixtures: dict[str, int],
    ) -> None:
        """Employees in different states do not get the same holiday list."""
        other_location = client.post(
            "/api/v1/org/work-locations",
            json={
                "code": "CCU",
                "name": "Kolkata Office",
                "timezone": "Asia/Kolkata",
                "state": "West Bengal",
            },
            headers=hr,
        )
        assert other_location.status_code == 201, other_location.text

        calendar = make_calendar(
            client, hr, code="WB", work_location_id=other_location.json()["id"]
        )
        add_holiday(client, hr, calendar["id"], name="Regional holiday")
        client.post(f"/api/v1/holidays/calendars/{calendar['id']}/publish", headers=hr)

        mine = client.get(
            f"/api/v1/holidays/employees/{employee['id']}"
            f"?start={WED.isoformat()}&end={WED.isoformat()}",
            headers=hr,
        ).json()
        assert mine == [], "the Bengaluru employee does not get the Kolkata holiday"

    def test_applicable_calendars_union_rather_than_replace(
        self, client: TestClient, hr: dict, employee: dict, org_fixtures: dict[str, int]
    ) -> None:
        national = make_calendar(client, hr, code="IN")
        add_holiday(client, hr, national["id"], holiday_date=WED.isoformat(), name="National")
        client.post(f"/api/v1/holidays/calendars/{national['id']}/publish", headers=hr)

        regional = make_calendar(
            client, hr, code="KA", work_location_id=org_fixtures["work_location_id"]
        )
        add_holiday(client, hr, regional["id"], holiday_date=THU.isoformat(), name="Regional")
        client.post(f"/api/v1/holidays/calendars/{regional['id']}/publish", headers=hr)

        rows = client.get(
            f"/api/v1/holidays/employees/{employee['id']}"
            f"?start={WED.isoformat()}&end={FRI.isoformat()}",
            headers=hr,
        ).json()
        assert sorted(row["name"] for row in rows) == ["National", "Regional"]


class TestOptionalHolidays:
    def _published_optional(self, client: TestClient, hr: dict) -> tuple[dict, dict]:
        calendar = make_calendar(client, hr)
        first = add_holiday(
            client,
            hr,
            calendar["id"],
            holiday_date=WED.isoformat(),
            name="Optional one",
            holiday_type="restricted",
        )
        second = add_holiday(
            client,
            hr,
            calendar["id"],
            holiday_date=THU.isoformat(),
            name="Optional two",
            holiday_type="optional",
        )
        client.post(f"/api/v1/holidays/calendars/{calendar['id']}/publish", headers=hr)
        return first, second

    def test_an_unelected_optional_holiday_does_not_apply(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        """It is listed, so the employee knows it exists, but marked as not applying."""
        self._published_optional(client, hr)
        rows = client.get(
            f"/api/v1/holidays/employees/{employee['id']}"
            f"?start={WED.isoformat()}&end={FRI.isoformat()}",
            headers=hr,
        ).json()
        assert len(rows) == 2
        assert all(row["applies"] is False for row in rows)
        assert all(row["requires_election"] is True for row in rows)

    def test_electing_one_makes_it_apply_for_that_employee_only(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        first, _second = self._published_optional(client, hr)
        staff = auth_header(client, "e.employee")

        elected = client.post(
            "/api/v1/holidays/me/elections",
            json={"holiday_id": first["id"]},
            headers=staff,
        )
        assert elected.status_code == 201, elected.text

        rows = client.get(
            f"/api/v1/holidays/me?start={WED.isoformat()}&end={FRI.isoformat()}",
            headers=staff,
        ).json()
        applying = [row for row in rows if row["applies"]]
        assert [row["name"] for row in applying] == ["Optional one"]

    def test_an_elected_optional_holiday_shows_in_attendance(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        day_shift: dict,
    ) -> None:
        """The day is non-working for the employee who chose it, through the one
        shared resolver - the same path a public holiday takes."""
        client.post(
            "/api/v1/shifts/assignments",
            json={
                "employee_id": employee["id"],
                "shift_id": day_shift["id"],
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        first, _second = self._published_optional(client, hr)
        staff = auth_header(client, "e.employee")
        client.post(
            "/api/v1/holidays/me/elections",
            json={"holiday_id": first["id"]},
            headers=staff,
        )

        client.post(
            "/api/v1/attendance/recompute",
            json={
                "employee_ids": [employee["id"]],
                "start": WED.isoformat(),
                "end": THU.isoformat(),
            },
            headers=hr,
        )
        days = {
            row.work_date: row.day_type
            for row in session.scalars(
                select(AttendanceDay).where(AttendanceDay.employee_id == employee["id"])
            ).all()
        }
        assert days.get(WED) == "holiday", "the elected one is a holiday"
        assert days.get(THU, "working") == "working", "the unelected one is not"

    def test_the_election_limit_is_enforced(
        self, client: TestClient, hr: dict, employee: dict, session: Session, seeded: None
    ) -> None:
        """Choosing a few from a longer list is what a restricted holiday means."""
        from app.modules.audit.service import AuditContext
        from app.modules.settings import service as settings_service

        settings_service.update_setting(
            session,
            "holiday.max_optional_elections",
            1,
            context=AuditContext(actor_label="test"),
            reason="Narrowing the limit for this test",
        )
        session.commit()

        first, second = self._published_optional(client, hr)
        staff = auth_header(client, "e.employee")

        assert (
            client.post(
                "/api/v1/holidays/me/elections",
                json={"holiday_id": first["id"]},
                headers=staff,
            ).status_code
            == 201
        )
        refused = client.post(
            "/api/v1/holidays/me/elections",
            json={"holiday_id": second["id"]},
            headers=staff,
        )
        assert refused.status_code == 422, refused.text
        assert "already chosen" in refused.text

    def test_an_automatic_holiday_cannot_be_elected(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        calendar = make_calendar(client, hr)
        public = add_holiday(client, hr, calendar["id"], name="Republic Day")
        client.post(f"/api/v1/holidays/calendars/{calendar['id']}/publish", headers=hr)

        response = client.post(
            "/api/v1/holidays/me/elections",
            json={"holiday_id": public["id"]},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422, response.text
        assert "applies automatically" in response.text

    def test_an_unpublished_holiday_cannot_be_elected(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        calendar = make_calendar(client, hr)
        optional = add_holiday(
            client, hr, calendar["id"], name="Draft optional", holiday_type="restricted"
        )
        response = client.post(
            "/api/v1/holidays/me/elections",
            json={"holiday_id": optional["id"]},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422, response.text
        assert "not published" in response.text

    def test_withdrawing_keeps_the_record(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        """Attendance may already have treated the day as non-working, and what
        was elected when is what explains it."""
        first, _second = self._published_optional(client, hr)
        staff = auth_header(client, "e.employee")
        client.post(
            "/api/v1/holidays/me/elections",
            json={"holiday_id": first["id"]},
            headers=staff,
        )
        withdrawn = client.delete(f"/api/v1/holidays/me/elections/{first['id']}", headers=staff)
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["status"] == "withdrawn"

        elections = client.get("/api/v1/holidays/me/elections", headers=staff).json()
        assert len(elections) == 1, "the row is kept, not deleted"
        assert elections[0]["status"] == "withdrawn"

    def test_a_holiday_with_elections_cannot_be_deleted(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        first, _second = self._published_optional(client, hr)
        client.post(
            "/api/v1/holidays/me/elections",
            json={"holiday_id": first["id"]},
            headers=auth_header(client, "e.employee"),
        )
        response = client.delete(f"/api/v1/holidays/holidays/{first['id']}", headers=hr)
        assert response.status_code == 409, response.text
        assert "Deactivate it" in response.text

    def test_remaining_elections_are_reported(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        first, _second = self._published_optional(client, hr)
        staff = auth_header(client, "e.employee")

        before = client.get("/api/v1/holidays/me/optional/limits", headers=staff).json()
        assert before["elected_count"] == 0
        assert before["remaining"] == before["max_elections"]

        client.post(
            "/api/v1/holidays/me/elections",
            json={"holiday_id": first["id"]},
            headers=staff,
        )
        after = client.get("/api/v1/holidays/me/optional/limits", headers=staff).json()
        assert after["elected_count"] == 1
        assert after["remaining"] == before["remaining"] - 1


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/holidays/calendars"),
            ("post", "/api/v1/holidays/calendars/1/holidays"),
            ("post", "/api/v1/holidays/calendars/1/publish"),
            ("post", "/api/v1/holidays/calendars/1/import"),
        ],
    )
    def test_an_employee_cannot_manage_calendars(
        self,
        client: TestClient,
        admin_user: User,
        employee_user: User,
        method: str,
        path: str,
    ) -> None:
        response = getattr(client, method)(path, json={}, headers=auth_header(client, "e.employee"))
        assert response.status_code == 403, f"{method.upper()} {path} -> {response.status_code}"

    def test_an_employee_can_read_calendars_and_elect(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        """Reading the holiday list and choosing an optional day is self-service."""
        staff = auth_header(client, "e.employee")
        assert client.get("/api/v1/holidays/calendars", headers=staff).status_code == 200
        assert client.get("/api/v1/holidays/me/optional", headers=staff).status_code == 200

    def test_an_anonymous_caller_is_refused(self, client: TestClient) -> None:
        assert client.get("/api/v1/holidays/calendars").status_code == 401
        assert client.post("/api/v1/holidays/calendars", json={}).status_code == 401
