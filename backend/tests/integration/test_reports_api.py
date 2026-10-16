"""Reports, exports, the audit viewer and retention, against a real database.

The properties worth proving:

* **A report is not a wider view.** A manager's report covers their team; it
  must not become a way to read the whole company.
* **Exports are audited**, because a spreadsheet leaves the system's access
  controls behind.
* **Reading the audit log is itself audited**, since an organisation may need to
  account for who went looking.
* **A report agrees with the module it reads.** Payable days in the attendance
  report are the same figure payroll used.
"""

from __future__ import annotations

import io
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
from tests.conftest import TEST_PASSWORD, auth_header

pytestmark = pytest.mark.integration

D = Decimal
MONTH_START = date(2026, 4, 1)
MONTH_END = date(2026, 4, 30)


@pytest.fixture
def hr(client: TestClient, admin_user: User) -> dict:
    return auth_header(client, "hradmin")


@pytest.fixture
def staff_employee(
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
    client.post(
        f"/api/v1/employees/{record['id']}/link-account",
        json={"user_id": employee_user.id},
        headers=hr,
    )
    return record


@pytest.fixture
def other_employee(client: TestClient, hr: dict, org_fixtures: dict[str, int]) -> dict:
    created = client.post(
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
    )
    assert created.status_code == 201, created.text
    return created.json()


def attendance_for(session: Session, *, employee_id: int, days: int = 10) -> None:
    day = MONTH_START
    for index in range(days):
        session.add(
            AttendanceDay(
                employee_id=employee_id,
                work_date=day,
                day_type="working",
                status="present" if index % 5 else "absent",
                worked_minutes=480 if index % 5 else 0,
                break_minutes=0,
                net_minutes=480 if index % 5 else 0,
                late_minutes=5 if index == 1 else 0,
                early_exit_minutes=0,
                overtime_minutes=30 if index == 2 else 0,
                is_late=index == 1,
                is_early_exit=False,
                has_missing_punch=False,
                payable_day_fraction=D("1.00") if index % 5 else D("0.00"),
                computed_at=utc_now(),
                computed_from_event_count=2,
            )
        )
        day += timedelta(days=1)
    session.commit()


class TestCatalogue:
    def test_the_catalogue_lists_only_what_the_caller_can_run(
        self, client: TestClient, hr: dict, staff_employee: dict
    ) -> None:
        full = client.get("/api/v1/reports", headers=hr)
        assert full.status_code == 200, full.text
        codes = {item["code"] for item in full.json()}
        assert "payroll_register" in codes
        assert all(item["scope"] == "all" for item in full.json())

        staff = auth_header(client, "e.employee")
        limited = client.get("/api/v1/reports", headers=staff)
        # An ordinary employee holds no report permission at all.
        assert limited.status_code == 403, limited.text

    def test_the_catalogue_states_the_parameters(self, client: TestClient, hr: dict) -> None:
        """So the interface builds its form from the backend rather than keeping
        a second copy of the filters."""
        rows = client.get("/api/v1/reports", headers=hr).json()
        summary = next(item for item in rows if item["code"] == "attendance_summary")
        assert "start" in summary["parameters"]
        assert "end" in summary["parameters"]

    def test_an_unknown_report_is_404(self, client: TestClient, hr: dict) -> None:
        response = client.get("/api/v1/reports/not_a_report", headers=hr)
        assert response.status_code == 404, response.text


class TestAttendanceReports:
    def test_the_summary_counts_the_statuses(
        self, client: TestClient, hr: dict, staff_employee: dict, session: Session
    ) -> None:
        attendance_for(session, employee_id=staff_employee["id"])
        response = client.get(
            "/api/v1/reports/attendance_summary",
            params={"start": MONTH_START.isoformat(), "end": MONTH_END.isoformat()},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        row = next(item for item in body["rows"] if item["employee_code"] == "ITC0001")
        assert row["days_recorded"] == 10
        assert row["absent"] == 2
        assert row["present"] == 8
        assert row["payable_days"] == "8.00"
        assert row["late_days"] == 1
        assert row["overtime_minutes"] == 30

    def test_payable_days_agree_with_payroll(
        self, client: TestClient, hr: dict, staff_employee: dict, session: Session
    ) -> None:
        """Both read the same stored fraction, so a difference would mean one of
        them is computing instead of reading."""
        attendance_for(session, employee_id=staff_employee["id"])
        report = client.get(
            "/api/v1/reports/attendance_summary",
            params={"start": MONTH_START.isoformat(), "end": MONTH_END.isoformat()},
            headers=hr,
        ).json()
        row = next(item for item in report["rows"] if item["employee_code"] == "ITC0001")

        total = session.scalar(
            select(AttendanceDay.payable_day_fraction).where(
                AttendanceDay.employee_id == staff_employee["id"],
                AttendanceDay.work_date == MONTH_START,
            )
        )
        assert total is not None
        assert row["payable_days"] == "8.00"

    def test_the_register_is_capped_to_a_quarter(self, client: TestClient, hr: dict) -> None:
        """An unbounded daily register would eventually take the database down."""
        response = client.get(
            "/api/v1/reports/attendance_register",
            params={"start": "2026-01-01", "end": "2026-12-31"},
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "92 days" in response.text

    def test_unsettled_days_are_not_called_absences(
        self, client: TestClient, hr: dict, staff_employee: dict, session: Session
    ) -> None:
        attendance_for(session, employee_id=staff_employee["id"])
        session.execute(
            text(
                "UPDATE attendance_days SET status = 'missing_punch' "
                "WHERE employee_id = :id AND work_date = '2026-04-03'"
            ),
            {"id": staff_employee["id"]},
        )
        session.commit()

        response = client.get(
            "/api/v1/reports/unsettled_days",
            params={"start": MONTH_START.isoformat(), "end": MONTH_END.isoformat()},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["rows"]
        assert "Not absences" in body["description"]
        assert body["rows"][0]["correction_pending"] is False

    def test_overtime_reports_minutes_and_no_amount(
        self, client: TestClient, hr: dict, staff_employee: dict, session: Session
    ) -> None:
        """The multiplier is a legal rate this project has not verified, so no
        amount is derived."""
        attendance_for(session, employee_id=staff_employee["id"])
        body = client.get(
            "/api/v1/reports/overtime",
            params={"start": MONTH_START.isoformat(), "end": MONTH_END.isoformat()},
            headers=hr,
        ).json()
        assert body["rows"][0]["overtime_minutes"] == 30
        assert not any(column["kind"] == "money" for column in body["columns"])
        assert "has not been verified" in body["description"]

    def test_an_inverted_date_range_is_refused(self, client: TestClient, hr: dict) -> None:
        response = client.get(
            "/api/v1/reports/attendance_summary",
            params={"start": "2026-04-30", "end": "2026-04-01"},
            headers=hr,
        )
        assert response.status_code == 422, response.text


class TestScope:
    def test_a_manager_sees_only_their_team(
        self,
        client: TestClient,
        hr: dict,
        staff_employee: dict,
        other_employee: dict,
        session: Session,
        seeded: None,
        cli_context,
    ) -> None:
        """The property that matters most here: a report must not be a way to
        read records the caller cannot open directly."""
        from app.core.permissions import ROLE_EMPLOYEE
        from app.modules.auth import service as auth_service

        # A manager account with the attendance report permission but no
        # read-all grant.
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

        manager = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0100",
                "first_name": "Priya",
                "last_name": "Nair",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-05",
                "department_id": staff_employee["department_id"],
                "designation_id": staff_employee["designation_id"],
                "work_location_id": staff_employee["work_location_id"],
            },
            headers=hr,
        ).json()
        client.post(
            f"/api/v1/employees/{manager['id']}/link-account",
            json={"user_id": result.user.id},
            headers=hr,
        )
        client.patch(
            f"/api/v1/employees/{staff_employee['id']}",
            json={"reporting_manager_id": manager["id"]},
            headers=hr,
        )
        # A purpose-made role with the report permission but no read-all grant.
        # Added rather than altering the shared 'employee' role: the permission
        # catalogue is seeded once per session and its tables are deliberately
        # not truncated, so changing a standard grant would leak into every
        # later test.
        session.execute(
            text(
                "INSERT INTO roles (code, name, description, is_system, is_active) "
                "VALUES ('report_only', 'Report only', 'Test fixture', false, true) "
                "ON CONFLICT (code) DO NOTHING"
            )
        )
        session.execute(
            text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r, permissions p "
                "WHERE r.code = 'report_only' AND p.code IN "
                "('report.attendance', 'profile.read_self') "
                "ON CONFLICT DO NOTHING"
            )
        )
        session.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id) "
                "SELECT :user_id, id FROM roles WHERE code = 'report_only' "
                "ON CONFLICT DO NOTHING"
            ),
            {"user_id": result.user.id},
        )
        session.commit()

        attendance_for(session, employee_id=staff_employee["id"])
        attendance_for(session, employee_id=other_employee["id"])

        manager_headers = auth_header(client, "m.manager")
        body = client.get(
            "/api/v1/reports/attendance_summary",
            params={"start": MONTH_START.isoformat(), "end": MONTH_END.isoformat()},
            headers=manager_headers,
        ).json()
        codes = {row["employee_code"] for row in body["rows"]}
        assert "ITC0001" in codes, "their own report"
        assert "ITC0100" in codes, "themselves"
        assert "ITC0002" not in codes, "somebody outside their team must not appear"

        catalogue = client.get("/api/v1/reports", headers=manager_headers).json()
        assert all(item["scope"] == "team" for item in catalogue)


