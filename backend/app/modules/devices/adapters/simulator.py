"""Simulator adapter - for exercising the pipeline without hardware.

Generates synthetic punches on demand. It exists so the ingestion path,
deduplication, employee mapping and reconciliation screens can be exercised
before any real device is available, which is the normal state of affairs while
the hardware is still being procured.

Marked ``development_only`` and refuses to run outside a local or test
environment, so it cannot quietly fabricate attendance in production. That
guard is the entire reason it is safe to ship this adapter at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.config import get_settings
from app.modules.devices.adapters.base import (
    AdapterDescription,
    ConfigField,
    FetchResult,
    register,
)
from app.modules.devices.policy import DeviceDataError

MAX_REFS = 50


class SimulatorAdapter:
    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_type="simulator",
            name="Simulator (no hardware)",
            mode="pull",
            maturity="development_only",
            summary=(
                "Generates synthetic punches so the ingestion pipeline, mapping and "
                "reconciliation can be exercised before real hardware arrives."
            ),
            config_fields=(
                ConfigField(
                    key="employee_refs",
                    label="Device identifiers to generate for",
                    required=True,
                    help_text=(
                        "The device-side references, as you would map them. Each gets "
                        "an in and an out punch."
                    ),
                    example='["47", "48"]',
                ),
                ConfigField(
                    key="date",
                    label="Date to generate for",
                    help_text="YYYY-MM-DD. Defaults to today.",
                    example="2026-11-03",
                ),
                ConfigField(
                    key="in_time",
                    label="Punch-in local time",
                    example="09:32",
                ),
                ConfigField(
                    key="out_time",
                    label="Punch-out local time",
                    example="18:34",
                ),
            ),
            caveats=(
                "Refuses to run when the environment is staging or production. It "
                "must not be able to fabricate attendance in a live system.",
                "Generated events carry no device event id, so they exercise the "
                "derived-id deduplication path.",
            ),
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        self._assert_environment_allows()

        refs = config.get("employee_refs")
        if not isinstance(refs, list) or not refs:
            raise DeviceDataError(
                "employee_refs must be a non-empty list of device identifiers.",
                field_name="employee_refs",
            )
        if len(refs) > MAX_REFS:
            raise DeviceDataError(f"At most {MAX_REFS} identifiers.", field_name="employee_refs")

        for key in ("in_time", "out_time"):
            value = config.get(key)
            if value is not None:
                try:
                    datetime.strptime(str(value), "%H:%M")
                except ValueError as exc:
                    raise DeviceDataError(
                        f"{key} must be a local time as HH:MM.", field_name=key
                    ) from exc

        if config.get("date") is not None:
            try:
                datetime.strptime(str(config["date"]), "%Y-%m-%d")
            except ValueError as exc:
                raise DeviceDataError("date must be YYYY-MM-DD.", field_name="date") from exc

    def fetch(
        self,
        *,
        config: dict[str, Any],
        secret: str | None,
        cursor: str | None,
    ) -> FetchResult:
        self._assert_environment_allows()

        refs = [str(ref) for ref in config.get("employee_refs", [])][:MAX_REFS]
        day = str(config.get("date") or datetime.now(UTC).date().isoformat())
        in_time = str(config.get("in_time", "09:32"))
        out_time = str(config.get("out_time", "18:34"))

        payloads: list[dict[str, Any]] = []
        for ref in refs:
            for clock in (in_time, out_time):
                payloads.append(
                    {
                        "employee_id": ref,
                        # A naive local timestamp, which is what most devices
                        # actually send - so the timezone-anchoring path is
                        # exercised rather than bypassed.
                        "timestamp": f"{day} {clock}:00",
                        "source": "simulator",
                    }
                )

        # A stable cursor so repeated runs are recognisable as repeats.
        return FetchResult(
            payloads=payloads,
            next_cursor=f"sim-{day}",
            has_more=False,
            diagnostics={"generated": len(payloads), "simulated_date": day},
        )

    @staticmethod
    def _assert_environment_allows() -> None:
        environment = get_settings().app_env
        if environment not in {"local", "test"}:
            raise DeviceDataError(
                "The simulator adapter is disabled outside local and test "
                f"environments (this one is {environment!r}). It must not be able "
                "to fabricate attendance in a live system.",
                field_name="adapter_type",
            )


register(SimulatorAdapter())
