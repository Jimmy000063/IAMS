"""Leave endpoint tests against a real database and the real HTTP stack.

These cover what the pure rules cannot: the two-stage approval chain and who may
act at each stage, the append-only ledger and its trigger, idempotent accrual,
balance consumption by pending requests, cancellation reversing a deduction, and
the fact that approving leave actually changes attendance.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.attendance.models import AttendanceDay
from app.modules.auth.models import User
from app.modules.leave.models import LeaveLedgerEntry
from tests.conftest import TEST_PASSWORD, auth_header

pytestmark = pytest.mark.integration

D = Decimal

# A Monday-to-Friday week in a month comfortably inside the backdating and
# forward windows, so window rules never interfere with the case under test.
MON = date(2026, 11, 16)
TUE = date(2026, 11, 17)
WED = date(2026, 11, 18)
FRI = date(2026, 11, 20)
SAT = date(2026, 11, 21)
SUN = date(2026, 11, 22)
NEXT_MON = date(2026, 11, 23)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hr(client: TestClient, admin_user: User) -> dict:
    return auth_header(client, "hradmin")


@pytest.fixture
def casual_type(client: TestClient, hr: dict) -> dict:
    response = client.post(
        "/api/v1/leave/types",
        json={
            "code": "CL",
            "name": "Casual leave",
            "is_paid": True,
            "allows_half_day": True,
            "requires_reason": True,
        },
        headers=hr,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def unpaid_type(client: TestClient, hr: dict) -> dict:
    response = client.post(
        "/api/v1/leave/types",
        json={
            "code": "LWP",
            "name": "Leave without pay",
            "is_paid": False,
            "deducts_balance": False,
            "requires_reason": True,
        },
        headers=hr,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def casual_policy(client: TestClient, hr: dict, casual_type: dict) -> dict:
    response = client.post(
        "/api/v1/leave/policies",
        json={
            "leave_type_id": casual_type["id"],
            "name": "Casual leave 2026-27",
            "effective_from": "2026-04-01",
            "annual_quota_days": "12.00",
            "accrual_frequency": "monthly",
            "accrual_rounding": "nearest_half",
        },
        headers=hr,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def employee(
    client: TestClient, hr: dict, employee_user: User, org_fixtures: dict[str, int]
) -> dict:
    """An employee linked to a sign-in account, with no reporting manager."""
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
def manager_account(client: TestClient, session: Session, seeded: None, cli_context) -> User:
    from app.core.permissions import ROLE_EMPLOYEE
    from app.modules.auth import service as auth_service

    result = auth_service.create_user(
        session,
        username="m.manager",
        email="manager@example.com",
        role_codes=[ROLE_EMPLOYEE],
        password=TEST_PASSWORD,
        must_change_password=False,
        context=cli_context,
    )
    session.commit()
    return result.user


@pytest.fixture
def managed_employee(
    client: TestClient,
    hr: dict,
    employee: dict,
    employee_user: User,
    manager_account: User,
    org_fixtures: dict[str, int],
) -> dict:
    """``employee`` reporting to a manager who has their own sign-in account."""
    manager = client.post(
        "/api/v1/employees",
        json={
            "employee_code": "ITC0100",
            "first_name": "Priya",
            "last_name": "Nair",
            "employment_type": "full_time",
            "date_of_joining": "2025-01-05",
            **org_fixtures,
        },
        headers=hr,
    )
    assert manager.status_code == 201, manager.text
    manager_record = manager.json()

    link = client.post(
        f"/api/v1/employees/{manager_record['id']}/link-account",
        json={"user_id": manager_account.id},
        headers=hr,
    )
    assert link.status_code == 200, link.text

    updated = client.patch(
        f"/api/v1/employees/{employee['id']}",
        json={"reporting_manager_id": manager_record["id"]},
        headers=hr,
    )
    assert updated.status_code == 200, updated.text
    return manager_record


def grant(client: TestClient, hr: dict, *, employee_id: int, leave_type_id: int, days: str) -> None:
    """Give an opening balance so a request has something to draw on."""
    response = client.post(
        "/api/v1/leave/opening-balances",
        json={
            "employee_id": employee_id,
            "leave_type_id": leave_type_id,
            "days": days,
            "reason": "Test opening balance",
        },
        headers=hr,
    )
    assert response.status_code == 201, response.text


def apply_leave(
    client: TestClient,
    headers: dict,
    *,
    leave_type_id: int,
    start: date,
    end: date,
    reason: str = "Personal.",
    **extra,
) -> object:
    return client.post(
        "/api/v1/leave/me/applications",
        json={
            "leave_type_id": leave_type_id,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "reason": reason,
            **extra,
        },
        headers=headers,
    )


def balance_of(client: TestClient, headers: dict, code: str) -> dict:
    rows = client.get("/api/v1/leave/me/balances", headers=headers).json()
    return next(row for row in rows if row["leave_type_code"] == code)


# ---------------------------------------------------------------------------
# Types and policies
# ---------------------------------------------------------------------------


class TestTypesAndPolicies:
    def test_no_leave_types_are_seeded(self, client: TestClient, hr: dict, seeded: None) -> None:
        """Statutory entitlements have not been verified, so none are invented."""
        response = client.get("/api/v1/leave/types?include_inactive=true", headers=hr)
        assert response.status_code == 200
        assert response.json() == []

    def test_a_type_can_be_flagged_as_needing_legal_review(
        self, client: TestClient, hr: dict
    ) -> None:
        response = client.post(
            "/api/v1/leave/types",
            json={
                "code": "ML",
                "name": "Maternity leave",
                "gender_restriction": "female",
                "counts_non_working_days": True,
                "requires_legal_review": True,
            },
            headers=hr,
        )
        assert response.status_code == 201, response.text
        assert response.json()["requires_legal_review"] is True

    def test_a_duplicate_type_code_is_refused(
        self, client: TestClient, hr: dict, casual_type: dict
    ) -> None:
        response = client.post(
            "/api/v1/leave/types", json={"code": "cl", "name": "Clash"}, headers=hr
        )
        assert response.status_code == 409, response.text

    def test_two_policies_cannot_start_on_the_same_date(
        self, client: TestClient, hr: dict, casual_type: dict, casual_policy: dict
    ) -> None:
        """Which rules applied on a date must have exactly one answer."""
        response = client.post(
            "/api/v1/leave/policies",
            json={
                "leave_type_id": casual_type["id"],
                "name": "Clashing policy",
                "effective_from": "2026-04-01",
                "annual_quota_days": "20.00",
                "accrual_frequency": "monthly",
            },
            headers=hr,
        )
        assert response.status_code == 409, response.text

    def test_a_quota_with_no_accrual_frequency_is_refused(
        self, client: TestClient, hr: dict, casual_type: dict
    ) -> None:
        """Nothing would ever be credited, so it is a configuration mistake."""
        response = client.post(
            "/api/v1/leave/policies",
            json={
                "leave_type_id": casual_type["id"],
                "name": "Never credits",
                "effective_from": "2026-04-01",
                "annual_quota_days": "12.00",
                "accrual_frequency": "none",
            },
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "ever be credited" in response.text

    def test_a_second_open_assignment_is_refused(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_policy: dict,
    ) -> None:
        body = {
            "employee_id": employee["id"],
            "leave_policy_id": casual_policy["id"],
            "effective_from": "2026-04-01",
        }
        first = client.post("/api/v1/leave/policy-assignments", json=body, headers=hr)
        assert first.status_code == 201, first.text

        second = client.post(
            "/api/v1/leave/policy-assignments",
            json={**body, "effective_from": "2026-10-01"},
            headers=hr,
        )
        assert second.status_code == 409, second.text
        assert "Close it with an end date first" in second.text


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


class TestApplying:
    def test_a_weekend_inside_the_range_is_not_deducted(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        """Friday to Monday costs two days, not four."""
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        response = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=FRI, end=NEXT_MON
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["total_days"] == "2.00"
        assert len(body["days"]) == 4
        free = [day for day in body["days"] if not day["counts_against_balance"]]
        assert [day["leave_date"] for day in free] == [SAT.isoformat(), SUN.isoformat()]

    def test_a_half_day_consumes_half(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        response = apply_leave(
            client,
            staff,
            leave_type_id=casual_type["id"],
            start=MON,
            end=MON,
            start_portion="second_half",
        )
        assert response.status_code == 201, response.text
        assert response.json()["total_days"] == "0.50"

    def test_a_request_with_no_working_days_is_refused(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        response = apply_leave(client, staff, leave_type_id=casual_type["id"], start=SAT, end=SUN)
        assert response.status_code == 422, response.text
        assert "working days" in response.text

    def test_every_problem_is_reported_at_once(
        self, client: TestClient, hr: dict, employee: dict, casual_type: dict
    ) -> None:
        """So an applicant fixes one form instead of being refused repeatedly."""
        client.patch(
            f"/api/v1/leave/types/{casual_type['id']}",
            json={"max_consecutive_days": 1, "min_notice_days": 30},
            headers=hr,
        )
        staff = auth_header(client, "e.employee")

        response = apply_leave(
            client,
            staff,
            leave_type_id=casual_type["id"],
            start=MON,
            end=WED,
            reason="",
        )
        assert response.status_code == 422, response.text
        details = response.json()["error"]["details"]
        assert len(details) >= 2
        fields = {detail["field"] for detail in details}
        assert "reason" in fields

    def test_an_overlapping_request_is_refused(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        first = apply_leave(client, staff, leave_type_id=casual_type["id"], start=MON, end=WED)
        assert first.status_code == 201, first.text

        clash = apply_leave(client, staff, leave_type_id=casual_type["id"], start=TUE, end=TUE)
        assert clash.status_code == 422, clash.text
        assert "already covered by application" in clash.text

    def test_two_different_halves_of_a_day_are_allowed(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_policy: dict,
        casual_type: dict,
        unpaid_type: dict,
    ) -> None:
        """An employee may legitimately split a day between two leave types."""
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        first = apply_leave(
            client,
            staff,
            leave_type_id=casual_type["id"],
            start=MON,
            end=MON,
            start_portion="first_half",
        )
        assert first.status_code == 201, first.text

        second = apply_leave(
            client,
            staff,
            leave_type_id=unpaid_type["id"],
            start=MON,
            end=MON,
            start_portion="second_half",
        )
        assert second.status_code == 201, second.text

    def test_a_request_without_a_policy_is_refused_for_a_balance_type(
        self, client: TestClient, hr: dict, employee: dict, casual_type: dict
    ) -> None:
        """No entitlement configured means there is nothing to draw on."""
        staff = auth_header(client, "e.employee")
        response = apply_leave(client, staff, leave_type_id=casual_type["id"], start=MON, end=MON)
        assert response.status_code == 422, response.text
        assert "No leave policy is in force" in response.text

    def test_an_unpaid_type_needs_no_policy_or_balance(
        self, client: TestClient, hr: dict, employee: dict, unpaid_type: dict
    ) -> None:
        staff = auth_header(client, "e.employee")
        response = apply_leave(client, staff, leave_type_id=unpaid_type["id"], start=MON, end=MON)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["paid_days"] == "0.00"
        assert body["unpaid_days"] == "1.00"

    def test_a_request_beyond_the_balance_is_split_not_refused(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        """Two days in hand and three needed: one unpaid, not a rejection."""
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="2")
        staff = auth_header(client, "e.employee")

        response = apply_leave(client, staff, leave_type_id=casual_type["id"], start=MON, end=WED)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["total_days"] == "3.00"
        assert body["paid_days"] == "2.00"
        assert body["unpaid_days"] == "1.00"

    def test_the_policy_snapshot_records_what_governed_the_request(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        """So the decision stays explicable after everything is reconfigured."""
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        body = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()
        snapshot = body["policy_snapshot"]
        assert snapshot["leave_type_code"] == "CL"
        assert snapshot["annual_quota_days"] == "12.00"
        assert snapshot["approval_chain"] == ["reporting_manager", "hr_admin"]
        assert snapshot["available_at_submission"] == "10.00"


# ---------------------------------------------------------------------------
# Balance consumption
# ---------------------------------------------------------------------------


class TestBalances:
    def test_a_pending_request_reduces_available_but_not_the_balance(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="5")
        staff = auth_header(client, "e.employee")

        apply_leave(client, staff, leave_type_id=casual_type["id"], start=MON, end=WED)

        balance = balance_of(client, staff, "CL")
        assert balance["balance_days"] == "5.00", "nothing is deducted until approval"
        assert balance["pending_days"] == "3.00"
        assert balance["available_days"] == "2.00"

    def test_pending_requests_cannot_exceed_the_balance_twice_over(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        """Without the pending subtraction, both would pass the balance check."""
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="3")
        staff = auth_header(client, "e.employee")

        first = apply_leave(client, staff, leave_type_id=casual_type["id"], start=MON, end=WED)
        assert first.status_code == 201
        assert first.json()["unpaid_days"] == "0.00"

        second = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=NEXT_MON, end=NEXT_MON
        )
        assert second.status_code == 201
        # The three days are already committed, so this one is unpaid.
        assert second.json()["unpaid_days"] == "1.00"

    def test_an_employee_can_read_their_own_ledger(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        """The arithmetic behind the balance, without having to ask HR."""
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="5")
        staff = auth_header(client, "e.employee")

        entries = client.get("/api/v1/leave/me/ledger", headers=staff).json()
        assert len(entries) == 1
        assert entries[0]["txn_type"] == "opening"
        assert entries[0]["days"] == "5.00"

    def test_an_opening_balance_cannot_be_granted_twice(
        self, client: TestClient, hr: dict, employee: dict, casual_type: dict
    ) -> None:
        """Importing the same file twice must credit once."""
        body = {
            "employee_id": employee["id"],
            "leave_type_id": casual_type["id"],
            "days": "5",
        }
        first = client.post("/api/v1/leave/opening-balances", json=body, headers=hr)
        second = client.post("/api/v1/leave/opening-balances", json=body, headers=hr)

        assert first.status_code == 201 and first.json() is not None
        assert second.json() is None, "the repeat is recognised and credits nothing"

    def test_an_adjustment_requires_a_reason(
        self, client: TestClient, hr: dict, employee: dict, casual_type: dict
    ) -> None:
        response = client.post(
            "/api/v1/leave/adjustments",
            json={
                "employee_id": employee["id"],
                "leave_type_id": casual_type["id"],
                "days": "1",
                "effective_date": "2026-11-01",
                "reason": "",
                "token": "t1",
            },
            headers=hr,
        )
        assert response.status_code == 422, response.text

    def test_a_double_submitted_adjustment_posts_once(
        self, client: TestClient, hr: dict, employee: dict, casual_type: dict
    ) -> None:
        body = {
            "employee_id": employee["id"],
            "leave_type_id": casual_type["id"],
            "days": "2.5",
            "effective_date": "2026-11-01",
            "reason": "Agreed correction.",
            "token": "form-submit-1",
        }
        first = client.post("/api/v1/leave/adjustments", json=body, headers=hr)
        second = client.post("/api/v1/leave/adjustments", json=body, headers=hr)

        assert first.status_code == 201, first.text
        assert second.status_code == 409, second.text
        assert "already been posted" in second.text


# ---------------------------------------------------------------------------
# Approval chain
# ---------------------------------------------------------------------------


class TestApprovalChain:
    def test_a_missing_manager_routes_straight_to_hr(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        """Otherwise the request waits in a queue nobody can act on."""
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        body = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()

        assert body["status"] == "pending_hr"
        assert body["current_stage"] == "hr_admin"
        assert body["escalated_from_missing_manager"] is True
        # The skip is recorded as a decision, so the history explains itself.
        skipped = [row for row in body["approvals"] if row["decision"] == "skipped"]
        assert len(skipped) == 1
        assert skipped[0]["stage"] == "reporting_manager"
        assert skipped[0]["was_auto_skipped"] is True

    def test_with_a_manager_the_request_waits_for_them_first(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        managed_employee: dict,
        casual_policy: dict,
        casual_type: dict,
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        body = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()
        assert body["status"] == "pending_manager"
        assert body["current_stage"] == "reporting_manager"

    def test_nobody_can_approve_their_own_application(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        admin_user: User,
        org_fixtures: dict[str, int],
        casual_policy: dict,
        casual_type: dict,
    ) -> None:
        """Even holding every approval permission. This is the rule most worth
        enforcing in the backend, since the UI would simply hide the button."""
        created = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0900",
                "first_name": "Admin",
                "last_name": "Person",
                "employment_type": "full_time",
                "date_of_joining": "2025-01-05",
                **org_fixtures,
            },
            headers=hr,
        )
        assert created.status_code == 201, created.text
        hr_employee = created.json()
        client.post(
            f"/api/v1/employees/{hr_employee['id']}/link-account",
            json={"user_id": admin_user.id},
            headers=hr,
        )
        grant(
            client,
            hr,
            employee_id=hr_employee["id"],
            leave_type_id=casual_type["id"],
            days="10",
        )

        application = apply_leave(client, hr, leave_type_id=casual_type["id"], start=MON, end=MON)
        assert application.status_code == 201, application.text

        decision = client.post(
            f"/api/v1/leave/applications/{application.json()['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        assert decision.status_code == 403, decision.text
        assert "your own leave" in decision.text

    def test_a_manager_cannot_decide_someone_elses_report(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        managed_employee: dict,
        manager_account: User,
        org_fixtures: dict[str, int],
        casual_policy: dict,
        casual_type: dict,
    ) -> None:
        """Holding the permission is a precondition; the relationship decides."""
        # The stranger reports to someone else, so their application sits at
        # the manager stage. Without a manager it would route straight to HR and
        # the relationship rule would never be reached.
        stranger = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0777",
                "first_name": "Other",
                "last_name": "Person",
                "employment_type": "full_time",
                "date_of_joining": "2025-01-05",
                "reporting_manager_id": employee["id"],
                **org_fixtures,
            },
            headers=hr,
        ).json()
        grant(client, hr, employee_id=stranger["id"], leave_type_id=casual_type["id"], days="10")

        raised = client.post(
            "/api/v1/leave/applications/on-behalf",
            json={
                "employee_id": stranger["id"],
                "leave_type_id": casual_type["id"],
                "start_date": MON.isoformat(),
                "end_date": MON.isoformat(),
                "reason": "Recorded by HR.",
            },
            headers=hr,
        )
        assert raised.status_code == 201, raised.text

        manager = auth_header(client, "m.manager")
        decision = client.post(
            f"/api/v1/leave/applications/{raised.json()['id']}/decide",
            json={"approve": True},
            headers=manager,
        )
        # 404, not 403: confirming the application exists would leak who reports
        # to whom.
        assert decision.status_code == 404, decision.text

    def test_the_full_chain_runs_manager_then_hr(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        managed_employee: dict,
        casual_policy: dict,
        casual_type: dict,
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        manager = auth_header(client, "m.manager")

        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()

        # Manager approval advances the stage but deducts nothing.
        first = client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True, "comment": "Fine by me."},
            headers=manager,
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "pending_hr"
        assert (
            session.scalar(
                select(func.count())
                .select_from(LeaveLedgerEntry)
                .where(LeaveLedgerEntry.txn_type == "deduction")
            )
            == 0
        ), "an intermediate stage must not touch the balance"

        # HR approval is what finalises and deducts.
        final = client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        assert final.status_code == 200, final.text
        body = final.json()
        assert body["status"] == "approved"
        assert body["current_stage"] is None
        assert [row["stage"] for row in body["approvals"]] == [
            "reporting_manager",
            "hr_admin",
        ]

    def test_a_manager_approval_then_hr_rejection_never_deducts(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        managed_employee: dict,
        casual_policy: dict,
        casual_type: dict,
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        manager = auth_header(client, "m.manager")

        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()
        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True},
            headers=manager,
        )
        rejected = client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": False, "comment": "Cover not arranged."},
            headers=hr,
        )

        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"
        assert (
            session.scalar(
                select(func.count())
                .select_from(LeaveLedgerEntry)
                .where(LeaveLedgerEntry.txn_type == "deduction")
            )
            == 0
        )
        # Both decisions remain on the record.
        assert [row["decision"] for row in rejected.json()["approvals"]] == [
            "approved",
            "rejected",
        ]

    def test_a_decided_application_cannot_be_decided_again(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()
        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        again = client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        assert again.status_code == 409, again.text

    def test_an_approver_queue_holds_only_their_own_reports(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        managed_employee: dict,
        casual_policy: dict,
        casual_type: dict,
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        apply_leave(client, staff, leave_type_id=casual_type["id"], start=MON, end=MON)

        manager_queue = client.get(
            "/api/v1/leave/approvals/pending", headers=auth_header(client, "m.manager")
        )
        assert manager_queue.status_code == 200, manager_queue.text
        assert [row["employee_code"] for row in manager_queue.json()] == ["ITC0001"]

        # HR's queue is the HR stage, which this request has not reached.
        hr_queue = client.get("/api/v1/leave/approvals/pending", headers=hr)
        assert hr_queue.json() == []


# ---------------------------------------------------------------------------
# Ledger and cancellation
# ---------------------------------------------------------------------------


class TestNotifications:
    """Milestone 7 wiring: a decision has to reach the person it is about.

    These are here rather than with the notification tests because what is being
    checked is the *integration* - that the leave service calls the hook on each
    of its decision paths - not the notification machinery itself.
    """

    def test_an_approval_notifies_the_applicant_once(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_policy: dict,
        casual_type: dict,
        session: Session,
    ) -> None:
        from app.modules.notifications.models import Notification

        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()

        decided = client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True, "comment": "Fine."},
            headers=hr,
        )
        assert decided.status_code == 200, decided.text

        rows = session.scalars(select(Notification).where(Notification.category == "leave")).all()
        mine = [row for row in rows if row.employee_id == employee["id"]]
        assert len(mine) == 1, [row.title for row in rows]
        assert "approved" in mine[0].title
        assert mine[0].link_path == f"/leave/applications/{application['id']}"
        assert mine[0].read_at is None

    def test_a_rejection_carries_the_reason(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_policy: dict,
        casual_type: dict,
        session: Session,
    ) -> None:
        from app.modules.notifications.models import Notification

        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()

        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": False, "comment": "Release is that week."},
            headers=hr,
        )

        row = session.scalars(
            select(Notification).where(
                Notification.category == "leave",
                Notification.employee_id == employee["id"],
            )
        ).one()
        assert "declined" in row.title
        assert "Release is that week." in (row.body or "")

    def test_a_manager_is_told_a_request_is_waiting(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        managed_employee: dict,
        manager_account: User,
        casual_policy: dict,
        casual_type: dict,
        session: Session,
    ) -> None:
        """The person who can act is the person who is told."""
        from app.modules.notifications.models import Notification

        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        apply_leave(client, staff, leave_type_id=casual_type["id"], start=MON, end=MON)

        rows = session.scalars(
            select(Notification).where(Notification.user_id == manager_account.id)
        ).all()
        assert len(rows) == 1
        assert "requested" in rows[0].title
        assert "awaiting your" in (rows[0].body or "")

    def test_cancelling_tells_the_employee_the_days_came_back(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_policy: dict,
        casual_type: dict,
        session: Session,
    ) -> None:
        from app.modules.notifications.models import Notification

        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=NEXT_MON, end=NEXT_MON
        ).json()
        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True, "comment": None},
            headers=hr,
        )
        cancelled = client.post(
            f"/api/v1/leave/applications/{application['id']}/cancel",
            json={"reason": "Plans changed."},
            headers=staff,
        )
        assert cancelled.status_code == 200, cancelled.text

        titles = [
            row.title
            for row in session.scalars(
                select(Notification).where(Notification.employee_id == employee["id"])
            ).all()
        ]
        assert any("cancelled" in title for title in titles), titles

    def test_the_applicant_sees_it_in_their_own_centre(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_policy: dict,
        casual_type: dict,
    ) -> None:
        """End to end, through the HTTP stack the browser actually uses."""
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()
        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True, "comment": None},
            headers=hr,
        )

        summary = client.get("/api/v1/notifications/unread-count", headers=staff).json()
        assert summary["unread_total"] == 1
        assert summary["unread_by_category"] == {"leave": 1}

        listed = client.get("/api/v1/notifications", headers=staff).json()
        assert listed[0]["category"] == "leave"
        assert listed[0]["entity_type"] == "leave_application"


class TestLedger:
    def test_approval_deducts_exactly_once(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=WED
        ).json()
        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )

        balance = balance_of(client, staff, "CL")
        # The deduction is dated on the leave's own start date, which is next
        # month, so today's ledger total is untouched - but the days are
        # committed and must not be available to book again.
        assert balance["balance_days"] == "10.00"
        assert balance["pending_days"] == "0.00", "no longer awaiting a decision"
        assert balance["committed_days"] == "3.00"
        assert balance["available_days"] == "7.00"
        assert balance["used_days"] == "3.00"

    def test_cancelling_approved_leave_returns_the_days_by_reversal(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_policy: dict,
        casual_type: dict,
    ) -> None:
        """The deduction is reversed, not deleted - both rows stay on the record."""
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=WED
        ).json()
        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        cancelled = client.post(
            f"/api/v1/leave/applications/{application['id']}/cancel",
            json={"reason": "Trip called off."},
            headers=staff,
        )
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

        assert balance_of(client, staff, "CL")["balance_days"] == "10.00"

        entries = client.get("/api/v1/leave/me/ledger", headers=staff).json()
        kinds = [entry["txn_type"] for entry in entries]
        assert "deduction" in kinds and "reversal" in kinds
        reversal = next(entry for entry in entries if entry["txn_type"] == "reversal")
        assert reversal["reverses_entry_id"] is not None
        assert "Trip called off." in reversal["reason"]

    def test_cancelling_requires_a_reason(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()
        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        response = client.post(
            f"/api/v1/leave/applications/{application['id']}/cancel",
            json={"reason": ""},
            headers=staff,
        )
        assert response.status_code == 422, response.text

    def test_a_pending_request_is_withdrawn_not_cancelled(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()

        cancel_attempt = client.post(
            f"/api/v1/leave/applications/{application['id']}/cancel",
            json={"reason": "Changed my mind."},
            headers=staff,
        )
        assert cancel_attempt.status_code == 409, cancel_attempt.text

        withdrawn = client.post(
            f"/api/v1/leave/applications/{application['id']}/withdraw",
            json={"reason": "Changed my mind."},
            headers=staff,
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["status"] == "withdrawn"
        # Nothing was deducted, so the dates are free again.
        assert balance_of(client, staff, "CL")["available_days"] == "10.00"

        reapplied = apply_leave(client, staff, leave_type_id=casual_type["id"], start=MON, end=MON)
        assert reapplied.status_code == 201, reapplied.text

    def test_the_ledger_cannot_be_rewritten(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_type: dict,
    ) -> None:
        """A balance is the sum of this table, so the database refuses edits."""
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="5")
        entry = session.scalars(select(LeaveLedgerEntry)).one()

        with pytest.raises(DBAPIError) as error:
            session.execute(
                text("UPDATE leave_ledger_entries SET days = 500 WHERE id = :id"),
                {"id": entry.id},
            )
            session.flush()
        assert "append-only" in str(error.value)
        session.rollback()

    def test_the_ledger_cannot_be_deleted_from(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_type: dict,
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="5")
        entry = session.scalars(select(LeaveLedgerEntry)).one()

        with pytest.raises(DBAPIError):
            session.execute(
                text("DELETE FROM leave_ledger_entries WHERE id = :id"), {"id": entry.id}
            )
            session.flush()
        session.rollback()

    def test_an_approval_decision_cannot_be_rewritten(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_policy: dict,
        casual_type: dict,
    ) -> None:
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()
        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": False, "comment": "No."},
            headers=hr,
        )

        with pytest.raises(DBAPIError) as error:
            session.execute(
                text("UPDATE leave_approvals SET decision = 'approved' WHERE application_id = :id"),
                {"id": application["id"]},
            )
            session.flush()
        assert "append-only" in str(error.value)
        session.rollback()


# ---------------------------------------------------------------------------
# Attendance integration
# ---------------------------------------------------------------------------


class TestAttendanceIntegration:
    @pytest.fixture
    def day_shift(self, client: TestClient, hr: dict) -> dict:
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

    def test_approving_paid_leave_makes_the_day_payable_leave(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_policy: dict,
        casual_type: dict,
        day_shift: dict,
    ) -> None:
        """Approval must change attendance, or payroll would read an absence."""
        client.post(
            "/api/v1/shifts/assignments",
            json={
                "employee_id": employee["id"],
                "shift_id": day_shift["id"],
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=TUE
        ).json()
        decided = client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        assert decided.status_code == 200, decided.text

        days = session.scalars(
            select(AttendanceDay)
            .where(
                AttendanceDay.employee_id == employee["id"],
                AttendanceDay.work_date.in_([MON, TUE]),
            )
            .order_by(AttendanceDay.work_date)
        ).all()
        assert len(days) == 2
        for day in days:
            assert day.status == "on_leave"
            assert day.payable_day_fraction == D("1.00"), "paid leave is a payable day"
            assert day.leave_application_id == application["id"]

    def test_unpaid_leave_is_recorded_but_pays_nothing(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        unpaid_type: dict,
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
        staff = auth_header(client, "e.employee")
        application = apply_leave(
            client, staff, leave_type_id=unpaid_type["id"], start=MON, end=MON
        ).json()
        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )

        day = session.scalars(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == employee["id"],
                AttendanceDay.work_date == MON,
            )
        ).one()
        assert day.status == "on_leave"
        assert day.payable_day_fraction == D("0.00")

    def test_cancelling_leave_returns_the_day_to_absent(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_policy: dict,
        casual_type: dict,
        day_shift: dict,
    ) -> None:
        """Attendance must follow the cancellation, not keep the stale leave."""
        client.post(
            "/api/v1/shifts/assignments",
            json={
                "employee_id": employee["id"],
                "shift_id": day_shift["id"],
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")

        application = apply_leave(
            client, staff, leave_type_id=casual_type["id"], start=MON, end=MON
        ).json()
        client.post(
            f"/api/v1/leave/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        client.post(
            f"/api/v1/leave/applications/{application['id']}/cancel",
            json={"reason": "Not needed."},
            headers=staff,
        )

        day = session.scalars(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == employee["id"],
                AttendanceDay.work_date == MON,
            )
        ).one_or_none()
        assert day is not None
        assert day.status == "absent", "no longer on leave, and no punches exist"
        assert day.payable_day_fraction == D("0.00")

    def test_a_pending_request_does_not_mark_the_day_as_leave(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        managed_employee: dict,
        casual_policy: dict,
        casual_type: dict,
        day_shift: dict,
    ) -> None:
        """Otherwise an employee could avoid being marked absent by applying."""
        client.post(
            "/api/v1/shifts/assignments",
            json={
                "employee_id": employee["id"],
                "shift_id": day_shift["id"],
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="10")
        staff = auth_header(client, "e.employee")
        apply_leave(client, staff, leave_type_id=casual_type["id"], start=MON, end=MON)

        recomputed = client.post(
            "/api/v1/attendance/recompute",
            json={
                "employee_ids": [employee["id"]],
                "start": MON.isoformat(),
                "end": MON.isoformat(),
            },
            headers=hr,
        )
        assert recomputed.status_code == 200, recomputed.text

        day = session.scalars(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == employee["id"],
                AttendanceDay.work_date == MON,
            )
        ).one_or_none()
        assert day is None or day.status != "on_leave"


# ---------------------------------------------------------------------------
# Accrual
# ---------------------------------------------------------------------------


class TestAccrual:
    def test_a_monthly_accrual_credits_one_twelfth(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        response = client.post(
            "/api/v1/leave/accrual/run",
            json={"as_of": "2026-11-30", "leave_type_id": casual_type["id"]},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["period_key"] == "2026-11"
        assert body["entries_posted"] == 1
        assert body["days_credited"] == "1.00"

    def test_re_running_the_same_period_credits_nothing(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        """The brief's requirement: a re-run must not duplicate leave credits."""
        body = {"as_of": "2026-11-30", "leave_type_id": casual_type["id"]}
        first = client.post("/api/v1/leave/accrual/run", json=body, headers=hr).json()
        second = client.post("/api/v1/leave/accrual/run", json=body, headers=hr).json()

        assert first["entries_posted"] == 1
        assert second["entries_posted"] == 0
        assert second["already_posted"] == 1
        assert second["days_credited"] == "0.00"

        # One credit in the ledger, not two. Asked as of the period end, since
        # the accrual is dated there.
        rows = client.get(
            "/api/v1/leave/employees/{}/balances?as_of=2026-11-30".format(employee["id"]),
            headers=hr,
        ).json()
        casual = next(row for row in rows if row["leave_type_code"] == "CL")
        assert casual["balance_days"] == "1.00"

    def test_different_periods_each_credit_once(
        self, client: TestClient, hr: dict, employee: dict, casual_policy: dict, casual_type: dict
    ) -> None:
        for as_of in ("2026-10-31", "2026-11-30", "2026-12-31"):
            run = client.post(
                "/api/v1/leave/accrual/run",
                json={"as_of": as_of, "leave_type_id": casual_type["id"]},
                headers=hr,
            )
            assert run.status_code == 200, run.text
            assert run.json()["entries_posted"] == 1, as_of

        # Each accrual is dated at its own period end, so the balance depends on
        # when you ask. Asking at the end of December sees all three.
        rows = client.get(
            "/api/v1/leave/employees/{}/balances?as_of=2026-12-31".format(employee["id"]),
            headers=hr,
        ).json()
        december = next(row for row in rows if row["leave_type_code"] == "CL")
        assert december["balance_days"] == "3.00"

        # Asking in the middle of October sees none of them yet, which is the
        # point of dating a movement rather than storing a running total.
        rows = client.get(
            "/api/v1/leave/employees/{}/balances?as_of=2026-10-15".format(employee["id"]),
            headers=hr,
        ).json()
        october = next(row for row in rows if row["leave_type_code"] == "CL")
        assert october["balance_days"] == "0.00"

    def test_a_mid_month_joiner_is_prorated(
        self,
        client: TestClient,
        hr: dict,
        org_fixtures: dict[str, int],
        casual_policy: dict,
        casual_type: dict,
    ) -> None:
        """18-day quota, so a half month is 0.75 rounding to 1.0 - not 1.5."""
        client.post(
            f"/api/v1/leave/policies/{casual_policy['id']}/close",
            json={"effective_to": "2026-04-01"},
            headers=hr,
        )
        policy = client.post(
            "/api/v1/leave/policies",
            json={
                "leave_type_id": casual_type["id"],
                "name": "Eighteen days",
                "effective_from": "2026-04-02",
                "annual_quota_days": "18.00",
                "accrual_frequency": "monthly",
                "accrual_rounding": "nearest_half",
                "prorate_on_joining": True,
            },
            headers=hr,
        )
        assert policy.status_code == 201, policy.text

        joiner = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0555",
                "first_name": "New",
                "last_name": "Joiner",
                "employment_type": "full_time",
                "date_of_joining": "2026-11-16",
                **org_fixtures,
            },
            headers=hr,
        )
        assert joiner.status_code == 201, joiner.text

        run = client.post(
            "/api/v1/leave/accrual/run",
            json={
                "as_of": "2026-11-30",
                "leave_type_id": casual_type["id"],
                "employee_id": joiner.json()["id"],
            },
            headers=hr,
        )
        assert run.status_code == 200, run.text
        assert run.json()["days_credited"] == "1.00"

        ledger = client.get(
            f"/api/v1/leave/employees/{joiner.json()['id']}/ledger", headers=hr
        ).json()
        assert "prorated" in ledger[0]["reason"]

    def test_an_employee_with_no_policy_is_skipped_not_failed(
        self, client: TestClient, hr: dict, employee: dict, casual_type: dict
    ) -> None:
        response = client.post(
            "/api/v1/leave/accrual/run",
            json={"as_of": "2026-11-30", "leave_type_id": casual_type["id"]},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["entries_posted"] == 0
        assert body["skipped_no_policy"] >= 1

    def test_the_accumulation_ceiling_stops_accrual(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_type: dict,
    ) -> None:
        policy = client.post(
            "/api/v1/leave/policies",
            json={
                "leave_type_id": casual_type["id"],
                "name": "Capped",
                "effective_from": "2026-04-01",
                "annual_quota_days": "12.00",
                "accrual_frequency": "monthly",
                "max_accumulation_days": "2.00",
            },
            headers=hr,
        )
        assert policy.status_code == 201, policy.text
        grant(client, hr, employee_id=employee["id"], leave_type_id=casual_type["id"], days="2")

        run = client.post(
            "/api/v1/leave/accrual/run",
            json={"as_of": "2026-11-30", "leave_type_id": casual_type["id"]},
            headers=hr,
        ).json()
        assert run["entries_posted"] == 0
        assert run["skipped_at_ceiling"] >= 1


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/leave/types"),
            ("get", "/api/v1/leave/policies"),
            ("post", "/api/v1/leave/policies"),
            ("post", "/api/v1/leave/policy-assignments"),
            ("post", "/api/v1/leave/adjustments"),
            ("post", "/api/v1/leave/opening-balances"),
            ("post", "/api/v1/leave/accrual/run"),
            ("post", "/api/v1/leave/applications/on-behalf"),
        ],
    )
    def test_an_employee_cannot_administer_leave(
        self,
        client: TestClient,
        admin_user: User,
        employee_user: User,
        method: str,
        path: str,
    ) -> None:
        """Backend-enforced, not merely hidden from the navigation."""
        headers = auth_header(client, "e.employee")
        response = (
            client.get(path, headers=headers)
            if method == "get"
            else client.post(path, json={}, headers=headers)
        )
        assert response.status_code == 403, f"{method.upper()} {path} -> {response.status_code}"

    def test_an_employee_cannot_read_another_employees_balances(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        org_fixtures: dict[str, int],
        employee_user: User,
    ) -> None:
        stranger = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0888",
                "first_name": "Someone",
                "last_name": "Else",
                "employment_type": "full_time",
                "date_of_joining": "2025-01-05",
                **org_fixtures,
            },
            headers=hr,
        ).json()

        response = client.get(
            f"/api/v1/leave/employees/{stranger['id']}/balances",
            headers=auth_header(client, "e.employee"),
        )
        # 404, not 403. An ordinary employee does hold leave.read_team - it is a
        # precondition for being a manager - so the route is reachable and the
        # *scope* check refuses it. Answering 404 means an employee cannot
        # enumerate ids to discover who exists.
        assert response.status_code == 404, response.text

    def test_a_manager_sees_a_report_but_not_a_stranger(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        managed_employee: dict,
        org_fixtures: dict[str, int],
    ) -> None:
        stranger = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0999",
                "first_name": "Not",
                "last_name": "Mine",
                "employment_type": "full_time",
                "date_of_joining": "2025-01-05",
                **org_fixtures,
            },
            headers=hr,
        ).json()
        manager = auth_header(client, "m.manager")

        allowed = client.get(f"/api/v1/leave/employees/{employee['id']}/balances", headers=manager)
        assert allowed.status_code == 200, allowed.text

        refused = client.get(f"/api/v1/leave/employees/{stranger['id']}/balances", headers=manager)
        # 404 rather than 403, so ids cannot be enumerated.
        assert refused.status_code == 404, refused.text

    def test_an_anonymous_caller_is_refused(self, client: TestClient) -> None:
        assert client.get("/api/v1/leave/me/balances").status_code == 401
        assert client.post("/api/v1/leave/me/applications", json={}).status_code == 401


