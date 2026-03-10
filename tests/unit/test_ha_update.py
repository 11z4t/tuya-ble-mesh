"""Unit tests for the Tuya BLE Mesh firmware update entity."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from homeassistant.components.update import UpdateDeviceClass  # noqa: E402

from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshDeviceState  # noqa: E402
from custom_components.tuya_ble_mesh.update import (  # noqa: E402
    TuyaBLEMeshFirmwareUpdateEntity,
)


def _make_mock_coordinator(
    *,
    firmware_version: str | None = None,
    available: bool = True,
) -> MagicMock:
    coord = MagicMock()
    coord.state = TuyaBLEMeshDeviceState(
        firmware_version=firmware_version,
        available=available,
    )
    coord.async_add_listener = MagicMock(return_value=MagicMock())
    return coord


class TestFirmwareUpdateEntity:
    """MESH-18: TuyaBLEMeshFirmwareUpdateEntity exposes firmware version."""

    def test_entity_device_class_is_firmware(self) -> None:
        """Entity must report UpdateDeviceClass.FIRMWARE."""
        coord = _make_mock_coordinator(firmware_version="1.6.0")
        entity = TuyaBLEMeshFirmwareUpdateEntity(coord, "entry_abc", MagicMock())

        assert entity.device_class == UpdateDeviceClass.FIRMWARE

    def test_installed_version_matches_state(self) -> None:
        """installed_version must reflect coordinator.state.firmware_version."""
        coord = _make_mock_coordinator(firmware_version="1.6.0")
        entity = TuyaBLEMeshFirmwareUpdateEntity(coord, "entry_abc", MagicMock())

        assert entity.installed_version == "1.6.0"

    def test_installed_version_none_when_unknown(self) -> None:
        """installed_version is None when firmware_version not yet received."""
        coord = _make_mock_coordinator(firmware_version=None)
        entity = TuyaBLEMeshFirmwareUpdateEntity(coord, "entry_abc", MagicMock())

        assert entity.installed_version is None

    def test_latest_version_equals_installed(self) -> None:
        """latest_version matches installed_version (no update detection)."""
        coord = _make_mock_coordinator(firmware_version="2.0.1")
        entity = TuyaBLEMeshFirmwareUpdateEntity(coord, "entry_abc", MagicMock())

        assert entity.latest_version == entity.installed_version

    def test_available_reflects_coordinator_state(self) -> None:
        """Entity availability tracks coordinator state.available."""
        coord = _make_mock_coordinator(available=False)
        entity = TuyaBLEMeshFirmwareUpdateEntity(coord, "entry_abc", MagicMock())

        assert entity.available is False

    def test_unique_id_uses_entry_id(self) -> None:
        """Unique ID must be scoped to entry_id to avoid collisions."""
        coord = _make_mock_coordinator()
        entry_id = "test_entry_42"
        entity = TuyaBLEMeshFirmwareUpdateEntity(coord, entry_id, MagicMock())

        assert entity.unique_id == f"{entry_id}_firmware"

    def test_firmware_version_updates_on_state_change(self) -> None:
        """Entity reports updated firmware version after state changes."""
        coord = _make_mock_coordinator(firmware_version="1.0.0")
        entity = TuyaBLEMeshFirmwareUpdateEntity(coord, "entry_abc", MagicMock())

        assert entity.installed_version == "1.0.0"

        coord.state = replace(coord.state, firmware_version="2.0.0")
        assert entity.installed_version == "2.0.0"

    @pytest.mark.asyncio
    async def test_async_setup_entry_creates_update_entity(self) -> None:
        """async_setup_entry must register one update entity per config entry."""
        from custom_components.tuya_ble_mesh.update import async_setup_entry

        coord = _make_mock_coordinator(firmware_version="1.0.0")
        mock_entry = MagicMock()
        mock_entry.entry_id = "setup_entry_test"
        mock_entry.runtime_data = MagicMock()
        mock_entry.runtime_data.coordinator = coord
        mock_entry.runtime_data.device_info = MagicMock()

        added_entities = []

        def mock_add(entities: list) -> None:
            added_entities.extend(entities)

        await async_setup_entry(MagicMock(), mock_entry, mock_add)

        assert len(added_entities) == 1
        assert isinstance(added_entities[0], TuyaBLEMeshFirmwareUpdateEntity)