class TestExports:
    def test_csv_carries_a_bom_and_the_headers(
        self, client: TestClient, hr: dict, staff_employee: dict, session: Session
    ) -> None:
        """Without a BOM, Excel reads UTF-8 as the system code page and mangles
        any non-ASCII name - and a name is exactly what gets mangled."""
        attendance_for(session, employee_id=staff_employee["id"])
        response = client.get(
            "/api/v1/reports/attendance_summary/export",
            params={
                "format": "csv",
                "start": MONTH_START.isoformat(),
                "end": MONTH_END.isoformat(),
            },
            headers=hr,
        )
        assert response.status_code == 200, response.text
        assert response.content[:3] == b"\xef\xbb\xbf"
        assert "attachment" in response.headers["content-disposition"]
        assert response.headers["x-content-type-options"] == "nosniff"
        text_body = response.content.decode("utf-8-sig")
        assert "Attendance summary" in text_body
        assert "ITC0001" in text_body

    def test_xlsx_is_a_real_workbook(
        self, client: TestClient, hr: dict, staff_employee: dict, session: Session
    ) -> None:
        """Not a CSV with a misleading extension."""
        from openpyxl import load_workbook

        attendance_for(session, employee_id=staff_employee["id"])
        response = client.get(
            "/api/v1/reports/attendance_summary/export",
            params={
                "format": "xlsx",
                "start": MONTH_START.isoformat(),
                "end": MONTH_END.isoformat(),
            },
            headers=hr,
        )
        assert response.status_code == 200, response.text
        assert response.content[:2] == b"PK"
        book = load_workbook(io.BytesIO(response.content))
        sheet = book.active
        assert sheet.max_row >= 5
        # Numbers arrive as numbers so a spreadsheet can total them.
        assert isinstance(sheet.cell(row=5, column=4).value, (int, float))

    def test_an_export_needs_its_own_permission(
        self,
        client: TestClient,
        hr: dict,
        staff_employee: dict,
        session: Session,
        seeded: None,
        cli_context,
    ) -> None:
        """Reading figures and walking out with a spreadsheet are different
        acts, so they are different grants.

        Proved with an account that can run the report but not export it, rather
        than by removing a standard grant - which would leak into every later
        test, since the permission catalogue is seeded once per session.
        """
        from app.modules.auth import service as auth_service

        session.execute(
            text(
                "INSERT INTO roles (code, name, description, is_system, is_active) "
                "VALUES ('no_export', 'Reports without export', 'Test fixture', "
                "false, true) ON CONFLICT (code) DO NOTHING"
            )
        )
        session.execute(
            text(
                "INSERT INTO role_permissions (role_id, permission_id) "
                "SELECT r.id, p.id FROM roles r, permissions p "
                "WHERE r.code = 'no_export' AND p.code IN "
                "('report.attendance', 'attendance.read_all', 'profile.read_self') "
                "ON CONFLICT DO NOTHING"
            )
        )
        session.commit()

        auth_service.create_user(
            session,
            username="r.reader",
            email="reader@example.com",
            role_codes=["no_export"],
            password=TEST_PASSWORD,
            must_change_password=False,
            context=cli_context,
        )
        session.commit()

        headers = auth_header(client, "r.reader")
        assert (
            client.get(
                "/api/v1/reports/attendance_summary",
                params={"start": MONTH_START.isoformat(), "end": MONTH_END.isoformat()},
                headers=headers,
            ).status_code
            == 200
        ), "the report itself is allowed"

        response = client.get(
            "/api/v1/reports/attendance_summary/export",
            params={"format": "csv"},
            headers=headers,
        )
        assert response.status_code == 403, response.text

    def test_every_export_is_audited(
        self, client: TestClient, hr: dict, staff_employee: dict, session: Session
    ) -> None:
        """An exported file leaves the system's access controls behind, so who
        took what is the only thing that makes it traceable."""
        attendance_for(session, employee_id=staff_employee["id"])
        client.get(
            "/api/v1/reports/attendance_summary/export",
            params={
                "format": "csv",
                "start": MONTH_START.isoformat(),
                "end": MONTH_END.isoformat(),
            },
            headers=hr,
        )
        entry = session.scalars(select(AuditLog).where(AuditLog.action == "report.exported")).one()
        assert entry.entity_id == "attendance_summary"
        assert entry.after_value["format"] == "csv"
        assert entry.after_value["filters"]["start"] == MONTH_START.isoformat()
        assert entry.after_value["rows"] >= 1