# ---------------------------------------------------------------------------
# Leave-year closing
# ---------------------------------------------------------------------------

#: A year that has already ended, so it can actually be closed. The leave year
#: runs April to March, so 2024 closed on 2025-03-31.
CLOSED_YEAR = 2024


class TestYearEnd:
    @pytest.fixture
    def capped_policy(self, client: TestClient, hr: dict, casual_type: dict) -> dict:
        """A policy in force for the closed year, with a carry-forward cap."""
        response = client.post(
            "/api/v1/leave/policies",
            json={
                "leave_type_id": casual_type["id"],
                "name": "Casual leave 2024-25",
                "effective_from": "2024-04-01",
                "effective_to": "2025-03-31",
                "annual_quota_days": "12.00",
                "accrual_frequency": "monthly",
                "carry_forward_cap_days": "5.00",
                "carry_forward_expires_after_months": 6,
            },
            headers=hr,
        )
        assert response.status_code == 201, response.text
        return response.json()

    def _opening(
        self, client: TestClient, hr: dict, employee: dict, casual_type: dict, days: str
    ) -> None:
        response = client.post(
            "/api/v1/leave/adjustments",
            json={
                "employee_id": employee["id"],
                "leave_type_id": casual_type["id"],
                "days": days,
                "effective_date": "2024-04-01",
                "reason": "Opening balance for the year under test.",
                "token": f"year-end-setup-{days}",
            },
            headers=hr,
        )
        assert response.status_code == 201, response.text

    def test_a_dry_run_writes_nothing(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_type: dict,
        capped_policy: dict,
    ) -> None:
        """Closing a year moves everybody's entitlement at once, so reading the
        outcome first is not a luxury."""
        self._opening(client, hr, employee, casual_type, "12")
        before = session.scalar(select(func.count()).select_from(LeaveLedgerEntry))

        response = client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": CLOSED_YEAR, "dry_run": True},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["dry_run"] is True
        assert body["carried_days"] == "5.00"
        assert body["lapsed_days"] == "7.00"

        after = session.scalar(select(func.count()).select_from(LeaveLedgerEntry))
        assert after == before, "a dry run must not post a single entry"

    def test_closing_carries_the_cap_and_lapses_the_rest(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_type: dict,
        capped_policy: dict,
    ) -> None:
        self._opening(client, hr, employee, casual_type, "12")

        response = client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": CLOSED_YEAR, "dry_run": False},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["employees_closed"] == 1
        assert body["carried_days"] == "5.00"
        assert body["lapsed_days"] == "7.00"

        kinds = {
            row[0]: row[1]
            for row in session.execute(
                select(LeaveLedgerEntry.txn_type, LeaveLedgerEntry.days).where(
                    LeaveLedgerEntry.employee_id == employee["id"]
                )
            ).all()
        }
        assert kinds["carry_forward_out"] == D("-5.00")
        assert kinds["carry_forward_in"] == D("5.00")
        assert kinds["expiry"] == D("-7.00")

    def test_the_closing_year_nets_to_zero_and_the_new_one_opens_with_the_carry(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_type: dict,
        capped_policy: dict,
    ) -> None:
        """Two entries rather than one is what keeps each year self-contained."""
        self._opening(client, hr, employee, casual_type, "12")
        client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": CLOSED_YEAR, "dry_run": False},
            headers=hr,
        )

        closing = client.get(
            f"/api/v1/leave/employees/{employee['id']}/balances?as_of=2025-03-31",
            headers=hr,
        ).json()
        opening = client.get(
            f"/api/v1/leave/employees/{employee['id']}/balances?as_of=2025-04-01",
            headers=hr,
        ).json()

        assert next(r for r in closing if r["leave_type_code"] == "CL")["balance_days"] == "0.00"
        assert next(r for r in opening if r["leave_type_code"] == "CL")["balance_days"] == "5.00"

    def test_re_running_the_year_end_posts_nothing_more(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_type: dict,
        capped_policy: dict,
    ) -> None:
        """The brief's explicit requirement: re-running must not duplicate leave
        credits, deductions or encashment entries."""
        self._opening(client, hr, employee, casual_type, "12")
        body = {"leave_year": CLOSED_YEAR, "dry_run": False}

        first = client.post("/api/v1/leave/year-end/run", json=body, headers=hr).json()
        count_after_first = session.scalar(select(func.count()).select_from(LeaveLedgerEntry))
        second = client.post("/api/v1/leave/year-end/run", json=body, headers=hr).json()
        count_after_second = session.scalar(select(func.count()).select_from(LeaveLedgerEntry))

        assert first["employees_closed"] == 1
        assert second["employees_closed"] == 0
        assert second["already_closed"] == 1
        assert count_after_second == count_after_first, "no entry was posted twice"

        balances = client.get(
            f"/api/v1/leave/employees/{employee['id']}/balances?as_of=2025-04-01",
            headers=hr,
        ).json()
        assert (
            next(r for r in balances if r["leave_type_code"] == "CL")["balance_days"] == "5.00"
        ), "the carry-forward was not doubled"

    def test_a_year_that_has_not_ended_cannot_be_closed(
        self, client: TestClient, hr: dict, employee: dict, casual_type: dict
    ) -> None:
        response = client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": 2099, "dry_run": False},
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "does not end until" in response.text

    def test_a_future_year_can_still_be_previewed(
        self, client: TestClient, hr: dict, employee: dict, casual_type: dict
    ) -> None:
        response = client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": 2099, "dry_run": True},
            headers=hr,
        )
        assert response.status_code == 200, response.text

    def test_carried_days_expire_when_their_date_passes(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_type: dict,
        capped_policy: dict,
    ) -> None:
        """A balance carried into April with a six-month life expires in
        September, not at the next year end."""
        self._opening(client, hr, employee, casual_type, "12")
        client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": CLOSED_YEAR, "dry_run": False},
            headers=hr,
        )

        carried = session.scalars(
            select(LeaveLedgerEntry).where(LeaveLedgerEntry.txn_type == "carry_forward_in")
        ).one()
        assert carried.expires_on == date(2025, 9, 30), "six months from 1 April"

        expired = client.post(
            "/api/v1/leave/year-end/expire-carried",
            json={"as_of": "2025-10-01"},
            headers=hr,
        )
        assert expired.status_code == 200, expired.text
        assert expired.json()["entries_posted"] == 1
        assert expired.json()["days_expired"] == "5.00"

        balances = client.get(
            f"/api/v1/leave/employees/{employee['id']}/balances?as_of=2025-10-01",
            headers=hr,
        ).json()
        assert next(r for r in balances if r["leave_type_code"] == "CL")["balance_days"] == "0.00"

    def test_re_running_the_expiry_job_posts_nothing_more(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_type: dict,
        capped_policy: dict,
    ) -> None:
        self._opening(client, hr, employee, casual_type, "12")
        client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": CLOSED_YEAR, "dry_run": False},
            headers=hr,
        )
        body = {"as_of": "2025-10-01"}
        first = client.post("/api/v1/leave/year-end/expire-carried", json=body, headers=hr).json()
        second = client.post("/api/v1/leave/year-end/expire-carried", json=body, headers=hr).json()
        assert first["entries_posted"] == 1
        assert second["entries_posted"] == 0
        assert second["already_expired"] == 1

    def test_carried_days_already_used_do_not_expire(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_type: dict,
        capped_policy: dict,
    ) -> None:
        """Only the unused portion lapses."""
        self._opening(client, hr, employee, casual_type, "12")
        client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": CLOSED_YEAR, "dry_run": False},
            headers=hr,
        )
        # Spend the carried balance before it expires.
        client.post(
            "/api/v1/leave/adjustments",
            json={
                "employee_id": employee["id"],
                "leave_type_id": casual_type["id"],
                "days": "-5",
                "effective_date": "2025-05-01",
                "reason": "Leave taken against the carried balance.",
                "token": "spend-carried",
            },
            headers=hr,
        )

        expired = client.post(
            "/api/v1/leave/year-end/expire-carried",
            json={"as_of": "2025-10-01"},
            headers=hr,
        ).json()
        assert expired["entries_posted"] == 0
        assert expired["nothing_left_to_expire"] == 1

    def test_a_negative_balance_is_carried_rather_than_written_off(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        employee: dict,
        casual_type: dict,
        capped_policy: dict,
    ) -> None:
        """Lapsing a debt would quietly hand back days already taken."""
        self._opening(client, hr, employee, casual_type, "-2")

        response = client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": CLOSED_YEAR, "dry_run": False},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        assert response.json()["carried_days"] == "-2.00"
        assert response.json()["lapsed_days"] == "0.00"

        balances = client.get(
            f"/api/v1/leave/employees/{employee['id']}/balances?as_of=2025-04-01",
            headers=hr,
        ).json()
        assert next(r for r in balances if r["leave_type_code"] == "CL")["balance_days"] == "-2.00"

    def test_an_employee_with_nothing_to_close_is_skipped(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_type: dict,
        capped_policy: dict,
    ) -> None:
        response = client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": CLOSED_YEAR, "dry_run": False},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        assert response.json()["skipped_nothing_to_close"] >= 1
        assert response.json()["employees_closed"] == 0


