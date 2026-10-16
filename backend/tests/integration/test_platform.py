"""Health, error-handling, audit and database-guarantee tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.timeutil import utc_now
from app.modules.audit.models import AuditLog, JobRun
from app.modules.auth.models import User
from tests.conftest import TEST_PASSWORD, auth_header

pytestmark = pytest.mark.integration


class TestHealth:
    def test_liveness_is_public(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_readiness_confirms_database_and_migrations(self, client: TestClient) -> None:
        response = client.get("/health/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["database"] == "ok"
        assert body["migrations"] == "ok"

    def test_health_does_not_leak_infrastructure_detail(self, client: TestClient) -> None:
        body = client.get("/health").text.lower()
        for secret in ("password", "postgresql://", "iams_local_dev", "secret_key"):
            assert secret not in body


class TestOpenApi:
    def test_schema_is_served(self, client: TestClient) -> None:
        response = client.get("/api/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"]
        assert "/api/v1/auth/login" in schema["paths"]

    def test_every_endpoint_has_a_summary(self, client: TestClient) -> None:
        """Documentation quality is part of the deliverable, so it is asserted."""
        schema = client.get("/api/openapi.json").json()
        missing = [
            f"{method.upper()} {path}"
            for path, methods in schema["paths"].items()
            for method, operation in methods.items()
            if method in {"get", "post", "put", "patch", "delete"} and not operation.get("summary")
        ]
        assert missing == []

    def test_docs_page_renders(self, client: TestClient) -> None:
        response = client.get("/api/docs")
        assert response.status_code == 200
        assert "swagger" in response.text.lower()

    def test_docs_csp_permits_its_cdn_bundle(self, client: TestClient) -> None:
        """Regression test: the API's deny-all CSP would blank the docs page."""
        csp = client.get("/api/docs").headers["Content-Security-Policy"]
        assert "cdn.jsdelivr.net" in csp

    def test_api_responses_use_a_deny_all_csp(self, client: TestClient) -> None:
        csp = client.get("/health").headers["Content-Security-Policy"]
        assert csp.startswith("default-src 'none'")
        assert "cdn.jsdelivr.net" not in csp