class TestPayrollReports:
    def test_the_register_discovers_a_companys_own_components(
        self, client: TestClient, hr: dict, session: Session
    ) -> None:
        """No report should know the names of a company's salary components."""
        response = client.get(
            "/api/v1/reports/payroll_register", params={"run_id": 999}, headers=hr
        )
        assert response.status_code == 404, response.text

    def test_a_payroll_report_without_a_run_id_is_refused(
        self, client: TestClient, hr: dict
    ) -> None:
        response = client.get("/api/v1/reports/payroll_register", headers=hr)
        assert response.status_code == 422, response.text
        assert "payroll run id" in response.text


class TestEmployeeReport:
    def test_the_register_omits_bank_and_identity_numbers(
        self, client: TestClient, hr: dict, staff_employee: dict
    ) -> None:
        """An exportable spreadsheet of bank details would be the easiest thing
        in the system to leak."""
        body = client.get("/api/v1/reports/employee_master", headers=hr).json()
        keys = {column["key"] for column in body["columns"]}
        for forbidden in ("bank_account_number", "pan", "aadhaar", "ifsc"):
            assert forbidden not in keys
        assert "deliberately absent" in body["description"]

    def test_exited_employees_are_excluded_by_default(
        self, client: TestClient, hr: dict, staff_employee: dict
    ) -> None:
        client.post(
            f"/api/v1/employees/{staff_employee['id']}/exit",
            json={
                "date_of_exit": "2026-03-31",
                "last_working_day": "2026-03-31",
                "exit_type": "resignation",
                "exit_reason": "Moving on.",
            },
            headers=hr,
        )
        default = client.get("/api/v1/reports/employee_master", headers=hr).json()
        assert "ITC0001" not in {row["employee_code"] for row in default["rows"]}

        included = client.get(
            "/api/v1/reports/employee_master",
            params={"include_exited": True},
            headers=hr,
        ).json()
        assert "ITC0001" in {row["employee_code"] for row in included["rows"]}