class TestReconciliation:
    def test_the_reconciliation_balances(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        casual_type: dict,
    ) -> None:
        """Every figure is a sum over the ledger by transaction type, so if this
        did not add up the ledger itself would be at fault."""
        policy = client.post(
            "/api/v1/leave/policies",
            json={
                "leave_type_id": casual_type["id"],
                "name": "Casual 2024-25",
                "effective_from": "2024-04-01",
                "effective_to": "2025-03-31",
                "annual_quota_days": "12.00",
                "accrual_frequency": "monthly",
                "carry_forward_cap_days": "5.00",
            },
            headers=hr,
        )
        assert policy.status_code == 201, policy.text

        client.post(
            "/api/v1/leave/adjustments",
            json={
                "employee_id": employee["id"],
                "leave_type_id": casual_type["id"],
                "days": "12",
                "effective_date": "2024-04-01",
                "reason": "Opening.",
                "token": "recon-opening",
            },
            headers=hr,
        )
        client.post(
            "/api/v1/leave/year-end/run",
            json={"leave_year": CLOSED_YEAR, "dry_run": False},
            headers=hr,
        )

        rows = client.get(
            f"/api/v1/leave/year-end/reconciliation?leave_year={CLOSED_YEAR}", headers=hr
        )
        assert rows.status_code == 200, rows.text
        row = next(item for item in rows.json() if item["employee_id"] == employee["id"])

        opening = D(row["opening_days"])
        accrued = D(row["accrued_days"])
        adjusted = D(row["adjusted_days"])
        used = D(row["used_days"])
        carried = D(row["carried_out_days"])
        lapsed = D(row["lapsed_days"])
        encashed = D(row["encashed_days"])
        closing = D(row["closing_days"])

        assert opening + accrued + adjusted - used - carried - lapsed - encashed == closing, (
            f"the reconciliation does not balance: {row}"
        )
        assert carried == D("5.00")
        assert lapsed == D("7.00")

    def test_an_empty_year_reconciles_to_nothing(
        self, client: TestClient, hr: dict, employee: dict, casual_type: dict
    ) -> None:
        rows = client.get("/api/v1/leave/year-end/reconciliation?leave_year=2019", headers=hr)
        assert rows.status_code == 200
        assert rows.json() == []


class TestYearEndAuthorization:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/leave/year-end/run",
            "/api/v1/leave/year-end/expire-carried",
        ],
    )
    def test_an_employee_cannot_close_a_leave_year(
        self, client: TestClient, admin_user: User, employee_user: User, path: str
    ) -> None:
        response = client.post(
            path, json={"leave_year": CLOSED_YEAR}, headers=auth_header(client, "e.employee")
        )
        assert response.status_code == 403, response.text

    def test_an_employee_cannot_read_the_reconciliation(
        self, client: TestClient, admin_user: User, employee_user: User
    ) -> None:
        response = client.get(
            f"/api/v1/leave/year-end/reconciliation?leave_year={CLOSED_YEAR}",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403, response.text
