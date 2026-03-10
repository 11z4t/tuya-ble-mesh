"""Light entity platform for Tuya BLE Mesh.

Brightness model (two separate internal scales):
- White brightness:  device 1-100  <-> HA 1-255  (linear, used in COLOR_TEMP mode)
- Color brightness:  device 0-255  <-> HA 0-255  (same scale, used in RGB mode)

All conversion helpers in this module use the HA scale (0/1–255) externally.
Device-native values are never exposed through entity properties.

Color temp mapping:
- device 0(warm)-127(cool) <-> mireds 370(warm)-153(cool) (inverse)

Supported modes: COLOR_TEMP, RGB
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (  # type: ignore[attr-defined]
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.tuya_ble_mesh.entity import TuyaBLEMeshEntity
from custom_components.tuya_ble_mesh.const import (
    CONF_DEVICE_TYPE,
    DEVICE_BRIGHTNESS_MAX,
    DEVICE_BRIGHTNESS_MIN,
    DEVICE_COLOR_BRIGHTNESS_MAX,
    DEVICE_COLOR_BRIGHTNESS_MIN,
    DEVICE_COLOR_TEMP_MAX,
    DEVICE_COLOR_TEMP_MIN,
    HA_BRIGHTNESS_MAX,
    HA_BRIGHTNESS_MIN,
    HA_MIRED_MAX,
    HA_MIRED_MIN,
    PLUG_DEVICE_TYPES,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from custom_components.tuya_ble_mesh import TuyaBLEMeshConfigEntry
    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

    AddEntitiesCallback = Callable[..., None]

_LOGGER = logging.getLogger(__name__)


def _log_task_exception(task: asyncio.Task[Any]) -> None:
    """Log exceptions from fire-and-forget tasks instead of silently swallowing."""
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        _LOGGER.warning("Background task %s failed: %s", task.get_name(), exc)


# BLE mesh serializes commands — limit to one concurrent update
PARALLEL_UPDATES = 1

# Debounce window for coalescing rapid slider commands (e.g. brightness drag)
_COMMAND_DEBOUNCE_INTERVAL = 0.05  # 50 ms


def brightness_to_ha(device_value: int) -> int:
    """Convert white brightness from device scale (1-100) to HA scale (1-255).

    Used in COLOR_TEMP mode.

    Args:
        device_value: Device white brightness (1-100).

    Returns:
        HA brightness (1-255).
    """
    clamped = max(DEVICE_BRIGHTNESS_MIN, min(device_value, DEVICE_BRIGHTNESS_MAX))
    return round(
        HA_BRIGHTNESS_MIN
        + (clamped - DEVICE_BRIGHTNESS_MIN)
        * (HA_BRIGHTNESS_MAX - HA_BRIGHTNESS_MIN)
        / (DEVICE_BRIGHTNESS_MAX - DEVICE_BRIGHTNESS_MIN)
    )


def brightness_to_device(ha_value: int) -> int:
    """Convert white brightness from HA scale (1-255) to device scale (1-100).

    Used in COLOR_TEMP mode.

    Args:
        ha_value: HA brightness (1-255).

    Returns:
        Device white brightness (1-100).
    """
    clamped = max(HA_BRIGHTNESS_MIN, min(ha_value, HA_BRIGHTNESS_MAX))
    return round(
        DEVICE_BRIGHTNESS_MIN
        + (clamped - HA_BRIGHTNESS_MIN)
        * (DEVICE_BRIGHTNESS_MAX - DEVICE_BRIGHTNESS_MIN)
        / (HA_BRIGHTNESS_MAX - HA_BRIGHTNESS_MIN)
    )


def color_brightness_to_ha(device_value: int) -> int:
    """Convert color brightness from device scale (0-255) to HA scale (0-255).

    Used in RGB mode. Scales are identical — this exists for symmetry and
    explicit clamping.

    Args:
        device_value: Device color brightness (0-255).

    Returns:
        HA brightness (0-255).
    """
    return max(DEVICE_COLOR_BRIGHTNESS_MIN, min(device_value, DEVICE_COLOR_BRIGHTNESS_MAX))


def color_brightness_to_device(ha_value: int) -> int:
    """Convert color brightness from HA scale (0-255) to device scale (0-255).

    Used in RGB mode. Scales are identical — this exists for symmetry and
    explicit clamping.

    Args:
        ha_value: HA brightness (0-255).

    Returns:
        Device color brightness (0-255).
    """
    return max(DEVICE_COLOR_BRIGHTNESS_MIN, min(ha_value, DEVICE_COLOR_BRIGHTNESS_MAX))


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


@dataclass(frozen=True)
class _TurnOnCommand:
    """Device-native turn-on parameters, ready to hand to the transition engine.

    All values are on the **device scale** (not HA scale):

    Attributes:
        power_on: True when no other parameter was provided — send power(True).
        brightness: Device-native brightness value, or None.
            - COLOR_TEMP mode: device white brightness (1-100).
            - RGB mode: device color brightness (0-255).
        use_color_brightness: True when brightness is on the 0-255 color scale
            (RGB mode); False when it is on the 1-100 white scale.
        color_temp: Device color temp (0-127), or None.
        rgb: RGB tuple, or None.
    """

    power_on: bool
    brightness: int | None
    use_color_brightness: bool
    color_temp: int | None
    rgb: tuple[int, int, int] | None


def _build_turn_on_command(
    brightness: int | None,
    color_temp: int | None,
    rgb_color: tuple[int, int, int] | None,
    has_target: bool,
    current_mode: int,
) -> _TurnOnCommand:
    """Compute device-native turn-on parameters from HA-scale inputs.

    Centralises the brightness-scale mode-detection that was previously
    duplicated between the transition branch and the debounced path.

    Args:
        brightness: HA brightness (1-255), or None.
        color_temp: Color temp in mireds, or None.
        rgb_color: RGB tuple in HA scale, or None.
        has_target: True if any parameter was provided.
        current_mode: Current device mode (0=COLOR_TEMP, 1=RGB).

    Returns:
        A :class:`_TurnOnCommand` with all values on the device scale.
    """
    # Determine which brightness scale to use:
    # - RGB target supplied → color scale (0-255)
    # - No CT target AND already in RGB mode → color scale (0-255)
    # - Otherwise → white brightness scale (1-100)
    use_color_brightness = rgb_color is not None or (
        brightness is not None and color_temp is None and current_mode == 1
    )

    device_brightness: int | None = None
    if brightness is not None:
        if use_color_brightness:
            device_brightness = color_brightness_to_device(brightness)
        else:
            device_brightness = brightness_to_device(brightness)

    device_color_temp: int | None = None
    if color_temp is not None:
        device_color_temp = color_temp_to_device(color_temp)

    return _TurnOnCommand(
        power_on=not has_target,
        brightness=device_brightness,
        use_color_brightness=use_color_brightness,
        color_temp=device_color_temp,
        rgb=rgb_color,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuyaBLEMeshConfigEntry,
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
    runtime_data = entry.runtime_data
    coordinator: TuyaBLEMeshCoordinator = runtime_data.coordinator
    device_info: DeviceInfo = runtime_data.device_info
    async_add_entities([TuyaBLEMeshLight(coordinator, entry.entry_id, device_info)])


class TuyaBLEMeshLight(TuyaBLEMeshEntity, LightEntity):
    """Light entity for a Tuya BLE Mesh device."""

    _attr_should_poll = False
    _attr_supported_features = LightEntityFeature.TRANSITION
    _attr_name = None  # Use device name as entity name
    _attr_unique_id: str

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        """Initialize the light entity.

        Args:
            coordinator: Coordinator managing the BLE mesh device state.
            entry_id: Config entry ID used to scope the unique entity ID.
            device_info: Device registry info for grouping entities under a device.
        """
        super().__init__(coordinator, entry_id, device_info)
        self._attr_unique_id = f"{coordinator.device.address}_light"
        self._transition_task: asyncio.Task[None] | None = None
        self._pending_command_task: asyncio.Task[None] | None = None

    @property
    def is_on(self) -> bool:
        """Return True if the light is on."""
        return self.coordinator.state.is_on

    @property
    def brightness(self) -> int | None:
        """Return the current brightness in HA scale (0/1-255).

        In RGB mode (mode==1): returns color brightness (0-255).
        In COLOR_TEMP mode: returns white brightness converted from device 1-100 to HA 1-255.
        Both paths return values on the HA scale — the difference is the underlying
        device scale, handled by the conversion helpers.
        """
        if not self.coordinator.state.is_on:
            return None
        if self.coordinator.state.mode == 1:
            return color_brightness_to_ha(self.coordinator.state.color_brightness)
        return brightness_to_ha(self.coordinator.state.brightness)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the current color temperature in kelvin."""
        if not self.coordinator.state.is_on:
            return None
        if self.coordinator.state.color_temp == 0:
            return None
        mired = color_temp_to_ha(self.coordinator.state.color_temp)
        if mired == 0:
            return None
        return round(1_000_000 / mired)

    _attr_min_color_temp_kelvin = 2703  # warmest (370 mireds)
    _attr_max_color_temp_kelvin = 6535  # coolest (153 mireds)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the current RGB color."""
        if not self.coordinator.state.is_on:
            return None
        if self.coordinator.state.mode != 1:
            return None
        state = self.coordinator.state
        return (state.red, state.green, state.blue)

    @property
    def color_mode(self) -> ColorMode:
        """Return the current color mode."""
        if self.coordinator.state.mode == 1:
            return ColorMode.RGB
        return ColorMode.COLOR_TEMP

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        """Return supported color modes."""
        return {ColorMode.COLOR_TEMP, ColorMode.RGB}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light.

        Immediate (non-transition) commands are debounced over a short window
        to coalesce rapid slider moves (e.g. brightness drag) into a single
        BLE command, reducing mesh traffic.

        Args:
            **kwargs: Optional brightness, color_temp, rgb_color, and transition.
        """
        self._cancel_transition()
        self._cancel_pending_command()

        transition: float | None = kwargs.get(ATTR_TRANSITION)
        brightness = kwargs.get("brightness")
        color_temp_kelvin: int | None = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        color_temp = round(1_000_000 / color_temp_kelvin) if color_temp_kelvin else None
        rgb_color: tuple[int, int, int] | None = kwargs.get(ATTR_RGB_COLOR)
        has_target = brightness is not None or color_temp is not None or rgb_color is not None

        if transition is not None and transition > 0 and has_target:
            cmd = _build_turn_on_command(
                brightness, color_temp, rgb_color, has_target, self.coordinator.state.mode
            )
            self._transition_task = asyncio.create_task(
                self._run_transition(
                    cmd.brightness,
                    cmd.color_temp,
                    transition,
                    target_rgb=cmd.rgb,
                    use_color_brightness=cmd.use_color_brightness,
                )
            )
            self._transition_task.add_done_callback(_log_task_exception)
            return

        # Debounce: schedule command after short window so rapid slider
        # moves cancel the previous pending command and only the latest fires.
        self._pending_command_task = asyncio.create_task(
            self._debounced_send_turn_on(brightness, color_temp, rgb_color, has_target)
        )
        self._pending_command_task.add_done_callback(_log_task_exception)

    async def _debounced_send_turn_on(
        self,
        brightness: int | None,
        color_temp: int | None,
        rgb_color: tuple[int, int, int] | None,
        has_target: bool,
    ) -> None:
        """Send turn-on command after debounce interval.

        Called from a task; cancelled if a newer command arrives within
        _COMMAND_DEBOUNCE_INTERVAL.

        Args:
            brightness: HA brightness value (1-255), or None.
            color_temp: Color temp in mireds, or None.
            rgb_color: RGB tuple, or None.
            has_target: True if any parameter was specified.
        """
        await asyncio.sleep(_COMMAND_DEBOUNCE_INTERVAL)
        self._pending_command_task = None

        device = self.coordinator.device
        retry = self.coordinator.send_command_with_retry

        if rgb_color is not None:
            r, g, b = rgb_color
            await retry(
                lambda: device.send_color(r, g, b),  # type: ignore[arg-type]
                description=f"send_color({r},{g},{b})",
            )
            await retry(lambda: device.send_light_mode(1), description="send_light_mode(1)")  # type: ignore[arg-type]
            _LOGGER.debug("Set RGB color: (%d,%d,%d)", *rgb_color)
            if brightness is not None:
                device_color_bright = color_brightness_to_device(brightness)
                await retry(
                    lambda: device.send_color_brightness(device_color_bright),  # type: ignore[arg-type]
                    description=f"send_color_brightness({device_color_bright})",
                )
                _LOGGER.debug(
                    "Set color brightness: HA %d -> device %d", brightness, device_color_bright
                )
            return

        if color_temp is not None:
            if self.coordinator.state.mode == 1:
                await retry(lambda: device.send_light_mode(0), description="send_light_mode(0)")  # type: ignore[arg-type]
            device_temp = color_temp_to_device(color_temp)
            await retry(
                lambda: device.send_color_temp(device_temp),  # type: ignore[arg-type]
                description=f"send_color_temp({device_temp})",
            )
            _LOGGER.debug("Set color temp: HA %d mireds -> device %d", color_temp, device_temp)

        if brightness is not None:
            if self.coordinator.state.mode == 1:
                # RGB mode, brightness-only update — use color brightness scale
                device_color_bright = color_brightness_to_device(brightness)
                await retry(
                    lambda: device.send_color_brightness(device_color_bright),  # type: ignore[arg-type]
                    description=f"send_color_brightness({device_color_bright})",
                )
                _LOGGER.debug(
                    "Set color brightness: HA %d -> device %d", brightness, device_color_bright
                )
            else:
                device_brightness = brightness_to_device(brightness)
                await retry(
                    lambda: device.send_brightness(device_brightness),  # type: ignore[arg-type]
                    description=f"send_brightness({device_brightness})",
                )
                _LOGGER.debug("Set brightness: HA %d -> device %d", brightness, device_brightness)

        if not has_target:
            await retry(lambda: device.send_power(True), description="send_power(True)")  # type: ignore[arg-type]

    def _cancel_pending_command(self) -> None:
        """Cancel any pending debounced command task."""
        if self._pending_command_task is not None and not self._pending_command_task.done():
            self._pending_command_task.cancel()
        self._pending_command_task = None

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light.

        Args:
            **kwargs: Optional transition.
        """
        self._cancel_transition()
        self._cancel_pending_command()
        transition: float | None = kwargs.get(ATTR_TRANSITION)

        if transition is not None and transition > 0:
            current_mode = self.coordinator.state.mode
            # In RGB mode, fade color brightness (0-255 device) to 0.
            # In COLOR_TEMP mode, fade white brightness (1-100 device) to 1.
            if current_mode == 1:
                target_b = DEVICE_COLOR_BRIGHTNESS_MIN  # 0
                use_color_b = True
            else:
                target_b = DEVICE_BRIGHTNESS_MIN  # 1
                use_color_b = False
            self._transition_task = asyncio.create_task(
                self._run_transition(
                    target_brightness=target_b,
                    target_color_temp=None,
                    duration=transition,
                    power_off_after=True,
                    use_color_brightness=use_color_b,
                )
            )
            self._transition_task.add_done_callback(_log_task_exception)
            return

        await self.coordinator.send_command_with_retry(
            lambda: self.coordinator.device.send_power(False),  # type: ignore[arg-type]
            description="send_power(False)",
        )

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
        use_color_brightness: bool = False,
    ) -> None:
        """Run a gradual transition by sending incremental commands.

        Args:
            target_brightness: Target brightness on the device scale:
                - COLOR_TEMP mode: device white brightness (1-100)
                - RGB mode (use_color_brightness=True): device color brightness (0-255)
            target_color_temp: Target device color temp (0-127), or None.
            duration: Transition duration in seconds.
            power_off_after: Send power off after transition completes.
            target_rgb: Target RGB color tuple, or None.
            use_color_brightness: If True, fade via send_color_brightness (0-255 scale)
                instead of send_brightness (1-100 scale). Set for RGB mode fades.
        """
        device = self.coordinator.device
        state = self.coordinator.state

        steps = min(int(duration * 10), 50)
        if steps < 2:
            steps = 2
        interval = duration / steps

        # Pick the right starting brightness based on mode
        if use_color_brightness:
            start_bright = state.color_brightness if target_brightness is not None else None
            bright_min = DEVICE_COLOR_BRIGHTNESS_MIN
            bright_max = DEVICE_COLOR_BRIGHTNESS_MAX
        else:
            start_bright = state.brightness if target_brightness is not None else None
            bright_min = DEVICE_BRIGHTNESS_MIN
            bright_max = DEVICE_BRIGHTNESS_MAX

        start_temp = state.color_temp if target_color_temp is not None else None
        start_rgb: tuple[int, int, int] | None = None
        if target_rgb is not None:
            start_rgb = (state.red, state.green, state.blue)

        for i in range(1, steps + 1):
            fraction = i / steps

            if target_brightness is not None and start_bright is not None:
                val = round(start_bright + (target_brightness - start_bright) * fraction)
                val = max(bright_min, min(val, bright_max))
                if use_color_brightness:
                    await device.send_color_brightness(val)
                else:
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
            await self.coordinator.send_command_with_retry(
                lambda: device.send_power(False),  # type: ignore[arg-type]
                description="send_power(False) post-transition",
            )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up transition and pending command tasks."""
        self._cancel_transition()
        self._cancel_pending_command()
        await super().async_will_remove_from_hass()
