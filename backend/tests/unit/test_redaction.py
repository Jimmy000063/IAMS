"""Tests for audit and log redaction.

The brief forbids logging or storing passwords, tokens, bank details and
unnecessary personal information. These are the tests for the two enforcement
points: the audit writer's redactor and the logging filter.
"""

from __future__ import annotations

import logging

import pytest

from app.core.logging import RedactingFilter
from app.modules.audit.service import REDACTED, redact_sensitive

pytestmark = pytest.mark.unit


class TestAuditRedaction:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "Password",
            "new_password",
            "password_hash",
            "secret",
            "access_token",
            "refresh_token",
            "api_key",
            "authorization",
            "account_number",
            "bank_account_number",
            "ifsc",
            "aadhaar",
            "pan_number",
        ],
    )
    def test_sensitive_keys_are_redacted(self, key: str) -> None:
        assert redact_sensitive({key: "s3cret-value"}) == {key: REDACTED}

    def test_ordinary_fields_pass_through_unchanged(self) -> None:
        payload = {"username": "r.sharma", "department": "Engineering", "days": 2}
        assert redact_sensitive(payload) == payload

    def test_redacts_inside_nested_structures(self) -> None:
        result = redact_sensitive(
            {
                "employee": {"name": "A", "bank_details": {"account_number": "123456789012"}},
                "sessions": [{"refresh_token": "abc"}, {"refresh_token": "def"}],
            }
        )
        assert result["employee"]["bank_details"]["account_number"] == REDACTED
        assert result["sessions"][0]["refresh_token"] == REDACTED
        assert result["sessions"][1]["refresh_token"] == REDACTED
        assert result["employee"]["name"] == "A"

    def test_a_sensitive_key_redacts_its_whole_subtree(self) -> None:
        """A container named for a secret is replaced entirely, not walked into."""
        assert redact_sensitive({"tokens": [{"value": "abc"}]}) == {"tokens": REDACTED}

    def test_redaction_is_deliberately_over_inclusive(self) -> None:
        """Matching by key name redacts harmless fields whose name collides.

        This is the intended trade-off - safe by default - so audit payloads
        avoid such names when the value is worth keeping. See
        ``auth.service.create_user``, which records
        ``temporary_credentials_issued`` rather than ``must_change_password``.
        """
        assert redact_sensitive({"must_change_password": True}) == {
            "must_change_password": REDACTED
        }
        assert redact_sensitive({"temporary_credentials_issued": True}) == {
            "temporary_credentials_issued": True
        }

    def test_recursion_is_bounded(self) -> None:
        """A deeply nested or cyclic-looking payload must not exhaust the stack."""
        payload: dict = {"level": 0}
        node = payload
        for depth in range(1, 40):
            node["child"] = {"level": depth}
            node = node["child"]
        result = redact_sensitive(payload)
        assert result is not None

    def test_non_dict_values_are_returned_as_is(self) -> None:
        assert redact_sensitive("plain") == "plain"
        assert redact_sensitive(7) == 7
        assert redact_sensitive(None) is None


class TestLogRedaction:
    def _render(self, message: str, *args: object) -> str:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=message,
            args=args,
            exc_info=None,
        )
        RedactingFilter().filter(record)
        return record.getMessage()

    def test_password_in_a_message_is_redacted(self) -> None:
        assert "hunter2" not in self._render('login password="hunter2" ok')

    def test_bearer_token_is_redacted(self) -> None:
        rendered = self._render("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def")
        assert "eyJhbGciOiJIUzI1NiJ9" not in rendered
        assert "[redacted]" in rendered

    def test_long_digit_runs_are_redacted(self) -> None:
        """Catches bank account and Aadhaar numbers that slip into a message."""
        rendered = self._render("credited to account 123456789012")
        assert "123456789012" not in rendered

    def test_short_numbers_survive(self) -> None:
        """Ordinary quantities must stay readable, or logs become useless."""
        assert "42" in self._render("processed 42 records")

    def test_ordinary_messages_are_untouched(self) -> None:
        assert self._render("payroll run 7 finalized") == "payroll run 7 finalized"

    def test_broken_format_string_does_not_crash_logging(self) -> None:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="missing %s %s",
            args=("only-one",),
            exc_info=None,
        )
        assert RedactingFilter().filter(record) is True
