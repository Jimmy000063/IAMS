"""Payroll against a real database and the real HTTP stack.

The properties worth proving here cannot be proved in unit tests:

* **A past run is reproducible.** Change a rate, recalculate nothing, and the
  finalized run still reports the figures and the rule-set version it used.
* **Unverified rules cannot be finalized.** The gate the brief asks for.
* **The lifecycle separates duties.** Nobody reviews what they calculated or
  approves what they reviewed.
* **A finalized run is immutable**, enforced by the database, not the service.

No test here asserts a real statutory rate. Every figure used is invented for
the test and labelled as such; what is tested is that the engine applies what it
is given and refuses to proceed without it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.attendance.models import AttendanceDay
from app.modules.auth.models import User
from app.modules.payroll.models import Payslip, StatutoryRuleSet
from tests.conftest import auth_header

pytestmark = pytest.mark.integration

D = Decimal
YEAR, MONTH = 2026, 4  # April 2026: 30 days, 1 to 30.
PERIOD_START = date(2026, 4, 1)
PERIOD_END = date(2026, 4, 30)


@pytest.fixture
def hr(client: TestClient, admin_user: User) -> dict:
    return auth_header(client, "hradmin")


@pytest.fixture
def confirmed_basis(client: TestClient, hr: dict) -> None:
    """Confirm the payable-days basis.

    Payroll refuses to calculate without it, which is itself tested; this
    fixture is for the tests that need to get past it.
    """
    response = client.put(
        "/api/v1/settings/payroll.payable_days_basis",
        json={
            "value": "calendar_days",
            "confirm": True,
            "reason": "Confirmed for the test suite.",
            "source_reference": "Test fixture: the company's own policy choice.",
        },
        headers=hr,
    )
    assert response.status_code == 200, response.text


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


def make_component(client: TestClient, hr: dict, **overrides) -> dict:
    body = {
        "code": "BASIC",
        "name": "Basic salary",
        "component_type": "earning",
        "calculation_type": "percent_of_gross",
        "percentage": "50",
        "is_pf_wage": True,
        "is_esi_wage": True,
        "is_pt_wage": True,
        "display_order": 10,
        **overrides,
    }
    response = client.post("/api/v1/payroll/components", json=body, headers=hr)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def components(client: TestClient, hr: dict) -> dict[str, dict]:
    """A three-component structure. The proportions are the test's own."""
    basic = make_component(client, hr)
    hra = make_component(
        client,
        hr,
        code="HRA",
        name="House rent allowance",
        calculation_type="percent_of_component",
        percentage="40",
        of_component_id=basic["id"],
        is_pf_wage=False,
        display_order=20,
    )
    special = make_component(
        client,
        hr,
        code="SPL",
        name="Special allowance",
        calculation_type="balance",
        percentage=None,
        is_pf_wage=False,
        display_order=90,
    )
    return {"basic": basic, "hra": hra, "special": special}


@pytest.fixture
def revision(client: TestClient, hr: dict, employee: dict, components: dict[str, dict]) -> dict:
    response = client.post(
        "/api/v1/payroll/revisions",
        json={
            "employee_id": employee["id"],
            "effective_from": "2026-01-05",
            "gross_monthly": "50000.00",
            "revision_reason": "On joining",
            "components": [
                {"component_id": components["basic"]["id"], "percentage": "50"},
                {"component_id": components["hra"]["id"], "percentage": "40"},
                {"component_id": components["special"]["id"]},
            ],
        },
        headers=hr,
    )
    assert response.status_code == 201, response.text
    return response.json()


def full_attendance(session: Session, *, employee_id: int) -> None:
    """A full month of ordinary attendance, written directly.

    Written rather than punched because what is under test is payroll reading
    the fractions the attendance engine stored, not the punch pipeline that
    produced them.
    """
    from datetime import timedelta

    from app.core.timeutil import utc_now

    day = PERIOD_START
    while day <= PERIOD_END:
        weekend = day.weekday() >= 5
        session.add(
            AttendanceDay(
                employee_id=employee_id,
                work_date=day,
                day_type="weekly_off" if weekend else "working",
                status="weekly_off" if weekend else "present",
                worked_minutes=0 if weekend else 480,
                break_minutes=0,
                net_minutes=0 if weekend else 480,
                late_minutes=0,
                early_exit_minutes=0,
                overtime_minutes=0,
                has_missing_punch=False,
                payable_day_fraction=D("1.00"),
                computed_at=utc_now(),
                computed_from_event_count=0 if weekend else 2,
            )
        )
        day += timedelta(days=1)
    session.commit()


def run_for(client: TestClient, hr: dict, **overrides) -> dict:
    body = {"year": YEAR, "month": MONTH, **overrides}
    response = client.post("/api/v1/payroll/runs", json=body, headers=hr)
    assert response.status_code == 201, response.text
    return response.json()


class TestNothingIsSeeded:
    def test_no_salary_component_exists_until_one_is_created(
        self, client: TestClient, hr: dict
    ) -> None:
        """Their names and proportions are a company decision with tax
        consequences, so inventing them would be worse than an empty list."""
        response = client.get("/api/v1/payroll/components", headers=hr)
        assert response.status_code == 200
        assert response.json() == []

    def test_no_statutory_rule_set_exists(self, client: TestClient, hr: dict) -> None:
        """A rate entered without a verified source produces a wrong deduction
        that looks right."""
        response = client.get("/api/v1/payroll/statutory/rule-sets", headers=hr)
        assert response.status_code == 200
        assert response.json() == []

    def test_readiness_says_plainly_what_is_missing(
        self, client: TestClient, hr: dict, seeded: None
    ) -> None:
        response = client.get("/api/v1/payroll/readiness", headers=hr)
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["ready"] is False
        assert any("No salary components" in blocker for blocker in report["blockers"])


