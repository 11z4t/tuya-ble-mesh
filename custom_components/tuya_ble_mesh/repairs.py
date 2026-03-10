"""Repair issues for the Tuya BLE Mesh integration.

Creates actionable repair issues in HA when connectivity, authentication,
or protocol problems are detected. Each issue includes:
  - What is wrong
  - How it manifests (symptoms)
  - What to check / how to fix
  - Link to documentation

Issues are automatically cleared when the problem resolves.

Issue IDs are scoped per config entry using a ``--`` separator:
  ``{base_id}--{entry_id}``

This ensures that multiple devices can have independent issues and that
a recovery for device A does not clear issues for device B.

Base issue IDs:
  bridge_unreachable      — HTTP bridge endpoint not reachable
  auth_or_mesh_mismatch   — bridge reachable but device rejects credentials
  unsupported_vendor      — vendor ID not supported by bridge firmware
  device_not_found        — device MAC not visible from bridge
  timeout                 — repeated operation timeouts (bridge or device)
  reconnect_storm         — excessive reconnect attempts (possible loop)
  protocol_mismatch       — bridge/device protocol version incompatible
  provisioning_failed     — SIG Mesh PB-GATT provisioning failed
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.repairs import RepairsFlow
from homeassistant.data_entry_flow import FlowResult

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Base issue IDs — keep in sync with strings.json
ISSUE_BRIDGE_UNREACHABLE = "bridge_unreachable"
ISSUE_AUTH_OR_MESH_MISMATCH = "auth_or_mesh_mismatch"
ISSUE_UNSUPPORTED_VENDOR = "unsupported_vendor"
ISSUE_DEVICE_NOT_FOUND = "device_not_found"
ISSUE_TIMEOUT = "timeout"
ISSUE_RECONNECT_STORM = "reconnect_storm"
ISSUE_PROTOCOL_MISMATCH = "protocol_mismatch"
ISSUE_PROVISIONING_FAILED = "provisioning_failed"
ISSUE_KEY_MISMATCH = "key_mismatch"

# All base issue IDs — used for bulk deletion
_ALL_BASE_ISSUE_IDS = (
    ISSUE_BRIDGE_UNREACHABLE,
    ISSUE_AUTH_OR_MESH_MISMATCH,
    ISSUE_UNSUPPORTED_VENDOR,
    ISSUE_DEVICE_NOT_FOUND,
    ISSUE_TIMEOUT,
    ISSUE_RECONNECT_STORM,
    ISSUE_PROTOCOL_MISMATCH,
    ISSUE_PROVISIONING_FAILED,
    ISSUE_KEY_MISMATCH,
)

DOMAIN = "tuya_ble_mesh"

# Separator between base issue ID and entry_id.
# Using "--" because base issue IDs use underscores; this avoids ambiguity.
_ENTRY_SEP = "--"


def _scoped_issue_id(base_id: str, entry_id: str) -> str:
    """Return an entry-scoped issue ID.

    Example: "bridge_unreachable--abc123"
    """
    return f"{base_id}{_ENTRY_SEP}{entry_id}"


def _base_of_scoped(scoped_id: str) -> str:
    """Extract the base issue ID from a scoped issue ID.

    Example: "bridge_unreachable--abc123" → "bridge_unreachable"
    Falls back to the full scoped_id if it contains no separator.
    """
    return scoped_id.split(_ENTRY_SEP)[0]


async def async_create_issue_bridge_unreachable(
    hass: HomeAssistant,
    host: str,
    port: int,
    entry_id: str,
) -> None:
    """Create a repair issue when the bridge daemon cannot be reached.

    Symptoms: All entities show unavailable, no BLE commands sent.
    Checks: Is bridge running? Is host/port correct? Is network OK?
    """
    from homeassistant.helpers import issue_registry as ir

    issue_id = _scoped_issue_id(ISSUE_BRIDGE_UNREACHABLE, entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_BRIDGE_UNREACHABLE,
        translation_placeholders={
            "host": host,
            "port": str(port),
        },
    )
    _LOGGER.warning(
        "[%s] Repair issue created: %s for %s:%d",
        entry_id[:8],
        ISSUE_BRIDGE_UNREACHABLE,
        host,
        port,
    )


async def async_create_issue_auth_or_mesh_mismatch(
    hass: HomeAssistant,
    device_name: str,
    entry_id: str,
) -> None:
    """Create repair issue when device rejects mesh credentials.

    Symptoms: Bridge reachable, device discovered, but commands fail.
    Checks: Mesh name/password match factory defaults or custom values?
            Was the device factory-reset since last pairing?
    Fix: Re-enter credentials via Options Flow or re-pair device.
    """
    from homeassistant.helpers import issue_registry as ir

    issue_id = _scoped_issue_id(ISSUE_AUTH_OR_MESH_MISMATCH, entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_AUTH_OR_MESH_MISMATCH,
        translation_placeholders={"device": device_name},
    )
    _LOGGER.warning(
        "[%s] Repair issue created: %s for %s",
        entry_id[:8],
        ISSUE_AUTH_OR_MESH_MISMATCH,
        device_name,
    )


async def async_create_issue_unsupported_vendor(
    hass: HomeAssistant,
    device_name: str,
    vendor_id: str,
    entry_id: str,
) -> None:
    """Create repair issue when vendor ID is not supported.

    Symptoms: Device found but all commands silently ignored.
    Checks: Verify vendor ID in BLE snoop logs at payload offset [3:5].
            Update vendor_id in Options Flow to match your hardware.
    """
    from homeassistant.helpers import issue_registry as ir

    issue_id = _scoped_issue_id(ISSUE_UNSUPPORTED_VENDOR, entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_UNSUPPORTED_VENDOR,
        translation_placeholders={"device": device_name, "vendor_id": vendor_id},
    )
    _LOGGER.warning(
        "[%s] Repair issue created: %s for %s (vendor=%s)",
        entry_id[:8],
        ISSUE_UNSUPPORTED_VENDOR,
        device_name,
        vendor_id,
    )


async def async_create_issue_device_not_found(
    hass: HomeAssistant,
    device_name: str,
    mac_address: str,
    entry_id: str,
) -> None:
    """Create repair issue when device MAC is no longer visible.

    Symptoms: Entities unavailable for extended period, no BLE activity.
    Checks: Is device powered? Is it within BLE range of bridge/adapter?
            Run bridge scan: curl http://bridge:8099/scan
    """
    from homeassistant.helpers import issue_registry as ir

    mac_display = mac_address[-8:] if len(mac_address) >= 8 else mac_address
    issue_id = _scoped_issue_id(ISSUE_DEVICE_NOT_FOUND, entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_DEVICE_NOT_FOUND,
        translation_placeholders={"device": device_name, "mac": mac_display},
    )
    _LOGGER.warning(
        "[%s] Repair issue created: %s for %s (%s)",
        entry_id[:8],
        ISSUE_DEVICE_NOT_FOUND,
        device_name,
        mac_address,
    )


async def async_create_issue_timeout(
    hass: HomeAssistant,
    device_name: str,
    entry_id: str,
    operation: str = "connect",
) -> None:
    """Create repair issue for repeated operation timeouts.

    Symptoms: Commands sent but no response, long delay before unavailable.
    Checks: Is device in deep sleep mode? Is BLE range marginal (RSSI < -85)?
            Check logs for repeated 'timed out' messages.
    """
    from homeassistant.helpers import issue_registry as ir

    issue_id = _scoped_issue_id(ISSUE_TIMEOUT, entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_TIMEOUT,
        translation_placeholders={"device": device_name, "operation": operation},
    )
    _LOGGER.warning(
        "[%s] Repair issue created: %s for %s (op=%s)",
        entry_id[:8],
        ISSUE_TIMEOUT,
        device_name,
        operation,
    )


async def async_create_issue_reconnect_storm(
    hass: HomeAssistant,
    device_name: str,
    reconnect_count: int,
    entry_id: str,
    window_minutes: int = 5,
) -> None:
    """Create repair issue when reconnects are excessive (possible loop).

    Symptoms: Log spam with repeated 'Reconnecting to...' messages,
              high CPU usage, device flapping available/unavailable.
    Checks: Is device stable (not resetting repeatedly)?
            Check bridge logs for error patterns.
            Consider adjusting reconnect_storm_threshold in Options.
    """
    from homeassistant.helpers import issue_registry as ir

    issue_id = _scoped_issue_id(ISSUE_RECONNECT_STORM, entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_RECONNECT_STORM,
        translation_placeholders={
            "device": device_name,
            "count": str(reconnect_count),
            "window": str(window_minutes),
        },
    )
    _LOGGER.warning(
        "[%s] Repair issue created: %s for %s (%d reconnects in %dm)",
        entry_id[:8],
        ISSUE_RECONNECT_STORM,
        device_name,
        reconnect_count,
        window_minutes,
    )


async def async_create_issue_protocol_mismatch(
    hass: HomeAssistant,
    device_name: str,
    expected_protocol: str,
    entry_id: str,
    actual_info: str = "",
) -> None:
    """Create repair issue when protocol negotiation fails.

    Symptoms: Connection established but commands produce unexpected responses.
    Checks: Is device firmware updated? Does device type match (SIG vs Telink)?
            Check if bridge firmware is compatible with device protocol version.
    """
    from homeassistant.helpers import issue_registry as ir

    issue_id = _scoped_issue_id(ISSUE_PROTOCOL_MISMATCH, entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_PROTOCOL_MISMATCH,
        translation_placeholders={
            "device": device_name,
            "protocol": expected_protocol,
            "info": actual_info or "unknown",
        },
    )
    _LOGGER.error(
        "[%s] Repair issue created: %s for %s (protocol=%s info=%s)",
        entry_id[:8],
        ISSUE_PROTOCOL_MISMATCH,
        device_name,
        expected_protocol,
        actual_info,
    )


async def async_create_issue_provisioning_failed(
    hass: HomeAssistant,
    device_name: str,
    entry_id: str,
) -> None:
    """Create a repair issue when device provisioning fails.

    Symptoms: SIG Mesh device not controllable after setup.
    Checks: Was device in factory-reset state? Was it in BLE range?
    Fix: Delete integration and re-add. Factory-reset device first (5x power cycle).
    """
    from homeassistant.helpers import issue_registry as ir

    issue_id = _scoped_issue_id(ISSUE_PROVISIONING_FAILED, entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_PROVISIONING_FAILED,
        translation_placeholders={"device": device_name},
    )
    _LOGGER.warning(
        "[%s] Repair issue created: %s for %s",
        entry_id[:8],
        ISSUE_PROVISIONING_FAILED,
        device_name,
    )


def async_delete_issue(hass: HomeAssistant, base_id: str, entry_id: str) -> None:
    """Clear a repair issue for a specific entry when the problem resolves."""
    from homeassistant.helpers import issue_registry as ir

    issue_id = _scoped_issue_id(base_id, entry_id)
    ir.async_delete_issue(hass, DOMAIN, issue_id)
    _LOGGER.debug("[%s] Repair issue cleared: %s", entry_id[:8], base_id)


def async_delete_all_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Clear all repair issues for a specific config entry.

    Called on successful reconnect. Only deletes issues belonging to the
    given entry_id — issues for other entries are left intact.
    """
    for base_id in _ALL_BASE_ISSUE_IDS:
        async_delete_issue(hass, base_id, entry_id)


