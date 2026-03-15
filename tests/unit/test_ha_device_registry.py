"""Unit tests for the Tuya BLE Mesh device registry — PLAT-422."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from custom_components.tuya_ble_mesh.device_registry import (
    _RSSI_HISTORY_MAXLEN,
    DeviceMetadata,
    TuyaBLEMeshDeviceRegistry,
)


def _make_hass() -> MagicMock:
    """Create a minimal mock HomeAssistant instance."""
    hass = MagicMock()
    return hass


# ---------------------------------------------------------------------------
# DeviceMetadata
# ---------------------------------------------------------------------------


@pytest.mark.requires_ha
class TestDeviceMetadata:
    """Test DeviceMetadata dataclass."""

    def test_defaults(self) -> None:
        """Default values are set correctly."""
        meta = DeviceMetadata(address="AA:BB:CC:DD:EE:FF", name="Light", device_type="light")
        assert meta.address == "AA:BB:CC:DD:EE:FF"
        assert meta.name == "Light"
        assert meta.device_type == "light"
        assert meta.connection_count == 0
        assert meta.error_count == 0
        assert meta.last_error is None
        assert meta.firmware_version is None
        assert len(meta.rssi_history) == 0

    def test_to_dict_excludes_rssi_history(self) -> None:
        """to_dict() excludes in-memory rssi_history."""
        meta = DeviceMetadata(address="AA:BB:CC:DD:EE:FF", name="Light", device_type="light")
        meta.rssi_history.append(-65)
        d = meta.to_dict()
        assert "rssi_history" not in d
        assert "address" in d

    def test_to_dict_includes_all_persistent_fields(self) -> None:
        """to_dict() includes all fields that should persist."""
        meta = DeviceMetadata(
            address="AA:BB:CC:DD:EE:FF",
            name="Light",
            device_type="light",
            connection_count=5,
            error_count=2,
            last_error="timeout",
            firmware_version="1.2.3",
        )
        d = meta.to_dict()
        assert d["connection_count"] == 5
        assert d["error_count"] == 2
        assert d["last_error"] == "timeout"
        assert d["firmware_version"] == "1.2.3"

    def test_from_dict_round_trip(self) -> None:
        """from_dict(to_dict()) round-trip preserves all persistent fields."""
        meta = DeviceMetadata(
            address="AA:BB:CC:DD:EE:FF",
            name="Switch",
            device_type="plug",
            connection_count=3,
            error_count=1,
            firmware_version="v2",
        )
        restored = DeviceMetadata.from_dict(meta.to_dict())
        assert restored.address == meta.address
        assert restored.name == meta.name
        assert restored.connection_count == meta.connection_count
        assert restored.error_count == meta.error_count
        assert restored.firmware_version == meta.firmware_version

    def test_from_dict_missing_optional_fields(self) -> None:
        """from_dict() handles missing optional fields gracefully."""
        minimal = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "Dev",
            "device_type": "light",
        }
        meta = DeviceMetadata.from_dict(minimal)
        assert meta.connection_count == 0
        assert meta.last_error is None
        assert meta.firmware_version is None

    def test_avg_rssi_empty(self) -> None:
        """avg_rssi returns None when rssi_history is empty."""
        meta = DeviceMetadata(address="A", name="B", device_type="light")
        assert meta.avg_rssi is None

    def test_avg_rssi_single(self) -> None:
        """avg_rssi returns the single value when only one reading."""
        meta = DeviceMetadata(address="A", name="B", device_type="light")
        meta.rssi_history.append(-70)
        assert meta.avg_rssi == pytest.approx(-70.0)

    def test_avg_rssi_multiple(self) -> None:
        """avg_rssi returns mean of all readings."""
        meta = DeviceMetadata(address="A", name="B", device_type="light")
        meta.rssi_history.extend([-60, -80])
        assert meta.avg_rssi == pytest.approx(-70.0)

    def test_uptime_fraction_no_events(self) -> None:
        """uptime_fraction returns 1.0 when no events recorded."""
        meta = DeviceMetadata(address="A", name="B", device_type="light")
        assert meta.uptime_fraction == pytest.approx(1.0)

    def test_uptime_fraction_all_success(self) -> None:
        """uptime_fraction returns 1.0 when no errors."""
        meta = DeviceMetadata(address="A", name="B", device_type="light")
        meta.connection_count = 10
        assert meta.uptime_fraction == pytest.approx(1.0)

    def test_uptime_fraction_mixed(self) -> None:
        """uptime_fraction = connections / (connections + errors)."""
        meta = DeviceMetadata(address="A", name="B", device_type="light")
        meta.connection_count = 7
        meta.error_count = 3
        assert meta.uptime_fraction == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# TuyaBLEMeshDeviceRegistry
# ---------------------------------------------------------------------------


@pytest.mark.requires_ha
class TestDeviceRegistryInit:
    """Test registry initialization."""

    def test_init_no_devices(self) -> None:
        """Registry starts empty."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        assert len(registry.get_all_devices()) == 0


