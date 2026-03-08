"""Unit tests for HA integration constants."""

import sys
from pathlib import Path

import pytest

# Add project root so `custom_components.tuya_ble_mesh` is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from custom_components.tuya_ble_mesh.const import (
    CONF_MAC_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DEVICE_BRIGHTNESS_MAX,
    DEVICE_BRIGHTNESS_MIN,
    DEVICE_COLOR_TEMP_MAX,
    DEVICE_COLOR_TEMP_MIN,
    DOMAIN,
    HA_BRIGHTNESS_MAX,
    HA_BRIGHTNESS_MIN,
    HA_MIRED_MAX,
    HA_MIRED_MIN,
    PLATFORMS,
)


@pytest.mark.requires_ha
class TestDomain:
    """Test DOMAIN constant."""

    def test_domain_is_string(self) -> None:
        assert isinstance(DOMAIN, str)

    def test_domain_value(self) -> None:
        assert DOMAIN == "tuya_ble_mesh"


@pytest.mark.requires_ha
class TestPlatforms:
    """Test PLATFORMS constant."""

    def test_platforms_is_list(self) -> None:
        assert isinstance(PLATFORMS, list)

    def test_light_in_platforms(self) -> None:
        assert "light" in PLATFORMS

    def test_sensor_in_platforms(self) -> None:
        assert "sensor" in PLATFORMS


@pytest.mark.requires_ha
class TestConfigKeys:
    """Test config entry data keys."""

    def test_conf_keys_are_strings(self) -> None:
        assert isinstance(CONF_MESH_NAME, str)
        assert isinstance(CONF_MESH_PASSWORD, str)
        assert isinstance(CONF_MAC_ADDRESS, str)

    def test_conf_keys_unique(self) -> None:
        keys = [CONF_MESH_NAME, CONF_MESH_PASSWORD, CONF_MAC_ADDRESS]
        assert len(keys) == len(set(keys))


@pytest.mark.requires_ha
class TestBrightnessMapping:
    """Test brightness mapping constants."""

    def test_device_range(self) -> None:
        assert DEVICE_BRIGHTNESS_MIN == 1
        assert DEVICE_BRIGHTNESS_MAX == 100

    def test_ha_range(self) -> None:
        assert HA_BRIGHTNESS_MIN == 1
        assert HA_BRIGHTNESS_MAX == 255

    def test_device_min_less_than_max(self) -> None:
        assert DEVICE_BRIGHTNESS_MIN < DEVICE_BRIGHTNESS_MAX

    def test_ha_min_less_than_max(self) -> None:
        assert HA_BRIGHTNESS_MIN < HA_BRIGHTNESS_MAX


@pytest.mark.requires_ha
class TestColorTempMapping:
    """Test color temperature mapping constants."""

    def test_device_range(self) -> None:
        assert DEVICE_COLOR_TEMP_MIN == 0
        assert DEVICE_COLOR_TEMP_MAX == 127

    def test_mired_range(self) -> None:
        assert HA_MIRED_MIN == 153
        assert HA_MIRED_MAX == 370

    def test_mired_min_less_than_max(self) -> None:
        assert HA_MIRED_MIN < HA_MIRED_MAX

    def test_inverse_relationship(self) -> None:
        """Device 0 (warm) maps to mired 370 (warm), device 127 (cool) to 153 (cool)."""
        assert DEVICE_COLOR_TEMP_MIN == 0  # warmest on device
        assert HA_MIRED_MAX == 370  # warmest in mireds
        assert DEVICE_COLOR_TEMP_MAX == 127  # coolest on device
        assert HA_MIRED_MIN == 153  # coolest in mireds