class TestSecurityHeaders:
    @pytest.mark.parametrize(
        "header,expected",
        [
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
        ],
    )
    def test_baseline_headers_are_present(
        self, client: TestClient, header: str, expected: str
    ) -> None:
        assert client.get("/health").headers[header] == expected

    def test_request_id_is_returned(self, client: TestClient) -> None:
        assert client.get("/health").headers["X-Request-ID"]

    def test_supplied_request_id_is_echoed_for_correlation(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Request-ID": "frontend-trace-1"})
        assert response.headers["X-Request-ID"] == "frontend-trace-1"

    def test_oversized_request_id_is_truncated(self, client: TestClient) -> None:
        """An unbounded client-supplied value must not reach the logs or database."""
        response = client.get("/health", headers={"X-Request-ID": "x" * 500})
        assert len(response.headers["X-Request-ID"]) <= 40


class TestErrorEnvelope:
    def test_validation_errors_use_the_standard_envelope(self, client: TestClient) -> None:
        response = client.post("/api/v1/auth/login", json={"identifier": "only"})
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "validation_error"
        assert body["error"]["details"]
        assert body["request_id"]

    def test_not_found_uses_the_standard_envelope(self, client: TestClient) -> None:
        body = client.get("/api/v1/does-not-exist").json()
        assert body["error"]["code"] == "not_found"

    def test_errors_never_contain_a_stack_trace(self, client: TestClient, admin_user: User) -> None:
        response = client.get(
            "/api/v1/settings/no.such.key", headers=auth_header(client, "hradmin")
        )
        assert "Traceback" not in response.text
        assert "app/modules" not in response.text


class TestAuditTrail:
    def test_successful_sign_in_is_audited(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        client.post("/api/v1/auth/login", json={"identifier": "hradmin", "password": TEST_PASSWORD})
        entry = session.scalar(select(AuditLog).where(AuditLog.action == "auth.login_succeeded"))
        assert entry is not None
        assert entry.actor_user_id == admin_user.id
        assert entry.request_id

    def test_failed_sign_in_is_audited(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        client.post(
            "/api/v1/auth/login", json={"identifier": "hradmin", "password": "Wr0ng!Password"}
        )
        entry = session.scalar(select(AuditLog).where(AuditLog.action == "auth.login_failed"))
        assert entry is not None
        assert entry.succeeded is False

    def test_settings_change_is_audited(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        client.put(
            "/api/v1/settings/attendance.grace_late_minutes",
            json={"value": 20, "reason": "policy review"},
            headers=auth_header(client, "hradmin"),
        )
        entry = session.scalar(select(AuditLog).where(AuditLog.action == "settings.updated"))
        assert entry is not None
        assert entry.before_value == {"value": 15}
        assert entry.after_value == {"value": 20}

    def test_no_password_or_token_is_ever_stored_in_the_audit_trail(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        """The central privacy guarantee, asserted against real recorded rows."""
        headers = auth_header(client, "hradmin")
        created = client.post(
            "/api/v1/auth/users",
            json={"username": "audit.check", "role_codes": ["employee"]},
            headers=headers,
        ).json()
        temporary_password = created["temporary_password"]

        dumped = "\n".join(
            str(row)
            for row in session.execute(
                text("SELECT before_value, after_value, context FROM audit_logs")
            ).all()
        )
        assert temporary_password not in dumped
        assert TEST_PASSWORD not in dumped
        for token in ("password_hash", "$argon2"):
            assert token not in dumped

    def test_audit_rows_cannot_be_modified(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        """The append-only promise is enforced by the database, not by convention."""
        client.post("/api/v1/auth/login", json={"identifier": "hradmin", "password": TEST_PASSWORD})
        with pytest.raises(DBAPIError):
            session.execute(text("UPDATE audit_logs SET action = 'tampered'"))
        session.rollback()


class TestDatabaseGuarantees:
    def test_transaction_rolls_back_on_failure(
        self, session: Session, seeded: None, cli_context
    ) -> None:
        """A failed operation must leave nothing behind."""
        from app.core.errors import Conflict
        from app.modules.auth import service as auth_service

        auth_service.create_user(
            session,
            username="first.user",
            email=None,
            role_codes=["employee"],
            password=TEST_PASSWORD,
            context=cli_context,
        )
        session.commit()
        before = session.scalar(select(func.count()).select_from(User))

        with pytest.raises(Conflict):
            auth_service.create_user(
                session,
                username="first.user",
                email=None,
                role_codes=["employee"],
                password=TEST_PASSWORD,
                context=cli_context,
            )
        session.rollback()

        assert session.scalar(select(func.count()).select_from(User)) == before

    def test_lowercase_constraint_blocks_a_mixed_case_username(
        self, session: Session, seeded: None
    ) -> None:
        """Belt and braces: the service normalises, and the database insists."""
        session.add(User(username="MixedCase", password_hash="x", must_change_password=True))
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_job_idempotency_key_is_unique(self, session: Session) -> None:
        """This constraint is what stops a retried job double-crediting leave."""
        for _ in range(2):
            session.add(
                JobRun(
                    job_name="leave_accrual",
                    idempotency_key="leave_accrual:2026-04",
                    status="succeeded",
                    started_at=utc_now(),
                )
            )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_job_status_must_be_a_known_value(self, session: Session) -> None:
        session.add(
            JobRun(
                job_name="x",
                idempotency_key="x:1",
                status="banana",
                started_at=utc_now(),
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

    def test_timestamps_round_trip_as_utc_aware(self, session: Session) -> None:
        """Naive timestamps would corrupt overnight-shift and cross-timezone logic."""
        session.add(
            JobRun(
                job_name="tz_check",
                idempotency_key="tz_check:1",
                status="running",
                started_at=utc_now(),
            )
        )
        session.commit()
        row = session.scalar(select(JobRun).where(JobRun.job_name == "tz_check"))
        assert row is not None
        assert row.started_at.tzinfo is not None
        assert row.started_at.utcoffset().total_seconds() == 0

    def test_session_parameters_survive_a_rollback(self, session: Session) -> None:
        """Session settings must be connection properties, not `SET` statements.

        psycopg opens an implicit transaction on a connection's first statement,
        so a `SET` issued in a pool handler runs inside one and is undone when
        SQLAlchemy rolls the connection back on return to the pool. It reads
        back correctly if you check it straight away, which is why the statement
        timeout sat in the engine for ten milestones without ever applying, and
        why the timezone silently followed whatever the server was installed
        with - UTC in a container, Asia/Kolkata on a developer's laptop.

        Both are now passed as libpq startup options. Asserting *after* an
        explicit rollback is the whole point of this test: the previous
        arrangement passed every check that did not roll back first.
        """
        session.rollback()

        timezone = session.execute(text("SHOW TimeZone")).scalar()
        assert timezone == "UTC", (
            f"session timezone is {timezone!r}; timestamps would be returned in that "
            "zone and behaviour would depend on where PostgreSQL is installed"
        )

        timeout = session.execute(text("SHOW statement_timeout")).scalar()
        assert timeout not in (None, "0"), (
            "statement_timeout is not applied, so a runaway report query has nothing to stop it"
        )
