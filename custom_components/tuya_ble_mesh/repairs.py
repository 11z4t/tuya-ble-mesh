"""Repair issues for the Tuya BLE Mesh integration.

Creates actionable repair issues in HA when provisioning fails,
bridge becomes unreachable, or key mismatches are detected.
Issues are automatically cleared when the problem resolves.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.repairs import RepairsFlow
from homeassistant.data_entry_flow import FlowResult

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Issue IDs
ISSUE_PROVISIONING_FAILED = "provisioning_failed"
ISSUE_BRIDGE_UNREACHABLE = "bridge_unreachable"
ISSUE_KEY_MISMATCH = "key_mismatch"
ISSUE_AUTH_OR_MESH_MISMATCH = "auth_or_mesh_mismatch"
ISSUE_DEVICE_NOT_FOUND = "device_not_found"
ISSUE_TIMEOUT = "timeout"
ISSUE_RECONNECT_STORM = "reconnect_storm"

DOMAIN = "tuya_ble_mesh"


async def async_create_issue_provisioning_failed(
    hass: HomeAssistant,
    device_name: str,
) -> None:
    """Create a repair issue when device provisioning fails.

    Args:
        hass: Home Assistant instance.
        device_name: Display name of the device that failed provisioning.
    """
    from homeassistant.helpers import issue_registry as ir

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_PROVISIONING_FAILED,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="provisioning_failed",
        translation_placeholders={"device": device_name},
    )
    _LOGGER.warning("Repair issue created: provisioning_failed for %s", device_name)


async def async_create_issue_bridge_unreachable(
    hass: HomeAssistant,
    host: str,
    port: int,
    entry_id: str | None = None,
) -> None:
    """Create a repair issue when the bridge daemon cannot be reached."""
    from homeassistant.helpers import issue_registry as ir

    issue_id = f"{ISSUE_BRIDGE_UNREACHABLE}_{entry_id}" if entry_id else ISSUE_BRIDGE_UNREACHABLE
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="bridge_unreachable",
        translation_placeholders={"host": host, "port": str(port)},
    )
    _LOGGER.warning("Repair issue created: bridge_unreachable for %s:%d", host, port)


async def async_create_issue_auth_or_mesh_mismatch(
    hass: HomeAssistant,
    device_name: str,
    entry_id: str | None = None,
) -> None:
    """Create a repair issue when mesh authentication fails."""
    from homeassistant.helpers import issue_registry as ir

    issue_id = f"{ISSUE_AUTH_OR_MESH_MISMATCH}_{entry_id}" if entry_id else ISSUE_AUTH_OR_MESH_MISMATCH  # noqa: E501
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="auth_failed",
        translation_placeholders={"device": device_name},
    )
    _LOGGER.warning("Repair issue created: auth_or_mesh_mismatch for %s", device_name)


async def async_create_issue_device_not_found(
    hass: HomeAssistant,
    device_name: str,
    mac: str,
    entry_id: str | None = None,
) -> None:
    """Create a repair issue when the device cannot be found."""
    from homeassistant.helpers import issue_registry as ir

    issue_id = f"{ISSUE_DEVICE_NOT_FOUND}_{entry_id}" if entry_id else ISSUE_DEVICE_NOT_FOUND
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="device_not_found",
        translation_placeholders={"device": device_name},
    )
    _LOGGER.warning("Repair issue created: device_not_found for %s (%s)", device_name, mac)


async def async_create_issue_timeout(
    hass: HomeAssistant,
    device_name: str,
    entry_id: str | None = None,
) -> None:
    """Create a repair issue when device connection times out."""
    from homeassistant.helpers import issue_registry as ir

    issue_id = f"{ISSUE_TIMEOUT}_{entry_id}" if entry_id else ISSUE_TIMEOUT
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="connection_timeout",
        translation_placeholders={"device": device_name},
    )
    _LOGGER.warning("Repair issue created: timeout for %s", device_name)


async def async_create_issue_reconnect_storm(
    hass: HomeAssistant,
    device_name: str,
    reconnect_count: int,
    entry_id: str | None = None,
    window_minutes: int = 5,
) -> None:
    """Create a repair issue when reconnect storm is detected."""
    from homeassistant.helpers import issue_registry as ir

    issue_id = f"{ISSUE_RECONNECT_STORM}_{entry_id}" if entry_id else ISSUE_RECONNECT_STORM
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="reconnect_storm",
        translation_placeholders={"device": device_name, "count": str(reconnect_count)},
    )
    _LOGGER.warning(
        "Repair issue created: reconnect_storm for %s (%d reconnects in %d min)",
        device_name,
        reconnect_count,
        window_minutes,
    )


def async_delete_issue(hass: HomeAssistant, issue_id: str, entry_id: str | None = None) -> None:
    """Clear a repair issue when the problem resolves."""
    from homeassistant.helpers import issue_registry as ir

    full_id = f"{issue_id}_{entry_id}" if entry_id else issue_id
    ir.async_delete_issue(hass, DOMAIN, full_id)
    _LOGGER.debug("Repair issue cleared: %s", full_id)


class TuyaBLEMeshRepairFlow(RepairsFlow):
    """Repair flow for Tuya BLE Mesh issues.

    Guides the user through resolving provisioning or connectivity issues.
    """

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Handle the first step of the repair flow.

        Args:
            user_input: User-provided input (unused for confirmation step).

        Returns:
            Flow result dict.
        """
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        """Confirm the repair action.

        Args:
            user_input: User-provided input.

        Returns:
            Flow result dict.
        """
        if user_input is not None:
            return self.async_create_entry(data={})

        return self.async_show_form(step_id="confirm")


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Return the repair flow for the given issue.

    Args:
        hass: Home Assistant instance.
        issue_id: The issue ID to create a fix flow for.
        data: Optional additional data associated with the issue.

    Returns:
        A RepairsFlow instance to guide the user through fixing the issue.
    """
    return TuyaBLEMeshRepairFlow()
