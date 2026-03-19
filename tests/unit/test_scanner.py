"""Unit tests for BLE scanner module."""

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "custom_components"
        / "tuya_ble_mesh"
        / "lib"
    ),
)

from tuya_ble_mesh.exceptions import ProtocolError
from tuya_ble_mesh.scanner import (
    DiscoveredDevice,
    is_telink_mesh_device,
    is_tuya_mesh_device,
    mac_to_bytes,
)

# --- is_tuya_mesh_device ---


class TestIsTuyaMeshDevice:
    """Test Tuya mesh device detection."""

    def test_out_of_mesh_name(self) -> None:
        assert is_tuya_mesh_device("out_of_mesh", None) is True

    def test_tymesh_name(self) -> None:
        assert is_tuya_mesh_device("tymesh_abc", None) is True

    def test_case_insensitive(self) -> None:
        assert is_tuya_mesh_device("OUT_OF_MESH", None) is True

    def test_service_uuid_match(self) -> None:
        uuids = ["0000fe07-0000-1000-8000-00805f9b34fb"]
        assert is_tuya_mesh_device(None, uuids) is True

    def test_no_match(self) -> None:
        uuids = ["0000180a-0000-1000-8000-00805f9b34fb"]
        assert is_tuya_mesh_device("SomeOtherDevice", uuids) is False

    def test_none_name_no_uuids(self) -> None:
        assert is_tuya_mesh_device(None, None) is False

    def test_empty_name(self) -> None:
        assert is_tuya_mesh_device("", None) is False


# --- is_telink_mesh_device ---


class TestIsTelinkMeshDevice:
    """Test Telink mesh UUID detection."""

    def test_telink_uuid(self) -> None:
        uuids = ["00010203-0405-0607-0809-0a0b0c0d1910"]
        assert is_telink_mesh_device(uuids) is True

    def test_non_telink_uuid(self) -> None:
        uuids = ["0000fe07-0000-1000-8000-00805f9b34fb"]
        assert is_telink_mesh_device(uuids) is False

    def test_no_uuids(self) -> None:
        assert is_telink_mesh_device(None) is False

    def test_empty_uuids(self) -> None:
        assert is_telink_mesh_device([]) is False


# --- DiscoveredDevice ---


class TestDiscoveredDevice:
    """Test DiscoveredDevice dataclass."""

    def test_frozen(self) -> None:
        dd = DiscoveredDevice(name="test", address="AA:BB:CC:DD:EE:FF", rssi=-50, service_uuids=())
        with pytest.raises(AttributeError):
            dd.name = "changed"  # type: ignore[misc]

    def test_defaults(self) -> None:
        dd = DiscoveredDevice(name="test", address="AA:BB:CC:DD:EE:FF", rssi=-50, service_uuids=())
        assert dd.manufacturer_data == {}
        assert dd.is_tuya_mesh is False
        assert dd.is_telink_mesh is False

    def test_tuya_flag(self) -> None:
        dd = DiscoveredDevice(
            name="out_of_mesh",
            address="AA:BB:CC:DD:EE:FF",
            rssi=-50,
            service_uuids=(),
            is_tuya_mesh=True,
        )
        assert dd.is_tuya_mesh is True


# --- _make_discovered ---


class TestMakeDiscovered:
    """Test _make_discovered helper function."""

    def test_make_discovered_with_all_fields(self) -> None:
        from unittest.mock import MagicMock

        from tuya_ble_mesh.scanner import _make_discovered

        device = MagicMock()
        device.name = "out_of_mesh_test"
        device.address = "AA:BB:CC:DD:EE:FF"

        adv = MagicMock()
        adv.rssi = -50
        adv.service_uuids = ["0000fe07-0000-1000-8000-00805f9b34fb"]
        adv.manufacturer_data = {0x07A0: b"\x01\x02"}

        result = _make_discovered(device, adv)

        assert result.name == "out_of_mesh_test"
        assert result.address == "AA:BB:CC:DD:EE:FF"
        assert result.rssi == -50
        assert result.service_uuids == ("0000fe07-0000-1000-8000-00805f9b34fb",)
        assert result.manufacturer_data == {0x07A0: b"\x01\x02"}
        assert result.is_tuya_mesh is True

    def test_make_discovered_with_none_name(self) -> None:
        from unittest.mock import MagicMock

        from tuya_ble_mesh.scanner import _make_discovered

        device = MagicMock()
        device.name = None
        device.address = "AA:BB:CC:DD:EE:FF"

        adv = MagicMock()
        adv.rssi = -60
        adv.service_uuids = None
        adv.manufacturer_data = None

        result = _make_discovered(device, adv)

        assert result.name == ""
        assert result.service_uuids == ()
        assert result.manufacturer_data == {}
        assert result.is_tuya_mesh is False


# --- mac_to_bytes ---


