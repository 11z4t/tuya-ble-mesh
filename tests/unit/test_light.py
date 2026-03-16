"""Unit tests for Tuya BLE Mesh light entities and brightness/color conversions."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from homeassistant.components.light import ColorMode  # noqa: E402

from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
)
from custom_components.tuya_ble_mesh.light import (  # noqa: E402
    TuyaBLEMeshLight,
    brightness_to_device,
    brightness_to_ha,
    color_brightness_to_device,
    color_brightness_to_ha,
    color_temp_to_device,
    color_temp_to_ha,
)


class TestBrightnessConversions:
    """Test white brightness conversion (device 1-100 ↔ HA 1-255)."""

    def test_brightness_to_ha_min(self) -> None:
        """Test minimum brightness conversion (1 → 1)."""
        assert brightness_to_ha(1) == 1

    def test_brightness_to_ha_max(self) -> None:
        """Test maximum brightness conversion (100 → 255)."""
        assert brightness_to_ha(100) == 255

    def test_brightness_to_ha_mid(self) -> None:
        """Test mid-range brightness conversion (50 → 128)."""
        result = brightness_to_ha(50)
        assert 127 <= result <= 129  # Allow rounding variance

    def test_brightness_to_ha_clamping_low(self) -> None:
        """Test clamping below minimum (0 → 1)."""
        assert brightness_to_ha(0) == 1

    def test_brightness_to_ha_clamping_high(self) -> None:
        """Test clamping above maximum (150 → 255)."""
        assert brightness_to_ha(150) == 255

    def test_brightness_to_device_min(self) -> None:
        """Test minimum brightness to device (1 → 1)."""
        assert brightness_to_device(1) == 1

    def test_brightness_to_device_max(self) -> None:
        """Test maximum brightness to device (255 → 100)."""
        assert brightness_to_device(255) == 100

    def test_brightness_to_device_mid(self) -> None:
        """Test mid-range to device (128 → ~50)."""
        result = brightness_to_device(128)
        assert 49 <= result <= 51

    def test_brightness_to_device_clamping_low(self) -> None:
        """Test clamping below minimum (0 → 1)."""
        assert brightness_to_device(0) == 1

    def test_brightness_to_device_clamping_high(self) -> None:
        """Test clamping above maximum (300 → 100)."""
        assert brightness_to_device(300) == 100

    def test_brightness_roundtrip(self) -> None:
        """Test roundtrip conversion (device → HA → device)."""
        for device_val in [1, 25, 50, 75, 100]:
            ha_val = brightness_to_ha(device_val)
            roundtrip = brightness_to_device(ha_val)
            # Allow ±1 difference due to rounding
            assert abs(roundtrip - device_val) <= 1


class TestColorBrightnessConversions:
    """Test color brightness conversion (device 0-255 ↔ HA 0-255)."""

    def test_color_brightness_to_ha_identity(self) -> None:
        """Test that color brightness HA conversion is identity (same scale)."""
        for val in [0, 1, 100, 127, 200, 255]:
            assert color_brightness_to_ha(val) == val

    def test_color_brightness_to_ha_clamping_low(self) -> None:
        """Test clamping below minimum (-10 → 0)."""
        assert color_brightness_to_ha(-10) == 0

    def test_color_brightness_to_ha_clamping_high(self) -> None:
        """Test clamping above maximum (300 → 255)."""
        assert color_brightness_to_ha(300) == 255

    def test_color_brightness_to_device_identity(self) -> None:
        """Test that color brightness device conversion is identity."""
        for val in [0, 1, 100, 127, 200, 255]:
            assert color_brightness_to_device(val) == val

    def test_color_brightness_to_device_clamping_low(self) -> None:
        """Test clamping below minimum (-5 → 0)."""
        assert color_brightness_to_device(-5) == 0

    def test_color_brightness_to_device_clamping_high(self) -> None:
        """Test clamping above maximum (500 → 255)."""
        assert color_brightness_to_device(500) == 255


class TestColorTempConversions:
    """Test color temp conversion (device 0-127 ↔ mireds 370-153, inverse)."""

    def test_color_temp_to_ha_warmest(self) -> None:
        """Test warmest device value (0 → 370 mireds)."""
        assert color_temp_to_ha(0) == 370

    def test_color_temp_to_ha_coolest(self) -> None:
        """Test coolest device value (127 → 153 mireds)."""
        assert color_temp_to_ha(127) == 153

    def test_color_temp_to_ha_mid(self) -> None:
        """Test mid-range device value (~64 → ~261 mireds)."""
        result = color_temp_to_ha(64)
        # Midpoint of 370-153 = 261.5
        assert 260 <= result <= 263

    def test_color_temp_to_ha_clamping_low(self) -> None:
        """Test clamping below minimum (-10 → 370)."""
        assert color_temp_to_ha(-10) == 370

    def test_color_temp_to_ha_clamping_high(self) -> None:
        """Test clamping above maximum (200 → 153)."""
        assert color_temp_to_ha(200) == 153

    def test_color_temp_to_device_warmest(self) -> None:
        """Test warmest mireds (370 → 0)."""
        assert color_temp_to_device(370) == 0

    def test_color_temp_to_device_coolest(self) -> None:
        """Test coolest mireds (153 → 127)."""
        assert color_temp_to_device(153) == 127

    def test_color_temp_to_device_mid(self) -> None:
        """Test mid-range mireds (~261 → ~64)."""
        result = color_temp_to_device(261)
        assert 63 <= result <= 65

    def test_color_temp_to_device_clamping_low(self) -> None:
        """Test clamping below minimum (100 → 127)."""
        assert color_temp_to_device(100) == 127

    def test_color_temp_to_device_clamping_high(self) -> None:
        """Test clamping above maximum (500 → 0)."""
        assert color_temp_to_device(500) == 0

    def test_color_temp_roundtrip(self) -> None:
        """Test roundtrip conversion (device → HA → device)."""
        for device_val in [0, 32, 64, 96, 127]:
            ha_val = color_temp_to_ha(device_val)
            roundtrip = color_temp_to_device(ha_val)
            # Allow ±1 difference due to rounding
            assert abs(roundtrip - device_val) <= 1


class TestLightEntity:
    """Test TuyaBLEMeshLight entity creation and basic properties."""

    def test_light_entity_creation_telink(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test creating a Telink light entity."""
        mock_config_entry.data["device_type"] = DEVICE_TYPE_LIGHT
        light = TuyaBLEMeshLight(mock_coordinator, mock_config_entry)

        assert light is not None
        assert light.unique_id is not None
        assert light.coordinator == mock_coordinator

    def test_light_entity_creation_bridge(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test creating a bridge-controlled light entity."""
        mock_config_entry.data["device_type"] = DEVICE_TYPE_TELINK_BRIDGE_LIGHT
        light = TuyaBLEMeshLight(mock_coordinator, mock_config_entry)

        assert light is not None
        assert light.unique_id is not None

    def test_light_supported_color_modes(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test light supports COLOR_TEMP and RGB color modes."""
        light = TuyaBLEMeshLight(mock_coordinator, mock_config_entry)

        # Should support both COLOR_TEMP and RGB
        assert ColorMode.COLOR_TEMP in light.supported_color_modes
        assert ColorMode.RGB in light.supported_color_modes

    @pytest.mark.parametrize(
        "device_type",
        [DEVICE_TYPE_LIGHT, DEVICE_TYPE_TELINK_BRIDGE_LIGHT],
    )
    def test_light_parametrized_device_types(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
        device_type: str,
    ) -> None:
        """Test light entity works with both Telink and bridge device types."""
        mock_config_entry.data["device_type"] = device_type
        light = TuyaBLEMeshLight(mock_coordinator, mock_config_entry)

        assert light is not None
        assert light.unique_id is not None