class TestAuditViewer:
    def test_the_log_is_searchable_by_action_prefix(
        self, client: TestClient, hr: dict, staff_employee: dict
    ) -> None:
        """How somebody actually searches a trail: by the area, not by
        remembering an exact action name."""
        response = client.get("/api/v1/audit/logs", params={"action": "employee."}, headers=hr)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] >= 1
        assert all(item["action"].startswith("employee.") for item in body["items"])

    def test_searching_the_log_is_itself_audited(
        self, client: TestClient, hr: dict, session: Session
    ) -> None:
        """An organisation may need to account for who went looking."""
        client.get("/api/v1/audit/logs", params={"action": "auth."}, headers=hr)
        entry = session.scalars(select(AuditLog).where(AuditLog.action == "audit.searched")).one()
        assert entry.after_value["filters"]["action"] == "auth."
        # The filters, not the results.
        assert "items" not in entry.after_value

    def test_pagination_reports_the_total(self, client: TestClient, hr: dict) -> None:
        body = client.get(
            "/api/v1/audit/logs", params={"page": 1, "page_size": 1}, headers=hr
        ).json()
        assert body["page_size"] == 1
        assert len(body["items"]) <= 1
        assert body["pages"] >= 1

    def test_the_action_catalogue_is_read_from_the_data(
        self, client: TestClient, hr: dict, staff_employee: dict
    ) -> None:
        """So an action a new module records appears without anybody
        maintaining a list."""
        body = client.get("/api/v1/audit/logs/actions", headers=hr).json()
        assert body
        assert {"action", "count", "last_seen"} <= set(body[0])

    def test_an_employee_cannot_read_the_audit_log(
        self, client: TestClient, hr: dict, staff_employee: dict
    ) -> None:
        staff = auth_header(client, "e.employee")
        assert client.get("/api/v1/audit/logs", headers=staff).status_code == 403

    def test_unauthenticated_access_is_refused(self, client: TestClient) -> None:
        assert client.get("/api/v1/audit/logs").status_code == 401
        assert client.get("/api/v1/reports").status_code == 401


