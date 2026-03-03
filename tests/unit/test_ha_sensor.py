"""Unit tests for the Tuya BLE Mesh sensor entities."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh.const import DOMAIN  # noqa: E402
from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshDeviceState,
)
from custom_components.tuya_ble_mesh.sensor import (  # noqa: E402
    TuyaBLEMeshFirmwareSensor,
    TuyaBLEMeshRSSISensor,
    async_setup_entry,
)


def make_mock_coordinator(
    *,
    rssi: int | None = -65,
    firmware_version: str | None = "1.6",
    available: bool = True,
) -> MagicMock:
    """Create a mock coordinator."""
    coord = MagicMock()
    coord.state = TuyaBLEMeshDeviceState(
        rssi=rssi,
        firmware_version=firmware_version,
        available=available,
    )
    coord.device = MagicMock()
    coord.device.address = "DC:23:4D:21:43:A5"
    coord.add_listener = MagicMock(return_value=MagicMock())
    return coord


class TestRSSISensor:
    """Test TuyaBLEMeshRSSISensor."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert "rssi" in sensor.unique_id
        assert "DC:23:4D:21:43:A5" in sensor.unique_id

    def test_name(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert "RSSI" in sensor.name

    def test_available(self) -> None:
        coord = make_mock_coordinator(available=True)
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.available is True

    def test_not_available(self) -> None:
        coord = make_mock_coordinator(available=False)
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.available is False

    def test_native_value(self) -> None:
        coord = make_mock_coordinator(rssi=-65)
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.native_value == -65

    def test_native_value_none(self) -> None:
        coord = make_mock_coordinator(rssi=None)
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.native_value is None

    def test_unit(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.native_unit_of_measurement == "dBm"

    def test_device_class(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.device_class == "signal_strength"

    def test_entity_category(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.entity_category == "diagnostic"

    def test_should_poll_false(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.should_poll is False


class TestFirmwareSensor:
    """Test TuyaBLEMeshFirmwareSensor."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert "firmware" in sensor.unique_id
        assert "DC:23:4D:21:43:A5" in sensor.unique_id

    def test_name(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert "Firmware" in sensor.name

    def test_native_value(self) -> None:
        coord = make_mock_coordinator(firmware_version="1.6")
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert sensor.native_value == "1.6"

    def test_native_value_none(self) -> None:
        coord = make_mock_coordinator(firmware_version=None)
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert sensor.native_value is None

    def test_entity_category(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert sensor.entity_category == "diagnostic"

    def test_should_poll_false(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert sensor.should_poll is False


class TestSensorLifecycle:
    """Test HA lifecycle methods for sensors."""

    @pytest.mark.asyncio
    async def test_rssi_added_to_hass(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")

        await sensor.async_added_to_hass()

        coord.add_listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_rssi_removed_from_hass(self) -> None:
        coord = make_mock_coordinator()
        remove_fn = MagicMock()
        coord.add_listener.return_value = remove_fn
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")

        await sensor.async_added_to_hass()
        await sensor.async_will_remove_from_hass()

        remove_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_firmware_added_to_hass(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")

        await sensor.async_added_to_hass()

        coord.add_listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_firmware_removed_from_hass(self) -> None:
        coord = make_mock_coordinator()
        remove_fn = MagicMock()
        coord.add_listener.return_value = remove_fn
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")

        await sensor.async_added_to_hass()
        await sensor.async_will_remove_from_hass()

        remove_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_rssi_update_triggers_ha_state_write(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        sensor.async_write_ha_state = MagicMock()

        await sensor.async_added_to_hass()
        callback = coord.add_listener.call_args[0][0]
        callback()

        sensor.async_write_ha_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_firmware_update_triggers_ha_state_write(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        sensor.async_write_ha_state = MagicMock()

        await sensor.async_added_to_hass()
        callback = coord.add_listener.call_args[0][0]
        callback()

        sensor.async_write_ha_state.assert_called_once()


class TestSensorPlatformSetup:
    """Test async_setup_entry for the sensor platform."""

    @pytest.mark.asyncio
    async def test_setup_entry_creates_two_sensors(self) -> None:
        coord = make_mock_coordinator()
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry1": {"coordinator": coord}}}
        entry = MagicMock()
        entry.entry_id = "entry1"
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]
        assert len(entities) == 2

    @pytest.mark.asyncio
    async def test_setup_entry_creates_rssi_and_firmware(self) -> None:
        coord = make_mock_coordinator()
        hass = MagicMock()
        hass.data = {DOMAIN: {"entry1": {"coordinator": coord}}}
        entry = MagicMock()
        entry.entry_id = "entry1"
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        entities = add_entities.call_args[0][0]
        types = {type(e) for e in entities}
        assert TuyaBLEMeshRSSISensor in types
        assert TuyaBLEMeshFirmwareSensor in types

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
        for entity in entities:
            assert entity._coordinator is coord
