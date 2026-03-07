"""Unit tests for the Tuya BLE Mesh light entity platform."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from homeassistant.components.light import ColorMode  # noqa: E402

from custom_components.tuya_ble_mesh.const import DOMAIN  # noqa: E402
from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshDeviceState,
)
from custom_components.tuya_ble_mesh.light import (  # noqa: E402
    TuyaBLEMeshLight,
    async_setup_entry,
    brightness_to_device,
    brightness_to_ha,
    color_temp_to_device,
    color_temp_to_ha,
)


def make_mock_coordinator(
    *,
    is_on: bool = True,
    brightness: int = 64,
    color_temp: int = 32,
    mode: int = 0,
    red: int = 0,
    green: int = 0,
    blue: int = 0,
    color_brightness: int = 0,
    available: bool = True,
) -> MagicMock:
    """Create a mock coordinator with configurable state."""
    coord = MagicMock()
    coord.state = TuyaBLEMeshDeviceState(
        is_on=is_on,
        brightness=brightness,
        color_temp=color_temp,
        mode=mode,
        red=red,
        green=green,
        blue=blue,
        color_brightness=color_brightness,
        available=available,
    )
    coord.device = MagicMock()
    coord.device.address = "DC:23:4D:21:43:A5"
    coord.device.send_power = AsyncMock()
    coord.device.send_brightness = AsyncMock()
    coord.device.send_color_temp = AsyncMock()
    coord.device.send_color = AsyncMock()
    coord.device.send_color_brightness = AsyncMock()
    coord.device.send_light_mode = AsyncMock()
    coord.add_listener = MagicMock(return_value=MagicMock())
    return coord


class TestBrightnessToHa:
    """Test device-to-HA brightness mapping."""

    def test_min_device_to_min_ha(self) -> None:
        assert brightness_to_ha(1) == 1

    def test_max_device_to_max_ha(self) -> None:
        assert brightness_to_ha(100) == 255

    def test_midpoint(self) -> None:
        result = brightness_to_ha(50)
        assert 125 <= result <= 130  # approximately 127

    def test_clamps_below_min(self) -> None:
        assert brightness_to_ha(0) == 1

    def test_clamps_above_max(self) -> None:
        assert brightness_to_ha(200) == 255


class TestBrightnessToDevice:
    """Test HA-to-device brightness mapping."""

    def test_min_ha_to_min_device(self) -> None:
        assert brightness_to_device(1) == 1

    def test_max_ha_to_max_device(self) -> None:
        assert brightness_to_device(255) == 100

    def test_midpoint(self) -> None:
        result = brightness_to_device(128)
        assert 49 <= result <= 51  # approximately 50

    def test_clamps_below_min(self) -> None:
        assert brightness_to_device(0) == 1

    def test_roundtrip(self) -> None:
        """Device -> HA -> device should be close to original."""
        for device_val in [1, 25, 50, 75, 100]:
            ha_val = brightness_to_ha(device_val)
            back = brightness_to_device(ha_val)
            assert abs(back - device_val) <= 1


class TestColorTempToHa:
    """Test device-to-HA color temp mapping (inverse)."""

    def test_warmest_device_to_warmest_mired(self) -> None:
        # Device 0 (warmest) -> mired 370 (warmest)
        assert color_temp_to_ha(0) == 370

    def test_coolest_device_to_coolest_mired(self) -> None:
        # Device 127 (coolest) -> mired 153 (coolest)
        assert color_temp_to_ha(127) == 153

    def test_midpoint(self) -> None:
        result = color_temp_to_ha(64)
        # Should be approximately midpoint between 153 and 370
        assert 255 <= result <= 265

    def test_clamps_below_min(self) -> None:
        assert color_temp_to_ha(-1) == 370

    def test_clamps_above_max(self) -> None:
        assert color_temp_to_ha(200) == 153


class TestColorTempToDevice:
    """Test HA-to-device color temp mapping (inverse)."""

    def test_warmest_mired_to_warmest_device(self) -> None:
        # Mired 370 (warmest) -> device 0 (warmest)
        assert color_temp_to_device(370) == 0

    def test_coolest_mired_to_coolest_device(self) -> None:
        # Mired 153 (coolest) -> device 127 (coolest)
        assert color_temp_to_device(153) == 127

    def test_midpoint(self) -> None:
        result = color_temp_to_device(262)
        assert 60 <= result <= 65

    def test_roundtrip(self) -> None:
        """Device -> HA -> device should be close to original."""
        for device_val in [0, 32, 64, 96, 127]:
            ha_val = color_temp_to_ha(device_val)
            back = color_temp_to_device(ha_val)
            assert abs(back - device_val) <= 1


class TestLightProperties:
    """Test TuyaBLEMeshLight properties."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert "DC:23:4D:21:43:A5" in light.unique_id

    def test_name(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert "21:43:A5" in light.name

    def test_available(self) -> None:
        coord = make_mock_coordinator(available=True)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.available is True

    def test_not_available(self) -> None:
        coord = make_mock_coordinator(available=False)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.available is False

    def test_is_on_true(self) -> None:
        coord = make_mock_coordinator(is_on=True)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.is_on is True

    def test_is_on_false(self) -> None:
        coord = make_mock_coordinator(is_on=False)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.is_on is False

    def test_brightness_when_on(self) -> None:
        coord = make_mock_coordinator(is_on=True, brightness=64)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.brightness is not None
        assert light.brightness > 0

    def test_brightness_none_when_off(self) -> None:
        coord = make_mock_coordinator(is_on=False)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.brightness is None

    def test_color_temp_when_on(self) -> None:
        coord = make_mock_coordinator(is_on=True, color_temp=64)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.color_temp is not None

    def test_color_temp_none_when_off(self) -> None:
        coord = make_mock_coordinator(is_on=False)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.color_temp is None

    def test_min_max_color_temp_kelvin(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.min_color_temp_kelvin == 2703
        assert light.max_color_temp_kelvin == 6535

    def test_color_mode_white(self) -> None:
        coord = make_mock_coordinator(mode=0)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.color_mode == ColorMode.COLOR_TEMP

    def test_supported_color_modes_includes_rgb(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.supported_color_modes == {ColorMode.COLOR_TEMP, ColorMode.RGB}

    def test_should_poll_false(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.should_poll is False


class TestRGBColorMode:
    """Test RGB color mode support."""

    def test_rgb_color_mode_when_mode_is_color(self) -> None:
        coord = make_mock_coordinator(mode=1, is_on=True)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.color_mode == ColorMode.RGB

    def test_rgb_color_property(self) -> None:
        coord = make_mock_coordinator(mode=1, is_on=True, red=255, green=128, blue=64)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.rgb_color == (255, 128, 64)

    def test_rgb_color_none_when_white_mode(self) -> None:
        coord = make_mock_coordinator(mode=0, is_on=True, red=255, green=128, blue=64)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.rgb_color is None

    def test_rgb_color_none_when_off(self) -> None:
        coord = make_mock_coordinator(mode=1, is_on=False)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.rgb_color is None

    def test_brightness_in_rgb_mode(self) -> None:
        coord = make_mock_coordinator(mode=1, is_on=True, color_brightness=200)
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.brightness == 200


class TestLightActions:
    """Test light turn_on/turn_off actions."""

    @pytest.mark.asyncio
    async def test_turn_on_no_args(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_on()

        coord.device.send_power.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_turn_on_with_brightness(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_on(brightness=128)

        coord.device.send_brightness.assert_called_once()
        args = coord.device.send_brightness.call_args[0]
        assert 1 <= args[0] <= 100  # device range

    @pytest.mark.asyncio
    async def test_turn_on_with_color_temp(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_on(color_temp=262)

        coord.device.send_color_temp.assert_called_once()
        args = coord.device.send_color_temp.call_args[0]
        assert 0 <= args[0] <= 127  # device range

    @pytest.mark.asyncio
    async def test_turn_on_with_both(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_on(brightness=200, color_temp=200)

        coord.device.send_brightness.assert_called_once()
        coord.device.send_color_temp.assert_called_once()
        coord.device.send_power.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_off(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_off()

        coord.device.send_power.assert_called_once_with(False)

    @pytest.mark.asyncio
    async def test_turn_on_with_rgb_color(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_on(rgb_color=(255, 0, 128))

        coord.device.send_color.assert_called_once_with(255, 0, 128)
        coord.device.send_light_mode.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_turn_on_brightness_in_rgb_mode(self) -> None:
        coord = make_mock_coordinator(mode=1)
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_on(brightness=200)

        coord.device.send_color_brightness.assert_called_once_with(200)
        coord.device.send_brightness.assert_not_called()

    @pytest.mark.asyncio
    async def test_color_temp_switches_to_white_mode(self) -> None:
        coord = make_mock_coordinator(mode=1)
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_on(color_temp=262)

        coord.device.send_light_mode.assert_called_once_with(0)
        coord.device.send_color_temp.assert_called_once()


class TestLightLifecycle:
    """Test HA lifecycle methods."""

    @pytest.mark.asyncio
    async def test_added_to_hass(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_added_to_hass()

        coord.add_listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_removed_from_hass(self) -> None:
        coord = make_mock_coordinator()
        remove_fn = MagicMock()
        coord.add_listener.return_value = remove_fn
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_added_to_hass()
        await light.async_will_remove_from_hass()

        remove_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_triggers_ha_state_write(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")
        light.async_write_ha_state = MagicMock()

        await light.async_added_to_hass()
        # Get the callback that was registered
        callback = coord.add_listener.call_args[0][0]
        callback()

        light.async_write_ha_state.assert_called_once()


class TestLightPlatformSetup:
    """Test async_setup_entry for the light platform."""

    @pytest.mark.asyncio
    async def test_setup_entry_creates_one_light(self) -> None:
        coord = make_mock_coordinator()
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry1": {"coordinator": coord, "device_info": None}}}
        entry = MagicMock()
        entry.entry_id = "entry1"
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], TuyaBLEMeshLight)

    @pytest.mark.asyncio
    async def test_setup_entry_uses_coordinator_from_hass_data(self) -> None:
        coord = make_mock_coordinator()
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry1": {"coordinator": coord, "device_info": None}}}
        entry = MagicMock()
        entry.entry_id = "entry1"
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        entities = add_entities.call_args[0][0]
        assert entities[0]._coordinator is coord

    @pytest.mark.asyncio
    async def test_setup_skips_plug_device_type(self) -> None:
        coord = make_mock_coordinator()
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry1": {"coordinator": coord, "device_info": None}}}
        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.data = {"device_type": "plug"}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_not_called()


class TestTransitions:
    """Test transition (gradual brightness/color temp) support."""

    @pytest.mark.asyncio
    async def test_turn_on_with_transition_brightness(self) -> None:
        """Transition sends multiple brightness steps."""
        coord = make_mock_coordinator(brightness=10)
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_on(brightness=255, transition=0.2)
        # Wait for the transition task to complete
        assert light._transition_task is not None
        await light._transition_task

        # Should have called send_brightness multiple times (>= 2 steps)
        assert coord.device.send_brightness.call_count >= 2
        # Last call should be close to device max (100)
        last_val = coord.device.send_brightness.call_args_list[-1][0][0]
        assert last_val == 100
        # Power should NOT have been called
        coord.device.send_power.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_on_with_transition_color_temp(self) -> None:
        """Transition sends multiple color_temp steps."""
        coord = make_mock_coordinator(color_temp=0)
        light = TuyaBLEMeshLight(coord, "test_entry")

        # 153 mireds (coolest) -> device 127
        await light.async_turn_on(color_temp=153, transition=0.2)
        assert light._transition_task is not None
        await light._transition_task

        assert coord.device.send_color_temp.call_count >= 2
        last_val = coord.device.send_color_temp.call_args_list[-1][0][0]
        assert last_val == 127

    @pytest.mark.asyncio
    async def test_turn_off_with_transition(self) -> None:
        """Turn off with transition ramps brightness down then powers off."""
        coord = make_mock_coordinator(brightness=80)
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_off(transition=0.2)
        assert light._transition_task is not None
        await light._transition_task

        # Should ramp brightness down
        assert coord.device.send_brightness.call_count >= 2
        # Last brightness should be min (1)
        last_val = coord.device.send_brightness.call_args_list[-1][0][0]
        assert last_val == 1
        # Then power off
        coord.device.send_power.assert_called_once_with(False)

    @pytest.mark.asyncio
    async def test_transition_cancelled_by_new_command(self) -> None:
        """A new turn_on cancels an in-progress transition."""
        coord = make_mock_coordinator(brightness=50)
        # Make send_brightness slow so we can cancel mid-transition
        call_count = 0

        async def slow_send(val: int) -> None:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                await asyncio.sleep(0.5)

        coord.device.send_brightness = AsyncMock(side_effect=slow_send)

        light = TuyaBLEMeshLight(coord, "test_entry")

        # Start a long transition
        await light.async_turn_on(brightness=255, transition=5.0)
        task1 = light._transition_task
        assert task1 is not None

        # Immediately send a new instant command — should cancel transition
        await light.async_turn_on(brightness=128)

        # Let event loop process the cancellation
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(asyncio.shield(task1), timeout=0.1)

        # First task should be cancelled
        assert task1.cancelled() or task1.done()

    @pytest.mark.asyncio
    async def test_no_transition_unchanged_behavior(self) -> None:
        """Without transition kwarg, behavior is unchanged (instant)."""
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_on(brightness=200)

        coord.device.send_brightness.assert_called_once()
        assert light._transition_task is None

    @pytest.mark.asyncio
    async def test_transition_zero_is_instant(self) -> None:
        """Transition=0 should use instant path, not transition."""
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")

        await light.async_turn_on(brightness=200, transition=0)

        coord.device.send_brightness.assert_called_once()
        assert light._transition_task is None

    @pytest.mark.asyncio
    async def test_supported_features_includes_transition(self) -> None:
        """Entity reports TRANSITION as supported feature."""
        from homeassistant.components.light import LightEntityFeature

        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.supported_features & LightEntityFeature.TRANSITION

    @pytest.mark.asyncio
    async def test_will_remove_cancels_transition(self) -> None:
        """Removing entity from HA cancels in-progress transition."""
        coord = make_mock_coordinator(brightness=50)

        async def slow_send(val: int) -> None:
            await asyncio.sleep(5.0)

        coord.device.send_brightness = AsyncMock(side_effect=slow_send)

        light = TuyaBLEMeshLight(coord, "test_entry")
        light._remove_listener = MagicMock()

        await light.async_turn_on(brightness=255, transition=5.0)
        task = light._transition_task
        assert task is not None

        await light.async_will_remove_from_hass()

        # Let event loop process the cancellation
        await asyncio.sleep(0)

        assert task.cancelled() or task.done()
        assert light._transition_task is None