@pytest.mark.requires_ha
class TestDeviceRegistryLoad:
    """Test registry async_load()."""

    @pytest.mark.asyncio
    async def test_load_empty_store(self) -> None:
        """Loading from empty store leaves registry empty."""
        hass = _make_hass()
        registry = TuyaBLEMeshDeviceRegistry(hass)

        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=None)

        with patch(
            "homeassistant.helpers.storage.Store",
            return_value=mock_store,
        ):
            await registry.async_load()

        assert len(registry.get_all_devices()) == 0

    @pytest.mark.asyncio
    async def test_load_restores_devices(self) -> None:
        """Loading from store restores saved devices."""
        hass = _make_hass()
        registry = TuyaBLEMeshDeviceRegistry(hass)

        stored_data = {
            "AA:BB:CC:DD:EE:FF": {
                "address": "AA:BB:CC:DD:EE:FF",
                "name": "Test Light",
                "device_type": "light",
                "connection_count": 5,
                "error_count": 1,
                "last_error": None,
                "last_error_time": None,
                "firmware_version": "1.0",
                "first_seen": 1700000000.0,
                "last_seen": 1700001000.0,
            }
        }

        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=stored_data)

        with patch(
            "homeassistant.helpers.storage.Store",
            return_value=mock_store,
        ):
            await registry.async_load()

        assert len(registry.get_all_devices()) == 1
        device = registry.get_device("AA:BB:CC:DD:EE:FF")
        assert device is not None
        assert device.name == "Test Light"
        assert device.connection_count == 5

    @pytest.mark.asyncio
    async def test_load_skips_corrupt_entries(self) -> None:
        """Corrupt store entries are skipped with a warning, not crash."""
        hass = _make_hass()
        registry = TuyaBLEMeshDeviceRegistry(hass)

        # Missing required fields
        stored_data = {
            "BAD:ADDR": {"this": "is", "missing": "required_fields"},
        }

        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=stored_data)

        with patch(
            "homeassistant.helpers.storage.Store",
            return_value=mock_store,
        ):
            await registry.async_load()  # should not raise

        assert len(registry.get_all_devices()) == 0


