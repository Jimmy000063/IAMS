"""One employee, end to end, through every module in order.

Why this exists when 1,193 tests already pass
---------------------------------------------
Those tests prove each module keeps its own promises. This one proves the
modules **compose**: that a shift assignment reaches attendance, that an
approved leave becomes a payable day, that a payable day becomes a payslip line,
that the payslip reaches a notification and a report, and that the audit trail
records the lot. Every one of those joins is a place where two correct modules
can still disagree, and none of them is visible from inside a single module's
tests.

It is deliberately one long test rather than many small ones. The order is the
point - each step depends on the state the previous step left - and splitting it
would mean either re-establishing that state repeatedly or quietly testing a
different thing.

Every statutory figure used here is invented for the test and labelled so.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.timeutil import utc_now
from app.modules.attendance.models import AttendanceDay
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.notifications.models import Notification
from tests.conftest import auth_header

pytestmark = pytest.mark.integration

D = Decimal

# April 2026. The 1st is a Wednesday, so the month has a clean working week and
# no month-boundary interactions.
PERIOD_START = date(2026, 4, 1)
PERIOD_END = date(2026, 4, 30)
WORKDAY = date(2026, 4, 8)  # a Wednesday
LEAVE_DAY = date(2026, 4, 9)  # the Thursday after
HOLIDAY = date(2026, 4, 14)  # a Tuesday
WEEKEND_WORKED = date(2026, 4, 11)  # a Saturday


def expect(response, status: int, label: str):
    """Assert a response's status, reporting the server's own message on failure.

    Without this, a failure halfway through a long workflow says "assert 422 ==
    201" and nothing about which rule refused.
    """
    assert response.status_code == status, f"{label}: {response.status_code} {response.text}"
    return response.json() if response.content else None


class TestFullWorkflow:
    def test_an_employee_from_hiring_to_payslip(
        self,
        client: TestClient,
        admin_user: User,
        employee_user: User,
        session: Session,
    ) -> None:
        hr = auth_header(client, "hradmin")

        # ------------------------------------------------------------------
        # 1. The organisation, then an employee, then an account for them.
        # ------------------------------------------------------------------
        department = expect(
            client.post(
                "/api/v1/org/departments",
                json={"code": "ENG", "name": "Engineering"},
                headers=hr,
            ),
            201,
            "department",
        )
        designation = expect(
            client.post(
                "/api/v1/org/designations",
                json={"code": "SE2", "name": "Software Engineer II", "level": 2},
                headers=hr,
            ),
            201,
            "designation",
        )
        location = expect(
            client.post(
                "/api/v1/org/work-locations",
                json={
                    "code": "BLR",
                    "name": "Bengaluru Office",
                    "timezone": "Asia/Kolkata",
                    "state": "Karnataka",
                },
                headers=hr,
            ),
            201,
            "work location",
        )

        employee = expect(
            client.post(
                "/api/v1/employees",
                json={
                    "employee_code": "ITC0001",
                    "first_name": "Ravi",
                    "last_name": "Sharma",
                    "employment_type": "full_time",
                    "date_of_joining": "2026-01-05",
                    "department_id": department["id"],
                    "designation_id": designation["id"],
                    "work_location_id": location["id"],
                },
                headers=hr,
            ),
            201,
            "employee",
        )
        expect(
            client.post(
                f"/api/v1/employees/{employee['id']}/link-account",
                json={"user_id": employee_user.id},
                headers=hr,
            ),
            200,
            "link account",
        )
        staff = auth_header(client, "e.employee")

        # The employee can see themselves, and nothing administrative.
        profile = expect(client.get("/api/v1/employees/me", headers=staff), 200, "own profile")
        assert profile["employee_code"] == "ITC0001"
        assert client.get("/api/v1/settings", headers=staff).status_code == 403

        # ------------------------------------------------------------------
        # 2. A shift and a calendar, which attendance will read.
        # ------------------------------------------------------------------
        shift = expect(
            client.post(
                "/api/v1/shifts",
                json={
                    "code": "GEN",
                    "name": "General shift",
                    "start_time": "09:30",
                    "end_time": "18:30",
                    "break_minutes": 60,
                },
                headers=hr,
            ),
            201,
            "shift",
        )
        pattern = expect(
            client.post(
                "/api/v1/shifts/weekly-off-patterns",
                json={
                    "code": "SATSUN",
                    "name": "Saturday and Sunday",
                    "off_days": ["sat", "sun"],
                },
                headers=hr,
            ),
            201,
            "weekly off pattern",
        )
        calendar = expect(
            client.post(
                "/api/v1/shifts/calendars",
                json={
                    "code": "IN-STD",
                    "name": "India standard",
                    "weekly_off_pattern_id": pattern["id"],
                },
                headers=hr,
            ),
            201,
            "work calendar",
        )
        expect(
            client.post(
                "/api/v1/shifts/assignments",
                json={
                    "employee_id": employee["id"],
                    "shift_id": shift["id"],
                    "effective_from": "2026-01-05",
                    "work_calendar_id": calendar["id"],
                },
                headers=hr,
            ),
            201,
            "shift assignment",
        )

        # The shift change reached the employee's notification centre.
        shift_notices = expect(
            client.get("/api/v1/notifications", params={"category": "shift"}, headers=staff),
            200,
            "shift notifications",
        )
        assert len(shift_notices) == 1, "a shift assignment is announced to the employee"

        # ------------------------------------------------------------------
        # 3. A published holiday, which must become non-working in attendance.
        # ------------------------------------------------------------------
        holiday_calendar = expect(
            client.post(
                "/api/v1/holidays/calendars",
                json={"code": "IN-2026", "name": "India 2026", "year": 2026},
                headers=hr,
            ),
            201,
            "holiday calendar",
        )
        expect(
            client.post(
                f"/api/v1/holidays/calendars/{holiday_calendar['id']}/holidays",
                json={"holiday_date": HOLIDAY.isoformat(), "name": "Company holiday"},
                headers=hr,
            ),
            201,
            "holiday",
        )
        expect(
            client.post(
                f"/api/v1/holidays/calendars/{holiday_calendar['id']}/publish",
                headers=hr,
            ),
            200,
            "publish holiday calendar",
        )

        # ------------------------------------------------------------------
        # 4. The employee punches, and the engine classifies the day.
        # ------------------------------------------------------------------
        first = expect(
            client.post("/api/v1/attendance/punch", json={"punch_type": "auto"}, headers=staff),
            200,
            "punch in",
        )
        assert first["event_type"] == "in"
        second = expect(
            client.post("/api/v1/attendance/punch", json={"punch_type": "auto"}, headers=staff),
            200,
            "punch out",
        )
        assert second["event_type"] == "out", "one button, alternating direction"

        # The day was recomputed from the punches, and the engine resolved the
        # shift and the day type by itself - which is the join being tested.
        assert second["day"]["shift_id"] == shift["id"]
        assert second["day"]["day_type"] == "working"
        # The request carried no timestamp: the server generates it, because a
        # browser clock can be set to any value.
        assert first["day"]["first_in_at"] is not None

        own_days = expect(client.get("/api/v1/attendance/me", headers=staff), 200, "my attendance")
        assert any(item["work_date"] == date.today().isoformat() for item in own_days), (
            "the employee can see the day their own punches produced"
        )

        # ------------------------------------------------------------------
        # 5. Attendance for the payroll month, written directly.
        #
        # Written rather than punched because the punch pipeline is already
        # covered above and in its own tests; what this test needs is a full
        # month for payroll to read.
        # ------------------------------------------------------------------
        day = PERIOD_START
        while day <= PERIOD_END:
            weekend = day.weekday() >= 5
            is_holiday = day == HOLIDAY
            # One Saturday was actually worked, which will earn comp-off: the
            # day type stays `weekly_off` - that is what makes it qualify - while
            # the minutes are real.
            worked = (not weekend and not is_holiday) or day == WEEKEND_WORKED
            session.add(
                AttendanceDay(
                    employee_id=employee["id"],
                    work_date=day,
                    day_type="weekly_off" if weekend else ("holiday" if is_holiday else "working"),
                    status=("present" if worked else ("holiday" if is_holiday else "weekly_off")),
                    worked_minutes=480 if worked else 0,
                    break_minutes=0,
                    net_minutes=480 if worked else 0,
                    late_minutes=0,
                    early_exit_minutes=0,
                    overtime_minutes=0,
                    has_missing_punch=False,
                    payable_day_fraction=D("1.00"),
                    computed_at=utc_now(),
                    computed_from_event_count=2 if worked else 0,
                )
            )
            day += timedelta(days=1)
        session.commit()

        # ------------------------------------------------------------------
        # 6. Leave: a type, a policy, an opening balance, a request, a decision.
        # ------------------------------------------------------------------
        leave_type = expect(
            client.post(
                "/api/v1/leave/types",
                json={
                    "code": "CL",
                    "name": "Casual leave",
                    "is_paid": True,
                    "deducts_balance": True,
                    "allows_half_day": True,
                },
                headers=hr,
            ),
            201,
            "leave type",
        )
        policy = expect(
            client.post(
                "/api/v1/leave/policies",
                json={
                    "leave_type_id": leave_type["id"],
                    "name": "Casual leave 2026",
                    "effective_from": "2026-01-01",
                    "annual_quota_days": "12.00",
                    "accrual_frequency": "annual",
                },
                headers=hr,
            ),
            201,
            "leave policy",
        )
        expect(
            client.post(
                "/api/v1/leave/policy-assignments",
                json={
                    "employee_id": employee["id"],
                    "leave_policy_id": policy["id"],
                    "effective_from": "2026-01-05",
                },
                headers=hr,
            ),
            201,
            "policy assignment",
        )
        expect(
            client.post(
                "/api/v1/leave/opening-balances",
                json={
                    "employee_id": employee["id"],
                    "leave_type_id": leave_type["id"],
                    "days": "10.00",
                    "reason": "Opening balance for the workflow test",
                },
                headers=hr,
            ),
            201,
            "opening balance",
        )

        balance_before = expect(
            client.get("/api/v1/leave/me/balances", headers=staff), 200, "balance"
        )
        assert balance_before[0]["available_days"] == "10.00"

        # Self-service is refused for a day 190 days back, which is the
        # backdated window working: the message names the path that exists for
        # it, and the test follows that path rather than widening the rule.
        too_old = client.post(
            "/api/v1/leave/me/applications",
            json={
                "leave_type_id": leave_type["id"],
                "start_date": LEAVE_DAY.isoformat(),
                "end_date": LEAVE_DAY.isoformat(),
                "reason": "Family commitment.",
            },
            headers=staff,
        )
        assert too_old.status_code == 422, too_old.text
        assert "ask HR to record it" in too_old.text

        application = expect(
            client.post(
                "/api/v1/leave/applications/on-behalf",
                json={
                    "employee_id": employee["id"],
                    "leave_type_id": leave_type["id"],
                    "start_date": LEAVE_DAY.isoformat(),
                    "end_date": LEAVE_DAY.isoformat(),
                    "reason": "Family commitment, recorded by HR.",
                },
                headers=hr,
            ),
            201,
            "leave application on behalf",
        )
        assert application["applied_on_behalf"] is True, (
            "recorded as what it is, not disguised as self-service"
        )
        # No manager, so the chain escalates to HR and records the skip.
        assert application["status"] == "pending_hr"
        assert application["escalated_from_missing_manager"] is True

        decided = expect(
            client.post(
                f"/api/v1/leave/applications/{application['id']}/decide",
                json={"approve": True, "comment": "Approved."},
                headers=hr,
            ),
            200,
            "leave decision",
        )
        assert decided["status"] == "approved"

        # The ledger moved, and the employee was told.
        balance_after = expect(
            client.get("/api/v1/leave/me/balances", headers=staff), 200, "balance after"
        )
        assert balance_after[0]["available_days"] == "9.00"
        leave_notices = expect(
            client.get("/api/v1/notifications", params={"category": "leave"}, headers=staff),
            200,
            "leave notifications",
        )
        assert any("approved" in item["title"] for item in leave_notices)

        # And the approved leave became a payable day in attendance.
        leave_attendance = session.scalar(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == employee["id"],
                AttendanceDay.work_date == LEAVE_DAY,
            )
        )
        assert leave_attendance is not None
        assert leave_attendance.status == "on_leave"
        assert leave_attendance.payable_day_fraction == D("1.00"), (
            "paid leave is a payable day, which is what stops payroll docking it"
        )

        # ------------------------------------------------------------------
        # 7. Comp-off, earned from the Saturday that was worked.
        # ------------------------------------------------------------------
        # Prove the input the job will read, so a zero result cannot be
        # mistaken for a broken rule.
        worked_saturday = session.scalar(
            select(AttendanceDay).where(
                AttendanceDay.employee_id == employee["id"],
                AttendanceDay.work_date == WEEKEND_WORKED,
            )
        )
        assert worked_saturday is not None
        assert worked_saturday.day_type == "weekly_off"
        assert worked_saturday.net_minutes == 480

        granted = expect(
            client.post(
                "/api/v1/compoff/credits/grant",
                json={
                    "start": WEEKEND_WORKED.isoformat(),
                    "end": WEEKEND_WORKED.isoformat(),
                },
                headers=hr,
            ),
            200,
            "comp-off grant",
        )
        assert granted["credits_created"] == 1

        again = expect(
            client.post(
                "/api/v1/compoff/credits/grant",
                json={
                    "start": WEEKEND_WORKED.isoformat(),
                    "end": WEEKEND_WORKED.isoformat(),
                },
                headers=hr,
            ),
            200,
            "comp-off re-grant",
        )
        assert again["credits_created"] == 0 and again["already_credited"] == 1, (
            "a day worked earns credit once, however often the job runs"
        )

        # ------------------------------------------------------------------
        # 8. Payroll: components, a revision, statutory rules, then the run.
        # ------------------------------------------------------------------
        expect(
            client.put(
                "/api/v1/settings/payroll.payable_days_basis",
                json={
                    "value": "calendar_days",
                    "confirm": True,
                    "reason": "Workflow test",
                    "source_reference": "The company's own policy choice.",
                },
                headers=hr,
            ),
            200,
            "confirm payable-days basis",
        )

        basic = expect(
            client.post(
                "/api/v1/payroll/components",
                json={
                    "code": "BASIC",
                    "name": "Basic salary",
                    "component_type": "earning",
                    "calculation_type": "percent_of_gross",
                    "percentage": "50",
                    "is_pf_wage": True,
                    "is_esi_wage": True,
                    "is_pt_wage": True,
                    "display_order": 10,
                },
                headers=hr,
            ),
            201,
            "basic component",
        )
        special = expect(
            client.post(
                "/api/v1/payroll/components",
                json={
                    "code": "SPL",
                    "name": "Special allowance",
                    "component_type": "earning",
                    "calculation_type": "balance",
                    "is_esi_wage": True,
                    "is_pt_wage": True,
                    "display_order": 90,
                },
                headers=hr,
            ),
            201,
            "balancing component",
        )
        expect(
            client.post(
                "/api/v1/payroll/revisions",
                json={
                    "employee_id": employee["id"],
                    "effective_from": "2026-01-05",
                    "gross_monthly": "40000.00",
                    "revision_reason": "On joining",
                    "components": [
                        {"component_id": basic["id"], "percentage": "50"},
                        {"component_id": special["id"]},
                    ],
                },
                headers=hr,
            ),
            201,
            "salary revision",
        )

        rule_set = expect(
            client.post(
                "/api/v1/payroll/statutory/rule-sets",
                json={
                    "domain": "epf",
                    "version_label": "workflow test (invented figures)",
                    "effective_from": "2026-01-01",
                    "parameters": [
                        {
                            "key": "employee_rate_percent",
                            "label": "Employee rate (invented for the test)",
                            "numeric_value": "10",
                        },
                        {
                            "key": "wage_ceiling",
                            "label": "Wage ceiling (invented for the test)",
                            "numeric_value": "15000",
                        },
                    ],
                },
                headers=hr,
            ),
            201,
            "statutory rule set",
        )
        expect(
            client.put(
                "/api/v1/settings/payroll.enabled_statutory_modules",
                json={"value": ["epf"], "reason": "Workflow test"},
                headers=hr,
            ),
            200,
            "enable epf",
        )
        expect(
            client.post(
                "/api/v1/payroll/statutory/eligibility",
                json={
                    "employee_id": employee["id"],
                    "domain": "epf",
                    "is_eligible": True,
                    "effective_from": "2026-01-05",
                },
                headers=hr,
            ),
            201,
            "eligibility",
        )

        run = expect(
            client.post("/api/v1/payroll/runs", json={"year": 2026, "month": 4}, headers=hr),
            201,
            "payroll run",
        )
        calculated = expect(
            client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr),
            200,
            "calculate",
        )
        assert calculated["payslips"] == 1
        # A full month of payable days, so the whole gross.
        assert calculated["total_gross"] == "40000.00"
        # 10% of a PF wage of 20000, capped at the invented ceiling of 15000.
        assert calculated["total_deductions"] == "1500.00"
        assert calculated["total_net"] == "38500.00"
        assert calculated["unverified_domains"] == ["epf"]

        # Finalization is refused while the rule set is unverified.
        session.execute(
            text("UPDATE payroll_runs SET calculated_by_user_id = NULL WHERE id = :id"),
            {"id": run["id"]},
        )
        session.commit()
        expect(client.post(f"/api/v1/payroll/runs/{run['id']}/review", headers=hr), 200, "review")
        session.execute(
            text("UPDATE payroll_runs SET reviewed_by_user_id = NULL WHERE id = :id"),
            {"id": run["id"]},
        )
        session.commit()
        expect(client.post(f"/api/v1/payroll/runs/{run['id']}/approve", headers=hr), 200, "approve")
        refused = client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=hr)
        assert refused.status_code == 422, refused.text
        assert "unverified statutory rule set" in refused.text, (
            "the verification gate is the whole point of the statutory design"
        )

        expect(
            client.post(
                f"/api/v1/payroll/statutory/rule-sets/{rule_set['id']}/verify",
                json={
                    "source_reference": (
                        "Workflow test: these figures are invented and are not a real rate."
                    )
                },
                headers=hr,
            ),
            200,
            "verify rule set",
        )
        expect(
            client.post(
                f"/api/v1/payroll/runs/{run['id']}/send-back",
                json={"reason": "Rule set verified; recalculating."},
                headers=hr,
            ),
            200,
            "send back",
        )
        expect(
            client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr),
            200,
            "recalculate",
        )
        session.execute(
            text("UPDATE payroll_runs SET calculated_by_user_id = NULL WHERE id = :id"),
            {"id": run["id"]},
        )
        session.commit()
        expect(
            client.post(f"/api/v1/payroll/runs/{run['id']}/review", headers=hr), 200, "re-review"
        )
        session.execute(
            text("UPDATE payroll_runs SET reviewed_by_user_id = NULL WHERE id = :id"),
            {"id": run["id"]},
        )
        session.commit()
        expect(
            client.post(f"/api/v1/payroll/runs/{run['id']}/approve", headers=hr),
            200,
            "re-approve",
        )
        finalized = expect(
            client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=hr),
            200,
            "finalize",
        )
        assert finalized["status"] == "finalized"

        # The run reconciles against its own lines.
        reconciliation = expect(
            client.get(f"/api/v1/payroll/runs/{run['id']}/reconciliation", headers=hr),
            200,
            "reconciliation",
        )
        assert reconciliation["balanced"] is True

        # ------------------------------------------------------------------
        # 9. The payslip reaches the employee, with its basis, once.
        # ------------------------------------------------------------------
        assert client.get("/api/v1/payroll/me/payslips", headers=staff).json() == [], (
            "an unreleased payslip is not shown: its figures could still change"
        )

        published = expect(
            client.post(f"/api/v1/payroll/runs/{run['id']}/publish", headers=hr),
            200,
            "publish payslips",
        )
        assert published["published"] == 1
        assert published["notifications_created"] == 1

        mine = expect(client.get("/api/v1/payroll/me/payslips", headers=staff), 200, "my payslips")
        assert len(mine) == 1
        assert mine[0]["net_pay"] == "38500.00"

        payslip = expect(
            client.get(f"/api/v1/payroll/payslips/{mine[0]['id']}", headers=staff),
            200,
            "payslip detail",
        )
        lines = {line["line_code"]: line for line in payslip["lines"]}
        assert lines["BASIC"]["amount"] == "20000.00"
        assert lines["SPL"]["amount"] == "20000.00"
        assert "10% of 15000" in lines["EPF_EE"]["basis_description"], (
            "every figure explains itself, or the employee cannot dispute it"
        )
        assert all(line["basis_description"] for line in payslip["lines"])

        payroll_notices = session.scalars(
            select(Notification).where(Notification.category == "payroll")
        ).all()
        assert len(payroll_notices) == 1
        assert not any(character.isdigit() for character in payroll_notices[0].body or ""), (
            "a notification is not a secure channel for pay"
        )

        # Releasing again tells nobody twice.
        again_published = expect(
            client.post(f"/api/v1/payroll/runs/{run['id']}/publish", headers=hr),
            200,
            "publish again",
        )
        assert again_published["published"] == 0
        assert again_published["notifications_created"] == 0

        # ------------------------------------------------------------------
        # 10. The reports agree with the modules, and the trail recorded it all.
        # ------------------------------------------------------------------
        attendance_report = expect(
            client.get(
                "/api/v1/reports/attendance_summary",
                params={"start": PERIOD_START.isoformat(), "end": PERIOD_END.isoformat()},
                headers=hr,
            ),
            200,
            "attendance report",
        )
        row = next(item for item in attendance_report["rows"] if item["employee_code"] == "ITC0001")
        assert row["payable_days"] == "30.00", "a full month, including the holiday and leave"
        assert row["on_leave"] == 1
        assert row["holiday"] == 1

        register = expect(
            client.get(f"/api/v1/reports/payroll_register?run_id={run['id']}", headers=hr),
            200,
            "payroll register report",
        )
        assert register["totals"]["net_pay"] == "38500.00", (
            "the report and the payroll module read the same rows"
        )

        leave_report = expect(
            client.get(
                "/api/v1/reports/leave_balances",
                params={"as_of": PERIOD_END.isoformat()},
                headers=hr,
            ),
            200,
            "leave report",
        )
        leave_row = next(
            item for item in leave_report["rows"] if item["employee_code"] == "ITC0001"
        )
        assert leave_row["balance"] == "9.00", "the report sums the same ledger"

        export = client.get(
            "/api/v1/reports/attendance_summary/export",
            params={
                "format": "csv",
                "start": PERIOD_START.isoformat(),
                "end": PERIOD_END.isoformat(),
            },
            headers=hr,
        )
        assert export.status_code == 200
        assert b"ITC0001" in export.content

        # The whole workflow is in the trail, including the export.
        actions = {row[0] for row in session.execute(select(AuditLog.action).distinct()).all()}
        for required in (
            "employee.created",
            "shift.assigned",
            "holiday.calendar_published",
            "leave.applied",
            "leave.approved",
            "compoff.credits_granted",
            "salary.component_created",
            "salary.revision_created",
            "statutory.rule_set_created",
            "statutory.rule_set_verified",
            "payroll.run_calculated",
            "payroll.run_reviewed",
            "payroll.run_approved",
            "payroll.run_finalized",
            "payroll.payslips_published",
            "report.exported",
        ):
            assert required in actions, f"{required} is missing from the audit trail"

        # And nothing in the trail can be rewritten.
        with pytest.raises(Exception, match="append-only"):
            session.execute(
                text(
                    "UPDATE audit_logs SET action = 'tampered' "
                    "WHERE id = (SELECT min(id) FROM audit_logs)"
                )
            )
        session.rollback()


class TestSelfServiceBoundaries:
    """One employee must not be able to reach anybody else's anything.

    Checked in one place, across every module, because this is the property
    most likely to be broken by a change in a single module and least likely to
    be noticed there.
    """

    def test_an_employee_is_confined_to_their_own_records(
        self,
        client: TestClient,
        admin_user: User,
        employee_user: User,
        org_fixtures: dict[str, int],
    ) -> None:
        hr = auth_header(client, "hradmin")

        mine = expect(
            client.post(
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
            ),
            201,
            "my employee",
        )
        expect(
            client.post(
                f"/api/v1/employees/{mine['id']}/link-account",
                json={"user_id": employee_user.id},
                headers=hr,
            ),
            200,
            "link",
        )
        theirs = expect(
            client.post(
                "/api/v1/employees",
                json={
                    "employee_code": "ITC0002",
                    "first_name": "Someone",
                    "last_name": "Else",
                    "employment_type": "full_time",
                    "date_of_joining": "2026-01-05",
                    **org_fixtures,
                },
                headers=hr,
            ),
            201,
            "their employee",
        )
        staff = auth_header(client, "e.employee")

        # Reading somebody else is 404, not 403: ids must not be enumerable.
        assert client.get(f"/api/v1/employees/{theirs['id']}", headers=staff).status_code == 404
        assert (
            client.get(f"/api/v1/payroll/revisions/{theirs['id']}", headers=staff).status_code
            == 404
        )

        # Administrative endpoints are refused outright.
        for method, path, body in [
            ("post", "/api/v1/payroll/runs", {"year": 2026, "month": 4}),
            ("get", "/api/v1/audit/logs", None),
            ("get", "/api/v1/reports", None),
            ("get", "/api/v1/settings", None),
            ("post", "/api/v1/admin/announcements", {"title": "No", "body": "No"}),
        ]:
            response = (
                client.get(path, headers=staff)
                if method == "get"
                else client.post(path, json=body, headers=staff)
            )
            assert response.status_code == 403, f"{path}: {response.status_code}"

        # Listing leave applications is allowed - with `leave.read_self` an
        # employee lists their **own**. The boundary here is scope, not refusal,
        # so it is checked as a scope.
        listed = client.get("/api/v1/leave/applications", headers=staff)
        assert listed.status_code == 200, listed.text
        assert all(item["employee_id"] == mine["id"] for item in listed.json()), (
            "an employee's own applications only"
        )

        # Their own self-service works.
        assert client.get("/api/v1/employees/me", headers=staff).status_code == 200
        assert client.get("/api/v1/notifications", headers=staff).status_code == 200
        assert client.get("/api/v1/announcements/feed", headers=staff).status_code == 200
        assert client.get("/api/v1/payroll/me/payslips", headers=staff).status_code == 200


class TestConfigurationHonesty:
    """The brief's central constraint, checked across the whole system.

    Nothing invented: no leave type, no holiday, no salary component, no
    statutory rate. A fresh installation holds no company policy at all, and
    the places that need one refuse rather than guessing.
    """

    def test_a_fresh_installation_invents_nothing(
        self, client: TestClient, admin_user: User, seeded: None
    ) -> None:
        hr = auth_header(client, "hradmin")

        for path, label in [
            ("/api/v1/leave/types?include_inactive=true", "leave types"),
            ("/api/v1/holidays/calendars", "holiday calendars"),
            ("/api/v1/payroll/components?include_inactive=true", "salary components"),
            ("/api/v1/payroll/statutory/rule-sets", "statutory rule sets"),
            ("/api/v1/shifts", "shifts"),
        ]:
            response = client.get(path, headers=hr)
            assert response.status_code == 200, f"{label}: {response.text}"
            assert response.json() == [], f"{label} must start empty, not invented"

        # And the configuration report says so plainly rather than reporting ready.
        readiness = expect(
            client.get("/api/v1/payroll/readiness", headers=hr), 200, "payroll readiness"
        )
        assert readiness["ready"] is False
        assert readiness["blockers"]

        health = expect(client.get("/api/v1/settings/health", headers=hr), 200, "settings health")
        assert health["unconfirmed"] > 0, "placeholders are reported as placeholders"
        assert health["unconfirmed_keys"], "and named, not merely counted"

    def test_payroll_refuses_to_run_on_an_unconfirmed_basis(
        self, client: TestClient, admin_user: User, org_fixtures: dict[str, int]
    ) -> None:
        """The denominator of every loss-of-pay amount. Guessing it would
        produce plausible numbers that are wrong for this company."""
        hr = auth_header(client, "hradmin")
        run = expect(
            client.post("/api/v1/payroll/runs", json={"year": 2026, "month": 4}, headers=hr),
            201,
            "run",
        )
        response = client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)
        assert response.status_code == 409, response.text
        assert "payable_days_basis" in response.text
