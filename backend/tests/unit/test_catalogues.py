"""Tests for the declarative permission and settings catalogues.

These guard the two places where a silent typo would be dangerous: a permission
code referenced by a route but absent from the catalogue (route unreachable), and
a setting that the brief marks as confirmed being shipped as a placeholder (wrong
value quietly in effect).
"""

from __future__ import annotations

import pytest

from app.core.permissions import (
    ALL_PERMISSION_CODES,
    PERMISSIONS,
    ROLE_EMPLOYEE,
    ROLE_HR_ADMIN,
    ROLES_BY_CODE,
)
from app.core.permissions import validate_catalogue as validate_permissions
from app.modules.settings.catalogue import (
    SETTINGS,
    SETTINGS_BY_KEY,
    VALUE_TYPES,
)
from app.modules.settings.catalogue import validate_catalogue as validate_settings

pytestmark = pytest.mark.unit


class TestPermissionCatalogue:
    def test_catalogue_is_internally_consistent(self) -> None:
        validate_permissions()

    def test_codes_are_unique(self) -> None:
        codes = [p.code for p in PERMISSIONS]
        assert len(codes) == len(set(codes))

    def test_every_code_is_module_dot_action(self) -> None:
        for permission in PERMISSIONS:
            assert permission.code.count(".") == 1, permission.code
            assert permission.description, f"{permission.code} has no description"

    def test_hr_admin_holds_every_permission(self) -> None:
        assert ROLES_BY_CODE[ROLE_HR_ADMIN].permission_codes == ALL_PERMISSION_CODES

    def test_employee_role_is_self_service_plus_manager_review(self) -> None:
        held = ROLES_BY_CODE[ROLE_EMPLOYEE].permission_codes
        assert "attendance.punch" in held
        assert "leave.apply" in held
        assert "payslip.read_self" in held
        # Manager review is a precondition only; the relationship check lives in
        # the service layer.
        assert "leave.approve_as_manager" in held

    @pytest.mark.parametrize(
        "forbidden",
        [
            "employee.manage",
            "employee.read_sensitive",
            "salary.read_all",
            "payslip.read_all",
            "payroll.finalize",
            "leave.approve_hr",
            "leave.adjust_ledger",
            "settings.manage",
            "statutory.manage",
            "audit.read",
            "user.manage",
            "attendance.manage",
            "compoff.verify",
        ],
    )
    def test_employee_role_withholds_privileged_permissions(self, forbidden: str) -> None:
        """An ordinary employee must not be able to approve, pay or administrate."""
        assert forbidden not in ROLES_BY_CODE[ROLE_EMPLOYEE].permission_codes

    def test_employee_cannot_read_other_peoples_data(self) -> None:
        held = ROLES_BY_CODE[ROLE_EMPLOYEE].permission_codes
        for code in ("attendance.read_all", "leave.read_all", "compoff.read_all"):
            assert code not in held

    def test_roles_only_grant_catalogued_permissions(self) -> None:
        for role in ROLES_BY_CODE.values():
            assert role.permission_codes <= ALL_PERMISSION_CODES


class TestSettingsCatalogue:
    def test_catalogue_is_internally_consistent(self) -> None:
        validate_settings()

    def test_keys_are_unique_and_namespaced_by_category(self) -> None:
        for setting in SETTINGS:
            assert setting.key.startswith(f"{setting.category}.")
        assert len(SETTINGS) == len(SETTINGS_BY_KEY)

    def test_every_value_type_is_known(self) -> None:
        for setting in SETTINGS:
            assert setting.value_type in VALUE_TYPES

    def test_enum_defaults_are_within_their_choices(self) -> None:
        for setting in SETTINGS:
            if setting.value_type == "enum":
                assert setting.default in (setting.constraints or {})["choices"]

    @pytest.mark.parametrize(
        "key,expected",
        [
            ("company.working_week", ["mon", "tue", "wed", "thu", "fri"]),
            ("leave.approval_chain", ["reporting_manager", "hr_admin"]),
            ("security.public_registration_enabled", False),
            ("attendance.web_punch_enabled", True),
            ("compoff.requires_hr_verification", True),
            ("payroll.statutory_jurisdiction", "IN"),
        ],
    )
    def test_defaults_confirmed_in_the_brief(self, key: str, expected: object) -> None:
        """These values come from the project brief, so they ship confirmed."""
        setting = SETTINGS_BY_KEY[key]
        assert setting.default == expected
        assert setting.confirmed is True

    def test_public_registration_is_not_editable(self) -> None:
        """The brief forbids self-registration, so it must not be switchable."""
        assert SETTINGS_BY_KEY["security.public_registration_enabled"].editable is False

    @pytest.mark.parametrize(
        "key",
        [
            "payroll.payable_days_basis",
            "payroll.enabled_statutory_modules",
            "payroll.professional_tax_states",
        ],
    )
    def test_statutory_settings_are_flagged_and_unconfirmed(self, key: str) -> None:
        """A statutory value must never ship as though it had been decided."""
        setting = SETTINGS_BY_KEY[key]
        assert setting.legal_review is True
        assert setting.confirmed is False

    def test_no_statutory_rates_are_stored_as_settings(self) -> None:
        """Rates belong in effective-dated statutory rule sets, not key/value rows.

        A rate in this catalogue would have no effective date, so historical
        payroll could not be reproduced after it changed.
        """
        rate_like = [
            s.key
            for s in SETTINGS
            if any(token in s.key for token in ("_rate", "_percent", "ceiling", "_slab"))
        ]
        assert rate_like == []

    def test_statutory_modules_default_to_empty(self) -> None:
        """Nothing statutory runs until it has been explicitly enabled and verified."""
        assert SETTINGS_BY_KEY["payroll.enabled_statutory_modules"].default == []