@pytest.mark.requires_ha
class TestDeviceRegistrySave:
    """Test registry async_save()."""

    @pytest.mark.asyncio
    async def test_save_noop_before_load(self) -> None:
        """async_save() is a no-op if async_load() was not called."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        await registry.async_save()  # should not raise

    @pytest.mark.asyncio
    async def test_save_persists_devices(self) -> None:
        """async_save() calls store with correct data."""
        hass = _make_hass()
        registry = TuyaBLEMeshDeviceRegistry(hass)

        mock_store = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=None)

        with patch(
            "homeassistant.helpers.storage.Store",
            return_value=mock_store,
        ):
            await registry.async_load()

        registry.register_device("AA:BB:CC:DD:EE:FF", "Light", "light")
        await registry.async_save()

        mock_store.async_save.assert_called_once()
        saved = mock_store.async_save.call_args[0][0]
        assert "AA:BB:CC:DD:EE:FF" in saved


@pytest.mark.requires_ha
class TestDeviceRegistryRegister:
    """Test register_device()."""

    def test_new_device_registered(self) -> None:
        """New device is added to registry."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        meta = registry.register_device("DC:23:4F:10:52:C4", "My Light", "light")

        assert meta.address == "DC:23:4F:10:52:C4"
        assert meta.name == "My Light"
        assert meta.device_type == "light"
        assert registry.get_device("DC:23:4F:10:52:C4") is meta

    def test_address_normalized_to_uppercase(self) -> None:
        """MAC address is stored in uppercase."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("dc:23:4f:10:52:c4", "Light", "light")
        assert registry.get_device("DC:23:4F:10:52:C4") is not None

    def test_re_register_updates_name_and_type(self) -> None:
        """Re-registering an existing device updates name and type."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("AA:BB:CC:DD:EE:FF", "Old Name", "light")
        registry.register_device("AA:BB:CC:DD:EE:FF", "New Name", "plug")

        meta = registry.get_device("AA:BB:CC:DD:EE:FF")
        assert meta is not None
        assert meta.name == "New Name"
        assert meta.device_type == "plug"

    def test_re_register_preserves_history(self) -> None:
        """Re-registering preserves existing connection count."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("AA:BB:CC:DD:EE:FF", "Dev", "light")
        registry.record_connection("AA:BB:CC:DD:EE:FF")
        registry.register_device("AA:BB:CC:DD:EE:FF", "New Name", "light")

        meta = registry.get_device("AA:BB:CC:DD:EE:FF")
        assert meta is not None
        assert meta.connection_count == 1


@pytest.mark.requires_ha
class TestDeviceRegistryGetDevice:
    """Test get_device() and get_all_devices()."""

    def test_get_unknown_device_returns_none(self) -> None:
        """get_device() returns None for unknown address."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        assert registry.get_device("FF:FF:FF:FF:FF:FF") is None

    def test_get_all_sorted_by_last_seen_descending(self) -> None:
        """get_all_devices() returns most recently seen first."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("AA:BB:CC:DD:EE:01", "Dev1", "light")
        registry.register_device("AA:BB:CC:DD:EE:02", "Dev2", "light")

        # Set last_seen manually
        registry.get_device("AA:BB:CC:DD:EE:01").last_seen = 1000.0  # type: ignore[union-attr]
        registry.get_device("AA:BB:CC:DD:EE:02").last_seen = 2000.0  # type: ignore[union-attr]

        devices = registry.get_all_devices()
        assert devices[0].address == "AA:BB:CC:DD:EE:02"
        assert devices[1].address == "AA:BB:CC:DD:EE:01"


@pytest.mark.requires_ha
class TestDeviceRegistryRecord:
    """Test record_connection(), record_error(), record_rssi()."""

    def test_record_connection_increments_count(self) -> None:
        """record_connection() increments connection_count and updates last_seen."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("AA:BB:CC:DD:EE:FF", "Dev", "light")

        before = time.time()
        registry.record_connection("AA:BB:CC:DD:EE:FF")
        after = time.time()

        meta = registry.get_device("AA:BB:CC:DD:EE:FF")
        assert meta is not None
        assert meta.connection_count == 1
        assert before <= meta.last_seen <= after

    def test_record_connection_unknown_address_no_crash(self) -> None:
        """record_connection() with unknown address is a no-op."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.record_connection("FF:FF:FF:FF:FF:FF")  # should not raise

    def test_record_error_increments_count(self) -> None:
        """record_error() increments error_count and stores message."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("AA:BB:CC:DD:EE:FF", "Dev", "light")
        registry.record_error("AA:BB:CC:DD:EE:FF", "ble_timeout")

        meta = registry.get_device("AA:BB:CC:DD:EE:FF")
        assert meta is not None
        assert meta.error_count == 1
        assert meta.last_error == "ble_timeout"
        assert meta.last_error_time is not None

    def test_record_error_unknown_address_no_crash(self) -> None:
        """record_error() with unknown address is a no-op."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.record_error("FF:FF:FF:FF:FF:FF", "oops")

    def test_record_rssi_appends_to_history(self) -> None:
        """record_rssi() appends RSSI to rolling history."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("AA:BB:CC:DD:EE:FF", "Dev", "light")

        registry.record_rssi("AA:BB:CC:DD:EE:FF", -65)
        registry.record_rssi("AA:BB:CC:DD:EE:FF", -70)

        meta = registry.get_device("AA:BB:CC:DD:EE:FF")
        assert meta is not None
        assert list(meta.rssi_history) == [-65, -70]

    def test_record_rssi_unknown_address_no_crash(self) -> None:
        """record_rssi() with unknown address is a no-op."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.record_rssi("FF:FF:FF:FF:FF:FF", -50)

    def test_rssi_history_max_length(self) -> None:
        """RSSI history respects _RSSI_HISTORY_MAXLEN deque limit."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("AA:BB:CC:DD:EE:FF", "Dev", "light")

        for rssi in range(-100, -100 + _RSSI_HISTORY_MAXLEN + 5):
            registry.record_rssi("AA:BB:CC:DD:EE:FF", rssi)

        meta = registry.get_device("AA:BB:CC:DD:EE:FF")
        assert meta is not None
        assert len(meta.rssi_history) == _RSSI_HISTORY_MAXLEN


@pytest.mark.requires_ha
class TestDeviceRegistryUpdateFirmware:
    """Test update_firmware_version()."""

    def test_update_firmware_version(self) -> None:
        """update_firmware_version() sets firmware_version field."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("AA:BB:CC:DD:EE:FF", "Dev", "light")
        registry.update_firmware_version("AA:BB:CC:DD:EE:FF", "2.3.4")

        meta = registry.get_device("AA:BB:CC:DD:EE:FF")
        assert meta is not None
        assert meta.firmware_version == "2.3.4"

    def test_update_firmware_unknown_address_no_crash(self) -> None:
        """update_firmware_version() with unknown address is a no-op."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.update_firmware_version("FF:FF:FF:FF:FF:FF", "1.0")


@pytest.mark.requires_ha
class TestDeviceRegistryRemove:
    """Test remove_device()."""

    def test_remove_existing_device(self) -> None:
        """remove_device() removes device and returns True."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("AA:BB:CC:DD:EE:FF", "Dev", "light")
        assert registry.remove_device("AA:BB:CC:DD:EE:FF") is True
        assert registry.get_device("AA:BB:CC:DD:EE:FF") is None

    def test_remove_unknown_device_returns_false(self) -> None:
        """remove_device() returns False for unknown address."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        assert registry.remove_device("FF:FF:FF:FF:FF:FF") is False

    def test_remove_case_insensitive(self) -> None:
        """remove_device() is case-insensitive."""
        registry = TuyaBLEMeshDeviceRegistry(_make_hass())
        registry.register_device("AA:BB:CC:DD:EE:FF", "Dev", "light")
        assert registry.remove_device("aa:bb:cc:dd:ee:ff") is True
        assert len(registry.get_all_devices()) == 0
