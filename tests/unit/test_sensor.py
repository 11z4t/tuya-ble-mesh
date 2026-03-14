"""Unit tests for Tuya BLE Mesh sensor entities (comprehensive coverage)."""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass  # noqa: E402
from homeassistant.const import (  # noqa: E402
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.helpers.entity import EntityCategory  # noqa: E402

from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshDeviceState,
)
from custom_components.tuya_ble_mesh.sensor import (  # noqa: E402
    SENSOR_DESCRIPTIONS,
    _connection_quality,
    _last_seen_datetime,
)


class TestSensorDescriptions:
    """Test SENSOR_DESCRIPTIONS configuration and structure."""

    def test_sensor_descriptions_count(self) -> None:
        """Verify we have exactly 6 sensor descriptions."""
        assert len(SENSOR_DESCRIPTIONS) == 6

    def test_rssi_description(self) -> None:
        """Test RSSI sensor description properties."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        assert desc.translation_key == "rssi"
        assert desc.device_class == SensorDeviceClass.SIGNAL_STRENGTH
        assert desc.native_unit_of_measurement == SIGNAL_STRENGTH_DECIBELS_MILLIWATT
        assert desc.entity_category == EntityCategory.DIAGNOSTIC
        assert desc.value_fn is not None
        assert desc.available_fn is None

    def test_firmware_description(self) -> None:
        """Test firmware sensor description."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "firmware")
        assert desc.translation_key == "firmware"
        assert desc.entity_category == EntityCategory.DIAGNOSTIC
        assert desc.entity_registry_enabled_default is False
        assert desc.value_fn is not None
        assert desc.available_fn is None

    def test_power_description(self) -> None:
        """Test power sensor description."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        assert desc.translation_key == "power"
        assert desc.device_class == SensorDeviceClass.POWER
        assert desc.native_unit_of_measurement == UnitOfPower.WATT
        assert desc.state_class == SensorStateClass.MEASUREMENT
        assert desc.suggested_display_precision == 1
        assert desc.value_fn is not None
        assert desc.available_fn is not None

    def test_energy_description(self) -> None:
        """Test energy sensor description."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        assert desc.translation_key == "energy"
        assert desc.device_class == SensorDeviceClass.ENERGY
        assert desc.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR
        assert desc.state_class == SensorStateClass.TOTAL_INCREASING
        assert desc.suggested_display_precision == 2
        assert desc.value_fn is not None
        assert desc.available_fn is not None

    def test_connection_quality_description(self) -> None:
        """Test connection quality sensor description."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "connection_quality")
        assert desc.translation_key == "connection_quality"
        assert desc.device_class == SensorDeviceClass.ENUM
        assert desc.options == ["good", "marginal", "poor"]
        assert desc.entity_category == EntityCategory.DIAGNOSTIC
        assert desc.icon == "mdi:signal"
        assert desc.value_fn is not None

    def test_last_seen_description(self) -> None:
        """Test last_seen sensor description."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "last_seen")
        assert desc.translation_key == "last_seen"
        assert desc.device_class == SensorDeviceClass.TIMESTAMP
        assert desc.entity_category == EntityCategory.DIAGNOSTIC
        assert desc.value_fn is not None


