"""Comp-off tests against a real database and the real HTTP stack.

The lot discipline is what these are mostly about: a day earns credit once, a
request spends specific credits earliest-expiry first, and a rejection gives
them back. None of that is checkable without a database.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.attendance.models import AttendanceDay
from app.modules.auth.models import User
from app.modules.compoff.models import CompoffAllocation, CompoffCredit
from tests.conftest import auth_header

pytestmark = pytest.mark.integration

D = Decimal

# 2026-11-07 is a Saturday and 11-08 a Sunday; 11-16 to 11-20 is a full week.
SAT = date(2026, 11, 7)
SUN = date(2026, 11, 8)
MON = date(2026, 11, 16)
TUE = date(2026, 11, 17)


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


@pytest.fixture
def assigned(client: TestClient, hr: dict, employee: dict, day_shift: dict) -> dict:
    response = client.post(
        "/api/v1/shifts/assignments",
        json={
            "employee_id": employee["id"],
            "shift_id": day_shift["id"],
            "effective_from": "2026-01-05",
        },
        headers=hr,
    )
    assert response.status_code == 201, response.text
    return employee


def weekend_work(
    session: Session, *, employee_id: int, work_date: date, net_minutes: int
) -> AttendanceDay:
    """An attendance day representing work on a day off.

    Written directly because the point under test is the credit job reading
    attendance, not the punch pipeline that produced it.
    """
    day = AttendanceDay(
        employee_id=employee_id,
        work_date=work_date,
        day_type="weekly_off",
        status="present",
        worked_minutes=net_minutes,
        break_minutes=0,
        net_minutes=net_minutes,
        late_minutes=0,
        early_exit_minutes=0,
        overtime_minutes=net_minutes,
        is_late=False,
        is_early_exit=False,
        has_missing_punch=False,
        payable_day_fraction=D("0.00"),
        computed_at=datetime.now(UTC),
        computed_from_event_count=2,
    )
    session.add(day)
    session.commit()
    return day


def grant_run(client: TestClient, hr: dict, start: date, end: date) -> dict:
    response = client.post(
        "/api/v1/compoff/credits/grant",
        json={"start": start.isoformat(), "end": end.isoformat()},
        headers=hr,
    )
    assert response.status_code == 200, response.text
    return response.json()


def verify_all(client: TestClient, hr: dict) -> int:
    pending = client.get("/api/v1/compoff/credits/pending-verification", headers=hr).json()
    for credit in pending:
        response = client.post(
            f"/api/v1/compoff/credits/{credit['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        assert response.status_code == 200, response.text
    return len(pending)


class TestEarning:
    def test_weekend_work_earns_a_credit_awaiting_verification(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        result = grant_run(client, hr, SUN, SUN)

        assert result["credits_created"] == 1
        assert result["days_credited"] == "1.00"

        credits = client.get(
            f"/api/v1/compoff/employees/{employee['id']}/credits", headers=hr
        ).json()
        assert len(credits) == 1
        assert credits[0]["status"] == "pending_verification"
        assert credits[0]["attendance_day_id"] is not None, "the credit cites its proof"

    def test_a_short_day_earns_nothing(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=120)
        result = grant_run(client, hr, SUN, SUN)
        assert result["credits_created"] == 0
        assert result["not_qualifying"] == 1

    def test_a_partial_day_earns_half(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=300)
        result = grant_run(client, hr, SUN, SUN)
        assert result["days_credited"] == "0.50"

    def test_re_running_the_job_credits_nothing_more(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        """A day earns credit once, however often attendance is recomputed."""
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        first = grant_run(client, hr, SUN, SUN)
        second = grant_run(client, hr, SUN, SUN)

        assert first["credits_created"] == 1
        assert second["credits_created"] == 0
        assert second["already_credited"] == 1

        total = session.scalar(select(func.count()).select_from(CompoffCredit))
        assert total == 1

    def test_the_same_day_cannot_be_credited_twice_at_the_database(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)

        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "INSERT INTO compoff_credits (employee_id, earned_date, source, "
                    "day_type, worked_minutes, credit_days, allocated_days, status) "
                    "VALUES (:emp, :day, 'attendance', 'weekly_off', 480, 1.00, 0.00, "
                    "'available')"
                ),
                {"emp": employee["id"], "day": SUN},
            )
            session.flush()
        session.rollback()

    def test_the_earned_facts_cannot_be_rewritten(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        """A credit's basis is evidence; a wrong one is reversed, not edited."""
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        credit = session.scalars(select(CompoffCredit)).one()

        with pytest.raises(DBAPIError) as error:
            session.execute(
                text("UPDATE compoff_credits SET credit_days = 5 WHERE id = :id"),
                {"id": credit.id},
            )
            session.flush()
        assert "keeps what was earned unchanged" in str(error.value)
        session.rollback()

    def test_a_lot_cannot_be_over_allocated(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        credit = session.scalars(select(CompoffCredit)).one()

        with pytest.raises(DBAPIError):
            session.execute(
                text("UPDATE compoff_credits SET allocated_days = 2 WHERE id = :id"),
                {"id": credit.id},
            )
            session.flush()
        session.rollback()

    def test_a_manual_credit_needs_a_reason_and_is_flagged(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        refused = client.post(
            "/api/v1/compoff/credits/manual",
            json={
                "employee_id": employee["id"],
                "earned_date": SUN.isoformat(),
                "credit_days": "1",
                "reason": "",
            },
            headers=hr,
        )
        assert refused.status_code == 422, refused.text

        granted = client.post(
            "/api/v1/compoff/credits/manual",
            json={
                "employee_id": employee["id"],
                "earned_date": SUN.isoformat(),
                "credit_days": "1",
                "reason": "Agreed with the employee; attendance was not recorded.",
            },
            headers=hr,
        )
        assert granted.status_code == 201, granted.text
        assert granted.json()["source"] == "manual"
        assert granted.json()["status"] == "available", "a deliberate grant needs no second check"


class TestVerification:
    def test_an_unverified_credit_is_not_spendable(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)

        balance = client.get(
            f"/api/v1/compoff/employees/{employee['id']}/balance", headers=hr
        ).json()
        assert balance["available_days"] == "0.00"
        assert balance["pending_verification_days"] == "1.00"

    def test_verifying_makes_it_available(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        assert verify_all(client, hr) == 1

        balance = client.get(
            f"/api/v1/compoff/employees/{employee['id']}/balance", headers=hr
        ).json()
        assert balance["available_days"] == "1.00"
        assert balance["pending_verification_days"] == "0.00"

    def test_rejecting_leaves_nothing_spendable(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        credit = client.get("/api/v1/compoff/credits/pending-verification", headers=hr).json()[0]

        response = client.post(
            f"/api/v1/compoff/credits/{credit['id']}/decide",
            json={"approve": False, "note": "The work was not authorised."},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "rejected"

        balance = client.get(
            f"/api/v1/compoff/employees/{employee['id']}/balance", headers=hr
        ).json()
        assert balance["available_days"] == "0.00"

    def test_a_decided_credit_cannot_be_decided_again(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        credit = client.get("/api/v1/compoff/credits/pending-verification", headers=hr).json()[0]
        client.post(
            f"/api/v1/compoff/credits/{credit['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        again = client.post(
            f"/api/v1/compoff/credits/{credit['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        assert again.status_code == 409, again.text


class TestReversal:
    def test_reversing_marks_the_credit_rather_than_deleting_it(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        """Deleting it would remove the evidence it was ever granted."""
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)
        credit = session.scalars(
            select(CompoffCredit).where(CompoffCredit.status == "available")
        ).one()

        response = client.post(
            f"/api/v1/compoff/credits/{credit.id}/reverse",
            json={"reason": "Granted for work that was already paid as overtime."},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "reversed"

        remaining = client.get(
            f"/api/v1/compoff/employees/{employee['id']}/credits", headers=hr
        ).json()
        assert any(item["status"] == "reversed" for item in remaining)

        balance = client.get(
            f"/api/v1/compoff/employees/{employee['id']}/balance", headers=hr
        ).json()
        assert balance["available_days"] == "0.00"

    def test_reversing_needs_a_reason(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        credit = session.scalars(select(CompoffCredit)).one()
        response = client.post(
            f"/api/v1/compoff/credits/{credit.id}/reverse",
            json={"reason": ""},
            headers=hr,
        )
        assert response.status_code == 422, response.text

    def test_a_spent_credit_cannot_be_reversed(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        assigned: dict,
    ) -> None:
        """The time off has been taken; correcting attendance is the remedy."""
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)

        staff = auth_header(client, "e.employee")
        applied = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=staff,
        )
        assert applied.status_code == 201, applied.text

        credit = session.scalars(
            select(CompoffCredit).where(CompoffCredit.status == "available")
        ).one()
        response = client.post(
            f"/api/v1/compoff/credits/{credit.id}/reverse",
            json={"reason": "Granted in error."},
            headers=hr,
        )
        assert response.status_code == 409, response.text
        assert "already been taken" in response.text


class TestApplying:
    def test_a_request_holds_specific_credits(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        """Held at submission, not approval: a credit about to expire must be
        reserved for this request rather than left for another."""
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)

        staff = auth_header(client, "e.employee")
        response = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=staff,
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["total_days"] == "1.00"
        assert len(body["allocations"]) == 1, "the request records which credit it drew on"

        balance = client.get("/api/v1/compoff/me/balance", headers=staff).json()
        assert balance["available_days"] == "0.00"
        assert balance["committed_days"] == "1.00"

    def test_a_request_beyond_the_credits_is_refused(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        """Unlike leave there is no unpaid fallback: comp-off is already earned."""
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)

        staff = auth_header(client, "e.employee")
        response = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": TUE.isoformat()},
            headers=staff,
        )
        assert response.status_code == 422, response.text
        assert "cannot be taken before it has been earned" in response.text

    def test_unverified_credit_does_not_fund_a_request(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        # Deliberately not verified.

        response = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422, response.text
        assert "awaiting verification" in response.text

    def test_the_earliest_expiring_credit_is_spent_first(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        """So an employee never loses a credit they could have used."""
        weekend_work(session, employee_id=assigned["id"], work_date=SAT, net_minutes=480)
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SAT, SUN)
        verify_all(client, hr)

        credits = session.scalars(select(CompoffCredit).order_by(CompoffCredit.earned_date)).all()
        earliest = next(item for item in credits if item.earned_date == SAT)

        applied = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=auth_header(client, "e.employee"),
        )
        assert applied.status_code == 201, applied.text
        assert applied.json()["allocations"][0]["credit_id"] == earliest.id

    def test_a_non_working_day_is_not_consumed(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        """Taking comp-off on a holiday would spend a credit for nothing."""
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)

        response = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": SAT.isoformat(), "end_date": SAT.isoformat()},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 422, response.text
        assert "are working days" in response.text

    def test_an_overlapping_request_is_refused(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        weekend_work(session, employee_id=assigned["id"], work_date=SAT, net_minutes=480)
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SAT, SUN)
        verify_all(client, hr)
        staff = auth_header(client, "e.employee")

        first = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=staff,
        )
        assert first.status_code == 201, first.text

        clash = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=staff,
        )
        assert clash.status_code == 409, clash.text


class TestApprovalAndRelease:
    def test_approval_marks_attendance_as_compoff_and_pays_the_day(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        """Comp-off is payable: it compensates work already done on a day off,
        which attendance itself paid nothing for."""
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)
        staff = auth_header(client, "e.employee")

        application = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=staff,
        ).json()
        decided = client.post(
            f"/api/v1/compoff/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"

        day = session.scalars(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == assigned["id"],
                AttendanceDay.work_date == MON,
            )
        ).one()
        assert day.status == "compoff"
        assert day.payable_day_fraction == D("1.00")

    def test_rejection_gives_the_credits_back(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        """Otherwise the credits would be stranded against a request that will
        never happen, and the employee would lose days they earned."""
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)
        staff = auth_header(client, "e.employee")

        application = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=staff,
        ).json()
        rejected = client.post(
            f"/api/v1/compoff/applications/{application['id']}/decide",
            json={"approve": False, "comment": "Cover not arranged."},
            headers=hr,
        )
        assert rejected.status_code == 200, rejected.text

        balance = client.get("/api/v1/compoff/me/balance", headers=staff).json()
        assert balance["available_days"] == "1.00"
        assert balance["committed_days"] == "0.00"

        assert session.scalar(select(func.count()).select_from(CompoffAllocation)) == 0

    def test_withdrawing_gives_the_credits_back(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)
        staff = auth_header(client, "e.employee")

        application = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=staff,
        ).json()
        withdrawn = client.post(
            f"/api/v1/compoff/applications/{application['id']}/withdraw",
            json={"reason": "Not needed."},
            headers=staff,
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert (
            client.get("/api/v1/compoff/me/balance", headers=staff).json()["available_days"]
            == "1.00"
        )

    def test_cancelling_returns_the_credits_with_their_original_expiry(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        """Cancelling does not extend a credit's life."""
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)
        original_expiry = session.scalars(select(CompoffCredit)).one().expiry_date

        staff = auth_header(client, "e.employee")
        application = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=staff,
        ).json()
        client.post(
            f"/api/v1/compoff/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        cancelled = client.post(
            f"/api/v1/compoff/applications/{application['id']}/cancel",
            json={"reason": "Plans changed."},
            headers=staff,
        )
        assert cancelled.status_code == 200, cancelled.text

        credit = session.scalars(select(CompoffCredit)).one()
        session.refresh(credit)
        assert credit.expiry_date == original_expiry
        assert (
            client.get("/api/v1/compoff/me/balance", headers=staff).json()["available_days"]
            == "1.00"
        )

        day = session.scalars(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == assigned["id"],
                AttendanceDay.work_date == MON,
            )
        ).one()
        assert day.status != "compoff", "attendance follows the cancellation"

    def test_nobody_can_approve_their_own_comp_off(
        self,
        client: TestClient,
        hr: dict,
        session: Session,
        admin_user: User,
        org_fixtures: dict[str, int],
    ) -> None:
        hr_employee = client.post(
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
        ).json()
        client.post(
            f"/api/v1/employees/{hr_employee['id']}/link-account",
            json={"user_id": admin_user.id},
            headers=hr,
        )
        client.post(
            "/api/v1/compoff/credits/manual",
            json={
                "employee_id": hr_employee["id"],
                "earned_date": SUN.isoformat(),
                "credit_days": "1",
                "reason": "Test credit.",
            },
            headers=hr,
        )
        application = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=hr,
        )
        assert application.status_code == 201, application.text

        decision = client.post(
            f"/api/v1/compoff/applications/{application.json()['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        assert decision.status_code == 403, decision.text
        assert "your own comp-off" in decision.text

    def test_a_decided_request_cannot_be_decided_again(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)
        application = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=auth_header(client, "e.employee"),
        ).json()
        client.post(
            f"/api/v1/compoff/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        again = client.post(
            f"/api/v1/compoff/applications/{application['id']}/decide",
            json={"approve": True},
            headers=hr,
        )
        assert again.status_code == 409, again.text

    def test_a_recorded_decision_cannot_be_rewritten(
        self, client: TestClient, hr: dict, session: Session, assigned: dict
    ) -> None:
        weekend_work(session, employee_id=assigned["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)
        application = client.post(
            "/api/v1/compoff/me/applications",
            json={"start_date": MON.isoformat(), "end_date": MON.isoformat()},
            headers=auth_header(client, "e.employee"),
        ).json()
        client.post(
            f"/api/v1/compoff/applications/{application['id']}/decide",
            json={"approve": False, "comment": "No."},
            headers=hr,
        )

        with pytest.raises(DBAPIError) as error:
            session.execute(
                text(
                    "UPDATE compoff_approvals SET decision = 'approved' WHERE application_id = :id"
                ),
                {"id": application["id"]},
            )
            session.flush()
        assert "append-only" in str(error.value)
        session.rollback()


class TestNotifications:
    """Milestone 7 wiring, checked where it matters: the credit job.

    The job is re-run routinely, so the thing to prove is that a second run
    tells nobody anything - which is also the brief's requirement for retried
    jobs.
    """

    def test_a_credit_notifies_the_employee_once_however_often_the_job_runs(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        assigned: dict,
        session: Session,
    ) -> None:
        from app.modules.notifications.models import Notification

        weekend_work(session, employee_id=employee["id"], work_date=SAT, net_minutes=480)

        first = grant_run(client, hr, SAT, SAT)
        assert first["credits_created"] == 1

        second = grant_run(client, hr, SAT, SAT)
        assert second["credits_created"] == 0
        assert second["already_credited"] == 1

        rows = session.scalars(select(Notification).where(Notification.category == "compoff")).all()
        assert len(rows) == 1, [row.title for row in rows]
        assert SAT.isoformat() in rows[0].title

    def test_verification_is_reported_and_says_it_is_now_usable(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        assigned: dict,
        session: Session,
    ) -> None:
        from app.modules.notifications.models import Notification

        weekend_work(session, employee_id=employee["id"], work_date=SAT, net_minutes=480)
        grant_run(client, hr, SAT, SAT)
        verified = verify_all(client, hr)
        assert verified == 1

        rows = session.scalars(
            select(Notification).where(Notification.category == "compoff").order_by(Notification.id)
        ).all()
        assert len(rows) == 2, [row.title for row in rows]
        assert "verified" in rows[1].title
        assert "available" in (rows[1].body or "")

    def test_the_employee_sees_both_in_their_own_centre(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        assigned: dict,
        session: Session,
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SAT, net_minutes=480)
        grant_run(client, hr, SAT, SAT)
        verify_all(client, hr)

        staff = auth_header(client, "e.employee")
        summary = client.get("/api/v1/notifications/unread-count", headers=staff).json()
        # Two comp-off notices: earned, then verified. The shift assignment the
        # fixture made is in there too, which is correct - being put on a shift
        # is something the employee is told about.
        assert summary["unread_by_category"]["compoff"] == 2
        assert summary["unread_total"] == sum(summary["unread_by_category"].values())


class TestExpiry:
    def test_a_credit_past_its_date_is_expired_and_the_days_lost(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)
        credit = session.scalars(select(CompoffCredit)).one()
        beyond = (credit.expiry_date or SUN) + timedelta(days=1)

        result = client.post(
            "/api/v1/compoff/credits/expire",
            json={"as_of": beyond.isoformat()},
            headers=hr,
        )
        assert result.status_code == 200, result.text
        assert result.json()["credits_expired"] == 1
        assert result.json()["days_lost"] == "1.00"

        session.refresh(credit)
        assert credit.status == "expired"

    def test_re_running_the_expiry_job_finds_nothing_left(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)
        credit = session.scalars(select(CompoffCredit)).one()
        beyond = ((credit.expiry_date or SUN) + timedelta(days=1)).isoformat()

        first = client.post(
            "/api/v1/compoff/credits/expire", json={"as_of": beyond}, headers=hr
        ).json()
        second = client.post(
            "/api/v1/compoff/credits/expire", json={"as_of": beyond}, headers=hr
        ).json()
        assert first["credits_expired"] == 1
        assert second["credits_expired"] == 0

    def test_a_balance_shows_an_overdue_credit_as_expired_before_the_job_runs(
        self, client: TestClient, hr: dict, session: Session, employee: dict
    ) -> None:
        """So the figure does not lie in the window between the date and the job."""
        weekend_work(session, employee_id=employee["id"], work_date=SUN, net_minutes=480)
        grant_run(client, hr, SUN, SUN)
        verify_all(client, hr)
        credit = session.scalars(select(CompoffCredit)).one()
        beyond = (credit.expiry_date or SUN) + timedelta(days=1)

        balance = client.get(
            f"/api/v1/compoff/employees/{employee['id']}/balance?as_of={beyond.isoformat()}",
            headers=hr,
        ).json()
        assert balance["available_days"] == "0.00"
        assert balance["expired_days"] == "1.00"


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/api/v1/compoff/credits/manual"),
            ("post", "/api/v1/compoff/credits/grant"),
            ("post", "/api/v1/compoff/credits/expire"),
            ("get", "/api/v1/compoff/credits/pending-verification"),
            ("post", "/api/v1/compoff/applications/on-behalf"),
        ],
    )
    def test_an_employee_cannot_administer_comp_off(
        self,
        client: TestClient,
        admin_user: User,
        employee_user: User,
        method: str,
        path: str,
    ) -> None:
        headers = auth_header(client, "e.employee")
        response = (
            client.get(path, headers=headers)
            if method == "get"
            else client.post(path, json={}, headers=headers)
        )
        assert response.status_code == 403, f"{method.upper()} {path} -> {response.status_code}"

    def test_an_employee_cannot_read_another_employees_credits(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        org_fixtures: dict[str, int],
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
            f"/api/v1/compoff/employees/{stranger['id']}/credits",
            headers=auth_header(client, "e.employee"),
        )
        # An employee holds compoff.approve_as_manager as a precondition, so the
        # route is reachable and the scope check answers 404.
        assert response.status_code in (403, 404), response.text

    def test_an_anonymous_caller_is_refused(self, client: TestClient) -> None:
        assert client.get("/api/v1/compoff/me/balance").status_code == 401
        assert client.post("/api/v1/compoff/me/applications", json={}).status_code == 401
