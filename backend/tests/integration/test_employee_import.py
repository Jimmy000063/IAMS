"""CSV import and document upload tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.employees.models import Employee
from tests.conftest import auth_header

pytestmark = pytest.mark.integration

HEADER = (
    "employee_code,first_name,last_name,employment_type,date_of_joining,"
    "department_code,reporting_manager_code,work_email\n"
)


def upload_csv(client: TestClient, headers: dict, body: str, *, dry_run: bool = True) -> dict:
    response = client.post(
        "/api/v1/employees/import",
        files={"file": ("employees.csv", body.encode("utf-8"), "text/csv")},
        data={"dry_run": str(dry_run).lower()},
        headers=headers,
    )
    assert response.status_code in {200, 422}, response.text
    return response.json()


class TestImportAccess:
    def test_import_requires_permission(self, client: TestClient, employee_user: User) -> None:
        response = client.post(
            "/api/v1/employees/import",
            files={"file": ("x.csv", b"employee_code\n", "text/csv")},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403

    def test_template_is_downloadable(self, client: TestClient, admin_user: User) -> None:
        response = client.get(
            "/api/v1/employees/import-template", headers=auth_header(client, "hradmin")
        )
        assert response.status_code == 200
        assert response.text.startswith("employee_code,first_name,")
        assert "attachment" in response.headers["content-disposition"]


class TestImportValidation:
    def test_missing_required_column_is_rejected(
        self, client: TestClient, admin_user: User
    ) -> None:
        body = upload_csv(
            client, auth_header(client, "hradmin"), "employee_code,first_name\nITC1,Ravi\n"
        )
        assert "Missing required column" in body["error"]["message"]

    def test_unknown_column_is_reported(self, client: TestClient, admin_user: User) -> None:
        """A typo'd header would otherwise be silently ignored."""
        csv = (
            "employee_code,first_name,employment_type,date_of_joining,salery\n"
            "ITC1,Ravi,full_time,2026-01-05,50000\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report["ok"] is False
        assert any("Unrecognised column" in e["message"] for e in report["errors"])

    def test_empty_file_is_rejected(self, client: TestClient, admin_user: User) -> None:
        body = upload_csv(client, auth_header(client, "hradmin"), "")
        assert "empty" in body["error"]["message"].lower()

    @pytest.mark.parametrize(
        "date_value,should_pass",
        [
            ("2026-01-05", True),
            ("05-01-2026", True),
            ("05/01/2026", True),
            ("05-Jan-2026", True),
            ("01/05/2026", True),  # read as 1 May, day-first - never month-first
            ("2026-13-01", False),
            ("not a date", False),
            ("", False),
        ],
    )
    def test_date_parsing(
        self, client: TestClient, admin_user: User, date_value: str, should_pass: bool
    ) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            f"ITC1,Ravi,full_time,{date_value}\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report.get("ok") is should_pass, report

    def test_month_first_dates_are_read_day_first(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        """03/04/2026 must mean 3 April, consistently - never 4 March.

        Guessing between the two conventions would silently corrupt joining
        dates, which drive leave accrual and payroll proration.
        """
        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC1,Ravi,full_time,03/04/2026\n"
        )
        upload_csv(client, auth_header(client, "hradmin"), csv, dry_run=False)
        employee = session.scalar(select(Employee))
        assert employee.date_of_joining.isoformat() == "2026-04-03"

    def test_invalid_employment_type_is_reported_with_its_row(
        self, client: TestClient, admin_user: User
    ) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC1,Ravi,full_time,2026-01-05\n"
            "ITC2,Priya,permanent,2026-01-06\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report["ok"] is False
        assert report["errors"][0]["row"] == 3, "row numbers account for the header"
        assert report["errors"][0]["column"] == "employment_type"

    def test_all_errors_are_reported_at_once(self, client: TestClient, admin_user: User) -> None:
        """So the operator fixes everything in one pass, not one row per attempt."""
        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC1,,full_time,2026-01-05\n"
            "ITC2,Priya,permanent,2026-01-06\n"
            "ITC3,Ravi,full_time,nonsense\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report["error_count"] == 3

    def test_cannot_import_as_exited(self, client: TestClient, admin_user: User) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining,employment_status\n"
            "ITC1,Ravi,full_time,2026-01-05,exited\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report["ok"] is False
        assert "exited" in report["errors"][0]["message"].lower()

    def test_utf8_bom_is_handled(self, client: TestClient, admin_user: User) -> None:
        """Excel exports CSV as UTF-8 with a BOM by default."""
        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC1,Ravi,full_time,2026-01-05\n"
        )
        response = client.post(
            "/api/v1/employees/import",
            files={"file": ("employees.csv", b"\xef\xbb\xbf" + csv.encode("utf-8"), "text/csv")},
            data={"dry_run": "true"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 200
        assert response.json()["ok"] is True


class TestDuplicateDetection:
    def test_clash_with_an_existing_employee(self, client: TestClient, admin_user: User) -> None:
        headers = auth_header(client, "hradmin")
        client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0001",
                "first_name": "Existing",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-01",
            },
            headers=headers,
        )
        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC0001,Clash,full_time,2026-02-01\n"
        )
        report = upload_csv(client, headers, csv)
        assert report["ok"] is False
        assert "already exists" in report["errors"][0]["message"]

    def test_duplicate_within_the_same_file(self, client: TestClient, admin_user: User) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC1,A,full_time,2026-01-05\n"
            "ITC1,B,full_time,2026-01-06\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report["ok"] is False
        assert "row 2 of this file" in report["errors"][0]["message"]

    def test_duplicate_email_within_the_file(self, client: TestClient, admin_user: User) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining,work_email\n"
            "ITC1,A,full_time,2026-01-05,same@example.com\n"
            "ITC2,B,full_time,2026-01-06,same@example.com\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report["ok"] is False
        assert report["errors"][0]["column"] == "work_email"