class TuyaBLEMeshRepairFlow(RepairsFlow):
    """Repair flow for Tuya BLE Mesh issues.

    Routes to the appropriate fix step based on the base issue ID.
    The full issue_id is scoped (e.g. "auth_or_mesh_mismatch--abc123"),
    so routing uses the extracted base ID.
    """

    def __init__(self, issue_id: str | None = None) -> None:
        """Initialize repair flow with the (scoped) issue_id."""
        super().__init__()
        self._issue_id = issue_id or ""
        # Extract base ID for routing (strips the --{entry_id} suffix)
        self._base_id = _base_of_scoped(self._issue_id)

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Dispatch to appropriate fix step based on base issue type."""
        if self._base_id in (ISSUE_AUTH_OR_MESH_MISMATCH, ISSUE_KEY_MISMATCH):
            return await self.async_step_reauth_hint()
        if self._base_id == ISSUE_RECONNECT_STORM:
            return await self.async_step_storm_confirm()
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Generic confirmation step — acknowledge the issue."""
        if user_input is not None:
            return self.async_create_entry(data={})

        return self.async_show_form(step_id="confirm")

    async def async_step_reauth_hint(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Guide user to re-enter credentials via options flow."""
        if user_input is not None:
            return self.async_create_entry(data={})

        return self.async_show_form(step_id="reauth_hint")

    async def async_step_storm_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Confirm reconnect storm acknowledgement — throttles reconnects."""
        if user_input is not None:
            return self.async_create_entry(data={})

        return self.async_show_form(step_id="storm_confirm")


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Return the repair flow for the given (scoped) issue ID."""
    return TuyaBLEMeshRepairFlow(issue_id)
