"""Unit tests for the Tuya BLE Mesh light entity platform."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh.const import DOMAIN  # noqa: E402
from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshDeviceState,
)
from custom_components.tuya_ble_mesh.light import (  # noqa: E402
    COLOR_MODE_COLOR_TEMP,
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
    available: bool = True,
) -> MagicMock:
    """Create a mock coordinator with configurable state."""
    coord = MagicMock()
    coord.state = TuyaBLEMeshDeviceState(
        is_on=is_on,
        brightness=brightness,
        color_temp=color_temp,
        available=available,
    )
    coord.device = MagicMock()
    coord.device.address = "DC:23:4D:21:43:A5"
    coord.device.send_power = AsyncMock()
    coord.device.send_brightness = AsyncMock()
    coord.device.send_color_temp = AsyncMock()
    coord.add_listener = MagicMock(return_value=MagicMock())
    return coord


class TestBrightnessToHa:
    """Test device-to-HA brightness mapping."""

    def test_min_device_to_min_ha(self) -> None:
        assert brightness_to_ha(1) == 1

    def test_max_device_to_max_ha(self) -> None:
        assert brightness_to_ha(127) == 255

    def test_midpoint(self) -> None:
        result = brightness_to_ha(64)
        assert 126 <= result <= 129  # approximately 128

    def test_clamps_below_min(self) -> None:
        assert brightness_to_ha(0) == 1

    def test_clamps_above_max(self) -> None:
        assert brightness_to_ha(200) == 255


class TestBrightnessToDevice:
    """Test HA-to-device brightness mapping."""

    def test_min_ha_to_min_device(self) -> None:
        assert brightness_to_device(1) == 1

    def test_max_ha_to_max_device(self) -> None:
        assert brightness_to_device(255) == 127

    def test_midpoint(self) -> None:
        result = brightness_to_device(128)
        assert 63 <= result <= 65  # approximately 64

    def test_clamps_below_min(self) -> None:
        assert brightness_to_device(0) == 1

    def test_roundtrip(self) -> None:
        """Device -> HA -> device should be close to original."""
        for device_val in [1, 32, 64, 96, 127]:
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

    def test_min_max_mireds(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.min_mireds == 153
        assert light.max_mireds == 370

    def test_color_mode(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.color_mode == COLOR_MODE_COLOR_TEMP

    def test_supported_color_modes(self) -> None:
        coord = make_mock_coordinator()
        light = TuyaBLEMeshLight(coord, "test_entry")
        assert light.supported_color_modes == {COLOR_MODE_COLOR_TEMP}


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
        assert 1 <= args[0] <= 127  # device range

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


class TestLightPlatformSetup:
    """Test async_setup_entry for the light platform."""

    @pytest.mark.asyncio
    async def test_setup_entry_creates_one_light(self) -> None:
        coord = make_mock_coordinator()
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry1": {"coordinator": coord}}}
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
        hass.data = {DOMAIN: {"entry1": {"coordinator": coord}}}
        entry = MagicMock()
        entry.entry_id = "entry1"
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        entities = add_entities.call_args[0][0]
        assert entities[0]._coordinator is coord
