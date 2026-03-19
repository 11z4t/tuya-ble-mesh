"""Unit tests for BLE mesh constants."""

import re
import sys
from pathlib import Path
from typing import ClassVar

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "custom_components"
        / "tuya_ble_mesh"
        / "lib"
    ),
)

from tuya_ble_mesh.const import (
    BT_BASE_UUID_FMT,
    DEVICE_INFORMATION_SERVICE,
    DIS_CHARACTERISTICS,
    DIS_FIRMWARE_REVISION,
    DIS_HARDWARE_REVISION,
    DIS_MANUFACTURER_NAME,
    DIS_MODEL_NUMBER,
    DIS_SOFTWARE_REVISION,
    DP_TYPE_BITMAP,
    DP_TYPE_BOOLEAN,
    DP_TYPE_ENUM,
    DP_TYPE_NAMES,
    DP_TYPE_RAW,
    DP_TYPE_STRING,
    DP_TYPE_VALUE,
    MESH_LIGHT_C,
    MESH_LIGHT_CW,
    MESH_LIGHT_RGB,
    MESH_LIGHT_RGBC,
    MESH_LIGHT_RGBCW,
    MESH_LIGHT_TYPE_NAMES,
    SIG_MESH_PROV_DATA_IN,
    SIG_MESH_PROV_DATA_OUT,
    SIG_MESH_PROVISIONING_SERVICE,
    SIG_MESH_PROXY_DATA_IN,
    SIG_MESH_PROXY_DATA_OUT,
    SIG_MESH_PROXY_SERVICE,
    TARGET_DEVICE_MAC,
    TARGET_DEVICE_MODEL,
    TELINK_BASE_UUID_PREFIX,
    TELINK_CHAR_COMMAND,
    TELINK_CHAR_OTA,
    TELINK_CHAR_PAIRING,
    TELINK_CHAR_STATUS,
    TELINK_CUSTOM_SERVICE,
    TUYA_CHAR_COMMAND_RX,
    TUYA_CHAR_COMMAND_TX,
    TUYA_CHAR_OTA,
    TUYA_CHAR_PAIRING,
    TUYA_CMD_DP_DATA,
    TUYA_CMD_TIMESTAMP_SYNC,
    TUYA_CUSTOM_SERVICE,
    TUYA_MESH_DEFAULT_NAME,
    TUYA_MESH_DEFAULT_PASSWORD,
    TUYA_MESH_NAME_PATTERNS,
    TUYA_MESH_SERVICE_UUID,
    TUYA_OPCODE_DATA,
    TUYA_OPCODE_READ,
    TUYA_OPCODE_STATUS,
    TUYA_OPCODE_WRITE_ACK,
    TUYA_OPCODE_WRITE_UNACK,
)

# Standard 128-bit UUID regex (lowercase hex, 8-4-4-4-12)
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# MAC address regex (uppercase, colon-separated)
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


class TestUUIDFormat:
    """All UUIDs must be valid 128-bit lowercase strings."""

    ALL_UUIDS: ClassVar[list[str]] = [
        TUYA_MESH_SERVICE_UUID,
        SIG_MESH_PROVISIONING_SERVICE,
        SIG_MESH_PROXY_SERVICE,
        SIG_MESH_PROV_DATA_IN,
        SIG_MESH_PROV_DATA_OUT,
        SIG_MESH_PROXY_DATA_IN,
        SIG_MESH_PROXY_DATA_OUT,
        DEVICE_INFORMATION_SERVICE,
        DIS_MANUFACTURER_NAME,
        DIS_MODEL_NUMBER,
        DIS_FIRMWARE_REVISION,
        DIS_HARDWARE_REVISION,
        DIS_SOFTWARE_REVISION,
        TUYA_CUSTOM_SERVICE,
        TUYA_CHAR_COMMAND_TX,
        TUYA_CHAR_COMMAND_RX,
        TUYA_CHAR_PAIRING,
        TUYA_CHAR_OTA,
        TELINK_CUSTOM_SERVICE,
        TELINK_CHAR_STATUS,
        TELINK_CHAR_COMMAND,
        TELINK_CHAR_OTA,
        TELINK_CHAR_PAIRING,
    ]

    def test_all_uuids_are_valid_128bit(self) -> None:
        for uuid in self.ALL_UUIDS:
            assert UUID_RE.match(uuid), f"Invalid UUID format: {uuid}"

    def test_bt_sig_uuids_use_standard_base(self) -> None:
        """BT SIG-based UUIDs use the standard base. Telink UUIDs use their own."""
        bt_sig_uuids = [u for u in self.ALL_UUIDS if not u.startswith(TELINK_BASE_UUID_PREFIX)]
        for uuid in bt_sig_uuids:
            assert uuid.endswith("-0000-1000-8000-00805f9b34fb"), (
                f"UUID does not use BT SIG base: {uuid}"
            )

    def test_telink_uuids_use_telink_base(self) -> None:
        """Telink UUIDs use the Telink-specific base."""
        telink_uuids = [u for u in self.ALL_UUIDS if u.startswith(TELINK_BASE_UUID_PREFIX)]
        assert len(telink_uuids) == 5
        for uuid in telink_uuids:
            assert uuid.startswith(TELINK_BASE_UUID_PREFIX)

    def test_bt_base_uuid_fmt_produces_valid_uuid(self) -> None:
        result = BT_BASE_UUID_FMT.format(0xFE07)
        assert result == TUYA_MESH_SERVICE_UUID

    def test_dis_characteristics_frozenset_complete(self) -> None:
        expected = {
            DIS_MANUFACTURER_NAME,
            DIS_MODEL_NUMBER,
            DIS_FIRMWARE_REVISION,
            DIS_HARDWARE_REVISION,
            DIS_SOFTWARE_REVISION,
        }
        assert expected == DIS_CHARACTERISTICS


