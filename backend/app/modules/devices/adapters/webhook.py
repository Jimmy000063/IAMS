"""Generic webhook adapter.

For devices or middleware that can POST a punch to a URL. Fully implemented and
tested against the contract below; whether a given machine can be configured to
call a webhook is a question about that machine, not about this code.

Security model
--------------
A webhook endpoint cannot require a bearer token - a device will not perform a
sign-in flow - so **HMAC-SHA256 over the raw request body** is the
authentication. The signature is verified before the body is parsed (see
``policy.verify_signature``), because validating after parsing would let an
attacker alter semantically-equivalent content without breaking the signature.

An optional source-address allowlist is available as defence in depth. It is
never the only control: addresses are spoofable and are rewritten by proxies.
"""

from __future__ import annotations

import json
from typing import Any

from app.modules.devices.adapters.base import (
    AdapterDescription,
    ConfigField,
    register,
)
from app.modules.devices.policy import DeviceDataError, read_path

MAX_BODY_BYTES = 1024 * 1024
MAX_EVENTS_PER_DELIVERY = 1000


class WebhookAdapter:
    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_type="webhook",
            name="Webhook (device pushes to us)",
            mode="push",
            maturity="tested",
            summary=(
                "The device or its middleware POSTs punches to a URL unique to the "
                "device. Authenticated by an HMAC-SHA256 signature over the request "
                "body."
            ),
            requires_webhook_secret=True,
            config_fields=(
                ConfigField(
                    key="events_path",
                    label="Path to the event list",
                    help_text=(
                        "Dotted path to the array of events, e.g. 'data.records'. "
                        "Leave empty if the body is itself an array, or a single event."
                    ),
                    example="data.records",
                ),
                ConfigField(
                    key="signature_header",
                    label="Signature header",
                    help_text="Header carrying the HMAC. Defaults to X-Device-Signature.",
                    example="X-Device-Signature",
                ),
                ConfigField(
                    key="field_mapping.employee_ref",
                    label="Employee field",
                    required=True,
                    example="user_id",
                ),
                ConfigField(
                    key="field_mapping.timestamp",
                    label="Timestamp field",
                    required=True,
                    example="punch_time",
                ),
                ConfigField(
                    key="field_mapping.event_id",
                    label="Event id field",
                    help_text=(
                        "Strongly recommended. Without it an id is derived from the "
                        "content, which still deduplicates but cannot distinguish two "
                        "genuinely identical punches."
                    ),
                    example="id",
                ),
            ),
            caveats=(
                "The endpoint is unauthenticated by design and secured by the "
                "signature. Rotating the secret invalidates deliveries signed with "
                "the old one.",
                "A redelivered payload is deduplicated by event id, so a device "
                "retrying after a failed POST cannot double-count a punch.",
            ),
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        events_path = config.get("events_path")
        if events_path is not None and not isinstance(events_path, str):
            raise DeviceDataError("events_path must be a string.", field_name="events_path")
        header = config.get("signature_header", "X-Device-Signature")
        if not isinstance(header, str) or not header.strip():
            raise DeviceDataError(
                "signature_header must be a non-empty string.",
                field_name="signature_header",
            )

    def extract(
        self, *, config: dict[str, Any], body: bytes, content_type: str | None
    ) -> list[dict[str, Any]]:
        if len(body) > MAX_BODY_BYTES:
            raise DeviceDataError(
                f"The payload exceeds {MAX_BODY_BYTES // 1024} KB.", field_name="body"
            )
        if not body:
            raise DeviceDataError("The request body is empty.", field_name="body")

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DeviceDataError(
                f"The body is not valid JSON: {exc.msg}", field_name="body"
            ) from exc

        events_path = (config.get("events_path") or "").strip()
        candidate = read_path(parsed, events_path) if events_path else parsed

        if candidate is None:
            raise DeviceDataError(
                f"No events found at {events_path!r} in the payload.",
                field_name="events_path",
            )

        # Accept a bare object as a single event: plenty of devices POST one
        # punch at a time, and requiring an array would reject them for no
        # reason.
        if isinstance(candidate, dict):
            events = [candidate]
        elif isinstance(candidate, list):
            events = candidate
        else:
            raise DeviceDataError(
                "The events value must be an object or an array.",
                field_name="events_path",
            )

        if len(events) > MAX_EVENTS_PER_DELIVERY:
            raise DeviceDataError(
                f"A single delivery may contain at most {MAX_EVENTS_PER_DELIVERY} events.",
                field_name="body",
            )

        return [item for item in events if isinstance(item, dict)]


register(WebhookAdapter())
