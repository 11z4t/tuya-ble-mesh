"""Unit tests for BLE scanner module."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

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
