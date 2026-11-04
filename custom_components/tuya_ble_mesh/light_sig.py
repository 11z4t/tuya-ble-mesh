"""Light entity for SIG Mesh lights (Generic OnOff / Light Lightness / CTL / HSL).

Mappings (HA <-> mesh):
- Brightness: HA 1-255 <-> Light Lightness 0-65535 (linear)
- Colour temperature: Kelvin on both sides, clamped to the device CTL range
- Colour: HA hs (hue 0-360, saturation 0-100) <-> Light HSL hue/saturation 0-65535.
  In HSL, lightness 100% is white for any hue and full colour is at 50%, so in
  colour mode HA brightness 1-255 maps to HSL lightness 0-50% (0-0x7FFF).
- Supported modes: COLOR_TEMP (Light CTL), HS (Light HSL)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)

from custom_components.tuya_ble_mesh.entity import TuyaBLEMeshEntity

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceInfo

    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

_LOGGER = logging.getLogger(__name__)

LIGHTNESS_MAX = 0xFFFF
HSL_LIGHTNESS_MAX = 0x7FFF  # fully saturated colour (HSL L = 0.5)
MODE_COLOR = "color"
MODE_WHITE = "white"


def brightness_to_lightness(brightness: int) -> int:
    """Convert HA brightness (0-255) to Light Lightness (0-65535)."""
    clamped = max(0, min(int(brightness), 255))
    return round(clamped * LIGHTNESS_MAX / 255)


def lightness_to_brightness(lightness: int) -> int:
    """Convert Light Lightness (0-65535) to HA brightness (1-255, 0 stays 0)."""
    if lightness <= 0:
        return 0
    return max(1, min(255, round(lightness * 255 / LIGHTNESS_MAX)))


def brightness_to_hsl_lightness(brightness: int) -> int:
    """Convert HA brightness (0-255) to HSL lightness (0-0x7FFF, i.e. up to L=0.5)."""
    clamped = max(0, min(int(brightness), 255))
    return round(clamped * HSL_LIGHTNESS_MAX / 255)


def hsl_lightness_to_brightness(lightness: int) -> int:
    """Convert HSL lightness to HA brightness (values above L=0.5 count as full)."""
    if lightness <= 0:
        return 0
    return max(1, min(255, round(lightness * 255 / HSL_LIGHTNESS_MAX)))


def hs_to_mesh(hs_color: tuple[float, float]) -> tuple[int, int]:
    """Convert HA (hue 0-360, saturation 0-100) to mesh hue/saturation (0-65535)."""
    hue, sat = hs_color
    mesh_hue = round((hue % 360.0) * LIGHTNESS_MAX / 360.0)
    mesh_sat = round(max(0.0, min(sat, 100.0)) * LIGHTNESS_MAX / 100.0)
    return mesh_hue, mesh_sat


class TuyaBLEMeshSIGLight(TuyaBLEMeshEntity, LightEntity):
    """Light entity for a SIG Mesh RGB+CCT light."""

    _attr_should_poll = False
    _attr_name = None  # Use device name as entity name
    _attr_unique_id: str
    _attr_supported_features = LightEntityFeature.TRANSITION

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        """Initialize the SIG Mesh light entity.

        Args:
            coordinator: Coordinator managing the device state.
            entry_id: Config entry ID.
            device_info: Device registry info.
        """
        super().__init__(coordinator, entry_id, device_info)
        self._attr_unique_id = f"{coordinator.device.address}_light"
        self._attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.HS}
        lo, hi = getattr(coordinator.device, "temp_range_k", (2700, 6500))
        self._attr_min_color_temp_kelvin = lo
        self._attr_max_color_temp_kelvin = hi

    # --- State ---

    @property
    def is_on(self) -> bool:
        """Return True if the light is on."""
        return self.coordinator.state.is_on

    @property
    def brightness(self) -> int | None:
        """Return the current brightness (HA 1-255)."""
        lightness = self.coordinator.state.lightness
        if not self.coordinator.state.is_on or lightness is None:
            return None
        if self.coordinator.state.light_mode == MODE_COLOR:
            return hsl_lightness_to_brightness(lightness)
        return lightness_to_brightness(lightness)

    @property
    def color_mode(self) -> ColorMode:
        """Return the active colour mode."""
        if self.coordinator.state.light_mode == MODE_COLOR:
            return ColorMode.HS
        return ColorMode.COLOR_TEMP

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the colour temperature in Kelvin (white mode)."""
        if self.color_mode != ColorMode.COLOR_TEMP:
            return None
        return self.coordinator.state.color_temp_kelvin

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return the hue/saturation colour (colour mode)."""
        if self.color_mode != ColorMode.HS:
            return None
        return self.coordinator.state.hs_color

    # --- Commands ---

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, optionally setting brightness, colour temperature or colour.

        Commands are serialized by the platform (PARALLEL_UPDATES = 1).
        """
        device: Any = self.coordinator.device
        state = self.coordinator.state
        brightness: int | None = kwargs.get(ATTR_BRIGHTNESS)
        kelvin: int | None = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        hs_color: tuple[float, float] | None = kwargs.get(ATTR_HS_COLOR)
        transition: float | None = kwargs.get(ATTR_TRANSITION)

        # Brightness to apply: requested, else the current one, else full
        target_brightness = brightness if brightness is not None else (self.brightness or 255)
        color_mode_now = state.light_mode == MODE_COLOR and state.hs_color is not None
        if hs_color is None and kelvin is None and brightness is not None and color_mode_now:
            # Brightness change while showing a colour: re-send HSL so the
            # colour is kept (Light Lightness above 50% would wash it to white)
            hs_color = state.hs_color

        if transition and not state.is_on:
            # Tuya firmware ignores the fade when a colour/white/brightness Set
            # turns the light on, but honours it on Generic OnOff: fade up first.
            await self.coordinator.send_command_with_retry(
                lambda: device.send_onoff(True, transition), description="power_on_fade"
            )
            if hs_color is None and kelvin is None and brightness is None:
                self.coordinator.assume_state(dict(kwargs), {"is_on": True})
                return

        if hs_color is not None:
            hsl_lightness = brightness_to_hsl_lightness(target_brightness)
            hue, sat = hs_to_mesh(hs_color)
            sent: dict[str, Any] = {
                "is_on": hsl_lightness > 0,
                "lightness": hsl_lightness,
                "hs_color": (float(hs_color[0]), float(hs_color[1])),
                "light_mode": MODE_COLOR,
            }
            await self.coordinator.send_command_with_retry(
                lambda: device.send_hsl(hsl_lightness, hue, sat, transition),
                description="hsl_set",
            )
            self.coordinator.assume_state(dict(kwargs), sent)
            return

        lightness = brightness_to_lightness(target_brightness)
        sent = {"is_on": lightness > 0}
        if kelvin is not None:
            lo, hi = self._attr_min_color_temp_kelvin, self._attr_max_color_temp_kelvin
            kelvin = max(lo, min(int(kelvin), hi))
            sent.update(lightness=lightness, color_temp_kelvin=kelvin, light_mode=MODE_WHITE)
            await self.coordinator.send_command_with_retry(
                lambda: device.send_ctl(lightness, kelvin, transition), description="ctl_set"
            )
        elif brightness is not None:
            sent["lightness"] = lightness
            await self.coordinator.send_command_with_retry(
                lambda: device.send_lightness(lightness, transition),
                description="lightness_set",
            )
        elif transition is not None:
            await self.coordinator.send_command_with_retry(
                lambda: device.send_onoff(True, transition), description="power_on"
            )
        else:
            await self.coordinator.send_command_with_retry(
                lambda: device.send_power(True), description="power_on"
            )
        self.coordinator.assume_state(dict(kwargs), sent)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off (Generic OnOff), optionally fading out."""
        device: Any = self.coordinator.device
        transition: float | None = kwargs.get(ATTR_TRANSITION)
        if transition is not None:
            await self.coordinator.send_command_with_retry(
                lambda: device.send_onoff(False, transition), description="power_off"
            )
        else:
            await self.coordinator.send_command_with_retry(
                lambda: device.send_power(False), description="power_off"
            )
        self.coordinator.assume_state({"is_on": False}, {"is_on": False})