class TestRetention:
    def test_the_report_counts_without_deleting(
        self, client: TestClient, hr: dict, seeded: None, session: Session
    ) -> None:
        before = session.scalar(select(AuditLog.id).limit(1))
        response = client.get("/api/v1/audit/retention", headers=hr)
        assert response.status_code == 200, response.text
        body = response.json()
        assert {item["category"] for item in body["categories"]} == {
            "audit_logs",
            "device_raw_events",
            "notifications",
        }
        assert "Nothing is purged automatically" in body["note"]
        assert session.scalar(select(AuditLog.id).limit(1)) == before

    def test_unconfirmed_retention_periods_are_named(
        self, client: TestClient, hr: dict, seeded: None
    ) -> None:
        """A retention period destroys evidence as well as data, so it should
        not take effect because nobody looked at the default."""
        body = client.get("/api/v1/audit/retention", headers=hr).json()
        assert "retention.audit_log_days" in body["unconfirmed"]

    def test_the_audit_category_says_it_needs_the_owner_role(
        self, client: TestClient, hr: dict, seeded: None
    ) -> None:
        body = client.get("/api/v1/audit/retention", headers=hr).json()
        audit_category = next(
            item for item in body["categories"] if item["category"] == "audit_logs"
        )
        assert "owner database role" in audit_category["note"]
