"""Device integration tests against a real database and the real HTTP stack.

These cover what the pure policy tests cannot: the signature-authenticated
webhook, deduplication through the unique constraint, the dated mapping that
stops a reused enrolment slot reattributing old punches, the reconciliation
path, the raw-event immutability trigger, and the authorization boundary around
all of it.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.modules.attendance.models import AttendanceDay, AttendanceEvent
from app.modules.auth.models import User
from app.modules.devices.models import Device, DeviceRawEvent
from tests.conftest import auth_header

pytestmark = pytest.mark.integration

WEBHOOK_SECRET = "a-long-random-webhook-secret-value"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def day_shift(client: TestClient, admin_user: User) -> dict:
    response = client.post(
        "/api/v1/shifts",
        json={
            "code": "GEN",
            "name": "General shift",
            "start_time": "09:30",
            "end_time": "18:30",
            "break_minutes": 60,
        },
        headers=auth_header(client, "hradmin"),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def night_shift(client: TestClient, admin_user: User) -> dict:
    response = client.post(
        "/api/v1/shifts",
        json={
            "code": "NIGHT",
            "name": "Night shift",
            "start_time": "22:00",
            "end_time": "06:00",
            "break_minutes": 0,
        },
        headers=auth_header(client, "hradmin"),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def employee(
    client: TestClient, admin_user: User, employee_user: User, org_fixtures: dict[str, int]
) -> dict:
    headers = auth_header(client, "hradmin")
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
        headers=headers,
    )
    assert created.status_code == 201, created.text
    record = created.json()
    client.post(
        f"/api/v1/employees/{record['id']}/link-account",
        json={"user_id": employee_user.id},
        headers=headers,
    )
    return record


@pytest.fixture
def second_employee(client: TestClient, admin_user: User, org_fixtures: dict[str, int]) -> dict:
    created = client.post(
        "/api/v1/employees",
        json={
            "employee_code": "ITC0002",
            "first_name": "Meera",
            "last_name": "Iyer",
            "employment_type": "full_time",
            "date_of_joining": "2026-01-05",
            **org_fixtures,
        },
        headers=auth_header(client, "hradmin"),
    )
    assert created.status_code == 201, created.text
    return created.json()


def make_device(
    client: TestClient,
    *,
    code: str = "GATE1",
    adapter_type: str = "webhook",
    direction_mode: str = "alternate",
    config: dict | None = None,
    secret: str | None = WEBHOOK_SECRET,
    work_location_id: int | None = None,
) -> dict:
    payload: dict = {
        "code": code,
        "name": f"{code} reader",
        "adapter_type": adapter_type,
        "direction_mode": direction_mode,
        "config": config
        if config is not None
        else {
            "field_mapping": {
                "employee_ref": "user_id",
                "timestamp": "punch_time",
                "timestamp_format": "ymd_hms",
                "event_id": "id",
            }
        },
    }
    if secret is not None and adapter_type == "webhook":
        payload["webhook_secret"] = secret
    if work_location_id is not None:
        payload["work_location_id"] = work_location_id

    response = client.post("/api/v1/devices", json=payload, headers=auth_header(client, "hradmin"))
    assert response.status_code == 201, response.text
    return response.json()


def map_identifier(
    client: TestClient,
    device_id: int,
    *,
    reference: str,
    employee_id: int,
    effective_from: str = "2026-01-05",
    effective_to: str | None = None,
) -> dict:
    payload: dict = {
        "external_employee_ref": reference,
        "employee_id": employee_id,
        "effective_from": effective_from,
    }
    if effective_to:
        payload["effective_to"] = effective_to
    response = client.post(
        f"/api/v1/devices/{device_id}/mappings",
        json=payload,
        headers=auth_header(client, "hradmin"),
    )
    assert response.status_code == 201, response.text
    return response.json()


def deliver(
    client: TestClient,
    device_code: str,
    events: list[dict] | dict,
    *,
    secret: str = WEBHOOK_SECRET,
    sign: bool = True,
    header: str = "X-Device-Signature",
) -> object:
    """POST a webhook delivery, signed the way a device would sign it."""
    body = json.dumps(events).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if sign:
        headers[header] = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(f"/api/v1/devices/webhook/{device_code}", content=body, headers=headers)


def assign_shift(client: TestClient, *, employee_id: int, shift_id: int) -> None:
    response = client.post(
        "/api/v1/shifts/assignments",
        json={
            "employee_id": employee_id,
            "shift_id": shift_id,
            "effective_from": "2026-01-05",
        },
        headers=auth_header(client, "hradmin"),
    )
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# Adapter catalogue
# ---------------------------------------------------------------------------


class TestAdapterCatalogue:
    def test_catalogue_states_each_adapter_maturity(
        self, client: TestClient, admin_user: User
    ) -> None:
        response = client.get("/api/v1/devices/adapters", headers=auth_header(client, "hradmin"))
        assert response.status_code == 200, response.text
        body = response.json()

        maturity = {item["adapter_type"]: item["maturity"] for item in body["adapters"]}
        assert maturity["csv_upload"] == "tested"
        assert maturity["webhook"] == "tested"
        # Polling is implemented but has never seen real hardware. Saying so is
        # the point of the field.
        assert maturity["rest_poll"] == "generic"
        assert maturity["simulator"] == "development_only"

    def test_no_adapter_claims_a_named_biometric_product(
        self, client: TestClient, admin_user: User
    ) -> None:
        """The brief forbids claiming a machine is integrated before testing it."""
        body = client.get("/api/v1/devices/adapters", headers=auth_header(client, "hradmin")).json()

        notes = " ".join(body["vendor_integration_notes"]).lower()
        assert "no adapter is written against any specific biometric" in notes
        assert "protocol documentation and a test unit are both required" in notes

        for adapter in body["adapters"]:
            assert adapter["maturity_meaning"]
            if adapter["maturity"] != "tested":
                assert adapter["caveats"], "a non-tested adapter must carry caveats"

    def test_catalogue_lists_timestamp_formats_rather_than_guessing(
        self, client: TestClient, admin_user: User
    ) -> None:
        body = client.get("/api/v1/devices/adapters", headers=auth_header(client, "hradmin")).json()
        assert "dmy_hms_slash" in body["timestamp_formats"]
        assert "mdy_hms_slash" in body["timestamp_formats"]


# ---------------------------------------------------------------------------
# Device registration
# ---------------------------------------------------------------------------


class TestDeviceRegistration:
    def test_a_device_secret_is_never_returned(self, client: TestClient, admin_user: User) -> None:
        device = make_device(client)
        assert device["has_webhook_secret"] is True
        assert "webhook_secret" not in device
        assert "webhook_secret_encrypted" not in device

        fetched = client.get(
            f"/api/v1/devices/{device['id']}", headers=auth_header(client, "hradmin")
        ).json()
        assert "webhook_secret" not in fetched
        assert WEBHOOK_SECRET not in json.dumps(fetched)

    def test_a_secret_is_stored_encrypted_not_in_plaintext(
        self, client: TestClient, admin_user: User, session: Session
    ) -> None:
        make_device(client)
        matches = session.scalar(
            select(func.count())
            .select_from(Device)
            .where(text("position(:needle in coalesce(webhook_secret_encrypted::text, '')) > 0"))
            .params(needle=WEBHOOK_SECRET)
        )
        assert matches == 0, "the webhook secret must not be readable in the database"

    def test_an_unknown_timestamp_format_is_refused_at_registration(
        self, client: TestClient, admin_user: User
    ) -> None:
        """Caught now, not at 2am when the scheduled sync runs."""
        response = client.post(
            "/api/v1/devices",
            json={
                "code": "BAD",
                "name": "Misconfigured",
                "adapter_type": "webhook",
                "webhook_secret": WEBHOOK_SECRET,
                "config": {"field_mapping": {"timestamp_format": "whatever"}},
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422, response.text
        assert "timestamp_format" in response.text

    def test_a_short_webhook_secret_is_refused(self, client: TestClient, admin_user: User) -> None:
        response = client.post(
            "/api/v1/devices",
            json={
                "code": "WEAK",
                "name": "Weak secret",
                "adapter_type": "webhook",
                "webhook_secret": "short",
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_a_duplicate_device_code_is_refused(self, client: TestClient, admin_user: User) -> None:
        make_device(client, code="GATE1")
        response = client.post(
            "/api/v1/devices",
            json={
                "code": "gate1",
                "name": "Same code, lower case",
                "adapter_type": "webhook",
                "webhook_secret": WEBHOOK_SECRET,
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 409, response.text

    def test_an_invalid_timezone_override_is_refused(
        self, client: TestClient, admin_user: User
    ) -> None:
        """'IST' is not an IANA zone, and accepting it would misdate every punch."""
        response = client.post(
            "/api/v1/devices",
            json={
                "code": "TZ",
                "name": "Bad zone",
                "adapter_type": "webhook",
                "webhook_secret": WEBHOOK_SECRET,
                "timezone_override": "IST",
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422, response.text

    def test_the_webhook_url_is_reported_without_the_secret(
        self, client: TestClient, admin_user: User
    ) -> None:
        device = make_device(client)
        response = client.get(
            f"/api/v1/devices/{device['id']}/webhook-url",
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["url"].endswith("/api/v1/devices/webhook/GATE1")
        assert body["secret_configured"] is True
        assert WEBHOOK_SECRET not in json.dumps(body)

    def test_a_polled_device_has_no_webhook_url(self, client: TestClient, admin_user: User) -> None:
        device = make_device(
            client,
            code="POLL",
            adapter_type="rest_poll",
            config={
                "url": "https://device.example.com/api/punches",
                "auth_mode": "none",
                "field_mapping": {
                    "employee_ref": "user_id",
                    "timestamp": "punch_time",
                    "timestamp_format": "iso8601",
                },
            },
            secret=None,
        )
        response = client.get(
            f"/api/v1/devices/{device['id']}/webhook-url",
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422, response.text


class TestSsrfProtection:
    def test_a_cloud_metadata_endpoint_cannot_be_configured(
        self, client: TestClient, admin_user: User
    ) -> None:
        """169.254.169.254 would hand over the instance's credentials."""
        response = client.post(
            "/api/v1/devices",
            json={
                "code": "META",
                "name": "Metadata probe",
                "adapter_type": "rest_poll",
                "config": {
                    "url": "http://169.254.169.254/latest/meta-data/",
                    "auth_mode": "none",
                    "allow_private_network": True,
                },
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422, response.text
        assert "link-local" in response.text

    def test_a_lan_address_needs_an_explicit_opt_in(
        self, client: TestClient, admin_user: User
    ) -> None:
        body = {
            "code": "LAN",
            "name": "Office reader",
            "adapter_type": "rest_poll",
            "config": {"url": "http://127.0.0.1:9/punches", "auth_mode": "none"},
        }
        refused = client.post("/api/v1/devices", json=body, headers=auth_header(client, "hradmin"))
        assert refused.status_code == 422, refused.text
        assert "allow_private_network" in refused.text

        body["config"]["allow_private_network"] = True
        allowed = client.post("/api/v1/devices", json=body, headers=auth_header(client, "hradmin"))
        assert allowed.status_code == 201, allowed.text

    def test_a_non_http_scheme_is_refused(self, client: TestClient, admin_user: User) -> None:
        response = client.post(
            "/api/v1/devices",
            json={
                "code": "FILE",
                "name": "File scheme",
                "adapter_type": "rest_poll",
                "config": {"url": "file:///etc/passwd", "auth_mode": "none"},
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_credentials_in_the_url_are_refused(self, client: TestClient, admin_user: User) -> None:
        response = client.post(
            "/api/v1/devices",
            json={
                "code": "CREDS",
                "name": "Credentialed URL",
                "adapter_type": "rest_poll",
                "config": {
                    "url": "https://admin:hunter2@device.example.com/api",
                    "auth_mode": "none",
                },
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422
        assert "credentials in the URL" in response.text


# ---------------------------------------------------------------------------
# Webhook authentication
# ---------------------------------------------------------------------------


class TestWebhookAuthentication:
    def test_a_correctly_signed_delivery_is_accepted(
        self, client: TestClient, admin_user: User, employee: dict, day_shift: dict
    ) -> None:
        device = make_device(client)
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        response = deliver(
            client,
            "GATE1",
            [
                {"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"},
                {"id": "e2", "user_id": "47", "punch_time": "2026-03-10 18:34:00"},
            ],
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["accepted"] == 2
        assert body["status"] == "succeeded"
        assert body["days_recomputed"] == 1

    def test_an_unsigned_delivery_is_rejected_and_stores_nothing(
        self, client: TestClient, admin_user: User, session: Session
    ) -> None:
        device = make_device(client)
        response = deliver(
            client, "GATE1", [{"id": "e1", "user_id": "47", "punch_time": "x"}], sign=False
        )
        assert response.status_code == 401, response.text

        stored = session.scalar(
            select(func.count())
            .select_from(DeviceRawEvent)
            .where(DeviceRawEvent.device_id == device["id"])
        )
        assert stored == 0, "an unauthenticated body must never be parsed or stored"

    def test_a_delivery_signed_with_the_wrong_secret_is_rejected(
        self, client: TestClient, admin_user: User
    ) -> None:
        make_device(client)
        response = deliver(
            client,
            "GATE1",
            [{"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"}],
            secret="the-wrong-secret-entirely",
        )
        assert response.status_code == 401

    def test_a_tampered_body_is_rejected(self, client: TestClient, admin_user: User) -> None:
        """The signature covers the raw bytes, so re-serialised JSON fails."""
        make_device(client)
        events = [{"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"}]
        signature = hmac.new(
            WEBHOOK_SECRET.encode(),
            json.dumps(events).encode(),
            hashlib.sha256,
        ).hexdigest()

        response = client.post(
            "/api/v1/devices/webhook/GATE1",
            content=json.dumps(events, indent=2).encode(),
            headers={"Content-Type": "application/json", "X-Device-Signature": signature},
        )
        assert response.status_code == 401

    def test_the_rejection_does_not_say_why_it_failed(
        self, client: TestClient, admin_user: User
    ) -> None:
        """Telling a caller which check failed tells them how to get closer."""
        make_device(client)
        response = deliver(client, "GATE1", [{}], secret="wrong-secret-value-here")
        assert "signature" not in response.text.lower()
        assert "allowlist" not in response.text.lower()

    def test_a_rejected_delivery_is_recorded_even_though_the_request_failed(
        self, client: TestClient, admin_user: User, session: Session
    ) -> None:
        """The audit entry must survive the rollback the 401 causes."""
        from app.modules.audit.models import AuditLog

        make_device(client)
        deliver(client, "GATE1", [{}], secret="wrong-secret-value-here")

        recorded = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "device.delivery_rejected")
        )
        assert recorded == 1, "probing the webhook must leave a trace"

    def test_an_unknown_device_code_cannot_be_distinguished_from_an_inactive_one(
        self, client: TestClient, admin_user: User
    ) -> None:
        device = make_device(client)
        client.patch(
            f"/api/v1/devices/{device['id']}",
            json={"is_active": False},
            headers=auth_header(client, "hradmin"),
        )

        inactive = deliver(client, "GATE1", [{}])
        unknown = deliver(client, "NOSUCHDEVICE", [{}])
        assert inactive.status_code == unknown.status_code == 404
        assert inactive.json()["error"]["message"] == unknown.json()["error"]["message"]

    def test_a_device_without_a_secret_refuses_deliveries(
        self, client: TestClient, admin_user: User
    ) -> None:
        """Unauthenticated acceptance is never the fallback."""
        response = client.post(
            "/api/v1/devices",
            json={
                "code": "NOSECRET",
                "name": "No secret yet",
                "adapter_type": "webhook",
                "config": {
                    "field_mapping": {
                        "employee_ref": "user_id",
                        "timestamp": "punch_time",
                        "timestamp_format": "ymd_hms",
                    }
                },
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 201, response.text

        delivered = deliver(client, "NOSECRET", [{}], sign=False)
        assert delivered.status_code == 422
        assert "no webhook secret" in delivered.text

    def test_a_source_ip_allowlist_blocks_an_unlisted_caller(
        self, client: TestClient, admin_user: User
    ) -> None:
        device = make_device(client)
        client.patch(
            f"/api/v1/devices/{device['id']}",
            json={"allowed_source_ips": ["203.0.113.7"]},
            headers=auth_header(client, "hradmin"),
        )
        response = deliver(
            client, "GATE1", [{"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:00:00"}]
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:
    def test_redelivering_the_same_batch_creates_no_second_punch(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        """A device re-sending its buffer after an outage is the normal case."""
        device = make_device(client)
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        events = [
            {"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"},
            {"id": "e2", "user_id": "47", "punch_time": "2026-03-10 18:34:00"},
        ]
        first = deliver(client, "GATE1", events).json()
        second = deliver(client, "GATE1", events).json()

        assert first["accepted"] == 2 and first["duplicate"] == 0
        assert second["accepted"] == 0 and second["duplicate"] == 2

        punches = session.scalar(
            select(func.count())
            .select_from(AttendanceEvent)
            .where(AttendanceEvent.employee_id == employee["id"])
        )
        assert punches == 2, "the second delivery must not double-count"

    def test_an_overlapping_batch_applies_only_the_new_events(
        self, client: TestClient, admin_user: User, employee: dict, day_shift: dict
    ) -> None:
        device = make_device(client)
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        deliver(
            client,
            "GATE1",
            [{"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"}],
        )
        second = deliver(
            client,
            "GATE1",
            [
                {"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"},
                {"id": "e2", "user_id": "47", "punch_time": "2026-03-10 18:34:00"},
            ],
        ).json()

        assert second["accepted"] == 1
        assert second["duplicate"] == 1

    def test_a_device_without_event_ids_is_still_deduplicated(
        self, client: TestClient, admin_user: User, employee: dict, day_shift: dict
    ) -> None:
        """Many devices number nothing. The id is derived from the content."""
        device = make_device(
            client,
            code="NOIDS",
            config={
                "field_mapping": {
                    "employee_ref": "user_id",
                    "timestamp": "punch_time",
                    "timestamp_format": "ymd_hms",
                }
            },
        )
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        events = [{"user_id": "47", "punch_time": "2026-03-10 09:32:00"}]
        assert deliver(client, "NOIDS", events).json()["accepted"] == 1
        assert deliver(client, "NOIDS", events).json()["duplicate"] == 1


# ---------------------------------------------------------------------------
# Employee mapping
# ---------------------------------------------------------------------------


class TestEmployeeMapping:
    def test_an_unmapped_identifier_is_queued_not_discarded(
        self, client: TestClient, admin_user: User
    ) -> None:
        device = make_device(client)
        result = deliver(
            client,
            "GATE1",
            [{"id": "e1", "user_id": "99", "punch_time": "2026-03-10 09:32:00"}],
        ).json()

        assert result["unmapped"] == 1
        assert result["accepted"] == 0
        assert result["status"] == "partial"
        assert "No employee is mapped" in result["problems"][0]["reason"]

        queued = client.get(
            f"/api/v1/devices/{device['id']}/events?status=unmapped",
            headers=auth_header(client, "hradmin"),
        ).json()
        assert len(queued) == 1
        assert queued[0]["external_employee_ref"] == "99"
        # The payload is retained, which is what makes it fixable later.
        assert queued[0]["raw_payload"]["user_id"] == "99"

    def test_adding_the_mapping_then_reprocessing_applies_the_stored_punch(
        self, client: TestClient, admin_user: User, employee: dict, day_shift: dict
    ) -> None:
        """A configuration mistake is correctable without the device re-sending."""
        device = make_device(client)
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        deliver(
            client,
            "GATE1",
            [
                {"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"},
                {"id": "e2", "user_id": "47", "punch_time": "2026-03-10 18:34:00"},
            ],
        )
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])

        reprocessed = client.post(
            f"/api/v1/devices/{device['id']}/reprocess",
            json={"statuses": ["unmapped"], "limit": 100},
            headers=auth_header(client, "hradmin"),
        )
        assert reprocessed.status_code == 200, reprocessed.text
        body = reprocessed.json()
        assert body["mapped"] == 2
        assert body["days_recomputed"] == 1

        days = client.get(
            f"/api/v1/attendance/employees/{employee['id']}?start=2026-03-10&end=2026-03-10",
            headers=auth_header(client, "hradmin"),
        ).json()
        assert [day["status"] for day in days] == ["present"]

    def test_already_mapped_events_cannot_be_reprocessed_into_duplicates(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        device = make_device(client)
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])
        deliver(
            client,
            "GATE1",
            [{"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"}],
        )

        # The schema refuses 'mapped' outright, which is the real guard.
        refused = client.post(
            f"/api/v1/devices/{device['id']}/reprocess",
            json={"statuses": ["mapped"]},
            headers=auth_header(client, "hradmin"),
        )
        assert refused.status_code == 422, refused.text

        # And a reprocess of everything reprocessable leaves the punch alone.
        client.post(
            f"/api/v1/devices/{device['id']}/reprocess",
            json={"statuses": ["pending", "unmapped", "failed"]},
            headers=auth_header(client, "hradmin"),
        )
        punches = session.scalar(
            select(func.count())
            .select_from(AttendanceEvent)
            .where(AttendanceEvent.employee_id == employee["id"])
        )
        assert punches == 1

    def test_a_reused_enrolment_slot_does_not_reattribute_old_punches(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        second_employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        """The case that silently corrupts history if mappings are not dated.

        Slot 47 belonged to Ravi until March, then to Meera. A punch from
        February must still be Ravi's.
        """
        device = make_device(client)
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])
        assign_shift(client, employee_id=second_employee["id"], shift_id=day_shift["id"])

        ravi_mapping = map_identifier(
            client,
            device["id"],
            reference="47",
            employee_id=employee["id"],
            effective_from="2026-01-05",
        )
        client.post(
            f"/api/v1/devices/mappings/{ravi_mapping['id']}/close",
            json={"effective_to": "2026-02-28"},
            headers=auth_header(client, "hradmin"),
        )
        map_identifier(
            client,
            device["id"],
            reference="47",
            employee_id=second_employee["id"],
            effective_from="2026-03-01",
        )

        deliver(
            client,
            "GATE1",
            [
                {"id": "feb", "user_id": "47", "punch_time": "2026-02-10 09:32:00"},
                {"id": "mar", "user_id": "47", "punch_time": "2026-03-10 09:32:00"},
            ],
        )

        owners = dict(
            session.execute(
                select(AttendanceEvent.external_event_id, AttendanceEvent.employee_id)
            ).all()
        )
        assert owners[f"dev{device['id']}:feb"] == employee["id"]
        assert owners[f"dev{device['id']}:mar"] == second_employee["id"]

    def test_a_second_open_mapping_for_one_identifier_is_refused(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        second_employee: dict,
    ) -> None:
        """Two open mappings would make a punch's owner ambiguous."""
        device = make_device(client)
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])

        response = client.post(
            f"/api/v1/devices/{device['id']}/mappings",
            json={
                "external_employee_ref": "47",
                "employee_id": second_employee["id"],
                "effective_from": "2026-03-01",
            },
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 409, response.text
        assert "Close that mapping first" in response.text

    def test_resolving_an_event_can_learn_the_mapping_for_next_time(
        self, client: TestClient, admin_user: User, employee: dict, day_shift: dict
    ) -> None:
        device = make_device(client)
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])
        deliver(
            client,
            "GATE1",
            [{"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"}],
        )

        queued = client.get(
            f"/api/v1/devices/{device['id']}/events?status=unmapped",
            headers=auth_header(client, "hradmin"),
        ).json()

        resolved = client.post(
            f"/api/v1/devices/events/{queued[0]['id']}/resolve",
            json={"employee_id": employee["id"], "create_mapping": True},
            headers=auth_header(client, "hradmin"),
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()["outcome"] == "mapped"
        assert resolved.json()["mapping_created"] is True

        # The next punch from the same identifier needs no intervention.
        follow_up = deliver(
            client,
            "GATE1",
            [{"id": "e2", "user_id": "47", "punch_time": "2026-03-10 18:34:00"}],
        ).json()
        assert follow_up["accepted"] == 1


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


class TestAttribution:
    def test_an_overnight_device_punch_lands_on_the_shift_start_date(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        night_shift: dict,
        session: Session,
    ) -> None:
        """A device punch must be attributed exactly as a web punch would be."""
        device = make_device(client)
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=night_shift["id"])

        result = deliver(
            client,
            "GATE1",
            [
                {"id": "in", "user_id": "47", "punch_time": "2026-03-10 22:00:00"},
                {"id": "out", "user_id": "47", "punch_time": "2026-03-11 06:00:00"},
            ],
        ).json()
        assert result["accepted"] == 2
        assert result["days_recomputed"] == 1, "one shift, therefore one day"

        days = session.scalars(
            select(AttendanceDay)
            .where(AttendanceDay.employee_id == employee["id"])
            .order_by(AttendanceDay.work_date)
        ).all()
        assert [day.work_date.isoformat() for day in days] == ["2026-03-10"]
        assert days[0].worked_minutes == 480
        assert days[0].status == "present"

    def test_a_day_first_export_is_read_as_configured(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        device = make_device(
            client,
            code="DMY",
            config={
                "field_mapping": {
                    "employee_ref": "user_id",
                    "timestamp": "punch_time",
                    "timestamp_format": "dmy_hms_slash",
                    "event_id": "id",
                }
            },
        )
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        deliver(
            client,
            "DMY",
            [{"id": "e1", "user_id": "47", "punch_time": "03/04/2026 09:32:00"}],
        )

        event = session.scalars(select(AttendanceEvent)).one()
        assert event.work_date.isoformat() == "2026-04-03", "day-first, as declared"

    def test_a_device_punch_is_attributed_in_the_devices_timezone(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        """The server's own zone is never consulted."""
        device = make_device(client, code="UTCDEV")
        client.patch(
            f"/api/v1/devices/{device['id']}",
            json={"timezone_override": "UTC"},
            headers=auth_header(client, "hradmin"),
        )
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        deliver(
            client,
            "UTCDEV",
            [{"id": "e1", "user_id": "47", "punch_time": "2026-03-10 04:02:00"}],
        )

        event = session.scalars(select(AttendanceEvent)).one()
        # 04:02 UTC is 09:32 IST, within the Bengaluru shift's window.
        assert event.occurred_at.isoformat() == "2026-03-10T04:02:00+00:00"
        assert event.work_date.isoformat() == "2026-03-10"

    def test_a_punch_before_joining_is_refused_rather_than_applied(
        self, client: TestClient, admin_user: User, employee: dict, day_shift: dict
    ) -> None:
        device = make_device(client)
        map_identifier(
            client,
            device["id"],
            reference="47",
            employee_id=employee["id"],
            effective_from="2025-01-01",
        )
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        result = deliver(
            client,
            "GATE1",
            [{"id": "e1", "user_id": "47", "punch_time": "2025-06-01 09:32:00"}],
        ).json()
        assert result["failed"] == 1
        assert result["accepted"] == 0
        assert "not on the rolls" in result["problems"][0]["reason"]


# ---------------------------------------------------------------------------
# Clock drift and malformed data
# ---------------------------------------------------------------------------


class TestBadData:
    def test_a_future_dated_punch_is_stored_as_failed_not_applied(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        """A drifted device clock must not credit unworked time."""
        device = make_device(client)
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        result = deliver(
            client,
            "GATE1",
            [{"id": "e1", "user_id": "47", "punch_time": "2031-01-01 09:00:00"}],
        ).json()

        assert result["failed"] == 1
        assert result["accepted"] == 0
        assert "device clock" in result["problems"][0]["reason"]
        assert session.scalar(select(func.count()).select_from(AttendanceEvent)) == 0

    def test_one_bad_row_does_not_discard_the_good_ones(
        self, client: TestClient, admin_user: User, employee: dict, day_shift: dict
    ) -> None:
        device = make_device(client)
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        result = deliver(
            client,
            "GATE1",
            [
                {"id": "good", "user_id": "47", "punch_time": "2026-03-10 09:32:00"},
                {"id": "bad", "user_id": "47", "punch_time": "not-a-timestamp"},
            ],
        ).json()
        assert result["accepted"] == 1
        assert result["failed"] == 1

    def test_a_heartbeat_is_ignored_rather_than_counted_as_a_failure(
        self, client: TestClient, admin_user: User, employee: dict, day_shift: dict
    ) -> None:
        device = make_device(
            client,
            code="HEART",
            config={
                "field_mapping": {
                    "employee_ref": "user_id",
                    "timestamp": "punch_time",
                    "timestamp_format": "ymd_hms",
                    "event_id": "id",
                    "ignore_when": {"type": "heartbeat"},
                }
            },
        )
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        result = deliver(
            client,
            "HEART",
            [
                {
                    "id": "hb",
                    "user_id": "47",
                    "punch_time": "2026-03-10 09:00:00",
                    "type": "heartbeat",
                },
                {"id": "p1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"},
            ],
        ).json()
        assert result["ignored"] == 1
        assert result["failed"] == 0
        assert result["accepted"] == 1
        assert result["status"] == "succeeded", "a heartbeat is not a problem"

    def test_an_unreadable_body_is_recorded_as_a_failed_run(
        self, client: TestClient, admin_user: User
    ) -> None:
        """The run must be visible, not only a 4xx the device discards."""
        device = make_device(client)
        body = b"this is not json"
        signature = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()

        response = client.post(
            "/api/v1/devices/webhook/GATE1",
            content=body,
            headers={"Content-Type": "application/json", "X-Device-Signature": signature},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "failed"

        runs = client.get(
            f"/api/v1/devices/{device['id']}/sync-runs",
            headers=auth_header(client, "hradmin"),
        ).json()
        assert runs[0]["status"] == "failed"
        assert "not valid JSON" in runs[0]["error_message"]

    def test_consecutive_failures_are_counted_on_the_device(
        self, client: TestClient, admin_user: User
    ) -> None:
        device = make_device(client)
        body = b"nope"
        signature = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        for _ in range(2):
            client.post(
                "/api/v1/devices/webhook/GATE1",
                content=body,
                headers={"Content-Type": "application/json", "X-Device-Signature": signature},
            )

        fetched = client.get(
            f"/api/v1/devices/{device['id']}", headers=auth_header(client, "hradmin")
        ).json()
        assert fetched["consecutive_failures"] == 2


# ---------------------------------------------------------------------------
# CSV upload
# ---------------------------------------------------------------------------


class TestCsvUpload:
    def test_an_exported_punch_log_is_ingested(
        self, client: TestClient, admin_user: User, employee: dict, day_shift: dict
    ) -> None:
        device = make_device(
            client,
            code="EXPORT",
            adapter_type="csv_upload",
            direction_mode="explicit",
            secret=None,
            config={
                "delimiter": ",",
                "field_mapping": {
                    "employee_ref": "EmpCode",
                    "timestamp": "PunchTime",
                    "timestamp_format": "dmy_hms_slash",
                    "direction": "InOut",
                    "direction_map": {"0": "in", "1": "out"},
                },
            },
        )
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        csv_body = (
            "EmpCode,PunchTime,InOut\r\n47,10/03/2026 09:32:00,0\r\n47,10/03/2026 18:34:00,1\r\n"
        )
        response = client.post(
            f"/api/v1/devices/{device['id']}/upload",
            files={"file": ("punches.csv", csv_body.encode(), "text/csv")},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["accepted"] == 2
        assert body["days_recomputed"] == 1

    def test_reuploading_an_overlapping_export_is_safe(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        device = make_device(
            client,
            code="EXPORT2",
            adapter_type="csv_upload",
            direction_mode="explicit",
            secret=None,
            config={
                "field_mapping": {
                    "employee_ref": "EmpCode",
                    "timestamp": "PunchTime",
                    "timestamp_format": "ymd_hms",
                    "direction": "InOut",
                    "direction_map": {"in": "in", "out": "out"},
                }
            },
        )
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        first = "EmpCode,PunchTime,InOut\n47,2026-03-10 09:32:00,in\n"
        overlapping = (
            "EmpCode,PunchTime,InOut\n47,2026-03-10 09:32:00,in\n47,2026-03-10 18:34:00,out\n"
        )
        headers = auth_header(client, "hradmin")
        client.post(
            f"/api/v1/devices/{device['id']}/upload",
            files={"file": ("a.csv", first.encode(), "text/csv")},
            headers=headers,
        )
        second = client.post(
            f"/api/v1/devices/{device['id']}/upload",
            files={"file": ("b.csv", overlapping.encode(), "text/csv")},
            headers=headers,
        ).json()

        assert second["duplicate"] == 1
        assert second["accepted"] == 1
        assert session.scalar(select(func.count()).select_from(AttendanceEvent)) == 2

    def test_an_empty_upload_is_refused(self, client: TestClient, admin_user: User) -> None:
        device = make_device(
            client, code="EXPORT3", adapter_type="csv_upload", secret=None, config={}
        )
        response = client.post(
            f"/api/v1/devices/{device['id']}/upload",
            files={"file": ("empty.csv", b"", "text/csv")},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422

    def test_a_file_cannot_be_uploaded_to_a_polled_device(
        self, client: TestClient, admin_user: User
    ) -> None:
        device = make_device(
            client,
            code="POLL2",
            adapter_type="rest_poll",
            secret=None,
            config={
                "url": "https://device.example.com/api",
                "auth_mode": "none",
                "field_mapping": {
                    "employee_ref": "user_id",
                    "timestamp": "punch_time",
                    "timestamp_format": "iso8601",
                },
            },
        )
        response = client.post(
            f"/api/v1/devices/{device['id']}/upload",
            files={"file": ("a.csv", b"a,b\n1,2\n", "text/csv")},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422
        assert "polled" in response.text


# ---------------------------------------------------------------------------
# Synchronisation
# ---------------------------------------------------------------------------


class TestSynchronisation:
    def test_the_simulator_exercises_the_whole_pipeline(
        self, client: TestClient, admin_user: User, employee: dict, day_shift: dict
    ) -> None:
        """Available because the test environment is local; refused in production."""
        device = make_device(
            client,
            code="SIM",
            adapter_type="simulator",
            secret=None,
            config={
                "employee_refs": ["47"],
                "date": "2026-03-10",
                "in_time": "09:32",
                "out_time": "18:34",
                "field_mapping": {
                    "employee_ref": "employee_id",
                    "timestamp": "timestamp",
                    "timestamp_format": "ymd_hms",
                },
            },
        )
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        response = client.post(
            f"/api/v1/devices/{device['id']}/sync",
            json={"window": "test-window-1"},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["accepted"] == 2
        assert body["status"] == "succeeded"

    def test_repeating_a_window_fetches_nothing_again(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        """The idempotency guard: a retried scheduled job must not double-apply."""
        device = make_device(
            client,
            code="SIM2",
            adapter_type="simulator",
            secret=None,
            config={
                "employee_refs": ["47"],
                "date": "2026-03-10",
                "field_mapping": {
                    "employee_ref": "employee_id",
                    "timestamp": "timestamp",
                    "timestamp_format": "ymd_hms",
                },
            },
        )
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])
        headers = auth_header(client, "hradmin")

        first = client.post(
            f"/api/v1/devices/{device['id']}/sync",
            json={"window": "same-window"},
            headers=headers,
        ).json()
        second = client.post(
            f"/api/v1/devices/{device['id']}/sync",
            json={"window": "same-window"},
            headers=headers,
        ).json()

        assert first["accepted"] == 2
        assert second["accepted"] == 0
        assert second["sync_run_id"] == first["sync_run_id"], "the original run is reported"
        assert session.scalar(select(func.count()).select_from(AttendanceEvent)) == 2

    def test_an_inactive_device_is_not_synchronised(
        self, client: TestClient, admin_user: User
    ) -> None:
        device = make_device(
            client,
            code="SIM3",
            adapter_type="simulator",
            secret=None,
            config={"employee_refs": ["47"]},
        )
        headers = auth_header(client, "hradmin")
        client.patch(f"/api/v1/devices/{device['id']}", json={"is_active": False}, headers=headers)
        response = client.post(f"/api/v1/devices/{device['id']}/sync", json={}, headers=headers)
        assert response.status_code == 422
        assert "inactive" in response.text

    def test_a_push_device_cannot_be_polled(self, client: TestClient, admin_user: User) -> None:
        device = make_device(client)
        response = client.post(
            f"/api/v1/devices/{device['id']}/sync",
            json={},
            headers=auth_header(client, "hradmin"),
        )
        assert response.status_code == 422
        assert "nothing to poll" in response.text

    def test_an_unreachable_device_records_a_failed_run_and_says_so(
        self, client: TestClient, admin_user: User
    ) -> None:
        """A 200 whose body says 'failed' - the run survives to be seen."""
        device = make_device(
            client,
            code="DEAD",
            adapter_type="rest_poll",
            secret=None,
            config={
                "url": "http://127.0.0.1:9/punches",
                "auth_mode": "none",
                "timeout_seconds": 1,
                "allow_private_network": True,
                "field_mapping": {
                    "employee_ref": "user_id",
                    "timestamp": "punch_time",
                    "timestamp_format": "iso8601",
                },
            },
        )
        headers = auth_header(client, "hradmin")
        response = client.post(f"/api/v1/devices/{device['id']}/sync", json={}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "failed"

        runs = client.get(f"/api/v1/devices/{device['id']}/sync-runs", headers=headers).json()
        assert len(runs) == 1
        assert runs[0]["status"] == "failed"
        assert runs[0]["error_message"]

    def test_a_failed_sync_never_echoes_the_device_credential(
        self, client: TestClient, admin_user: User
    ) -> None:
        device = make_device(
            client,
            code="DEAD2",
            adapter_type="rest_poll",
            secret=None,
            config={
                "url": "http://127.0.0.1:9/punches",
                "auth_mode": "bearer",
                "timeout_seconds": 1,
                "allow_private_network": True,
                "field_mapping": {
                    "employee_ref": "user_id",
                    "timestamp": "punch_time",
                    "timestamp_format": "iso8601",
                },
            },
        )
        headers = auth_header(client, "hradmin")
        client.patch(
            f"/api/v1/devices/{device['id']}",
            json={"api_secret": "super-secret-device-token"},
            headers=headers,
        )
        response = client.post(f"/api/v1/devices/{device['id']}/sync", json={}, headers=headers)
        assert "super-secret-device-token" not in response.text

        runs = client.get(f"/api/v1/devices/{device['id']}/sync-runs", headers=headers).json()
        assert "super-secret-device-token" not in json.dumps(runs)


class TestScheduledSync:
    """The scheduled job, which the brief requires to be idempotent."""

    @staticmethod
    def _simulator(client: TestClient, code: str) -> dict:
        return make_device(
            client,
            code=code,
            adapter_type="simulator",
            secret=None,
            config={
                "employee_refs": ["47"],
                "date": "2026-03-10",
                "field_mapping": {
                    "employee_ref": "employee_id",
                    "timestamp": "timestamp",
                    "timestamp_format": "ymd_hms",
                },
            },
        )

    def test_polling_is_off_until_it_is_turned_on(
        self, client: TestClient, admin_user: User, session: Session, seeded: None
    ) -> None:
        """Hardware polling is opt-in; web punching never depends on it."""
        from app.modules.devices import service as devices_service

        self._simulator(client, "SCHED1")
        result = devices_service.scheduled_sync(session, context=_context())

        assert result["status"] == "skipped"
        assert "auto_sync_enabled" in result["reason"]
        assert session.scalar(select(func.count()).select_from(AttendanceEvent)) == 0

    def test_two_runs_in_one_window_apply_the_punches_once(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        from app.modules.devices import service as devices_service

        device = self._simulator(client, "SCHED2")
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        first = devices_service.scheduled_sync(session, context=_context(), force=True)
        session.commit()
        second = devices_service.scheduled_sync(session, context=_context(), force=True)
        session.commit()

        assert first["accepted_total"] == 2
        assert second["accepted_total"] == 0, "the same window must not re-apply"
        assert session.scalar(select(func.count()).select_from(AttendanceEvent)) == 2

    def test_push_only_devices_are_not_polled(
        self, client: TestClient, admin_user: User, session: Session
    ) -> None:
        from app.modules.devices import service as devices_service

        make_device(client, code="PUSHONLY")
        result = devices_service.scheduled_sync(session, context=_context(), force=True)
        assert result["devices"] == [], "a webhook device has nothing to poll"

    def test_one_dead_device_does_not_stop_the_others(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        """A dead reader must not block the whole estate's attendance."""
        from app.modules.devices import service as devices_service

        working = self._simulator(client, "ALIVE")
        make_device(
            client,
            code="DEADONE",
            adapter_type="rest_poll",
            secret=None,
            config={
                "url": "http://127.0.0.1:9/punches",
                "auth_mode": "none",
                "timeout_seconds": 1,
                "allow_private_network": True,
                "field_mapping": {
                    "employee_ref": "user_id",
                    "timestamp": "punch_time",
                    "timestamp_format": "iso8601",
                },
            },
        )
        map_identifier(client, working["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])

        result = devices_service.scheduled_sync(session, context=_context(), force=True)
        session.commit()

        outcomes = {item["code"]: item["status"] for item in result["devices"]}
        assert outcomes["ALIVE"] == "succeeded"
        assert outcomes["DEADONE"] == "failed"
        assert result["accepted_total"] == 2, "the live device's punches still landed"


def _context():
    from app.modules.audit.service import AuditContext

    return AuditContext(actor_label="test-scheduler", user_agent="pytest")


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_the_database_refuses_to_rewrite_what_a_device_sent(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        device = make_device(client)
        map_identifier(client, device["id"], reference="47", employee_id=employee["id"])
        assign_shift(client, employee_id=employee["id"], shift_id=day_shift["id"])
        deliver(
            client,
            "GATE1",
            [{"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"}],
        )

        raw = session.scalars(select(DeviceRawEvent)).one()
        with pytest.raises(DBAPIError) as error:
            session.execute(
                text("UPDATE device_raw_events SET raw_timestamp = :value WHERE id = :id"),
                {"value": "2026-03-10 08:00:00", "id": raw.id},
            )
            session.flush()
        assert "keeps what the device sent unchanged" in str(error.value)
        session.rollback()

    def test_the_processing_columns_may_still_change(
        self,
        client: TestClient,
        admin_user: User,
        employee: dict,
        day_shift: dict,
        session: Session,
    ) -> None:
        """Otherwise an unmapped event could never become mapped."""
        make_device(client)
        deliver(
            client,
            "GATE1",
            [{"id": "e1", "user_id": "47", "punch_time": "2026-03-10 09:32:00"}],
        )
        raw = session.scalars(select(DeviceRawEvent)).one()
        session.execute(
            text("UPDATE device_raw_events SET status = 'ignored' WHERE id = :id"),
            {"id": raw.id},
        )
        session.commit()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_a_device_that_has_never_reported_is_listed_separately(
        self, client: TestClient, admin_user: User
    ) -> None:
        """Unproven is a different problem from gone quiet."""
        make_device(client)
        body = client.get("/api/v1/devices/health", headers=auth_header(client, "hradmin")).json()

        assert [item["code"] for item in body["never_reported"]] == ["GATE1"]
        assert body["quiet_devices"] == []

    def test_a_silent_device_is_surfaced(
        self, client: TestClient, admin_user: User, session: Session
    ) -> None:
        device = make_device(client)
        session.execute(
            text("UPDATE devices SET last_seen_at = now() - interval '3 days' WHERE id = :id"),
            {"id": device["id"]},
        )
        session.commit()

        body = client.get("/api/v1/devices/health", headers=auth_header(client, "hradmin")).json()
        assert [item["code"] for item in body["quiet_devices"]] == ["GATE1"]
        assert body["quiet_devices"][0]["silent_for_hours"] >= 71

    def test_events_needing_attention_are_counted(
        self, client: TestClient, admin_user: User
    ) -> None:
        make_device(client)
        deliver(
            client,
            "GATE1",
            [{"id": "e1", "user_id": "99", "punch_time": "2026-03-10 09:32:00"}],
        )
        body = client.get("/api/v1/devices/health", headers=auth_header(client, "hradmin")).json()
        assert body["events_needing_attention"]["unmapped"] == 1
        assert body["events_needing_attention_total"] == 1


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestAuthorization:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/devices"),
            ("get", "/api/v1/devices/adapters"),
            ("get", "/api/v1/devices/health"),
            ("post", "/api/v1/devices"),
            ("get", "/api/v1/devices/1"),
            ("patch", "/api/v1/devices/1"),
            ("get", "/api/v1/devices/1/mappings"),
            ("post", "/api/v1/devices/1/mappings"),
            ("post", "/api/v1/devices/1/sync"),
            ("get", "/api/v1/devices/1/events"),
            ("get", "/api/v1/devices/1/sync-runs"),
            ("post", "/api/v1/devices/1/reprocess"),
        ],
    )
    def test_an_employee_is_refused_every_device_endpoint(
        self,
        client: TestClient,
        admin_user: User,
        employee_user: User,
        method: str,
        path: str,
    ) -> None:
        """Backend-enforced, not merely hidden in the navigation."""
        headers = auth_header(client, "e.employee")
        if method == "get":
            response = client.get(path, headers=headers)
        else:
            response = getattr(client, method)(path, json={}, headers=headers)
        assert response.status_code == 403, f"{method.upper()} {path} -> {response.status_code}"

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/v1/devices"),
            ("post", "/api/v1/devices"),
            ("get", "/api/v1/devices/health"),
        ],
    )
    def test_an_anonymous_caller_is_refused(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.get(path) if method == "get" else client.post(path, json={})
        assert response.status_code == 401

    def test_the_webhook_is_the_only_unauthenticated_write(
        self, client: TestClient, admin_user: User
    ) -> None:
        """Documented explicitly so the exception stays deliberate."""
        spec = client.get("/api/openapi.json").json()
        unauthenticated: list[str] = []

        for path, operations in spec["paths"].items():
            for method, operation in operations.items():
                if method not in {"post", "put", "patch", "delete"}:
                    continue
                if operation.get("security") == []:
                    unauthenticated.append(f"{method.upper()} {path}")

        assert unauthenticated == ["POST /api/v1/devices/webhook/{device_code}"]