class TestMacToBytes:
    """Test MAC address string to bytes conversion."""

    def test_valid_mac(self) -> None:
        result = mac_to_bytes("DC:23:4D:21:43:A5")
        assert result == bytes([0xDC, 0x23, 0x4D, 0x21, 0x43, 0xA5])

    def test_lowercase_mac(self) -> None:
        result = mac_to_bytes("dc:23:4d:21:43:a5")
        assert result == bytes([0xDC, 0x23, 0x4D, 0x21, 0x43, 0xA5])

    def test_all_zeros(self) -> None:
        result = mac_to_bytes("00:00:00:00:00:00")
        assert result == b"\x00" * 6

    def test_all_ff(self) -> None:
        result = mac_to_bytes("FF:FF:FF:FF:FF:FF")
        assert result == b"\xff" * 6

    def test_returns_6_bytes(self) -> None:
        assert len(mac_to_bytes("AA:BB:CC:DD:EE:FF")) == 6

    def test_too_short_raises(self) -> None:
        with pytest.raises(ProtocolError, match="Invalid MAC"):
            mac_to_bytes("AA:BB:CC")

    def test_too_long_raises(self) -> None:
        with pytest.raises(ProtocolError, match="Invalid MAC"):
            mac_to_bytes("AA:BB:CC:DD:EE:FF:00")

    def test_invalid_hex_raises(self) -> None:
        with pytest.raises(ProtocolError, match="Invalid MAC"):
            mac_to_bytes("GG:HH:II:JJ:KK:LL")

    def test_no_colons_raises(self) -> None:
        with pytest.raises(ProtocolError, match="Invalid MAC"):
            mac_to_bytes("AABBCCDDEEFF")


# --- Async scan functions ---


class TestScanForDevices:
    """Test scan_for_devices function."""

    @pytest.mark.asyncio
    async def test_scan_for_devices(self) -> None:
        """Test basic scan functionality."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from tuya_ble_mesh.scanner import scan_for_devices

        # Mock BleakScanner
        with (
            patch("tuya_ble_mesh.scanner.BleakScanner") as mock_scanner_class,
            patch("tuya_ble_mesh.scanner.asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_scanner = MagicMock()
            mock_scanner.__aenter__ = AsyncMock(return_value=mock_scanner)
            mock_scanner.__aexit__ = AsyncMock(return_value=None)
            mock_scanner_class.return_value = mock_scanner

            # Capture the callback
            callback = None

            def capture_callback(detection_callback=None):
                nonlocal callback
                callback = detection_callback
                return mock_scanner

            mock_scanner_class.side_effect = capture_callback

            devices = await scan_for_devices(timeout=0.1)

            assert isinstance(devices, list)

    @pytest.mark.asyncio
    async def test_scan_for_tuya_devices(self) -> None:
        """Test scanning only for Tuya devices."""
        from unittest.mock import patch

        from tuya_ble_mesh.scanner import DiscoveredDevice, scan_for_tuya_devices

        mock_devices = [
            DiscoveredDevice(
                name="out_of_mesh",
                address="AA:BB:CC:DD:EE:F1",
                rssi=-50,
                service_uuids=(),
                is_tuya_mesh=True,
            ),
            DiscoveredDevice(
                name="other_device",
                address="AA:BB:CC:DD:EE:F2",
                rssi=-60,
                service_uuids=(),
                is_tuya_mesh=False,
            ),
        ]

        with patch("tuya_ble_mesh.scanner.scan_for_devices", return_value=mock_devices):
            result = await scan_for_tuya_devices(timeout=1.0)

        assert len(result) == 1
        assert result[0].name == "out_of_mesh"

    @pytest.mark.asyncio
    async def test_find_device_by_mac_success(self) -> None:
        """Test finding a device by MAC address."""
        from unittest.mock import patch

        from tuya_ble_mesh.scanner import DiscoveredDevice, find_device_by_mac

        mock_devices = [
            DiscoveredDevice(
                name="device1",
                address="AA:BB:CC:DD:EE:F1",
                rssi=-50,
                service_uuids=(),
            ),
            DiscoveredDevice(
                name="device2",
                address="AA:BB:CC:DD:EE:F2",
                rssi=-60,
                service_uuids=(),
            ),
        ]

        with patch("tuya_ble_mesh.scanner.scan_for_devices", return_value=mock_devices):
            result = await find_device_by_mac("aa:bb:cc:dd:ee:f1", timeout=1.0)

        assert result.address == "AA:BB:CC:DD:EE:F1"
        assert result.name == "device1"

    @pytest.mark.asyncio
    async def test_find_device_by_mac_not_found(self) -> None:
        """Test DeviceNotFoundError when device is not found."""
        from unittest.mock import patch

        from tuya_ble_mesh.exceptions import DeviceNotFoundError
        from tuya_ble_mesh.scanner import find_device_by_mac

        with (
            patch("tuya_ble_mesh.scanner.scan_for_devices", return_value=[]),
            pytest.raises(DeviceNotFoundError, match="not found"),
        ):
            await find_device_by_mac("AA:BB:CC:DD:EE:FF", timeout=1.0)