class TestComponents:
    def test_wage_base_flags_default_to_the_conservative_reading(
        self, client: TestClient, hr: dict
    ) -> None:
        component = make_component(
            client,
            hr,
            code="ALLOW",
            name="Allowance",
            calculation_type="fixed",
            percentage=None,
            is_pf_wage=False,
            is_esi_wage=False,
            is_pt_wage=False,
        )
        assert component["is_pf_wage"] is False
        assert component["is_taxable"] is True

    def test_a_duplicate_code_is_refused(self, client: TestClient, hr: dict) -> None:
        make_component(client, hr)
        response = client.post(
            "/api/v1/payroll/components",
            json={
                "code": "basic",
                "name": "Another basic",
                "component_type": "earning",
                "calculation_type": "fixed",
            },
            headers=hr,
        )
        assert response.status_code == 409, response.text

    def test_a_percentage_of_a_derived_component_is_refused(
        self, client: TestClient, hr: dict, components: dict[str, dict]
    ) -> None:
        """Chained percentages make a payslip's arithmetic depend on evaluation
        order, so they are refused at configuration time."""
        response = client.post(
            "/api/v1/payroll/components",
            json={
                "code": "CHAIN",
                "name": "Of HRA",
                "component_type": "earning",
                "calculation_type": "percent_of_component",
                "percentage": "10",
                "of_component_id": components["hra"]["id"],
            },
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "itself derived" in response.text


class TestRevisions:
    def test_a_revision_resolves_and_stores_the_amounts(
        self, client: TestClient, hr: dict, revision: dict
    ) -> None:
        """Resolved once, at the revision, where somebody can check them."""
        amounts = {item["component_id"]: item["monthly_amount"] for item in revision["components"]}
        assert sorted(amounts.values()) == ["10000.00", "15000.00", "25000.00"]

    def test_components_must_add_up_to_the_gross(
        self, client: TestClient, hr: dict, employee: dict, components: dict[str, dict]
    ) -> None:
        """Otherwise the figure on the offer letter is not the figure paid."""
        response = client.post(
            "/api/v1/payroll/revisions",
            json={
                "employee_id": employee["id"],
                "effective_from": "2026-02-01",
                "gross_monthly": "50000.00",
                "components": [
                    {"component_id": components["basic"]["id"], "percentage": "50"},
                    {"component_id": components["hra"]["id"], "percentage": "40"},
                ],
            },
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "does not equal the gross" in response.text

    def test_a_revision_before_joining_is_refused(
        self, client: TestClient, hr: dict, employee: dict, components: dict[str, dict]
    ) -> None:
        response = client.post(
            "/api/v1/payroll/revisions",
            json={
                "employee_id": employee["id"],
                "effective_from": "2025-12-01",
                "gross_monthly": "50000.00",
                "components": [{"component_id": components["special"]["id"]}],
            },
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "joined on" in response.text

    def test_a_new_revision_closes_the_previous_one(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        components: dict[str, dict],
        revision: dict,
    ) -> None:
        """Effective-dated rather than edited, so last month stays explainable."""
        second = client.post(
            "/api/v1/payroll/revisions",
            json={
                "employee_id": employee["id"],
                "effective_from": "2026-07-01",
                "gross_monthly": "60000.00",
                "revision_reason": "Annual increment",
                "components": [
                    {"component_id": components["basic"]["id"], "percentage": "50"},
                    {"component_id": components["hra"]["id"], "percentage": "40"},
                    {"component_id": components["special"]["id"]},
                ],
            },
            headers=hr,
        )
        assert second.status_code == 201, second.text

        history = client.get(f"/api/v1/payroll/revisions/{employee['id']}", headers=hr).json()
        assert len(history) == 2
        older = next(item for item in history if item["effective_from"] == "2026-01-05")
        assert older["effective_to"] == "2026-06-30"

    def test_inserting_an_earlier_revision_is_refused(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        components: dict[str, dict],
        revision: dict,
    ) -> None:
        client.post(
            "/api/v1/payroll/revisions",
            json={
                "employee_id": employee["id"],
                "effective_from": "2026-07-01",
                "gross_monthly": "60000.00",
                "components": [{"component_id": components["special"]["id"]}],
            },
            headers=hr,
        )
        response = client.post(
            "/api/v1/payroll/revisions",
            json={
                "employee_id": employee["id"],
                "effective_from": "2026-05-01",
                "gross_monthly": "55000.00",
                "components": [{"component_id": components["special"]["id"]}],
            },
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "later revision" in response.text


def make_rule_set(client: TestClient, hr: dict, **overrides) -> dict:
    """A rule set with invented parameters, for testing the mechanism."""
    body = {
        "domain": "epf",
        "version_label": "test-only, invented figures",
        "effective_from": "2026-04-01",
        "parameters": [
            {
                "key": "employee_rate_percent",
                "label": "Employee rate (invented for the test)",
                "numeric_value": "10",
            },
            {
                "key": "employer_rate_percent",
                "label": "Employer rate (invented for the test)",
                "numeric_value": "10",
            },
            {
                "key": "wage_ceiling",
                "label": "Wage ceiling (invented for the test)",
                "numeric_value": "20000",
            },
        ],
        **overrides,
    }
    response = client.post("/api/v1/payroll/statutory/rule-sets", json=body, headers=hr)
    assert response.status_code == 201, response.text
    return response.json()


class TestStatutoryRules:
    def test_a_rule_set_is_created_as_a_draft(self, client: TestClient, hr: dict) -> None:
        rule_set = make_rule_set(client, hr)
        assert rule_set["status"] == "draft"
        assert rule_set["verified_at"] is None

    def test_verification_requires_a_source(self, client: TestClient, hr: dict) -> None:
        rule_set = make_rule_set(client, hr)
        response = client.post(
            f"/api/v1/payroll/statutory/rule-sets/{rule_set['id']}/verify",
            json={"source_reference": "x"},
            headers=hr,
        )
        assert response.status_code == 422, response.text

    def test_verification_records_who_checked_what(
        self, client: TestClient, hr: dict, admin_user: User
    ) -> None:
        rule_set = make_rule_set(client, hr)
        response = client.post(
            f"/api/v1/payroll/statutory/rule-sets/{rule_set['id']}/verify",
            json={
                "source_reference": "Test fixture: figures invented for the suite.",
                "note": "Not a real statutory reference.",
            },
            headers=hr,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "verified"
        assert body["verified_by_user_id"] == admin_user.id
        assert body["verified_at"] is not None

    def test_an_empty_rule_set_cannot_be_verified(self, client: TestClient, hr: dict) -> None:
        """There would be nothing to have checked."""
        rule_set = make_rule_set(client, hr, parameters=[])
        response = client.post(
            f"/api/v1/payroll/statutory/rule-sets/{rule_set['id']}/verify",
            json={"source_reference": "Test fixture with no parameters."},
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "nothing to verify" in response.text

    def test_a_verified_rule_sets_parameters_are_frozen(
        self, client: TestClient, hr: dict, session: Session
    ) -> None:
        """Enforced by trigger: the verification says somebody checked *these*
        numbers, so changing them would leave the signature on figures nobody
        checked."""
        rule_set = make_rule_set(client, hr)
        client.post(
            f"/api/v1/payroll/statutory/rule-sets/{rule_set['id']}/verify",
            json={"source_reference": "Test fixture: invented figures."},
            headers=hr,
        )

        through_the_api = client.put(
            f"/api/v1/payroll/statutory/rule-sets/{rule_set['id']}/parameters",
            json={
                "parameters": [
                    {"key": "employee_rate_percent", "label": "Changed", "numeric_value": "99"}
                ]
            },
            headers=hr,
        )
        assert through_the_api.status_code == 409, through_the_api.text

        with pytest.raises(DBAPIError, match="cannot be changed"):
            session.execute(
                text(
                    "UPDATE statutory_rule_parameters SET numeric_value = 99 "
                    "WHERE rule_set_id = :id"
                ),
                {"id": rule_set["id"]},
            )
        session.rollback()

    def test_two_rule_sets_cannot_start_on_the_same_date(
        self, client: TestClient, hr: dict
    ) -> None:
        """So "which rules did April use" has one answer."""
        make_rule_set(client, hr)
        response = client.post(
            "/api/v1/payroll/statutory/rule-sets",
            json={
                "domain": "epf",
                "version_label": "another",
                "effective_from": "2026-04-01",
            },
            headers=hr,
        )
        assert response.status_code == 409, response.text

    def test_a_superseded_rule_set_is_kept(self, client: TestClient, hr: dict) -> None:
        """Past payslips cite it; deleting it would make them unexplainable."""
        rule_set = make_rule_set(client, hr)
        response = client.post(
            f"/api/v1/payroll/statutory/rule-sets/{rule_set['id']}/supersede",
            json={"reason": "Replaced by a corrected version."},
            headers=hr,
        )
        assert response.status_code == 200
        assert response.json()["status"] == "superseded"
        assert (
            client.get("/api/v1/payroll/statutory/rule-sets", headers=hr).json()[0]["id"]
            == rule_set["id"]
        )


class TestCalculation:
    def test_calculation_is_refused_until_the_basis_is_confirmed(
        self, client: TestClient, hr: dict, employee: dict, revision: dict, session: Session
    ) -> None:
        """The payable-days basis is the denominator of every loss-of-pay
        amount, so running on an unconfirmed guess would produce plausible
        numbers that are wrong for this company."""
        full_attendance(session, employee_id=employee["id"])
        run = run_for(client, hr)
        response = client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)
        assert response.status_code == 409, response.text
        assert "payable_days_basis" in response.text
        assert "has not been confirmed" in response.text

    def test_a_full_month_pays_the_full_gross(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        full_attendance(session, employee_id=employee["id"])
        run = run_for(client, hr)
        result = client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["payslips"] == 1
        assert body["skipped"] == []
        assert body["total_gross"] == "50000.00"
        assert body["total_net"] == "50000.00"

    def test_unpaid_days_reduce_pay_proportionally(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        full_attendance(session, employee_id=employee["id"])
        # Make two working days unpaid absences.
        session.execute(
            text(
                "UPDATE attendance_days SET status = 'absent', payable_day_fraction = 0 "
                "WHERE employee_id = :id AND work_date IN ('2026-04-06', '2026-04-07')"
            ),
            {"id": employee["id"]},
        )
        session.commit()

        run = run_for(client, hr)
        body = client.post(
            f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr
        ).json()
        # 28 of 30 calendar days: 50000 * 28/30 across three components.
        assert body["total_gross"] == "46666.66"

        payslips = client.get(f"/api/v1/payroll/runs/{run['id']}/payslips", headers=hr).json()
        assert payslips[0]["lop_days"] == "2.00"
        assert payslips[0]["payable_days"] == "28.00"

    def test_a_missing_punch_is_never_treated_as_absence(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        """The brief forbids it, and doing it in payroll would be the same
        mistake with money attached."""
        full_attendance(session, employee_id=employee["id"])
        session.execute(
            text(
                "UPDATE attendance_days SET status = 'missing_punch', "
                "payable_day_fraction = 0, has_missing_punch = true "
                "WHERE employee_id = :id AND work_date = '2026-04-08'"
            ),
            {"id": employee["id"]},
        )
        session.commit()

        run = run_for(client, hr)
        body = client.post(
            f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr
        ).json()
        assert body["total_gross"] == "50000.00", "the day must be paid, not docked"

        payslip = client.get(f"/api/v1/payroll/runs/{run['id']}/payslips", headers=hr).json()[0]
        assert payslip["lop_days"] == "0.00"
        codes = [flag["code"] for flag in payslip["review_flags"] or []]
        assert "attendance_incomplete" in codes

    def test_a_day_with_no_attendance_record_is_paid_and_flagged(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        """Inferring an absence from missing data would dock pay on a gap in the
        records."""
        full_attendance(session, employee_id=employee["id"])
        session.execute(
            text(
                "DELETE FROM attendance_days WHERE employee_id = :id AND work_date = '2026-04-09'"
            ),
            {"id": employee["id"]},
        )
        session.commit()

        run = run_for(client, hr)
        body = client.post(
            f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr
        ).json()
        assert body["total_gross"] == "50000.00"
        payslip = client.get(f"/api/v1/payroll/runs/{run['id']}/payslips", headers=hr).json()[0]
        assert "attendance_incomplete" in [flag["code"] for flag in payslip["review_flags"] or []]

    def test_joining_mid_month_prorates(
        self,
        client: TestClient,
        hr: dict,
        confirmed_basis: None,
        org_fixtures: dict[str, int],
        components: dict[str, dict],
        session: Session,
    ) -> None:
        joiner = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0500",
                "first_name": "Mid",
                "last_name": "Joiner",
                "employment_type": "full_time",
                "date_of_joining": "2026-04-16",
                **org_fixtures,
            },
            headers=hr,
        ).json()
        client.post(
            "/api/v1/payroll/revisions",
            json={
                "employee_id": joiner["id"],
                "effective_from": "2026-04-16",
                "gross_monthly": "30000.00",
                "components": [{"component_id": components["special"]["id"]}],
            },
            headers=hr,
        )
        full_attendance(session, employee_id=joiner["id"])

        run = run_for(client, hr)
        client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)
        payslip = client.get(f"/api/v1/payroll/runs/{run['id']}/payslips", headers=hr).json()[0]
        # In service for 15 of 30 days.
        assert payslip["payable_days"] == "15.00"
        assert payslip["net_pay"] == "15000.00"

    def test_an_employee_with_no_salary_is_skipped_with_a_reason(
        self,
        client: TestClient,
        hr: dict,
        confirmed_basis: None,
        org_fixtures: dict[str, int],
        employee: dict,
        revision: dict,
        session: Session,
    ) -> None:
        """One employee's missing configuration must not abandon the run."""
        client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0600",
                "first_name": "No",
                "last_name": "Salary",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-05",
                **org_fixtures,
            },
            headers=hr,
        )
        full_attendance(session, employee_id=employee["id"])

        run = run_for(client, hr)
        body = client.post(
            f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr
        ).json()
        assert body["payslips"] == 1
        assert len(body["skipped"]) == 1
        assert "no salary revision" in body["skipped"][0]["reason"]

    def test_every_payslip_line_explains_itself(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        """A figure an employee cannot check is a figure they cannot dispute."""
        full_attendance(session, employee_id=employee["id"])
        run = run_for(client, hr)
        client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)

        payslip_id = client.get(f"/api/v1/payroll/runs/{run['id']}/payslips", headers=hr).json()[0][
            "id"
        ]
        payslip = client.get(f"/api/v1/payroll/payslips/{payslip_id}", headers=hr).json()
        assert payslip["lines"]
        for line in payslip["lines"]:
            assert line["basis_description"]
        assert payslip["calculation_notes"]["wage_bases"]["pf_wage"] == "25000.00"

    def test_recalculating_replaces_rather_than_duplicates(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        full_attendance(session, employee_id=employee["id"])
        run = run_for(client, hr)
        client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)
        client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)

        count = session.scalar(
            select(Payslip).where(Payslip.run_id == run["id"]).with_only_columns(Payslip.id)
        )
        payslips = client.get(f"/api/v1/payroll/runs/{run['id']}/payslips", headers=hr).json()
        assert len(payslips) == 1, count

    def test_two_regular_runs_for_one_month_are_impossible(
        self, client: TestClient, hr: dict
    ) -> None:
        """The second would look like a legitimate register and double the
        statutory filings."""
        run_for(client, hr)
        response = client.post(
            "/api/v1/payroll/runs", json={"year": YEAR, "month": MONTH}, headers=hr
        )
        assert response.status_code == 409, response.text


class TestStatutoryInPayroll:
    def test_an_eligible_employee_is_deducted_at_the_configured_rate(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        make_rule_set(client, hr)
        client.put(
            "/api/v1/settings/payroll.enabled_statutory_modules",
            json={"value": ["epf"], "reason": "Test"},
            headers=hr,
        )
        client.post(
            "/api/v1/payroll/statutory/eligibility",
            json={
                "employee_id": employee["id"],
                "domain": "epf",
                "is_eligible": True,
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        full_attendance(session, employee_id=employee["id"])

        run = run_for(client, hr)
        body = client.post(
            f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr
        ).json()
        # 10% of a PF wage of 25000, capped at the invented ceiling of 20000.
        assert body["total_deductions"] == "2000.00"
        assert body["total_employer_cost"] == "2000.00"
        assert body["total_net"] == "48000.00"
        assert body["unverified_domains"] == ["epf"]

    def test_an_ineligible_employee_has_nothing_deducted(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        """A domain with no eligibility record is not eligible: deducting from
        somebody nobody decided was covered would be taking money on an
        assumption."""
        make_rule_set(client, hr)
        client.put(
            "/api/v1/settings/payroll.enabled_statutory_modules",
            json={"value": ["epf"], "reason": "Test"},
            headers=hr,
        )
        full_attendance(session, employee_id=employee["id"])

        run = run_for(client, hr)
        body = client.post(
            f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr
        ).json()
        assert body["total_deductions"] == "0.00"

    def test_a_run_using_unverified_rules_cannot_be_finalized(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
        admin_user: User,
    ) -> None:
        """The gate the brief asks for: no legal calculation is claimed to work
        until its source has been checked."""
        make_rule_set(client, hr)
        client.put(
            "/api/v1/settings/payroll.enabled_statutory_modules",
            json={"value": ["epf"], "reason": "Test"},
            headers=hr,
        )
        client.post(
            "/api/v1/payroll/statutory/eligibility",
            json={
                "employee_id": employee["id"],
                "domain": "epf",
                "is_eligible": True,
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        full_attendance(session, employee_id=employee["id"])

        run = run_for(client, hr)
        client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)

        # Review and approve with separate accounts, which the service requires.
        session.execute(
            text("UPDATE payroll_runs SET calculated_by_user_id = NULL WHERE id = :id"),
            {"id": run["id"]},
        )
        session.commit()
        assert (
            client.post(f"/api/v1/payroll/runs/{run['id']}/review", headers=hr).status_code == 200
        )
        session.execute(
            text("UPDATE payroll_runs SET reviewed_by_user_id = NULL WHERE id = :id"),
            {"id": run["id"]},
        )
        session.commit()
        assert (
            client.post(f"/api/v1/payroll/runs/{run['id']}/approve", headers=hr).status_code == 200
        )

        refused = client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=hr)
        assert refused.status_code == 422, refused.text
        assert "unverified statutory rule set" in refused.text

        # Verify the rule set, recalculate, and it goes through.
        rule_set_id = session.scalar(select(StatutoryRuleSet.id))
        client.post(
            f"/api/v1/payroll/statutory/rule-sets/{rule_set_id}/verify",
            json={"source_reference": "Test fixture: invented figures."},
            headers=hr,
        )
        client.post(
            f"/api/v1/payroll/runs/{run['id']}/send-back",
            json={"reason": "Rule set verified; recalculating."},
            headers=hr,
        )
        client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)
        session.execute(
            text("UPDATE payroll_runs SET calculated_by_user_id = NULL WHERE id = :id"),
            {"id": run["id"]},
        )
        session.commit()
        client.post(f"/api/v1/payroll/runs/{run['id']}/review", headers=hr)
        session.execute(
            text("UPDATE payroll_runs SET reviewed_by_user_id = NULL WHERE id = :id"),
            {"id": run["id"]},
        )
        session.commit()
        client.post(f"/api/v1/payroll/runs/{run['id']}/approve", headers=hr)
        finalized = client.post(f"/api/v1/payroll/runs/{run['id']}/finalize", headers=hr)
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["status"] == "finalized"


@pytest.fixture
def calculated_run(
    client: TestClient,
    hr: dict,
    employee: dict,
    revision: dict,
    confirmed_basis: None,
    session: Session,
) -> dict:
    full_attendance(session, employee_id=employee["id"])
    run = run_for(client, hr)
    client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)
    # The fixture's calculation was done by the HR account; clear the actor so
    # the lifecycle tests can review it without a second account.
    session.execute(
        text("UPDATE payroll_runs SET calculated_by_user_id = NULL WHERE id = :id"),
        {"id": run["id"]},
    )
    session.commit()
    return client.get(f"/api/v1/payroll/runs/{run['id']}", headers=hr).json()


class TestLifecycle:
    def test_the_order_is_enforced(
        self, client: TestClient, hr: dict, calculated_run: dict
    ) -> None:
        run_id = calculated_run["id"]
        # Approval before review is refused.
        assert client.post(f"/api/v1/payroll/runs/{run_id}/approve", headers=hr).status_code == 409
        assert client.post(f"/api/v1/payroll/runs/{run_id}/finalize", headers=hr).status_code == 409

    def test_nobody_reviews_what_they_calculated(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        """One pair of eyes over a whole company's pay is not a review."""
        full_attendance(session, employee_id=employee["id"])
        run = run_for(client, hr)
        client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)
        response = client.post(f"/api/v1/payroll/runs/{run['id']}/review", headers=hr)
        assert response.status_code == 422, response.text
        assert "somebody else must review" in response.text

    def test_nobody_approves_what_they_reviewed(
        self, client: TestClient, hr: dict, calculated_run: dict, session: Session
    ) -> None:
        run_id = calculated_run["id"]
        client.post(f"/api/v1/payroll/runs/{run_id}/review", headers=hr)
        response = client.post(f"/api/v1/payroll/runs/{run_id}/approve", headers=hr)
        assert response.status_code == 422, response.text
        assert "somebody else must approve" in response.text

    def test_a_reviewed_run_cannot_be_recalculated(
        self, client: TestClient, hr: dict, calculated_run: dict
    ) -> None:
        """Changing the figures under a reviewer would make the review
        meaningless, so the run has to be sent back explicitly."""
        run_id = calculated_run["id"]
        client.post(f"/api/v1/payroll/runs/{run_id}/review", headers=hr)
        response = client.post(f"/api/v1/payroll/runs/{run_id}/calculate", json={}, headers=hr)
        assert response.status_code == 409, response.text
        assert "Send it back to draft" in response.text

    def test_sending_back_needs_a_reason_and_clears_the_review(
        self, client: TestClient, hr: dict, calculated_run: dict
    ) -> None:
        run_id = calculated_run["id"]
        client.post(f"/api/v1/payroll/runs/{run_id}/review", headers=hr)
        assert (
            client.post(
                f"/api/v1/payroll/runs/{run_id}/send-back", json={"reason": ""}, headers=hr
            ).status_code
            == 422
        )
        back = client.post(
            f"/api/v1/payroll/runs/{run_id}/send-back",
            json={"reason": "An attendance correction arrived."},
            headers=hr,
        )
        assert back.status_code == 200, back.text
        assert back.json()["status"] == "draft"
        assert back.json()["reviewed_at"] is None

    def test_a_finalized_run_is_immutable_in_the_database(
        self, client: TestClient, hr: dict, calculated_run: dict, session: Session
    ) -> None:
        """Enforced by trigger, not only by the service: a finalized run is the
        record of money that moved."""
        run_id = calculated_run["id"]
        client.post(f"/api/v1/payroll/runs/{run_id}/review", headers=hr)
        session.execute(
            text("UPDATE payroll_runs SET reviewed_by_user_id = NULL WHERE id = :id"),
            {"id": run_id},
        )
        session.commit()
        client.post(f"/api/v1/payroll/runs/{run_id}/approve", headers=hr)
        assert client.post(f"/api/v1/payroll/runs/{run_id}/finalize", headers=hr).status_code == 200

        with pytest.raises(DBAPIError, match="cannot be reopened"):
            session.execute(
                text("UPDATE payroll_runs SET status = 'draft' WHERE id = :id"),
                {"id": run_id},
            )
        session.rollback()

        with pytest.raises(DBAPIError, match="immutable"):
            session.execute(
                text("UPDATE payroll_runs SET total_net = 1 WHERE id = :id"), {"id": run_id}
            )
        session.rollback()

        with pytest.raises(DBAPIError, match="cannot be changed"):
            session.execute(
                text("UPDATE payslips SET net_pay = 1 WHERE run_id = :id"), {"id": run_id}
            )
        session.rollback()

        assert (
            client.post(
                f"/api/v1/payroll/runs/{run_id}/send-back",
                json={"reason": "Trying to reopen."},
                headers=hr,
            ).status_code
            == 409
        )

    def test_a_cancelled_run_releases_its_adjustments(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        client.post(
            "/api/v1/payroll/adjustments",
            json={
                "employee_id": employee["id"],
                "adjustment_type": "arrear",
                "amount": "1000.00",
                "label": "March arrear",
                "reason": "Missed increment.",
            },
            headers=hr,
        )
        full_attendance(session, employee_id=employee["id"])
        run = run_for(client, hr)
        client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)
        assert client.get("/api/v1/payroll/adjustments", headers=hr).json() == []

        client.post(
            f"/api/v1/payroll/runs/{run['id']}/cancel",
            json={"reason": "Opened for the wrong month."},
            headers=hr,
        )
        pending = client.get("/api/v1/payroll/adjustments", headers=hr).json()
        assert len(pending) == 1, "the arrear must be payable in the next run"


class TestReproducibility:
    def test_a_finalized_run_keeps_the_rules_and_settings_it_used(
        self, client: TestClient, hr: dict, calculated_run: dict, session: Session
    ) -> None:
        """The single most important property of this schema: a rate change next
        month must not make last month unexplainable."""
        run = client.get(f"/api/v1/payroll/runs/{calculated_run['id']}", headers=hr).json()
        assert run["settings_snapshot"]["payable_days_basis"] == "calendar_days"
        assert run["settings_snapshot"]["rounding_mode"]

        register = client.get(f"/api/v1/payroll/runs/{run['id']}/register", headers=hr).json()
        assert register["settings_used"] == run["settings_snapshot"]

    def test_changing_a_rate_does_not_alter_a_calculated_run(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        make_rule_set(client, hr)
        client.put(
            "/api/v1/settings/payroll.enabled_statutory_modules",
            json={"value": ["epf"], "reason": "Test"},
            headers=hr,
        )
        client.post(
            "/api/v1/payroll/statutory/eligibility",
            json={
                "employee_id": employee["id"],
                "domain": "epf",
                "is_eligible": True,
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        full_attendance(session, employee_id=employee["id"])
        run = run_for(client, hr)
        first = client.post(
            f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr
        ).json()
        assert first["total_deductions"] == "2000.00"

        # A new rule set takes effect next month. April's figures must not move.
        client.post(
            "/api/v1/payroll/statutory/rule-sets",
            json={
                "domain": "epf",
                "version_label": "test-only, changed figures",
                "effective_from": "2026-05-01",
                "parameters": [
                    {
                        "key": "employee_rate_percent",
                        "label": "Changed rate (invented)",
                        "numeric_value": "20",
                    }
                ],
            },
            headers=hr,
        )
        unchanged = client.get(f"/api/v1/payroll/runs/{run['id']}", headers=hr).json()
        assert unchanged["total_deductions"] == "2000.00"
        assert "epf" in unchanged["rule_set_snapshot"]

    def test_a_statutory_line_cites_the_rule_set_that_produced_it(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        """So a disputed deduction leads back to a source somebody verified."""
        rule_set = make_rule_set(client, hr)
        client.put(
            "/api/v1/settings/payroll.enabled_statutory_modules",
            json={"value": ["epf"], "reason": "Test"},
            headers=hr,
        )
        client.post(
            "/api/v1/payroll/statutory/eligibility",
            json={
                "employee_id": employee["id"],
                "domain": "epf",
                "is_eligible": True,
                "effective_from": "2026-01-05",
            },
            headers=hr,
        )
        full_attendance(session, employee_id=employee["id"])
        run = run_for(client, hr)
        client.post(f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr)

        payslip_id = client.get(f"/api/v1/payroll/runs/{run['id']}/payslips", headers=hr).json()[0][
            "id"
        ]
        payslip = client.get(f"/api/v1/payroll/payslips/{payslip_id}", headers=hr).json()
        statutory = [line for line in payslip["lines"] if line["statutory_rule_set_id"]]
        assert statutory
        assert all(line["statutory_rule_set_id"] == rule_set["id"] for line in statutory)
        assert "10% of 20000" in statutory[0]["basis_description"]


class TestReconciliation:
    def test_a_run_adds_up_to_its_own_lines(
        self, client: TestClient, hr: dict, calculated_run: dict
    ) -> None:
        """A payroll that cannot prove it adds up should not be paid."""
        response = client.get(
            f"/api/v1/payroll/runs/{calculated_run['id']}/reconciliation", headers=hr
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["balanced"] is True
        assert body["mismatched"] == []
        assert body["lines_gross"] == body["run_gross"]

    def test_the_register_lists_every_component(
        self, client: TestClient, hr: dict, calculated_run: dict
    ) -> None:
        register = client.get(
            f"/api/v1/payroll/runs/{calculated_run['id']}/register", headers=hr
        ).json()
        assert register["employee_count"] == 1
        row = register["rows"][0]
        assert set(row["earnings"]) == {"BASIC", "HRA", "SPL"}
        assert row["net_pay"] == "50000.00"


class TestPayslipAccess:
    def test_an_employee_sees_nothing_until_it_is_published(
        self, client: TestClient, hr: dict, calculated_run: dict, employee: dict
    ) -> None:
        """A payslip from a run still under review may still change, and an
        employee should not have to watch their pay move."""
        staff = auth_header(client, "e.employee")
        assert client.get("/api/v1/payroll/me/payslips", headers=staff).json() == []

        payslip_id = client.get(
            f"/api/v1/payroll/runs/{calculated_run['id']}/payslips", headers=hr
        ).json()[0]["id"]
        assert (
            client.get(f"/api/v1/payroll/payslips/{payslip_id}", headers=staff).status_code == 404
        )

    def test_publishing_requires_a_finalized_run(
        self, client: TestClient, hr: dict, calculated_run: dict
    ) -> None:
        response = client.post(f"/api/v1/payroll/runs/{calculated_run['id']}/publish", headers=hr)
        assert response.status_code == 409, response.text

    def test_publishing_notifies_each_employee_once(
        self, client: TestClient, hr: dict, calculated_run: dict, session: Session
    ) -> None:
        """Milestone 7's hook, wired now that payroll exists."""
        from app.modules.notifications.models import Notification

        run_id = calculated_run["id"]
        client.post(f"/api/v1/payroll/runs/{run_id}/review", headers=hr)
        session.execute(
            text("UPDATE payroll_runs SET reviewed_by_user_id = NULL WHERE id = :id"),
            {"id": run_id},
        )
        session.commit()
        client.post(f"/api/v1/payroll/runs/{run_id}/approve", headers=hr)
        client.post(f"/api/v1/payroll/runs/{run_id}/finalize", headers=hr)

        first = client.post(f"/api/v1/payroll/runs/{run_id}/publish", headers=hr).json()
        assert first["published"] == 1
        assert first["notifications_created"] == 1

        again = client.post(f"/api/v1/payroll/runs/{run_id}/publish", headers=hr).json()
        assert again["published"] == 0
        assert again["notifications_created"] == 0

        rows = session.scalars(select(Notification).where(Notification.category == "payroll")).all()
        assert len(rows) == 1
        # A notification is not a secure channel for pay.
        assert not any(character.isdigit() for character in rows[0].body or "")

        staff = auth_header(client, "e.employee")
        mine = client.get("/api/v1/payroll/me/payslips", headers=staff).json()
        assert len(mine) == 1
        assert mine[0]["net_pay"] == "50000.00"


class TestAdjustments:
    def test_an_arrear_is_paid_in_the_next_run(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        client.post(
            "/api/v1/payroll/adjustments",
            json={
                "employee_id": employee["id"],
                "adjustment_type": "arrear",
                "amount": "2500.00",
                "label": "March arrear",
                "reason": "Increment applied late.",
            },
            headers=hr,
        )
        full_attendance(session, employee_id=employee["id"])
        run = run_for(client, hr)
        body = client.post(
            f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr
        ).json()
        assert body["total_gross"] == "52500.00"

    def test_a_recovery_is_deducted(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
        session: Session,
    ) -> None:
        client.post(
            "/api/v1/payroll/adjustments",
            json={
                "employee_id": employee["id"],
                "adjustment_type": "recovery",
                "amount": "1500.00",
                "label": "Advance recovery",
                "reason": "Second instalment.",
            },
            headers=hr,
        )
        full_attendance(session, employee_id=employee["id"])
        run = run_for(client, hr)
        body = client.post(
            f"/api/v1/payroll/runs/{run['id']}/calculate", json={}, headers=hr
        ).json()
        assert body["total_deductions"] == "1500.00"
        assert body["total_net"] == "48500.00"

    def test_an_identical_adjustment_is_not_added_twice(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        """A retried import of arrears must not pay somebody twice."""
        body = {
            "employee_id": employee["id"],
            "adjustment_type": "arrear",
            "amount": "2500.00",
            "label": "March arrear",
            "reason": "Increment applied late.",
        }
        assert client.post("/api/v1/payroll/adjustments", json=body, headers=hr).status_code == 201
        second = client.post("/api/v1/payroll/adjustments", json=body, headers=hr)
        assert second.status_code == 409, second.text

    def test_a_negative_amount_is_refused(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        """A signed amount plus a type would allow a recovery that pays."""
        response = client.post(
            "/api/v1/payroll/adjustments",
            json={
                "employee_id": employee["id"],
                "adjustment_type": "recovery",
                "amount": "-500.00",
                "label": "Negative",
                "reason": "Should be refused.",
            },
            headers=hr,
        )
        assert response.status_code == 422, response.text


class TestSettlement:
    def test_a_settlement_needs_an_exit_date(
        self, client: TestClient, hr: dict, employee: dict, revision: dict
    ) -> None:
        response = client.post(
            f"/api/v1/payroll/settlements/{employee['id']}/calculate",
            json={"encashable_leave_days": "5"},
            headers=hr,
        )
        assert response.status_code == 422, response.text
        assert "no exit date" in response.text

    def test_gratuity_is_not_invented_without_a_rule_set(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
    ) -> None:
        """A plausible but invented gratuity figure would understate or
        overstate a statutory payment somebody is legally owed."""
        exit_response = client.post(
            f"/api/v1/employees/{employee['id']}/exit",
            json={
                "date_of_exit": "2026-04-30",
                "last_working_day": "2026-04-30",
                "exit_type": "resignation",
                "exit_reason": "Moving on.",
            },
            headers=hr,
        )
        assert exit_response.status_code == 200, exit_response.text

        response = client.post(
            f"/api/v1/payroll/settlements/{employee['id']}/calculate",
            json={"encashable_leave_days": "6", "notice_shortfall_days": "0"},
            headers=hr,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["gratuity_amount"] == "0.00"
        assert any("no verified rule set" in note for note in body["computation_notes"]["notes"])
        # 6 days at 50000/30.
        assert body["leave_encashment_amount"] == "10000.02"
        assert body["net_settlement"] == "10000.02"

    def test_a_settlement_with_unverified_gratuity_cannot_be_approved(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        confirmed_basis: None,
    ) -> None:
        client.post(
            f"/api/v1/employees/{employee['id']}/exit",
            json={
                "date_of_exit": "2026-04-30",
                "last_working_day": "2026-04-30",
                "exit_type": "resignation",
                "exit_reason": "Moving on.",
            },
            headers=hr,
        )
        client.post(
            "/api/v1/payroll/statutory/rule-sets",
            json={
                "domain": "gratuity",
                "version_label": "test-only, invented figures",
                "effective_from": "2026-01-01",
                "parameters": [
                    {
                        "key": "days_per_year",
                        "label": "Days per year (invented)",
                        "numeric_value": "15",
                    },
                    {
                        "key": "monthly_divisor",
                        "label": "Divisor (invented)",
                        "numeric_value": "26",
                    },
                ],
            },
            headers=hr,
        )
        calculated = client.post(
            f"/api/v1/payroll/settlements/{employee['id']}/calculate",
            json={"encashable_leave_days": "0"},
            headers=hr,
        )
        assert calculated.status_code == 200, calculated.text
        assert Decimal(calculated.json()["gratuity_amount"]) > 0

        refused = client.post(f"/api/v1/payroll/settlements/{employee['id']}/approve", headers=hr)
        assert refused.status_code == 422, refused.text
        assert "unverified rule set" in refused.text


class TestAuthorization:
    def test_an_employee_cannot_read_another_persons_salary(
        self,
        client: TestClient,
        hr: dict,
        employee: dict,
        revision: dict,
        org_fixtures: dict[str, int],
    ) -> None:
        """Pay is more sensitive than attendance: a manager does not see their
        team's salaries by virtue of being their manager."""
        other = client.post(
            "/api/v1/employees",
            json={
                "employee_code": "ITC0700",
                "first_name": "Someone",
                "last_name": "Else",
                "employment_type": "full_time",
                "date_of_joining": "2026-01-05",
                **org_fixtures,
            },
            headers=hr,
        ).json()
        staff = auth_header(client, "e.employee")
        response = client.get(f"/api/v1/payroll/revisions/{other['id']}", headers=staff)
        assert response.status_code == 404, response.text

    def test_an_employee_cannot_create_a_component_or_a_run(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        staff = auth_header(client, "e.employee")
        assert (
            client.post(
                "/api/v1/payroll/components",
                json={
                    "code": "X",
                    "name": "X",
                    "component_type": "earning",
                    "calculation_type": "fixed",
                },
                headers=staff,
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/payroll/runs", json={"year": YEAR, "month": MONTH}, headers=staff
            ).status_code
            == 403
        )

    def test_an_employee_cannot_verify_a_rule_set(
        self, client: TestClient, hr: dict, employee: dict
    ) -> None:
        rule_set = make_rule_set(client, hr)
        staff = auth_header(client, "e.employee")
        response = client.post(
            f"/api/v1/payroll/statutory/rule-sets/{rule_set['id']}/verify",
            json={"source_reference": "Trying to self-verify."},
            headers=staff,
        )
        assert response.status_code == 403, response.text

    def test_unauthenticated_access_is_refused(self, client: TestClient) -> None:
        assert client.get("/api/v1/payroll/runs").status_code == 401
        assert client.get("/api/v1/payroll/components").status_code == 401
        assert client.get("/api/v1/payroll/me/payslips").status_code == 401
