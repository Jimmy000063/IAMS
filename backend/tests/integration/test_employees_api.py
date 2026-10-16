"""Employee endpoint tests.

The isolation tests here are the most important in the suite: they are what
establish that an employee cannot read another employee's record, and that
statutory and bank data stay behind their own permissions.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.employees import service as employee_service
from app.modules.employees.models import Employee, EmploymentHistory
from tests.conftest import TEST_PASSWORD, auth_header, login

pytestmark = pytest.mark.integration


def make_employee(
    client: TestClient,
    headers: dict,
    *,
    code: str,
    first_name: str = "Test",
    manager_id: int | None = None,
    joining: str = "2026-01-05",
    **extra,
) -> dict:
    payload = {
        "employee_code": code,
        "first_name": first_name,
        "employment_type": "full_time",
        "date_of_joining": joining,
        **extra,
    }
    if manager_id is not None:
        payload["reporting_manager_id"] = manager_id
    response = client.post("/api/v1/employees", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


class TestCreate:
    def test_hr_can_onboard_an_employee(
        self, client: TestClient, admin_user: User, org_fixtures: dict[str, int]
    ) -> None:
        headers = auth_header(client, "hradmin")
        body = make_employee(
            client,
            headers,
            code="itc0001",
            first_name="Ravi",
            last_name="Sharma",
            work_email="Ravi.Sharma@Example.com",
            **org_fixtures,
        )
        assert body["employee_code"] == "ITC0001", "codes normalise to upper case"
        assert body["work_email"] == "ravi.sharma@example.com", "emails normalise to lower"
        assert body["full_name"] == "Ravi Sharma"
        assert body["work_location_timezone"] == "Asia/Kolkata"

    def test_employees_cannot_onboard_anyone(self, client: TestClient, employee_user: User) -> None:
        response = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ROGUE",
                "first_name": "R",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-01",
            },
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403

    def test_onboarding_opens_an_employment_history_period(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        created = make_employee(client, headers, code="ITC0001")

        rows = session.scalars(
            select(EmploymentHistory).where(EmploymentHistory.employee_id == created["id"])
        ).all()
        assert len(rows) == 1
        assert rows[0].change_type == "joining"
        assert rows[0].effective_to is None, "the current period stays open"

    def test_duplicate_code_is_rejected(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        make_employee(client, headers, code="ITC0001")
        response = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "itc0001",
                "first_name": "Clash",
                "employment_type": "full_time",
                "date_of_joining": "2026-02-01",
            },
            headers=headers,
        )
        assert response.status_code == 409

    def test_duplicate_work_email_is_rejected(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        make_employee(client, headers, code="ITC0001", work_email="a@example.com")
        response = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0002",
                "first_name": "Clash",
                "employment_type": "full_time",
                "date_of_joining": "2026-02-01",
                "work_email": "a@example.com",
            },
            headers=headers,
        )
        assert response.status_code == 409

    def test_cannot_create_an_already_exited_employee(
        self, client: TestClient, admin_user: User
    ) -> None:
        """There would be no employment history for the period they worked."""
        response = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0001",
                "first_name": "Gone",
                "employment_type": "full_time",
                "employment_status": "exited",
                "date_of_joining": "2026-01-01",
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_inactive_department_is_rejected(
        self, client: TestClient, admin_user: User, org_fixtures: dict[str, int]
    ) -> None:
        headers = auth_header(client, "hradmin")
        client.patch(
            f"/api/v1/org/departments/{org_fixtures['department_id']}",
            json={"is_active": False},
            headers=headers,
        )
        response = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0001",
                "first_name": "X",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-01",
                "department_id": org_fixtures["department_id"],
            },
            headers=headers,
        )
        assert response.status_code == 422

    def test_next_code_suggestion(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        make_employee(client, headers, code="ITC0007")
        response = client.get(
            "/api/v1/employees/next-code", params={"prefix": "ITC"}, headers=headers
        )
        assert response.json()["suggested_code"] == "ITC0008"


class TestReportingHierarchy:
    def test_manager_can_be_assigned(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        manager = make_employee(client, headers, code="ITC0001", first_name="Manager")
        report = make_employee(
            client, headers, code="ITC0002", first_name="Report", manager_id=manager["id"]
        )
        assert report["reporting_manager_code"] == "ITC0001"

    def test_self_management_is_rejected(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")
        response = client.patch(
            f"/api/v1/employees/{employee['id']}",
            json={"reporting_manager_id": employee["id"]},
            headers=headers,
        )
        assert response.status_code == 422

    def test_reporting_loop_is_rejected(self, client: TestClient, admin_user: User) -> None:
        """A loop would make the leave-approval walk run forever."""
        headers = auth_header(client, "hradmin")
        top = make_employee(client, headers, code="ITC0001")
        middle = make_employee(client, headers, code="ITC0002", manager_id=top["id"])
        bottom = make_employee(client, headers, code="ITC0003", manager_id=middle["id"])

        response = client.patch(
            f"/api/v1/employees/{top['id']}",
            json={"reporting_manager_id": bottom["id"]},
            headers=headers,
        )
        assert response.status_code == 422
        assert "loop" in response.json()["error"]["message"].lower()

    def test_exited_manager_cannot_be_assigned(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        leaver = make_employee(client, headers, code="ITC0001")
        client.post(
            f"/api/v1/employees/{leaver['id']}/exit",
            json={"date_of_exit": "2026-06-30", "exit_type": "resignation"},
            headers=headers,
        )
        response = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0002",
                "first_name": "New",
                "employment_type": "full_time",
                "date_of_joining": "2026-07-01",
                "reporting_manager_id": leaver["id"],
            },
            headers=headers,
        )
        assert response.status_code == 422


class TestDataIsolation:
    """An employee must see only themselves and their reports."""

    @pytest.fixture
    def tree(
        self, client: TestClient, session: Session, admin_user: User, employee_user: User
    ) -> dict:
        headers = auth_header(client, "hradmin")
        manager = make_employee(client, headers, code="ITC0001", first_name="Manager")
        report = make_employee(
            client, headers, code="ITC0002", first_name="Report", manager_id=manager["id"]
        )
        sub_report = make_employee(
            client, headers, code="ITC0003", first_name="Sub", manager_id=report["id"]
        )
        stranger = make_employee(client, headers, code="ITC0009", first_name="Stranger")

        # Link the ordinary employee account to the manager record.
        client.post(
            f"/api/v1/employees/{manager['id']}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )
        return {
            "manager": manager,
            "report": report,
            "sub_report": sub_report,
            "stranger": stranger,
        }

    def test_employee_sees_own_record_via_me(self, client: TestClient, tree: dict) -> None:
        response = client.get("/api/v1/employees/me", headers=auth_header(client, "e.employee"))
        assert response.status_code == 200
        assert response.json()["employee_code"] == "ITC0001"

    def test_manager_sees_own_record_and_reports_only(self, client: TestClient, tree: dict) -> None:
        response = client.get(
            "/api/v1/employees",
            params={"page_size": 100},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 200
        codes = {row["employee_code"] for row in response.json()["items"]}
        assert codes == {"ITC0001", "ITC0002", "ITC0003"}, (
            "own record plus direct and indirect reports, and nobody else"
        )

    def test_stranger_record_returns_404_not_403(self, client: TestClient, tree: dict) -> None:
        """404 on purpose: a 403 would confirm the id exists, leaking headcount."""
        response = client.get(
            f"/api/v1/employees/{tree['stranger']['id']}",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 404

    def test_manager_can_read_an_indirect_report(self, client: TestClient, tree: dict) -> None:
        response = client.get(
            f"/api/v1/employees/{tree['sub_report']['id']}",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 200

    def test_stranger_history_is_also_hidden(self, client: TestClient, tree: dict) -> None:
        response = client.get(
            f"/api/v1/employees/{tree['stranger']['id']}/history",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 404

    def test_hr_sees_everyone(self, client: TestClient, tree: dict) -> None:
        response = client.get(
            "/api/v1/employees",
            params={"page_size": 100},
            headers=auth_header(client, "hradmin"),
        )
        codes = {row["employee_code"] for row in response.json()["items"]}
        assert {"ITC0001", "ITC0002", "ITC0003", "ITC0009"} <= codes

    def test_employee_without_a_linked_record_sees_nobody(
        self, client: TestClient, admin_user: User, employee_user: User
    ) -> None:
        """An empty scope must match nothing, not everything."""
        make_employee(client, auth_header(client, "hradmin"), code="ITC0001")
        response = client.get("/api/v1/employees", headers=auth_header(client, "e.employee"))
        assert response.status_code == 200
        assert response.json()["items"] == []

    def test_me_explains_an_unlinked_account(
        self, client: TestClient, employee_user: User, seeded: None
    ) -> None:
        response = client.get("/api/v1/employees/me", headers=auth_header(client, "e.employee"))
        assert response.status_code == 409
        assert "not linked" in response.json()["error"]["message"].lower()

    def test_team_endpoint_lists_direct_reports(self, client: TestClient, tree: dict) -> None:
        response = client.get(
            "/api/v1/employees/me/team", headers=auth_header(client, "e.employee")
        )
        assert [row["employee_code"] for row in response.json()] == ["ITC0002"]


class TestSelfServiceBoundary:
    @pytest.fixture
    def linked(self, client: TestClient, admin_user: User, employee_user: User) -> dict:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001", first_name="Ravi")
        client.post(
            f"/api/v1/employees/{employee['id']}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )
        return employee

    def test_employee_can_update_permitted_fields(self, client: TestClient, linked: dict) -> None:
        response = client.patch(
            "/api/v1/employees/me",
            json={"personal_phone": "+91 98765 43210", "city": "Pune"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 200
        assert response.json()["city"] == "Pune"

    @pytest.mark.parametrize(
        "field,value",
        [
            ("employment_type", "consultant"),
            ("department_id", 1),
            ("date_of_joining", "2020-01-01"),
            ("employee_code", "ITC9999"),
            ("reporting_manager_id", 1),
            ("employment_status", "active"),
        ],
    )
    def test_employee_cannot_change_position_or_pay_relevant_fields(
        self, client: TestClient, linked: dict, field: str, value: object
    ) -> None:
        """The schema rejects unknown fields, so these never reach the service."""
        response = client.patch(
            "/api/v1/employees/me",
            json={field: value},
            headers=auth_header(client, "e.employee"),
        )
        # Either the schema rejects the extra field or the service refuses it;
        # both are acceptable, silently applying it is not.
        assert response.status_code in {403, 422}
        fresh = client.get("/api/v1/employees/me", headers=auth_header(client, "e.employee")).json()
        assert fresh["employee_code"] == "ITC0001"
        assert fresh["employment_type"] == "full_time"

    def test_employee_cannot_patch_another_employee(
        self, client: TestClient, linked: dict, admin_user: User
    ) -> None:
        other = make_employee(client, auth_header(client, "hradmin"), code="ITC0002")
        response = client.patch(
            f"/api/v1/employees/{other['id']}",
            json={"first_name": "Hacked"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403

    def test_invalid_phone_is_rejected(self, client: TestClient, linked: dict) -> None:
        response = client.patch(
            "/api/v1/employees/me",
            json={"personal_phone": "not-a-phone!!"},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422


class TestTransfersAndHistory:
    def test_department_change_opens_a_new_period(
        self,
        client: TestClient,
        session: Session,
        admin_user: User,
        org_fixtures: dict[str, int],
    ) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001", **org_fixtures)
        other = client.post(
            "/api/v1/org/departments",
            json={"code": "OPS", "name": "Operations"},
            headers=headers,
        ).json()

        response = client.patch(
            f"/api/v1/employees/{employee['id']}",
            json={
                "department_id": other["id"],
                "effective_from": "2026-07-01",
                "change_reason": "Internal transfer",
            },
            headers=headers,
        )
        assert response.status_code == 200

        history = client.get(f"/api/v1/employees/{employee['id']}/history", headers=headers).json()
        assert len(history) == 2
        assert history[0]["change_type"] == "transfer"
        assert history[0]["effective_from"] == "2026-07-01"
        assert history[0]["effective_to"] is None
        # The joining period was closed the day before the transfer.
        assert history[1]["change_type"] == "joining"
        assert history[1]["effective_to"] == "2026-06-30"

    def test_name_change_does_not_create_a_period(
        self, client: TestClient, admin_user: User
    ) -> None:
        """Only position-relevant changes are effective-dated."""
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")
        client.patch(
            f"/api/v1/employees/{employee['id']}",
            json={"first_name": "Renamed"},
            headers=headers,
        )
        history = client.get(f"/api/v1/employees/{employee['id']}/history", headers=headers).json()
        assert len(history) == 1

    def test_manager_change_is_recorded_as_such(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        manager = make_employee(client, headers, code="ITC0001")
        employee = make_employee(client, headers, code="ITC0002")
        client.patch(
            f"/api/v1/employees/{employee['id']}",
            json={"reporting_manager_id": manager["id"]},
            headers=headers,
        )
        history = client.get(f"/api/v1/employees/{employee['id']}/history", headers=headers).json()
        assert history[0]["change_type"] == "manager_change"


class TestExit:
    def test_exit_preserves_the_record_and_deactivates_the_account(
        self, client: TestClient, session: Session, admin_user: User, employee_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")
        client.post(
            f"/api/v1/employees/{employee['id']}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )

        response = client.post(
            f"/api/v1/employees/{employee['id']}/exit",
            json={
                "date_of_exit": "2026-09-30",
                "exit_type": "resignation",
                "exit_reason": "Moved on",
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["employment_status"] == "exited"

        # The record is still there, by design.
        assert session.get(Employee, employee["id"]) is not None

        session.expire_all()
        assert session.get(User, employee_user.id).is_active is False

        denied = client.post(
            "/api/v1/auth/login",
            json={"identifier": "e.employee", "password": TEST_PASSWORD},
        )
        assert denied.status_code == 403

    def test_exit_date_before_joining_is_rejected(
        self, client: TestClient, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001", joining="2026-05-01")
        response = client.post(
            f"/api/v1/employees/{employee['id']}/exit",
            json={"date_of_exit": "2026-01-01", "exit_type": "resignation"},
            headers=headers,
        )
        assert response.status_code == 422

    def test_exited_employees_are_hidden_by_default(
        self, client: TestClient, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")
        client.post(
            f"/api/v1/employees/{employee['id']}/exit",
            json={"date_of_exit": "2026-09-30", "exit_type": "resignation"},
            headers=headers,
        )

        default = client.get("/api/v1/employees", headers=headers).json()
        assert default["items"] == []

        included = client.get(
            "/api/v1/employees", params={"include_exited": True}, headers=headers
        ).json()
        assert [row["employee_code"] for row in included["items"]] == ["ITC0001"]

    def test_orphaned_reports_are_recorded(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        """Their leave requests would otherwise stall with no visible cause."""
        headers = auth_header(client, "hradmin")
        manager = make_employee(client, headers, code="ITC0001")
        make_employee(client, headers, code="ITC0002", manager_id=manager["id"])

        client.post(
            f"/api/v1/employees/{manager['id']}/exit",
            json={"date_of_exit": "2026-09-30", "exit_type": "resignation"},
            headers=headers,
        )

        entry = session.scalar(select(AuditLog).where(AuditLog.action == "employee.exited"))
        assert entry is not None
        assert entry.context["reports_needing_reassignment"] == ["ITC0002"]

        orphans = employee_service.reports_needing_reassignment(session)
        assert [e.employee_code for e in orphans] == ["ITC0002"]

    def test_is_employed_on_boundaries(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        created = make_employee(client, headers, code="ITC0001", joining="2026-03-10")
        client.post(
            f"/api/v1/employees/{created['id']}/exit",
            json={
                "date_of_exit": "2026-08-31",
                "exit_type": "resignation",
                "last_working_day": "2026-08-31",
            },
            headers=headers,
        )
        session.expire_all()
        employee = session.get(Employee, created["id"])

        assert employee.is_employed_on(date(2026, 3, 9)) is False
        assert employee.is_employed_on(date(2026, 3, 10)) is True
        assert employee.is_employed_on(date(2026, 8, 31)) is True
        assert employee.is_employed_on(date(2026, 9, 1)) is False


class TestRestrictedFields:
    def test_employee_cannot_read_statutory_details(
        self, client: TestClient, admin_user: User, employee_user: User
    ) -> None:
        employee = make_employee(client, auth_header(client, "hradmin"), code="ITC0001")
        response = client.get(
            f"/api/v1/employees/{employee['id']}/statutory",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403

    def test_employee_cannot_read_bank_details(
        self, client: TestClient, admin_user: User, employee_user: User
    ) -> None:
        employee = make_employee(client, auth_header(client, "hradmin"), code="ITC0001")
        response = client.get(
            f"/api/v1/employees/{employee['id']}/bank-accounts",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403

    def test_pan_is_stored_encrypted_and_returned_masked(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")

        response = client.put(
            f"/api/v1/employees/{employee['id']}/statutory",
            json={"pan": "abcde1234f", "pf_uan": "100123456789", "aadhaar_last4": "6789"},
            headers=headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["pan_masked"] == "XXXXXX234F"
        assert "ABCDE1234F" not in response.text, "the full PAN must never be returned"

        # Nor stored in plaintext.
        raw = session.execute(
            select(
                __import__(
                    "app.modules.employees.models", fromlist=["EmployeeStatutoryDetails"]
                ).EmployeeStatutoryDetails.pan_encrypted
            )
        ).scalar_one()
        assert b"ABCDE1234F" not in bytes(raw)

    def test_full_aadhaar_is_rejected(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")
        response = client.put(
            f"/api/v1/employees/{employee['id']}/statutory",
            json={"aadhaar_last4": "123456789012"},
            headers=headers,
        )
        assert response.status_code == 422

    def test_bank_account_is_encrypted_and_masked(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")

        response = client.post(
            f"/api/v1/employees/{employee['id']}/bank-accounts",
            json={
                "account_holder_name": "Ravi Sharma",
                "account_number": "123456789012",
                "ifsc_code": "HDFC0000123",
                "bank_name": "HDFC Bank",
            },
            headers=headers,
        )
        assert response.status_code == 201
        assert response.json()["account_number_masked"].endswith("9012")
        assert "123456789012" not in response.text

        from app.modules.employees.models import EmployeeBankAccount

        stored = session.scalar(select(EmployeeBankAccount))
        assert b"123456789012" not in bytes(stored.account_number_encrypted)

    def test_invalid_ifsc_is_rejected(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")
        response = client.post(
            f"/api/v1/employees/{employee['id']}/bank-accounts",
            json={
                "account_holder_name": "X",
                "account_number": "123456789012",
                "ifsc_code": "BADIFSC0001",
                "bank_name": "Bank",
            },
            headers=headers,
        )
        assert response.status_code == 422

    def test_only_one_primary_account(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")
        base = {
            "account_holder_name": "X",
            "ifsc_code": "HDFC0000123",
            "bank_name": "HDFC",
            "is_primary": True,
        }
        client.post(
            f"/api/v1/employees/{employee['id']}/bank-accounts",
            json={**base, "account_number": "111111111111"},
            headers=headers,
        )
        client.post(
            f"/api/v1/employees/{employee['id']}/bank-accounts",
            json={**base, "account_number": "222222222222"},
            headers=headers,
        )
        accounts = client.get(
            f"/api/v1/employees/{employee['id']}/bank-accounts", headers=headers
        ).json()
        assert sum(1 for a in accounts if a["is_primary"]) == 1

    def test_reading_restricted_data_is_audited(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")
        client.get(f"/api/v1/employees/{employee['id']}/statutory", headers=headers)
        client.get(f"/api/v1/employees/{employee['id']}/bank-accounts", headers=headers)

        actions = {action for (action,) in session.execute(select(AuditLog.action)).all()}
        assert "employee.statutory_details_viewed" in actions
        assert "employee.bank_details_viewed" in actions

    def test_statutory_values_never_reach_the_audit_trail(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0001")
        client.put(
            f"/api/v1/employees/{employee['id']}/statutory",
            json={"pan": "ABCDE1234F", "pf_uan": "100123456789"},
            headers=headers,
        )
        client.post(
            f"/api/v1/employees/{employee['id']}/bank-accounts",
            json={
                "account_holder_name": "X",
                "account_number": "123456789012",
                "ifsc_code": "HDFC0000123",
                "bank_name": "HDFC",
            },
            headers=headers,
        )

        from sqlalchemy import text

        dumped = "\n".join(
            str(row)
            for row in session.execute(
                text("SELECT before_value, after_value, context FROM audit_logs")
            ).all()
        )
        assert "ABCDE1234F" not in dumped
        assert "100123456789" not in dumped
        assert "123456789012" not in dumped


class TestLinkAccount:
    def test_linking_enables_employee_code_sign_in(
        self, client: TestClient, admin_user: User, employee_user: User
    ) -> None:
        """The brief requires sign-in by employee ID as well as username."""
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0042")
        client.post(
            f"/api/v1/employees/{employee['id']}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )

        tokens = login(client, "ITC0042")
        assert tokens["access_token"]
        # Case-insensitively, too.
        assert login(client, "itc0042")["access_token"]

    def test_sign_in_by_work_email(
        self, client: TestClient, admin_user: User, employee_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        employee = make_employee(client, headers, code="ITC0042", work_email="priya@example.com")
        client.post(
            f"/api/v1/employees/{employee['id']}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )
        assert login(client, "priya@example.com")["access_token"]

    def test_one_account_per_employee(
        self, client: TestClient, admin_user: User, employee_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        first = make_employee(client, headers, code="ITC0001")
        second = make_employee(client, headers, code="ITC0002")

        client.post(
            f"/api/v1/employees/{first['id']}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )
        clash = client.post(
            f"/api/v1/employees/{second['id']}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )
        assert clash.status_code == 409


class TestDirectoryFilters:
    @pytest.fixture
    def population(
        self, client: TestClient, admin_user: User, org_fixtures: dict[str, int]
    ) -> None:
        headers = auth_header(client, "hradmin")
        make_employee(
            client,
            headers,
            code="ITC0001",
            first_name="Ravi",
            last_name="Sharma",
            work_email="ravi@example.com",
            **org_fixtures,
        )
        make_employee(
            client,
            headers,
            code="ITC0002",
            first_name="Priya",
            last_name="Nair",
            **org_fixtures,
        )
        make_employee(client, headers, code="CON0001", first_name="Contractor")

    def test_search_by_code(self, client: TestClient, population: None) -> None:
        response = client.get(
            "/api/v1/employees",
            params={"q": "ITC000"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.json()["meta"]["total_items"] == 2

    def test_search_by_name_is_case_insensitive(self, client: TestClient, population: None) -> None:
        response = client.get(
            "/api/v1/employees",
            params={"q": "PRIYA"},
            headers=auth_header(client, "hradmin"),
        )
        assert [row["employee_code"] for row in response.json()["items"]] == ["ITC0002"]

    def test_search_by_email(self, client: TestClient, population: None) -> None:
        response = client.get(
            "/api/v1/employees",
            params={"q": "ravi@"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.json()["meta"]["total_items"] == 1

    def test_filter_by_department(
        self, client: TestClient, population: None, org_fixtures: dict[str, int]
    ) -> None:
        response = client.get(
            "/api/v1/employees",
            params={"department_id": org_fixtures["department_id"]},
            headers=auth_header(client, "hradmin"),
        )
        assert response.json()["meta"]["total_items"] == 2

    def test_pagination_metadata(self, client: TestClient, population: None) -> None:
        response = client.get(
            "/api/v1/employees",
            params={"page": 1, "page_size": 2},
            headers=auth_header(client, "hradmin"),
        )
        meta = response.json()["meta"]
        assert meta["total_items"] == 3
        assert meta["total_pages"] == 2
        assert meta["has_next"] is True
        assert meta["has_previous"] is False

    def test_sorting_is_restricted_to_an_allow_list(
        self, client: TestClient, population: None
    ) -> None:
        """An unknown sort field falls back to the default, never interpolated."""
        response = client.get(
            "/api/v1/employees",
            params={"sort_by": "password_hash; DROP TABLE employees"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 200
        assert response.json()["meta"]["total_items"] == 3


class TestDashboards:
    def test_hr_dashboard_reports_real_counts(
        self, client: TestClient, admin_user: User, org_fixtures: dict[str, int]
    ) -> None:
        headers = auth_header(client, "hradmin")
        make_employee(client, headers, code="ITC0001", **org_fixtures)
        make_employee(client, headers, code="ITC0002", **org_fixtures)

        response = client.get("/api/v1/dashboard/hr", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["headcount"]["active_total"] == 2
        assert body["headcount"]["by_department"][0]["count"] == 2
        assert body["configuration"]["unconfirmed"] > 0

    def test_dashboard_is_honest_about_unbuilt_metrics(
        self, client: TestClient, admin_user: User
    ) -> None:
        """A zero would read as a fact. Name the milestone instead.

        Attendance figures became real in Milestone 3 and so are no longer
        listed here - which is exactly the transition this assertion is for.
        """
        response = client.get("/api/v1/dashboard/hr", headers=auth_header(client, "hradmin"))
        body = response.json()
        pending = body["not_yet_available"]

        assert pending["pending_leave_approvals"] == "Milestone 5"
        assert pending["payroll_status"] == "Milestone 8"
        assert "present_today" not in pending, "attendance is built; it reports real counts"
        assert body["attendance_today"]["active_employees"] >= 0
        assert "not_yet_computed" in body["attendance_today"]

    def test_employee_cannot_see_the_hr_dashboard(
        self, client: TestClient, employee_user: User
    ) -> None:
        response = client.get("/api/v1/dashboard/hr", headers=auth_header(client, "e.employee"))
        assert response.status_code == 403

    def test_employee_dashboard_without_a_linked_record(
        self, client: TestClient, employee_user: User, seeded: None
    ) -> None:
        response = client.get("/api/v1/dashboard/me", headers=auth_header(client, "e.employee"))
        assert response.status_code == 200
        assert response.json()["employee"] is None
        assert "contact hr" in response.json()["message"].lower()

    def test_employee_dashboard_reports_tenure_and_team(
        self, client: TestClient, admin_user: User, employee_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        joining = (date.today() - timedelta(days=100)).isoformat()
        manager = make_employee(client, headers, code="ITC0001", joining=joining)
        make_employee(client, headers, code="ITC0002", manager_id=manager["id"])
        client.post(
            f"/api/v1/employees/{manager['id']}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )

        body = client.get("/api/v1/dashboard/me", headers=auth_header(client, "e.employee")).json()
        assert body["employee"]["tenure_days"] == 100
        assert body["team"]["direct_reports"] == 1
        assert body["team"]["is_manager"] is True

    def test_probation_ending_soon_is_surfaced(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0001",
                "first_name": "Probie",
                "employment_type": "full_time",
                "employment_status": "probation",
                "date_of_joining": (date.today() - timedelta(days=150)).isoformat(),
                "probation_end_date": (date.today() + timedelta(days=10)).isoformat(),
            },
            headers=headers,
        )
        body = client.get("/api/v1/dashboard/hr", headers=headers).json()
        codes = [row["employee_code"] for row in body["attention"]["probation_ending_soon"]]
        assert codes == ["ITC0001"]