class TestTuyaMeshServiceUUID:
    """Verify the Tuya mesh advertising UUID."""

    def test_tuya_service_uuid_is_fe07(self) -> None:
        assert TUYA_MESH_SERVICE_UUID == "0000fe07-0000-1000-8000-00805f9b34fb"

    def test_sig_provisioning_is_1827(self) -> None:
        assert SIG_MESH_PROVISIONING_SERVICE == "00001827-0000-1000-8000-00805f9b34fb"

    def test_sig_proxy_is_1828(self) -> None:
        assert SIG_MESH_PROXY_SERVICE == "00001828-0000-1000-8000-00805f9b34fb"


class TestTuyaCustomCharacteristics:
    """Verify Tuya proprietary characteristic UUIDs."""

    def test_custom_service_is_1910(self) -> None:
        assert TUYA_CUSTOM_SERVICE == "00001910-0000-1000-8000-00805f9b34fb"

    def test_characteristics_are_sequential(self) -> None:
        """1911, 1912, 1913, 1914 are sequential."""
        uuids = [TUYA_CHAR_COMMAND_TX, TUYA_CHAR_COMMAND_RX, TUYA_CHAR_PAIRING, TUYA_CHAR_OTA]
        short_ids = [int(u[:8], 16) for u in uuids]
        assert short_ids == [0x1911, 0x1912, 0x1913, 0x1914]


class TestVendorOpcodes:
    """Verify Tuya vendor model opcodes."""

    def test_opcodes_share_wire_company_id(self) -> None:
        """All opcodes have the same lower 16 bits (company ID in wire format)."""
        wire_ids = {
            opcode & 0xFFFF
            for opcode in [
                TUYA_OPCODE_WRITE_ACK,
                TUYA_OPCODE_WRITE_UNACK,
                TUYA_OPCODE_STATUS,
                TUYA_OPCODE_READ,
                TUYA_OPCODE_DATA,
            ]
        }
        assert len(wire_ids) == 1

    def test_write_ack_opcode(self) -> None:
        assert TUYA_OPCODE_WRITE_ACK == 0xC9D007

    def test_data_opcode(self) -> None:
        assert TUYA_OPCODE_DATA == 0xCDD007

    def test_command_types(self) -> None:
        assert TUYA_CMD_DP_DATA == 0x01
        assert TUYA_CMD_TIMESTAMP_SYNC == 0x02


class TestDPTypes:
    """Verify data point type codes."""

    def test_dp_types_are_sequential(self) -> None:
        types = [DP_TYPE_RAW, DP_TYPE_BOOLEAN, DP_TYPE_VALUE, DP_TYPE_STRING, DP_TYPE_ENUM]
        assert types == [0, 1, 2, 3, 4]

    def test_bitmap_is_five(self) -> None:
        assert DP_TYPE_BITMAP == 0x05

    def test_dp_type_names_complete(self) -> None:
        """Every DP type code has a name."""
        all_types = [
            DP_TYPE_RAW,
            DP_TYPE_BOOLEAN,
            DP_TYPE_VALUE,
            DP_TYPE_STRING,
            DP_TYPE_ENUM,
            DP_TYPE_BITMAP,
        ]
        for t in all_types:
            assert t in DP_TYPE_NAMES, f"Missing name for DP type 0x{t:02X}"


class TestMeshCategories:
    """Verify mesh category codes."""

    def test_light_categories_start_at_0x1011(self) -> None:
        assert MESH_LIGHT_C == 0x1011

    def test_light_categories_are_sequential(self) -> None:
        cats = [MESH_LIGHT_C, MESH_LIGHT_CW, MESH_LIGHT_RGB, MESH_LIGHT_RGBC, MESH_LIGHT_RGBCW]
        assert cats == [0x1011, 0x1012, 0x1013, 0x1014, 0x1015]

    def test_light_type_names_complete(self) -> None:
        all_cats = [
            MESH_LIGHT_C,
            MESH_LIGHT_CW,
            MESH_LIGHT_RGB,
            MESH_LIGHT_RGBC,
            MESH_LIGHT_RGBCW,
        ]
        for cat in all_cats:
            assert cat in MESH_LIGHT_TYPE_NAMES


class TestDeviceDefaults:
    """Verify device defaults and target configuration."""

    def test_default_mesh_name(self) -> None:
        assert TUYA_MESH_DEFAULT_NAME == "out_of_mesh"

    def test_default_mesh_password(self) -> None:
        assert TUYA_MESH_DEFAULT_PASSWORD == "123456"

    def test_mesh_name_patterns(self) -> None:
        assert "out_of_mesh" in TUYA_MESH_NAME_PATTERNS
        assert "tymesh" in TUYA_MESH_NAME_PATTERNS

    def test_target_mac_format(self) -> None:
        assert MAC_RE.match(TARGET_DEVICE_MAC), f"Invalid MAC format: {TARGET_DEVICE_MAC}"

    def test_target_mac_value(self) -> None:
        assert TARGET_DEVICE_MAC == "DC:23:4D:21:43:A5"

    def test_target_model(self) -> None:
        assert TARGET_DEVICE_MODEL == "9952126"
