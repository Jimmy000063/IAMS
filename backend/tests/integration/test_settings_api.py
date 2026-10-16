"""Business policy configuration endpoint tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConfigurationMissing
from app.modules.auth.models import User
from app.modules.settings import service as settings_service
from app.modules.settings.catalogue import SETTINGS
from app.modules.settings.models import SettingChangeHistory
from tests.conftest import auth_header

pytestmark = pytest.mark.integration


class TestSeeding:
    def test_seeding_inserts_the_whole_catalogue(self, session: Session, seeded: None) -> None:
        rows = settings_service.list_settings(session)
        assert len(rows) == len(SETTINGS)

    def test_seeding_is_idempotent(self, session: Session, seeded: None) -> None:
        before = len(settings_service.list_settings(session))
        result = settings_service.seed_settings(session)
        session.commit()
        assert result["created"] == 0
        assert len(settings_service.list_settings(session)) == before

    def test_reseeding_does_not_overwrite_a_stored_decision(
        self, session: Session, seeded: None, cli_context
    ) -> None:
        """A code default must never silently revert an HR decision."""
        settings_service.update_setting(
            session, "attendance.grace_late_minutes", 25, context=cli_context, confirm=True
        )
        session.commit()

        settings_service.seed_settings(session)
        session.commit()

        assert settings_service.get_value(session, "attendance.grace_late_minutes") == 25
        assert settings_service.get_row(session, "attendance.grace_late_minutes").is_confirmed


class TestReading:
    def test_reading_requires_authentication(self, client: TestClient) -> None:
        assert client.get("/api/v1/settings").status_code == 401

    def test_employee_cannot_read_settings(self, client: TestClient, employee_user: User) -> None:
        response = client.get("/api/v1/settings", headers=auth_header(client, "e.employee"))
        assert response.status_code == 403

    def test_hr_can_list_settings(self, client: TestClient, admin_user: User) -> None:
        response = client.get("/api/v1/settings", headers=auth_header(client, "hradmin"))
        assert response.status_code == 200
        assert len(response.json()) == len(SETTINGS)

    def test_filtering_by_category(self, client: TestClient, admin_user: User) -> None:
        response = client.get(
            "/api/v1/settings",
            params={"category": "attendance"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 200
        assert {row["category"] for row in response.json()} == {"attendance"}

    def test_confirmed_values_from_the_brief_are_marked_confirmed(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.get(
            "/api/v1/settings/company.working_week", headers=auth_header(client, "hradmin")
        )
        body = response.json()
        assert body["value"] == ["mon", "tue", "wed", "thu", "fri"]
        assert body["is_confirmed"] is True

    def test_placeholder_values_are_marked_unconfirmed(
        self, client: TestClient, admin_user: User
    ) -> None:
        """Visible honesty: a working default is not a company decision."""
        response = client.get(
            "/api/v1/settings/attendance.grace_late_minutes",
            headers=auth_header(client, "hradmin"),
        )
        assert response.json()["is_confirmed"] is False

    def test_unknown_key_returns_404(self, client: TestClient, admin_user: User) -> None:
        response = client.get(
            "/api/v1/settings/no.such.key", headers=auth_header(client, "hradmin")
        )
        assert response.status_code == 404

    def test_health_reports_outstanding_decisions(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.get("/api/v1/settings/health", headers=auth_header(client, "hradmin"))
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == len(SETTINGS)
        assert body["unconfirmed"] > 0
        assert "payroll.payable_days_basis" in body["legal_review_keys"]


class TestWriting:
    def test_employee_cannot_change_a_setting(
        self, client: TestClient, employee_user: User
    ) -> None:
        response = client.put(
            "/api/v1/settings/attendance.grace_late_minutes",
            json={"value": 60},
            headers=auth_header(client, "e.employee"),
        )
        assert response.status_code == 403

    def test_hr_can_change_a_setting(self, client: TestClient, admin_user: User) -> None:
        response = client.put(
            "/api/v1/settings/attendance.grace_late_minutes",
            json={"value": 20, "reason": "HR policy review", "confirm": True},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 200
        assert response.json()["value"] == 20
        assert response.json()["is_confirmed"] is True

    def test_change_is_recorded_in_history(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        """So a past attendance figure can be explained by the value then in force."""
        client.put(
            "/api/v1/settings/attendance.grace_late_minutes",
            json={"value": 20, "reason": "HR policy review"},
            headers=auth_header(client, "hradmin"),
        )
        rows = session.scalars(
            select(SettingChangeHistory).where(
                SettingChangeHistory.setting_key == "attendance.grace_late_minutes"
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].old_value == {"value": 15}
        assert rows[0].new_value == {"value": 20}
        assert rows[0].change_reason == "HR policy review"

    def test_history_endpoint_returns_the_change(
        self, client: TestClient, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        client.put(
            "/api/v1/settings/attendance.grace_late_minutes",
            json={"value": 20},
            headers=headers,
        )
        response = client.get(
            "/api/v1/settings/attendance.grace_late_minutes/history", headers=headers
        )
        assert response.status_code == 200
        assert response.json()[0]["new_value"] == 20

    @pytest.mark.parametrize(
        "key,bad_value",
        [
            ("attendance.grace_late_minutes", 500),  # above max
            ("attendance.grace_late_minutes", -5),  # below min
            ("attendance.grace_late_minutes", "fifteen"),  # wrong type
            ("attendance.grace_late_minutes", True),  # bool is not an integer
            ("attendance.rounding_mode", "sideways"),  # not an enum choice
            ("company.working_week", ["mon", "funday"]),  # not an allowed item
            ("company.working_week", ["mon", "mon"]),  # duplicate entry
            ("leave.year_start_month", 13),  # above max
            ("notifications.email_enabled", "yes"),  # wrong type
        ],
    )
    def test_invalid_values_are_rejected(
        self, client: TestClient, admin_user: User, key: str, bad_value: object
    ) -> None:
        response = client.put(
            f"/api/v1/settings/{key}",
            json={"value": bad_value},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422, response.text

    def test_rejected_value_is_not_persisted(
        self, client: TestClient, session: Session, admin_user: User
    ) -> None:
        headers = auth_header(client, "hradmin")
        client.put(
            "/api/v1/settings/attendance.grace_late_minutes",
            json={"value": 9999},
            headers=headers,
        )
        session.expire_all()
        assert settings_service.get_value(session, "attendance.grace_late_minutes") == 15

    def test_non_editable_setting_cannot_be_changed(
        self, client: TestClient, admin_user: User
    ) -> None:
        """Self-registration is forbidden by the brief, so it must not be switchable."""
        response = client.put(
            "/api/v1/settings/security.public_registration_enabled",
            json={"value": True},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "policy_violation"

    def test_confirming_a_statutory_setting_requires_a_source(
        self, client: TestClient, admin_user: User
    ) -> None:
        """A statutory value must be traceable to its authority."""
        headers = auth_header(client, "hradmin")
        without_source = client.put(
            "/api/v1/settings/payroll.payable_days_basis",
            json={"value": "fixed_days", "confirm": True},
            headers=headers,
        )
        assert without_source.status_code == 422

        with_source = client.put(
            "/api/v1/settings/payroll.payable_days_basis",
            json={
                "value": "fixed_days",
                "confirm": True,
                "source_reference": "Board resolution 2026-04-01, clause 7",
                "reason": "Confirmed with the auditor",
            },
            headers=headers,
        )
        assert with_source.status_code == 200
        assert with_source.json()["is_confirmed"] is True


class TestRequireConfirmed:
    def test_unconfirmed_setting_raises_instead_of_returning_a_placeholder(
        self, session: Session, seeded: None
    ) -> None:
        """The core honesty guarantee for payroll.

        Code that needs a real decision must fail loudly rather than compute a
        plausible but wrong number from a placeholder.
        """
        with pytest.raises(ConfigurationMissing):
            settings_service.require_confirmed(session, "payroll.payable_days_basis")

    def test_confirmed_setting_is_returned(self, session: Session, seeded: None) -> None:
        assert settings_service.require_confirmed(session, "payroll.currency") == "INR"

    def test_value_is_returned_once_confirmed(
        self, session: Session, seeded: None, cli_context
    ) -> None:
        settings_service.update_setting(
            session,
            "payroll.payable_days_basis",
            "calendar_days",
            context=cli_context,
            confirm=True,
            source_reference="Board resolution 2026-04-01",
        )
        session.commit()
        assert (
            settings_service.require_confirmed(session, "payroll.payable_days_basis")
            == "calendar_days"
        )


class TestTypedReads:
    def test_json_setting_returns_a_list(self, session: Session, seeded: None) -> None:
        assert settings_service.get_value(session, "company.working_week") == [
            "mon",
            "tue",
            "wed",
            "thu",
            "fri",
        ]

    def test_integer_setting_returns_an_int(self, session: Session, seeded: None) -> None:
        value = settings_service.get_value(session, "attendance.grace_late_minutes")
        assert isinstance(value, int) and value == 15

    def test_boolean_setting_returns_a_bool(self, session: Session, seeded: None) -> None:
        value = settings_service.get_value(session, "attendance.web_punch_enabled")
        assert value is True

    def test_missing_key_raises_unless_a_default_is_given(
        self, session: Session, seeded: None
    ) -> None:
        from app.core.errors import NotFound

        with pytest.raises(NotFound):
            settings_service.get_value(session, "nope.missing")
        assert settings_service.get_value(session, "nope.missing", default=7) == 7