class TestImportApplication:
    def test_dry_run_writes_nothing(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC1,Ravi,full_time,2026-01-05\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv, dry_run=True)
        assert report["ok"] is True
        assert report["valid_rows"] == 1
        assert report["created_codes"] == []
        assert session.scalar(select(func.count()).select_from(Employee)) == 0

    def test_preview_shows_what_would_be_created(
        self, client: TestClient, admin_user: User
    ) -> None:
        csv = (
            "employee_code,first_name,last_name,employment_type,date_of_joining\n"
            "ITC1,Ravi,Sharma,full_time,2026-01-05\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report["preview"][0]["name"] == "Ravi Sharma"
        assert report["preview"][0]["employee_code"] == "ITC1"

    def test_commit_creates_employees(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC1,Ravi,full_time,2026-01-05\n"
            "ITC2,Priya,part_time,2026-02-10\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv, dry_run=False)
        assert report["ok"] is True
        assert sorted(report["created_codes"]) == ["ITC1", "ITC2"]
        assert session.scalar(select(func.count()).select_from(Employee)) == 2

    def test_a_single_bad_row_aborts_the_whole_import(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        """All-or-nothing: a partial import of master data is worse than none."""
        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC1,Ravi,full_time,2026-01-05\n"
            "ITC2,Priya,full_time,2026-02-10\n"
            "ITC3,Bad,not_a_type,2026-03-01\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv, dry_run=False)
        assert report["ok"] is False
        assert report["created_codes"] == []
        assert session.scalar(select(func.count()).select_from(Employee)) == 0

    def test_manager_referenced_later_in_the_file_resolves(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        """The report appears before their manager, which must still work."""
        csv = (
            "employee_code,first_name,employment_type,date_of_joining,reporting_manager_code\n"
            "ITC2,Report,full_time,2026-02-01,ITC1\n"
            "ITC1,Manager,full_time,2026-01-01,\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv, dry_run=False)
        assert report["ok"] is True, report

        manager = session.scalar(select(Employee).where(Employee.employee_code == "ITC1"))
        subordinate = session.scalar(select(Employee).where(Employee.employee_code == "ITC2"))
        assert subordinate.reporting_manager_id == manager.id

    def test_unknown_manager_code_is_reported(self, client: TestClient, admin_user: User) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining,reporting_manager_code\n"
            "ITC1,Ravi,full_time,2026-01-05,NOBODY\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report["ok"] is False
        assert report["errors"][0]["column"] == "reporting_manager_code"

    def test_self_referencing_manager_is_rejected(
        self, client: TestClient, admin_user: User
    ) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining,reporting_manager_code\n"
            "ITC1,Ravi,full_time,2026-01-05,ITC1\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report["ok"] is False

    def test_unknown_department_code_is_reported(
        self, client: TestClient, admin_user: User
    ) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining,department_code\n"
            "ITC1,Ravi,full_time,2026-01-05,NOSUCH\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv)
        assert report["ok"] is False
        assert "No active department" in report["errors"][0]["message"]

    def test_department_code_resolves(
        self,
        client: TestClient,
        session: Session,
        admin_user: User,
        org_fixtures: dict[str, int],
    ) -> None:
        csv = (
            "employee_code,first_name,employment_type,date_of_joining,department_code\n"
            "ITC1,Ravi,full_time,2026-01-05,ENG\n"
        )
        report = upload_csv(client, auth_header(client, "hradmin"), csv, dry_run=False)
        assert report["ok"] is True
        employee = session.scalar(select(Employee))
        assert employee.department_id == org_fixtures["department_id"]

    def test_import_creates_employment_history(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        from app.modules.employees.models import EmploymentHistory

        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC1,Ravi,full_time,2026-01-05\n"
        )
        upload_csv(client, auth_header(client, "hradmin"), csv, dry_run=False)
        assert session.scalar(select(func.count()).select_from(EmploymentHistory)) == 1

    def test_import_is_audited(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        from app.modules.audit.models import AuditLog

        csv = (
            "employee_code,first_name,employment_type,date_of_joining\n"
            "ITC1,Ravi,full_time,2026-01-05\n"
        )
        upload_csv(client, auth_header(client, "hradmin"), csv, dry_run=False)
        entry = session.scalar(select(AuditLog).where(AuditLog.action == "employee.bulk_imported"))
        assert entry is not None
        assert entry.context["count"] == 1


class TestDocumentUpload:
    MINIMAL_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"

    @pytest.fixture
    def employee_id(self, client: TestClient, admin_user: User) -> int:
        response = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0001",
                "first_name": "Ravi",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-05",
            },
            headers=auth_header(client, "hradmin"),
        )
        return response.json()["id"]

    def test_pdf_upload_and_download(
        self, client: TestClient, admin_user: User, employee_id: int
    ) -> None:
        headers = auth_header(client, "hradmin")
        uploaded = client.post(
            f"/api/v1/employees/{employee_id}/documents",
            files={"file": ("offer.pdf", self.MINIMAL_PDF, "application/pdf")},
            data={
                "document_type": "offer_letter",
                "title": "Offer letter",
                "visibility": "employee_visible",
            },
            headers=headers,
        )
        assert uploaded.status_code == 201, uploaded.text
        document_id = uploaded.json()["id"]

        downloaded = client.get(
            f"/api/v1/employees/{employee_id}/documents/{document_id}/download",
            headers=headers,
        )
        assert downloaded.status_code == 200
        assert downloaded.content == self.MINIMAL_PDF
        assert "attachment" in downloaded.headers["content-disposition"]
        assert downloaded.headers["x-content-type-options"] == "nosniff"

    def test_renamed_file_is_rejected(
        self, client: TestClient, admin_user: User, employee_id: int
    ) -> None:
        """Declaring a type is not enough; the magic bytes must agree."""
        response = client.post(
            f"/api/v1/employees/{employee_id}/documents",
            files={
                "file": (
                    "evil.pdf",
                    b"<html><script>alert(1)</script></html>",
                    "application/pdf",
                )
            },
            data={"document_type": "other", "title": "Not a PDF"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422
        assert "do not match" in response.json()["error"]["message"]

    @pytest.mark.parametrize(
        "content_type,filename",
        [
            ("text/html", "page.html"),
            ("image/svg+xml", "icon.svg"),
            ("application/zip", "bundle.zip"),
            ("application/x-msdownload", "setup.exe"),
        ],
    )
    def test_dangerous_types_are_rejected(
        self,
        client: TestClient,
        admin_user: User,
        employee_id: int,
        content_type: str,
        filename: str,
    ) -> None:
        response = client.post(
            f"/api/v1/employees/{employee_id}/documents",
            files={"file": (filename, b"whatever", content_type)},
            data={"document_type": "other", "title": "x"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_empty_file_is_rejected(
        self, client: TestClient, admin_user: User, employee_id: int
    ) -> None:
        response = client.post(
            f"/api/v1/employees/{employee_id}/documents",
            files={"file": ("empty.pdf", b"", "application/pdf")},
            data={"document_type": "other", "title": "x"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_oversized_file_is_rejected(
        self, client: TestClient, admin_user: User, employee_id: int
    ) -> None:
        from app.core.config import get_settings

        oversized = self.MINIMAL_PDF + b"0" * (get_settings().max_upload_bytes + 1)
        response = client.post(
            f"/api/v1/employees/{employee_id}/documents",
            files={"file": ("big.pdf", oversized, "application/pdf")},
            data={"document_type": "other", "title": "x"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422
        assert "larger than" in response.json()["error"]["message"]

    def test_employee_sees_only_visible_documents(
        self, client: TestClient, admin_user: User, employee_user: User, employee_id: int
    ) -> None:
        headers = auth_header(client, "hradmin")
        client.post(
            f"/api/v1/employees/{employee_id}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )
        for title, visibility in (
            ("Offer letter", "employee_visible"),
            ("Internal note", "hr_only"),
        ):
            client.post(
                f"/api/v1/employees/{employee_id}/documents",
                files={"file": ("doc.pdf", self.MINIMAL_PDF, "application/pdf")},
                data={"document_type": "other", "title": title, "visibility": visibility},
                headers=headers,
            )

        own = client.get(
            f"/api/v1/employees/{employee_id}/documents",
            headers=auth_header(client, "e.employee"),
        )
        assert [row["title"] for row in own.json()] == ["Offer letter"]

        all_docs = client.get(f"/api/v1/employees/{employee_id}/documents", headers=headers)
        assert len(all_docs.json()) == 2

    def test_employee_cannot_download_an_hr_only_document(
        self, client: TestClient, admin_user: User, employee_user: User, employee_id: int
    ) -> None:
        headers = auth_header(client, "hradmin")
        client.post(
            f"/api/v1/employees/{employee_id}/link-account",
            json={"user_id": employee_user.id},
            headers=headers,
        )
        document = client.post(
            f"/api/v1/employees/{employee_id}/documents",
            files={"file": ("note.pdf", self.MINIMAL_PDF, "application/pdf")},
            data={"document_type": "other", "title": "Internal", "visibility": "hr_only"},
            headers=headers,
        ).json()

        response = client.get(
            f"/api/v1/employees/{employee_id}/documents/{document['id']}/download",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403

    def test_employee_cannot_list_another_employees_documents(
        self, client: TestClient, admin_user: User, employee_user: User, employee_id: int
    ) -> None:
        response = client.get(
            f"/api/v1/employees/{employee_id}/documents",
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 404

    def test_download_is_audited(
        self, client: TestClient, session: Session, admin_user: User, employee_id: int
    ) -> None:
        from app.modules.audit.models import AuditLog

        headers = auth_header(client, "hradmin")
        document = client.post(
            f"/api/v1/employees/{employee_id}/documents",
            files={"file": ("doc.pdf", self.MINIMAL_PDF, "application/pdf")},
            data={"document_type": "other", "title": "Doc"},
            headers=headers,
        ).json()
        client.get(
            f"/api/v1/employees/{employee_id}/documents/{document['id']}/download",
            headers=headers,
        )

        entry = session.scalar(
            select(AuditLog).where(AuditLog.action == "employee.document_downloaded")
        )
        assert entry is not None

    def test_stored_filename_does_not_use_the_uploaded_name(
        self, client: TestClient, session: Session, admin_user: User, employee_id: int
    ) -> None:
        """A path-traversal attempt must not influence where the file lands."""
        from app.modules.employees.models import StoredFile

        client.post(
            f"/api/v1/employees/{employee_id}/documents",
            files={
                "file": (
                    "../../../etc/passwd.pdf",
                    self.MINIMAL_PDF,
                    "application/pdf",
                )
            },
            data={"document_type": "other", "title": "Traversal attempt"},
            headers=auth_header(client, "hradmin"),
        )
        stored = session.scalar(select(StoredFile))
        assert ".." not in stored.storage_key
        assert ".." not in stored.original_filename
        assert "/etc/" not in stored.storage_key
