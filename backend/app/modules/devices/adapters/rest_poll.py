"""Generic REST polling adapter.

For a device or middleware exposing an HTTP endpoint that returns recent
punches as JSON. The transport, authentication and pagination handling here are
implemented and tested; whether a given machine's API fits this shape is a
question about that machine.

Marked ``generic`` rather than ``tested``, deliberately: the code paths are
exercised against a stub, but no physical device has been polled with it. That
distinction is the whole point of the maturity field.

Security
--------
Outbound requests are the risk here, not inbound ones. The URL is validated
against server-side request forgery: a device endpoint must be http(s), must not
be a credentialed URL, and - unless the operator explicitly opts in - must not
resolve to a loopback, link-local or private address. A device on the office LAN
legitimately *is* a private address, which is why the opt-in exists rather than
a blanket ban.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

from app.modules.devices.adapters.base import (
    AdapterDescription,
    ConfigField,
    FetchResult,
    register,
)
from app.modules.devices.policy import DeviceDataError, read_path

DEFAULT_TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_EVENTS_PER_FETCH = 5000


class RestPollAdapter:
    def describe(self) -> AdapterDescription:
        return AdapterDescription(
            adapter_type="rest_poll",
            name="REST polling (we ask the device)",
            mode="pull",
            maturity="generic",
            summary=(
                "Polls an HTTP endpoint that returns punches as JSON. Suits devices "
                "and middleware with a simple query API."
            ),
            requires_api_secret=True,
            config_fields=(
                ConfigField(
                    key="url",
                    label="Endpoint URL",
                    required=True,
                    help_text=(
                        "May contain {cursor} and {limit}, which are substituted on each poll."
                    ),
                    example="http://192.168.1.50/api/attendance?since={cursor}",
                ),
                ConfigField(
                    key="auth_mode",
                    label="Authentication",
                    help_text="One of: none, bearer, header, query.",
                    example="bearer",
                ),
                ConfigField(
                    key="auth_header_name",
                    label="Header name",
                    help_text="Used when authentication is 'header'.",
                    example="X-API-Key",
                ),
                ConfigField(
                    key="auth_query_param",
                    label="Query parameter",
                    help_text="Used when authentication is 'query'.",
                    example="api_key",
                ),
                ConfigField(
                    key="events_path",
                    label="Path to the event list",
                    help_text="Dotted path, e.g. 'data.records'.",
                    example="data",
                ),
                ConfigField(
                    key="cursor_path",
                    label="Path to the next cursor",
                    help_text=(
                        "Where the response reports its paging cursor. If absent, the "
                        "cursor advances to the newest event timestamp seen."
                    ),
                    example="meta.next",
                ),
                ConfigField(
                    key="allow_private_network",
                    label="Allow a private address",
                    help_text=(
                        "Required for a device on the office LAN. Leave off for an "
                        "internet endpoint, where a private address would indicate a "
                        "misconfiguration or an SSRF attempt."
                    ),
                    example="true",
                ),
            ),
            caveats=(
                "Marked 'generic': the transport is tested, but no physical device "
                "has been polled with it. Verify against your unit before relying "
                "on it for payroll.",
                "Polling is only as current as its schedule. A device that goes "
                "offline produces no error until the next poll fails.",
            ),
        )

    def validate_config(self, config: dict[str, Any]) -> None:
        url = config.get("url")
        if not isinstance(url, str) or not url.strip():
            raise DeviceDataError("An endpoint URL is required.", field_name="url")

        validate_endpoint(url, allow_private=bool(config.get("allow_private_network", False)))

        auth_mode = config.get("auth_mode", "none")
        if auth_mode not in {"none", "bearer", "header", "query"}:
            raise DeviceDataError(
                "auth_mode must be one of: none, bearer, header, query.",
                field_name="auth_mode",
            )
        if auth_mode == "header" and not config.get("auth_header_name"):
            raise DeviceDataError(
                "auth_header_name is required when authentication is 'header'.",
                field_name="auth_header_name",
            )
        if auth_mode == "query" and not config.get("auth_query_param"):
            raise DeviceDataError(
                "auth_query_param is required when authentication is 'query'.",
                field_name="auth_query_param",
            )

        timeout = config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        if not isinstance(timeout, int) or not 1 <= timeout <= 120:
            raise DeviceDataError(
                "timeout_seconds must be between 1 and 120.", field_name="timeout_seconds"
            )

    def fetch(
        self,
        *,
        config: dict[str, Any],
        secret: str | None,
        cursor: str | None,
    ) -> FetchResult:
        import httpx

        allow_private = bool(config.get("allow_private_network", False))
        template = str(config["url"])
        limit = int(config.get("limit", 500))

        # Substituted rather than concatenated, and the cursor has already been
        # validated against a safe character set by the pipeline.
        url = template.replace("{cursor}", cursor or "").replace("{limit}", str(limit))
        validate_endpoint(url, allow_private=allow_private)

        headers: dict[str, str] = {"Accept": "application/json"}
        params: dict[str, str] = {}
        auth_mode = config.get("auth_mode", "none")

        if auth_mode == "bearer" and secret:
            headers["Authorization"] = f"Bearer {secret}"
        elif auth_mode == "header" and secret:
            headers[str(config["auth_header_name"])] = secret
        elif auth_mode == "query" and secret:
            params[str(config["auth_query_param"])] = secret

        timeout = int(config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))

        try:
            # follow_redirects is off: a redirect could send a credentialed
            # request to a host that was never validated.
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                response = client.get(url, headers=headers, params=params)
        except httpx.TimeoutException as exc:
            raise DeviceDataError(
                f"The device did not respond within {timeout}s.", field_name="url"
            ) from exc
        except httpx.HTTPError as exc:
            raise DeviceDataError(
                f"Could not reach the device: {type(exc).__name__}.", field_name="url"
            ) from exc

        if response.status_code >= 400:
            # The device's own body is not echoed back: it may contain the
            # credential we just sent it.
            raise DeviceDataError(
                f"The device returned HTTP {response.status_code}.", field_name="url"
            )

        if len(response.content) > MAX_RESPONSE_BYTES:
            raise DeviceDataError(
                f"The response exceeds {MAX_RESPONSE_BYTES // 1024 // 1024} MB.",
                field_name="url",
            )

        try:
            parsed = response.json()
        except ValueError as exc:
            raise DeviceDataError(
                "The device's response was not valid JSON.", field_name="url"
            ) from exc

        events_path = (config.get("events_path") or "").strip()
        candidate = read_path(parsed, events_path) if events_path else parsed

        if isinstance(candidate, dict):
            payloads = [candidate]
        elif isinstance(candidate, list):
            payloads = [item for item in candidate if isinstance(item, dict)]
        else:
            raise DeviceDataError(
                f"No event list found at {events_path or 'the response root'!r}.",
                field_name="events_path",
            )

        if len(payloads) > MAX_EVENTS_PER_FETCH:
            payloads = payloads[:MAX_EVENTS_PER_FETCH]

        next_cursor: str | None = None
        cursor_path = (config.get("cursor_path") or "").strip()
        if cursor_path:
            value = read_path(parsed, cursor_path)
            next_cursor = str(value) if value not in (None, "") else None

        return FetchResult(
            payloads=payloads,
            next_cursor=next_cursor,
            has_more=len(payloads) >= MAX_EVENTS_PER_FETCH,
            diagnostics={
                "http_status": response.status_code,
                "payload_count": len(payloads),
            },
        )


def validate_endpoint(url: str, *, allow_private: bool) -> None:
    """Reject endpoints that would make this a server-side request forgery tool.

    The system will fetch whatever URL an administrator configures, so the URL
    is treated as untrusted input even though only HR/Admin can set it:

    * **Scheme** must be http or https - no ``file://``, no ``gopher://``.
    * **No credentials in the URL**, which would otherwise end up in logs.
    * **No loopback, link-local or cloud metadata address** unless the operator
      has explicitly allowed private networking, which a LAN device needs.

    The link-local block is the one that matters most: ``169.254.169.254`` is
    the cloud instance metadata endpoint, and reaching it would hand over
    instance credentials.
    """
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise DeviceDataError(
            f"The endpoint scheme must be http or https, not {parsed.scheme or 'empty'!r}.",
            field_name="url",
        )
    if parsed.username or parsed.password:
        raise DeviceDataError(
            "Do not put credentials in the URL; configure authentication instead.",
            field_name="url",
        )
    if not parsed.hostname:
        raise DeviceDataError("The endpoint has no host.", field_name="url")

    host = parsed.hostname

    try:
        addresses = {
            info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        }
    except socket.gaierror:
        # An unresolvable host is left to fail at request time with a clearer
        # message; refusing here would block a device that is merely offline
        # while its configuration is being saved.
        return

    for address in addresses:
        try:
            parsed_ip = ipaddress.ip_address(address)
        except ValueError:  # pragma: no cover - getaddrinfo returns valid addresses
            continue

        if parsed_ip.is_link_local or parsed_ip.is_multicast or parsed_ip.is_reserved:
            raise DeviceDataError(
                f"{host} resolves to {address}, which is a link-local, multicast or "
                "reserved address. Cloud metadata endpoints live in this range and "
                "must never be polled.",
                field_name="url",
            )

        if (parsed_ip.is_private or parsed_ip.is_loopback) and not allow_private:
            raise DeviceDataError(
                f"{host} resolves to the private or loopback address {address}. If "
                "this device is on your local network, enable "
                "'allow_private_network' for it.",
                field_name="url",
            )


register(RestPollAdapter())
