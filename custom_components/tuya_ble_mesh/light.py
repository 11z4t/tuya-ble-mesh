"""Light entity platform for Tuya BLE Mesh.

Mappings:
- Brightness: device 1-100 <-> HA 1-255 (linear)
- Color temp: device 0(warm)-127(cool) <-> mireds 370(warm)-153(cool) (inverse)
- Color brightness: device 0-255 <-> HA 0-255 (same scale)
- Supported modes: COLOR_TEMP, RGB
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.tuya_ble_mesh.const import (
    CONF_DEVICE_TYPE,
    DEVICE_BRIGHTNESS_MAX,
    DEVICE_BRIGHTNESS_MIN,
    DEVICE_COLOR_TEMP_MAX,
    DEVICE_COLOR_TEMP_MIN,
    DOMAIN,
    HA_BRIGHTNESS_MAX,
    HA_BRIGHTNESS_MIN,
    HA_MIRED_MAX,
    HA_MIRED_MIN,
    PLUG_DEVICE_TYPES,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

    AddEntitiesCallback = Callable[..., None]

_LOGGER = logging.getLogger(__name__)


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
    if entry.data.get(CONF_DEVICE_TYPE) in PLUG_DEVICE_TYPES:
        return
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: TuyaBLEMeshCoordinator = entry_data["coordinator"]
    device_info: DeviceInfo = entry_data["device_info"]
    async_add_entities([TuyaBLEMeshLight(coordinator, entry.entry_id, device_info)])


class TuyaBLEMeshLight(LightEntity):
    """Light entity for a Tuya BLE Mesh device."""

    _attr_should_poll = False
    _attr_supported_features = LightEntityFeature.TRANSITION
    _attr_has_entity_name = True
    _attr_name = None  # Use device name as entity name

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device.address}_light"
        if device_info is not None:
            self._attr_device_info = device_info
        self._remove_listener: Any = None
        self._transition_task: asyncio.Task[None] | None = None

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._attr_unique_id

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
        if self._coordinator.state.mode == 1:
            return self._coordinator.state.color_brightness
        return brightness_to_ha(self._coordinator.state.brightness)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the current color temperature in kelvin."""
        if not self._coordinator.state.is_on:
            return None
        mired = color_temp_to_ha(self._coordinator.state.color_temp)
        return round(1_000_000 / mired)

    _attr_min_color_temp_kelvin = 2703  # warmest (370 mireds)
    _attr_max_color_temp_kelvin = 6535  # coolest (153 mireds)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the current RGB color."""
        if not self._coordinator.state.is_on:
            return None
        if self._coordinator.state.mode != 1:
            return None
        state = self._coordinator.state
        return (state.red, state.green, state.blue)

    @property
    def color_mode(self) -> ColorMode:
        """Return the current color mode."""
        if self._coordinator.state.mode == 1:
            return ColorMode.RGB
        return ColorMode.COLOR_TEMP

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return supported color modes."""
        return {ColorMode.COLOR_TEMP, ColorMode.RGB}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light.

        Args:
            **kwargs: Optional brightness, color_temp, rgb_color, and transition.
        """
        self._cancel_transition()
        device = self._coordinator.device
        transition: float | None = kwargs.get(ATTR_TRANSITION)

        brightness = kwargs.get("brightness")
        color_temp_kelvin: int | None = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        color_temp = round(1_000_000 / color_temp_kelvin) if color_temp_kelvin is not None else None
        rgb_color: tuple[int, int, int] | None = kwargs.get(ATTR_RGB_COLOR)

        has_target = brightness is not None or color_temp is not None or rgb_color is not None
        if transition is not None and transition > 0 and has_target:
            target_bright = brightness_to_device(brightness) if brightness is not None else None
            target_temp = color_temp_to_device(color_temp) if color_temp is not None else None
            target_rgb = rgb_color
            self._transition_task = asyncio.create_task(
                self._run_transition(target_bright, target_temp, transition, target_rgb=target_rgb)
            )
            return

        if rgb_color is not None:
            await device.send_color(rgb_color[0], rgb_color[1], rgb_color[2])
            await device.send_light_mode(1)
            _LOGGER.debug("Set RGB color: (%d,%d,%d)", *rgb_color)
            if brightness is not None:
                await device.send_color_brightness(brightness)
                _LOGGER.debug("Set color brightness: %d", brightness)
            return

        if color_temp is not None:
            if self._coordinator.state.mode == 1:
                await device.send_light_mode(0)
            device_temp = color_temp_to_device(color_temp)
            await device.send_color_temp(device_temp)
            _LOGGER.debug("Set color temp: HA %d mireds -> device %d", color_temp, device_temp)

        if brightness is not None:
            if self._coordinator.state.mode == 1:
                await device.send_color_brightness(brightness)
                _LOGGER.debug("Set color brightness: %d", brightness)
            else:
                device_brightness = brightness_to_device(brightness)
                await device.send_brightness(device_brightness)
                _LOGGER.debug("Set brightness: HA %d -> device %d", brightness, device_brightness)

        if not has_target:
            await device.send_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light.

        Args:
            **kwargs: Optional transition.
        """
        self._cancel_transition()
        transition: float | None = kwargs.get(ATTR_TRANSITION)

        if transition is not None and transition > 0:
            self._transition_task = asyncio.create_task(
                self._run_transition(
                    target_brightness=DEVICE_BRIGHTNESS_MIN,
                    target_color_temp=None,
                    duration=transition,
                    power_off_after=True,
                )
            )
            return

        await self._coordinator.device.send_power(False)

    def _cancel_transition(self) -> None:
        """Cancel any in-progress transition task."""
        if self._transition_task is not None and not self._transition_task.done():
            self._transition_task.cancel()
        self._transition_task = None

    async def _run_transition(
        self,
        target_brightness: int | None,
        target_color_temp: int | None,
        duration: float,
        *,
        power_off_after: bool = False,
        target_rgb: tuple[int, int, int] | None = None,
    ) -> None:
        """Run a gradual transition by sending incremental commands.

        Args:
            target_brightness: Target device brightness (1-100), or None.
            target_color_temp: Target device color temp (0-127), or None.
            duration: Transition duration in seconds.
            power_off_after: Send power off after transition completes.
            target_rgb: Target RGB color tuple, or None.
        """
        device = self._coordinator.device
        state = self._coordinator.state

        steps = min(int(duration * 10), 50)
        if steps < 2:
            steps = 2
        interval = duration / steps

        start_bright = state.brightness if target_brightness is not None else None
        start_temp = state.color_temp if target_color_temp is not None else None
        start_rgb: tuple[int, int, int] | None = None
        if target_rgb is not None:
            start_rgb = (state.red, state.green, state.blue)

        for i in range(1, steps + 1):
            fraction = i / steps

            if target_brightness is not None and start_bright is not None:
                val = round(start_bright + (target_brightness - start_bright) * fraction)
                val = max(DEVICE_BRIGHTNESS_MIN, min(val, DEVICE_BRIGHTNESS_MAX))
                await device.send_brightness(val)

            if target_color_temp is not None and start_temp is not None:
                val = round(start_temp + (target_color_temp - start_temp) * fraction)
                val = max(DEVICE_COLOR_TEMP_MIN, min(val, DEVICE_COLOR_TEMP_MAX))
                await device.send_color_temp(val)

            if target_rgb is not None and start_rgb is not None:
                r = round(start_rgb[0] + (target_rgb[0] - start_rgb[0]) * fraction)
                g = round(start_rgb[1] + (target_rgb[1] - start_rgb[1]) * fraction)
                b = round(start_rgb[2] + (target_rgb[2] - start_rgb[2]) * fraction)
                await device.send_color(
                    max(0, min(r, 255)),
                    max(0, min(g, 255)),
                    max(0, min(b, 255)),
                )

            if i < steps:
                await asyncio.sleep(interval)

        if power_off_after:
            await device.send_power(False)

    async def async_added_to_hass(self) -> None:
        """Register state listener when added to HA."""
        self._remove_listener = self._coordinator.add_listener(self._handle_coordinator_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove state listener when removed from HA."""
        self._cancel_transition()
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
