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
) -> None:
    """Create a repair issue when the bridge daemon cannot be reached.

    Args:
        hass: Home Assistant instance.
        host: Bridge hostname/IP.
        port: Bridge port.
    """
    from homeassistant.helpers import issue_registry as ir

    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_BRIDGE_UNREACHABLE,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="bridge_unreachable",
        translation_placeholders={"host": host, "port": str(port)},
    )
    _LOGGER.warning("Repair issue created: bridge_unreachable for %s:%d", host, port)


def async_delete_issue(hass: HomeAssistant, issue_id: str) -> None:
    """Clear a repair issue when the problem resolves.

    Args:
        hass: Home Assistant instance.
        issue_id: Issue ID to clear.
    """
    from homeassistant.helpers import issue_registry as ir

    ir.async_delete_issue(hass, DOMAIN, issue_id)
    _LOGGER.debug("Repair issue cleared: %s", issue_id)


class TuyaBLEMeshRepairFlow(RepairsFlow):  # type: ignore[misc]
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
