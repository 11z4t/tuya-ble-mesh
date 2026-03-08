"""Unit tests for the Tuya BLE Mesh sensor entities (EntityDescription pattern)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

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
    TuyaBLEMeshSensor,
    async_setup_entry,
)


def make_mock_coordinator(
    *,
    rssi: int | None = -65,
    firmware_version: str | None = "1.6",
    power_w: float | None = None,
    energy_kwh: float | None = None,
    available: bool = True,
) -> MagicMock:
    """Create a mock coordinator."""
    coord = MagicMock()
    coord.state = TuyaBLEMeshDeviceState(
        rssi=rssi,
        firmware_version=firmware_version,
        power_w=power_w,
        energy_kwh=energy_kwh,
        available=available,
    )
    coord.device = MagicMock()
    coord.device.address = "DC:23:4D:21:43:A5"
    coord.device.supports_power_monitoring = False
    coord.add_listener = MagicMock(return_value=MagicMock())
    return coord


@pytest.mark.requires_ha
class TestSensorDescriptions:
    """Test SENSOR_DESCRIPTIONS configuration."""

    def test_sensor_descriptions_count(self) -> None:
        """Verify we have exactly 4 sensor descriptions."""
        assert len(SENSOR_DESCRIPTIONS) == 4

    def test_rssi_description(self) -> None:
        """Test RSSI sensor description."""
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


@pytest.mark.requires_ha
class TestRSSISensor:
    """Test TuyaBLEMeshSensor with RSSI description."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.unique_id == "DC:23:4D:21:43:A5_rssi"

    def test_available(self) -> None:
        coord = make_mock_coordinator(available=True)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.available is True

    def test_not_available(self) -> None:
        coord = make_mock_coordinator(available=False)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.available is False

    def test_native_value(self) -> None:
        coord = make_mock_coordinator(rssi=-65)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.native_value == -65

    def test_native_value_none(self) -> None:
        coord = make_mock_coordinator(rssi=None)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.native_value is None

    def test_device_class(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.device_class == SensorDeviceClass.SIGNAL_STRENGTH

    def test_entity_category(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC

    def test_should_poll_false(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.should_poll is False


@pytest.mark.requires_ha
class TestFirmwareSensor:
    """Test TuyaBLEMeshSensor with firmware description."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "firmware")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.unique_id == "DC:23:4D:21:43:A5_firmware"

    def test_native_value(self) -> None:
        coord = make_mock_coordinator(firmware_version="1.6")
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "firmware")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.native_value == "1.6"

    def test_native_value_none(self) -> None:
        coord = make_mock_coordinator(firmware_version=None)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "firmware")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.native_value is None

    def test_entity_category(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "firmware")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.entity_category == EntityCategory.DIAGNOSTIC

    def test_should_poll_false(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "firmware")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.should_poll is False


@pytest.mark.requires_ha
class TestPowerSensor:
    """Test TuyaBLEMeshSensor with power description."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.unique_id == "DC:23:4D:21:43:A5_power"

    def test_native_value(self) -> None:
        coord = make_mock_coordinator(power_w=42.5)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.native_value == 42.5

    def test_native_value_none(self) -> None:
        coord = make_mock_coordinator(power_w=None)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.native_value is None

    def test_device_class(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.device_class == SensorDeviceClass.POWER

    def test_state_class(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.state_class == SensorStateClass.MEASUREMENT

    def test_should_poll_false(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.should_poll is False

    def test_available_when_power_none(self) -> None:
        """Power sensor should be unavailable when power_w is None."""
        coord = make_mock_coordinator(available=True, power_w=None)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.available is False

    def test_available_when_power_present(self) -> None:
        """Power sensor should be available when power_w has a value."""
        coord = make_mock_coordinator(available=True, power_w=42.5)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "power")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.available is True


@pytest.mark.requires_ha
class TestEnergySensor:
    """Test TuyaBLEMeshSensor with energy description."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.unique_id == "DC:23:4D:21:43:A5_energy"

    def test_native_value(self) -> None:
        coord = make_mock_coordinator(energy_kwh=12.34)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.native_value == 12.34

    def test_native_value_none(self) -> None:
        coord = make_mock_coordinator(energy_kwh=None)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.native_value is None

    def test_device_class(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.device_class == SensorDeviceClass.ENERGY

    def test_state_class(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.state_class == SensorStateClass.TOTAL_INCREASING

    def test_should_poll_false(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.should_poll is False

    def test_available_when_energy_none(self) -> None:
        """Energy sensor should be unavailable when energy_kwh is None."""
        coord = make_mock_coordinator(available=True, energy_kwh=None)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.available is False

    def test_available_when_energy_present(self) -> None:
        """Energy sensor should be available when energy_kwh has a value."""
        coord = make_mock_coordinator(available=True, energy_kwh=12.34)
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.available is True


@pytest.mark.requires_ha
class TestSensorLifecycle:
    """Test HA lifecycle methods for sensors."""

    @pytest.mark.asyncio
    async def test_added_to_hass(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)

        await sensor.async_added_to_hass()

        coord.add_listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_removed_from_hass(self) -> None:
        coord = make_mock_coordinator()
        remove_fn = MagicMock()
        coord.add_listener.return_value = remove_fn
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)

        await sensor.async_added_to_hass()
        await sensor.async_will_remove_from_hass()

        remove_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_triggers_ha_state_write(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        sensor.async_write_ha_state = MagicMock()

        await sensor.async_added_to_hass()
        callback = coord.add_listener.call_args[0][0]
        callback()

        sensor.async_write_ha_state.assert_called_once()


@pytest.mark.requires_ha
class TestSensorPlatformSetup:
    """Test async_setup_entry for the sensor platform."""

    @pytest.mark.asyncio
    async def test_setup_entry_creates_two_sensors_for_light(self) -> None:
        """Light devices get RSSI + Firmware = 2 sensors (no power/energy)."""
        coord = make_mock_coordinator()
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = MagicMock()
        entry.data = {"device_type": "light"}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]
        assert len(entities) == 2
        keys = {e.entity_description.key for e in entities}
        assert "rssi" in keys
        assert "firmware" in keys
        assert "power" not in keys
        assert "energy" not in keys

    @pytest.mark.asyncio
    async def test_setup_entry_creates_four_sensors_for_plug(self) -> None:
        """Plug devices with power monitoring get RSSI + Firmware + Power + Energy = 4."""
        coord = make_mock_coordinator()
        coord.device.supports_power_monitoring = True
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = MagicMock()
        entry.data = {"device_type": "sig_plug"}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        entities = add_entities.call_args[0][0]
        assert len(entities) == 4
        keys = {e.entity_description.key for e in entities}
        assert "rssi" in keys
        assert "firmware" in keys
        assert "power" in keys
        assert "energy" in keys

    @pytest.mark.asyncio
    async def test_setup_entry_uses_coordinator_from_runtime_data(self) -> None:
        coord = make_mock_coordinator()
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = MagicMock()
        entry.data = {"device_type": "light"}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        entities = add_entities.call_args[0][0]
        for entity in entities:
            assert entity._coordinator is coord

    @pytest.mark.asyncio
    async def test_setup_entry_sets_device_info(self) -> None:
        coord = make_mock_coordinator()
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.runtime_data.coordinator = coord
        device_info = MagicMock()
        entry.runtime_data.device_info = device_info
        entry.data = {"device_type": "light"}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        entities = add_entities.call_args[0][0]
        for entity in entities:
            assert entity._attr_device_info is device_info


@pytest.mark.requires_ha
class TestSensorHasEntityName:
    """Test that sensors use has_entity_name pattern."""

    def test_has_entity_name_true(self) -> None:
        coord = make_mock_coordinator()
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "rssi")
        sensor = TuyaBLEMeshSensor(coord, "entry1", desc)
        assert sensor.has_entity_name is True
