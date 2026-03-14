"""Minimal stub for homeassistant.components.light."""
from __future__ import annotations

from enum import IntFlag, StrEnum
from typing import Any

from homeassistant.helpers.entity import Entity


class ColorMode(StrEnum):
    UNKNOWN = "unknown"
    ONOFF = "onoff"
    BRIGHTNESS = "brightness"
    COLOR_TEMP = "color_temp"
    HS = "hs"
    XY = "xy"
    RGB = "rgb"
    RGBW = "rgbw"
    RGBWW = "rgbww"
    WHITE = "white"


class LightEntityFeature(IntFlag):
    EFFECT = 4
    FLASH = 8
    TRANSITION = 32


ATTR_BRIGHTNESS = "brightness"
ATTR_COLOR_MODE = "color_mode"
ATTR_COLOR_TEMP = "color_temp"
ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
ATTR_EFFECT = "effect"
ATTR_HS_COLOR = "hs_color"
ATTR_RGB_COLOR = "rgb_color"
ATTR_RGBW_COLOR = "rgbw_color"
ATTR_RGBWW_COLOR = "rgbww_color"
ATTR_SUPPORTED_COLOR_MODES = "supported_color_modes"
ATTR_TRANSITION = "transition"
ATTR_WHITE = "white"
SUPPORT_BRIGHTNESS = 1
SUPPORT_COLOR = 16
SUPPORT_COLOR_TEMP = 2


class LightEntity(Entity):
    _attr_color_mode: ColorMode | None = None
    _attr_brightness: int | None = None
    _attr_is_on: bool | None = None
    _attr_color_temp_kelvin: int | None = None
    _attr_rgb_color: tuple[int, int, int] | None = None
    _attr_effect: str | None = None
    _attr_supported_features: LightEntityFeature = LightEntityFeature(0)
    _attr_supported_color_modes: set[ColorMode] | None = None
    _attr_max_color_temp_kelvin: int = 6500
    _attr_min_color_temp_kelvin: int = 2000

    @property
    def is_on(self) -> bool | None:
        return self._attr_is_on

    @property
    def brightness(self) -> int | None:
        return self._attr_brightness

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._attr_color_temp_kelvin

    @property
    def min_color_temp_kelvin(self) -> int:
        return self._attr_min_color_temp_kelvin

    @property
    def max_color_temp_kelvin(self) -> int:
        return self._attr_max_color_temp_kelvin

    @property
    def supported_features(self) -> LightEntityFeature:
        return self._attr_supported_features

    @property
    def supported_color_modes(self) -> set[ColorMode] | None:
        return self._attr_supported_color_modes

    @property
    def color_mode(self) -> ColorMode | None:
        return self._attr_color_mode

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return self._attr_rgb_color

    @property
    def effect(self) -> str | None:
        return self._attr_effect

    async def async_turn_on(self, **kwargs: Any) -> None:
        pass

    async def async_turn_off(self, **kwargs: Any) -> None:
        pass

    def async_write_ha_state(self) -> None:
        pass
