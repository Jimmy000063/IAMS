"""The provider-independent adapter interface.

An adapter's only job is to produce a list of raw payload dictionaries. It does
**not** parse timestamps, infer direction, deduplicate, map employees or write
anything - all of that is shared downstream, so a new adapter cannot get those
parts subtly wrong in its own way.

Two shapes:

* **Pull** - the system asks the device (``fetch``). REST polling, scheduled
  synchronisation.
* **Push** - the device tells the system (``extract``). Webhooks, file uploads.

A deliberately honest registry
------------------------------
``AdapterDescription.maturity`` states what each adapter actually is. Nothing
here is described as supporting a named biometric product, because no such
device has been tested against this code. The brief requires exactly that
restraint, and a vendor adapter is added only once its protocol is in hand and
has been exercised against real hardware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from app.modules.devices.policy import DeviceDataError

Maturity = Literal["tested", "generic", "development_only"]


@dataclass(frozen=True, slots=True)
class AdapterDescription:
    """What an adapter is, in terms an operator can act on."""

    adapter_type: str
    name: str
    mode: Literal["pull", "push"]
    maturity: Maturity
    summary: str
    config_fields: tuple[ConfigField, ...] = ()
    requires_api_secret: bool = False
    requires_webhook_secret: bool = False
    caveats: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfigField:
    key: str
    label: str
    required: bool = False
    help_text: str = ""
    example: str = ""


@dataclass(frozen=True, slots=True)
class FetchResult:
    """What a pull adapter returned."""

    payloads: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    """True when the device has further pages; the caller may sync again."""

    diagnostics: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class DeviceAdapter(Protocol):
    """What every adapter must provide."""

    def describe(self) -> AdapterDescription:
        """Static metadata, used by the UI and the adapter catalogue endpoint."""
        ...

    def validate_config(self, config: dict[str, Any]) -> None:
        """Raise :class:`DeviceDataError` if the configuration is unusable.

        Called when a device is created or updated, so a misconfiguration is
        caught at that point rather than at 2am when the scheduled sync runs.
        """
        ...


@runtime_checkable
class PullAdapter(DeviceAdapter, Protocol):
    def fetch(
        self,
        *,
        config: dict[str, Any],
        secret: str | None,
        cursor: str | None,
    ) -> FetchResult:
        """Retrieve events after ``cursor``."""
        ...


@runtime_checkable
class PushAdapter(DeviceAdapter, Protocol):
    def extract(
        self, *, config: dict[str, Any], body: bytes, content_type: str | None
    ) -> list[dict[str, Any]]:
        """Pull the event list out of a delivered body."""
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, DeviceAdapter] = {}


def register(adapter: DeviceAdapter) -> DeviceAdapter:
    """Add an adapter to the registry. Called at import time by each module."""
    description = adapter.describe()
    if description.adapter_type in _REGISTRY:
        raise RuntimeError(f"Adapter {description.adapter_type!r} is already registered.")
    _REGISTRY[description.adapter_type] = adapter
    return adapter


def get_adapter(adapter_type: str) -> DeviceAdapter:
    adapter = _REGISTRY.get(adapter_type)
    if adapter is None:
        raise DeviceDataError(
            f"No adapter named {adapter_type!r}. Available: " + ", ".join(sorted(_REGISTRY)),
            field_name="adapter_type",
        )
    return adapter


def all_descriptions() -> list[AdapterDescription]:
    return sorted(
        (adapter.describe() for adapter in _REGISTRY.values()),
        key=lambda description: description.adapter_type,
    )


def load_builtin_adapters() -> None:
    """Import the bundled adapters so they register themselves.

    Called from the application factory. Importing for side effects is kept to
    this one explicit function rather than scattered through the package, so the
    set of available adapters is obvious.
    """
    from app.modules.devices.adapters import (  # noqa: F401
        csv_upload,
        rest_poll,
        simulator,
        webhook,
    )


#: Vendor integrations deliberately **not** implemented.
#:
#: Surfaced through the adapter catalogue so an operator looking for their
#: machine gets a straight answer instead of discovering the gap after
#: configuring a device. Each needs its protocol documentation and a real unit
#: to test against before it can be written.
UNIMPLEMENTED_VENDOR_NOTES: tuple[str, ...] = (
    "No adapter is written against any specific biometric or access-control "
    "product. Vendor protocols differ in transport, authentication, time "
    "handling and event semantics, and an adapter built from a datasheet alone "
    "cannot be trusted with attendance data.",
    "Many devices can already be integrated today without a vendor adapter: if "
    "the unit can export CSV, call a webhook, or expose a JSON endpoint, the "
    "generic adapters handle it via a field mapping.",
    "To add a vendor adapter, its protocol documentation and a test unit are "
    "both required. Until an adapter has been exercised against real hardware "
    "it is listed as 'generic' or 'development_only', never as 'tested'.",
)