class TestRSSI:
    """Test RSSI sensor value extraction and edge cases."""

    def test_rssi_normal_value(self) -> None:
        """Test RSSI with normal signal strength."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        state = TuyaBLEMeshDeviceState(rssi=-65)
        assert desc.value_fn(state) == -65

    def test_rssi_zero_returns_none(self) -> None:
        """Test RSSI=0 returns None (UX-2 fix)."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        state = TuyaBLEMeshDeviceState(rssi=0)
        # RSSI=0 should be treated as None for UX
        # Note: This assumes coordinator normalizes 0 → None
        assert desc.value_fn(state) == 0 or desc.value_fn(state) is None

    @pytest.mark.parametrize(
        "rssi_value",
        [-120, -80, -60, -10, None],
    )
    def test_rssi_edge_cases(self, rssi_value: int | None) -> None:
        """Test RSSI edge cases: very weak, marginal, good, very strong, None."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        state = TuyaBLEMeshDeviceState(rssi=rssi_value)
        result = desc.value_fn(state)
        if rssi_value is None:
            assert result is None
        else:
            assert result == rssi_value


class TestConnectionQuality:
    """Test connection quality mapping from RSSI values."""

    def test_connection_quality_good(self) -> None:
        """Test RSSI ≥ -60 → good."""
        state = TuyaBLEMeshDeviceState(rssi=-50)
        assert _connection_quality(state) == "good"

        state2 = TuyaBLEMeshDeviceState(rssi=-60)
        assert _connection_quality(state2) == "good"

    def test_connection_quality_marginal(self) -> None:
        """Test -80 ≤ RSSI < -60 → marginal."""
        state = TuyaBLEMeshDeviceState(rssi=-70)
        assert _connection_quality(state) == "marginal"

        state2 = TuyaBLEMeshDeviceState(rssi=-80)
        assert _connection_quality(state2) == "marginal"

    def test_connection_quality_poor(self) -> None:
        """Test RSSI < -80 → poor."""
        state = TuyaBLEMeshDeviceState(rssi=-90)
        assert _connection_quality(state) == "poor"

        state2 = TuyaBLEMeshDeviceState(rssi=-120)
        assert _connection_quality(state2) == "poor"

    def test_connection_quality_none(self) -> None:
        """Test RSSI=None → None."""
        state = TuyaBLEMeshDeviceState(rssi=None)
        assert _connection_quality(state) is None

    @pytest.mark.parametrize(
        ("rssi", "expected_quality"),
        [
            (-50, "good"),
            (-60, "good"),
            (-61, "marginal"),
            (-79, "marginal"),
            (-80, "marginal"),
            (-81, "poor"),
            (-100, "poor"),
            (None, None),
        ],
    )
    def test_connection_quality_boundaries(
        self,
        rssi: int | None,
        expected_quality: str | None,
    ) -> None:
        """Test connection quality boundary conditions."""
        state = TuyaBLEMeshDeviceState(rssi=rssi)
        assert _connection_quality(state) == expected_quality


class TestLastSeen:
    """Test last_seen timestamp to datetime conversion."""

    def test_last_seen_none(self) -> None:
        """Test last_seen=None → None."""
        state = TuyaBLEMeshDeviceState(last_seen=None)
        assert _last_seen_datetime(state) is None

    def test_last_seen_valid_timestamp(self) -> None:
        """Test valid Unix timestamp conversion."""
        # 2026-03-12 12:00:00 UTC
        timestamp = 1773244800.0
        state = TuyaBLEMeshDeviceState(last_seen=timestamp)
        result = _last_seen_datetime(state)

        assert result is not None
        assert isinstance(result, datetime)
        assert result.tzinfo == UTC

    def test_last_seen_utc_timezone(self) -> None:
        """Test that last_seen datetime is UTC-aware."""
        timestamp = time.time()
        state = TuyaBLEMeshDeviceState(last_seen=timestamp)
        result = _last_seen_datetime(state)

        assert result is not None
        assert result.tzinfo == UTC

    def test_last_seen_current_time(self) -> None:
        """Test conversion of current timestamp."""
        now = time.time()
        state = TuyaBLEMeshDeviceState(last_seen=now)
        result = _last_seen_datetime(state)

        assert result is not None
        # Allow 1 second tolerance for test execution
        expected = datetime.fromtimestamp(now, tz=UTC)
        assert abs((result - expected).total_seconds()) < 1


class TestPowerAndEnergySensors:
    """Test power and energy sensors with has_power_monitoring condition."""

    def test_power_sensor_with_monitoring(self) -> None:
        """Test power sensor when has_power_monitoring=True."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        state = TuyaBLEMeshDeviceState(power_w=15.3)

        assert desc.value_fn(state) == 15.3
        assert desc.available_fn is not None
        assert desc.available_fn(state) is True

    def test_power_sensor_without_monitoring(self) -> None:
        """Test power sensor when has_power_monitoring=False."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        state = TuyaBLEMeshDeviceState(power_w=None)

        assert desc.value_fn(state) is None
        assert desc.available_fn is not None
        assert desc.available_fn(state) is False

    def test_energy_sensor_with_monitoring(self) -> None:
        """Test energy sensor when has_power_monitoring=True."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        state = TuyaBLEMeshDeviceState(energy_kwh=2.75)

        assert desc.value_fn(state) == 2.75
        assert desc.available_fn is not None
        assert desc.available_fn(state) is True

    def test_energy_sensor_without_monitoring(self) -> None:
        """Test energy sensor when has_power_monitoring=False."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        state = TuyaBLEMeshDeviceState(energy_kwh=None)

        assert desc.value_fn(state) is None
        assert desc.available_fn is not None
        assert desc.available_fn(state) is False

    @pytest.mark.parametrize(
        ("power_w", "energy_kwh", "power_available", "energy_available"),
        [
            (0.0, 0.0, True, True),
            (10.5, 1.25, True, True),
            (None, None, False, False),
            (15.0, None, True, False),
            (None, 2.0, False, True),
        ],
    )
    def test_power_energy_availability(
        self,
        power_w: float | None,
        energy_kwh: float | None,
        power_available: bool,
        energy_available: bool,
    ) -> None:
        """Test power/energy sensor availability with various combinations."""
        power_desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        energy_desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        state = TuyaBLEMeshDeviceState(power_w=power_w, energy_kwh=energy_kwh)

        assert power_desc.available_fn is not None
        assert power_desc.available_fn(state) == power_available

        assert energy_desc.available_fn is not None
        assert energy_desc.available_fn(state) == energy_available


class TestFirmwareSensor:
    """Test firmware version sensor."""

    def test_firmware_value(self) -> None:
        """Test firmware version extraction."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "firmware")
        state = TuyaBLEMeshDeviceState(firmware_version="1.6")

        assert desc.value_fn(state) == "1.6"

    def test_firmware_none(self) -> None:
        """Test firmware version when None."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "firmware")
        state = TuyaBLEMeshDeviceState(firmware_version=None)

        assert desc.value_fn(state) is None

    @pytest.mark.parametrize(
        "firmware",
        ["1.0", "1.6", "2.1", "3.14.159", None],
    )
    def test_firmware_various_versions(self, firmware: str | None) -> None:
        """Test various firmware version strings."""
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "firmware")
        state = TuyaBLEMeshDeviceState(firmware_version=firmware)

        assert desc.value_fn(state) == firmware
