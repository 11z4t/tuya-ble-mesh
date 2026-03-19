"""Describe Tuya BLE Mesh logbook events."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.const import ATTR_FRIENDLY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict]], None],
) -> None:
    """Describe logbook events for Tuya BLE Mesh integration.

    Logs state changes for lights, switches, and sensors.
    """

    @callback
    def async_describe_state_change(event: Event) -> dict[str, str]:
        """Describe state change event for a Tuya BLE Mesh device."""
        entity_name = event.data.get(ATTR_FRIENDLY_NAME, "Device")
        old_state = event.data.get("old_state", "unknown")
        new_state = event.data.get("new_state", "unknown")

        return {
            LOGBOOK_ENTRY_NAME: "Tuya BLE Mesh",
            LOGBOOK_ENTRY_MESSAGE: (f"{entity_name} changed from {old_state} to {new_state}"),
        }

    # Register the state change event
    async_describe_event(DOMAIN, "state_changed", async_describe_state_change)
