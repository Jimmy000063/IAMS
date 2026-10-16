"""Organisation endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.org import service as org_service
from tests.conftest import auth_header

pytestmark = pytest.mark.integration


class TestAccessControl:
    def test_reads_require_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/org/departments").status_code == 401

    def test_employees_may_read_the_structure(
        self, client: TestClient, employee_user: User
    ) -> None:
        """Departments and locations are ordinary company information."""
        response = client.get("/api/v1/org/departments", headers=auth_header(client, "e.employee"))
        assert response.status_code == 200

    def test_employees_may_not_change_the_structure(
        self, client: TestClient, employee_user: User
    ) -> None:
        response = client.post(
            "/api/v1/org/departments",
            json={"code": "X", "name": "Rogue"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403


class TestDepartments:
    def test_create_and_list(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        created = client.post(
            "/api/v1/org/departments",
            json={"code": "eng", "name": "Engineering"},
            headers=headers,
        )
        assert created.status_code == 201
        assert created.json()["code"] == "ENG", "codes are normalised to upper case"

        listed = client.get("/api/v1/org/departments", headers=headers)
        assert [row["code"] for row in listed.json()] == ["ENG"]

    def test_duplicate_code_is_rejected(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        payload = {"code": "ENG", "name": "Engineering"}
        first = client.post("/api/v1/org/departments", json=payload, headers=headers)
        assert first.status_code == 201
        clash = client.post("/api/v1/org/departments", json=payload, headers=headers)
        assert clash.status_code == 409

    def test_code_comparison_is_case_insensitive(
        self, client: TestClient, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        client.post("/api/v1/org/departments", json={"code": "ENG", "name": "Eng"}, headers=headers)
        clash = client.post(
            "/api/v1/org/departments", json={"code": "eng", "name": "Eng 2"}, headers=headers
        )
        assert clash.status_code == 409

    def test_nesting_is_allowed(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        parent = client.post(
            "/api/v1/org/departments",
            json={"code": "TECH", "name": "Technology"},
            headers=headers,
        ).json()
        child = client.post(
            "/api/v1/org/departments",
            json={"code": "ENG", "name": "Engineering", "parent_id": parent["id"]},
            headers=headers,
        )
        assert child.status_code == 201
        assert child.json()["parent_id"] == parent["id"]

    def test_self_parenting_is_rejected(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        department = client.post(
            "/api/v1/org/departments", json={"code": "ENG", "name": "Eng"}, headers=headers
        ).json()
        response = client.patch(
            f"/api/v1/org/departments/{department['id']}",
            json={"parent_id": department["id"]},
            headers=headers,
        )
        assert response.status_code == 422

    def test_hierarchy_loop_is_rejected(self, client: TestClient, admin_user: User) -> None:
        """A loop would make any recursive org-chart query spin forever."""
        headers = auth_header(client, "hradmin")
        top = client.post(
            "/api/v1/org/departments", json={"code": "A", "name": "A"}, headers=headers
        ).json()
        middle = client.post(
            "/api/v1/org/departments",
            json={"code": "B", "name": "B", "parent_id": top["id"]},
            headers=headers,
        ).json()
        bottom = client.post(
            "/api/v1/org/departments",
            json={"code": "C", "name": "C", "parent_id": middle["id"]},
            headers=headers,
        ).json()

        # Making A report to C closes the loop A -> B -> C -> A.
        response = client.patch(
            f"/api/v1/org/departments/{top['id']}",
            json={"parent_id": bottom["id"]},
            headers=headers,
        )
        assert response.status_code == 422
        assert "loop" in response.json()["error"]["message"].lower()

    def test_deactivated_departments_are_hidden_but_retained(
        self, client: TestClient, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        department = client.post(
            "/api/v1/org/departments", json={"code": "OLD", "name": "Old"}, headers=headers
        ).json()
        client.patch(
            f"/api/v1/org/departments/{department['id']}",
            json={"is_active": False},
            headers=headers,
        )

        assert client.get("/api/v1/org/departments", headers=headers).json() == []
        with_inactive = client.get(
            "/api/v1/org/departments",
            params={"include_inactive": True},
            headers=headers,
        )
        assert [row["code"] for row in with_inactive.json()] == ["OLD"]


class TestWorkLocations:
    def test_create_with_a_valid_timezone(self, client: TestClient, admin_user: User) -> None:
        response = client.post(
            "/api/v1/org/work-locations",
            json={
                "code": "BLR",
                "name": "Bengaluru",
                "timezone": "Asia/Kolkata",
                "state": "Karnataka",
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 201
        assert response.json()["timezone"] == "Asia/Kolkata"

    @pytest.mark.parametrize("timezone", ["Mars/Olympus", "IST", "GMT+5:30", "", "   "])
    def test_unknown_timezone_is_rejected(
        self, client: TestClient, admin_user: User, timezone: str
    ) -> None:
        """The field attendance depends on most must not accept a bad value.

        A wrong timezone here would misdate every punch at this location, and
        the mistake would only surface in a month-end report.
        """
        response = client.post(
            "/api/v1/org/work-locations",
            json={"code": "X", "name": "X", "timezone": timezone},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_timezone_change_is_flagged_in_the_audit_log(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        from sqlalchemy import select

        from app.modules.audit.models import AuditLog

        headers = auth_header(client, "hradmin")
        location = client.post(
            "/api/v1/org/work-locations",
            json={"code": "BLR", "name": "Bengaluru", "timezone": "Asia/Kolkata"},
            headers=headers,
        ).json()

        client.patch(
            f"/api/v1/org/work-locations/{location['id']}",
            json={"timezone": "Asia/Dubai"},
            headers=headers,
        )

        entry = session.scalar(
            select(AuditLog)
            .where(AuditLog.action == "org.work_location_updated")
            .order_by(AuditLog.id.desc())
        )
        assert entry is not None
        assert entry.context["timezone_changed"] is True
        assert entry.before_value["timezone"] == "Asia/Kolkata"
        assert entry.after_value["timezone"] == "Asia/Dubai"

    def test_timezone_resolution_for_an_employee(
        self, session: Session, employee_record, org_fixtures: dict[str, int]
    ) -> None:
        """Attendance resolves the employee's location timezone, not the server's."""
        assert org_service.resolve_employee_timezone(session, employee_record.id) == "Asia/Kolkata"

    def test_falls_back_to_the_default_without_a_location(
        self, session: Session, seeded: None, cli_context
    ) -> None:
        from datetime import date

        from app.modules.employees import service as employee_service

        employee = employee_service.create_employee(
            session,
            data={
                "employee_code": "ITC9999",
                "first_name": "NoLocation",
                "employment_type": "contract",
                "date_of_joining": date(2026, 2, 1),
            },
            context=cli_context,
        )
        session.commit()
        assert org_service.resolve_employee_timezone(session, employee.id) == "Asia/Kolkata"


class TestDesignationsAndGroups:
    def test_designations_sort_by_level(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        for code, name, level in (("SE3", "Senior", 3), ("SE1", "Junior", 1)):
            client.post(
                "/api/v1/org/designations",
                json={"code": code, "name": name, "level": level},
                headers=headers,
            )
        listed = client.get("/api/v1/org/designations", headers=headers).json()
        assert [row["code"] for row in listed] == ["SE1", "SE3"]

    def test_group_membership_is_replaced_wholesale(
        self, client: TestClient, admin_user: User, employee_record
    ) -> None:
        headers = auth_header(client, "hradmin")
        group = client.post(
            "/api/v1/org/groups",
            json={"code": "BLRCAL", "name": "Bengaluru calendar"},
            headers=headers,
        ).json()

        added = client.put(
            f"/api/v1/org/groups/{group['id']}/members",
            json={"employee_ids": [employee_record.id]},
            headers=headers,
        )
        assert added.status_code == 200
        assert added.json()["member_count"] == 1

        cleared = client.put(
            f"/api/v1/org/groups/{group['id']}/members",
            json={"employee_ids": []},
            headers=headers,
        )
        assert cleared.json()["member_count"] == 0

    def test_unknown_employee_id_is_rejected(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        group = client.post(
            "/api/v1/org/groups", json={"code": "G", "name": "G"}, headers=headers
        ).json()
        response = client.put(
            f"/api/v1/org/groups/{group['id']}/members",
            json={"employee_ids": [999999]},
            headers=headers,
        )
        assert response.status_code == 422
