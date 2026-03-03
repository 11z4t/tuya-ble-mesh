"""Light entity platform for Tuya BLE Mesh.

Mappings:
- Brightness: device 1-100 <-> HA 1-255 (linear)
- Color temp: device 0(warm)-127(cool) <-> mireds 370(warm)-153(cool) (inverse)
- Supported modes: COLOR_TEMP
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from custom_components.tuya_ble_mesh.const import (
    DEVICE_BRIGHTNESS_MAX,
    DEVICE_BRIGHTNESS_MIN,
    DEVICE_COLOR_TEMP_MAX,
    DEVICE_COLOR_TEMP_MIN,
    DOMAIN,
    HA_BRIGHTNESS_MAX,
    HA_BRIGHTNESS_MIN,
    HA_MIRED_MAX,
    HA_MIRED_MIN,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

    AddEntitiesCallback = Callable[..., None]

_LOGGER = logging.getLogger(__name__)

# HA color mode constant (avoid importing homeassistant at module level)
COLOR_MODE_COLOR_TEMP = "color_temp"


def brightness_to_ha(device_value: int) -> int:
    """Convert device brightness (1-100) to HA brightness (1-255).

    Args:
        device_value: Device brightness value.

    Returns:
        HA brightness value.
    """
    clamped = max(DEVICE_BRIGHTNESS_MIN, min(device_value, DEVICE_BRIGHTNESS_MAX))
    return round(
        HA_BRIGHTNESS_MIN
        + (clamped - DEVICE_BRIGHTNESS_MIN)
        * (HA_BRIGHTNESS_MAX - HA_BRIGHTNESS_MIN)
        / (DEVICE_BRIGHTNESS_MAX - DEVICE_BRIGHTNESS_MIN)
    )


def brightness_to_device(ha_value: int) -> int:
    """Convert HA brightness (1-255) to device brightness (1-100).

    Args:
        ha_value: HA brightness value.

    Returns:
        Device brightness value.
    """
    clamped = max(HA_BRIGHTNESS_MIN, min(ha_value, HA_BRIGHTNESS_MAX))
    return round(
        DEVICE_BRIGHTNESS_MIN
        + (clamped - HA_BRIGHTNESS_MIN)
        * (DEVICE_BRIGHTNESS_MAX - DEVICE_BRIGHTNESS_MIN)
        / (HA_BRIGHTNESS_MAX - HA_BRIGHTNESS_MIN)
    )


def color_temp_to_ha(device_value: int) -> int:
    """Convert device color temp (0=warm, 127=cool) to mireds (370=warm, 153=cool).

    Inverse mapping: higher device value = cooler = lower mireds.

    Args:
        device_value: Device color temp value.

    Returns:
        HA color temp in mireds.
    """
    clamped = max(DEVICE_COLOR_TEMP_MIN, min(device_value, DEVICE_COLOR_TEMP_MAX))
    return round(
        HA_MIRED_MAX
        - (clamped - DEVICE_COLOR_TEMP_MIN)
        * (HA_MIRED_MAX - HA_MIRED_MIN)
        / (DEVICE_COLOR_TEMP_MAX - DEVICE_COLOR_TEMP_MIN)
    )


def color_temp_to_device(mired_value: int) -> int:
    """Convert mireds (370=warm, 153=cool) to device color temp (0=warm, 127=cool).

    Inverse mapping: lower mireds = cooler = higher device value.

    Args:
        mired_value: HA color temp in mireds.

    Returns:
        Device color temp value.
    """
    clamped = max(HA_MIRED_MIN, min(mired_value, HA_MIRED_MAX))
    return round(
        DEVICE_COLOR_TEMP_MAX
        - (clamped - HA_MIRED_MIN)
        * (DEVICE_COLOR_TEMP_MAX - DEVICE_COLOR_TEMP_MIN)
        / (HA_MIRED_MAX - HA_MIRED_MIN)
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE Mesh light entities from a config entry.

    Args:
        hass: Home Assistant instance.
        entry: Config entry being set up.
        async_add_entities: Callback to register new entities.
    """
    coordinator: TuyaBLEMeshCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([TuyaBLEMeshLight(coordinator, entry.entry_id)])


class TuyaBLEMeshLight:
    """Light entity for a Tuya BLE Mesh device.

    Duck-typed to match HA's LightEntity interface.
    """

    def __init__(self, coordinator: TuyaBLEMeshCoordinator, entry_id: str) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device.address}_light"
        self._attr_name = f"Tuya BLE Mesh {coordinator.device.address[-8:]}"
        self._remove_listener: Any = None

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._attr_unique_id

    @property
    def name(self) -> str:
        """Return entity name."""
        return self._attr_name

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._coordinator.state.available

    @property
    def is_on(self) -> bool:
        """Return True if the light is on."""
        return self._coordinator.state.is_on

    @property
    def brightness(self) -> int | None:
        """Return the current brightness (HA 1-255)."""
        if not self._coordinator.state.is_on:
            return None
        return brightness_to_ha(self._coordinator.state.brightness)

    @property
    def color_temp(self) -> int | None:
        """Return the current color temperature in mireds."""
        if not self._coordinator.state.is_on:
            return None
        return color_temp_to_ha(self._coordinator.state.color_temp)

    @property
    def min_mireds(self) -> int:
        """Return minimum color temp in mireds (coolest)."""
        return HA_MIRED_MIN

    @property
    def max_mireds(self) -> int:
        """Return maximum color temp in mireds (warmest)."""
        return HA_MIRED_MAX

    @property
    def color_mode(self) -> str:
        """Return the current color mode."""
        return COLOR_MODE_COLOR_TEMP

    @property
    def supported_color_modes(self) -> set[str]:
        """Return supported color modes."""
        return {COLOR_MODE_COLOR_TEMP}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light.

        Args:
            **kwargs: Optional brightness and color_temp.
        """
        device = self._coordinator.device

        brightness = kwargs.get("brightness")
        color_temp = kwargs.get("color_temp")

        if brightness is not None:
            device_brightness = brightness_to_device(brightness)
            await device.send_brightness(device_brightness)
            _LOGGER.debug("Set brightness: HA %d -> device %d", brightness, device_brightness)

        if color_temp is not None:
            device_temp = color_temp_to_device(color_temp)
            await device.send_color_temp(device_temp)
            _LOGGER.debug("Set color temp: HA %d mireds -> device %d", color_temp, device_temp)

        if brightness is None and color_temp is None:
            await device.send_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        await self._coordinator.device.send_power(False)

    async def async_added_to_hass(self) -> None:
        """Register state listener when added to HA."""
        self._remove_listener = self._coordinator.add_listener(self._handle_coordinator_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove state listener when removed from HA."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # In HA, this would call self.async_write_ha_state()
        _LOGGER.debug("Light state updated: on=%s", self.is_on)
