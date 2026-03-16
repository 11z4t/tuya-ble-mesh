"""Unit tests for Telink BLE Mesh protocol encoder/decoder."""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.const import (
    COMPACT_DP_BRIGHTNESS,
    COMPACT_DP_POWER,
    DP_TYPE_BOOLEAN,
    DP_TYPE_ENUM,
    DP_TYPE_RAW,
    DP_TYPE_STRING,
    DP_TYPE_VALUE,
    PAIR_OPCODE_FAILURE,
    PAIR_OPCODE_SET_OK,
    PAIR_OPCODE_SUCCESS,
    STATUS_OFFSET_BLUE,
    STATUS_OFFSET_COLOR_BRIGHTNESS,
    STATUS_OFFSET_GREEN,
    STATUS_OFFSET_MESH_ID,
    STATUS_OFFSET_MODE,
    STATUS_OFFSET_RED,
    STATUS_OFFSET_WHITE_BRIGHTNESS,
    STATUS_OFFSET_WHITE_TEMP,
    TELINK_CMD_POWER,
    TELINK_VENDOR_ID,
)
from tuya_ble_mesh.crypto import crypt_payload, make_checksum
from tuya_ble_mesh.exceptions import (
    AuthenticationError,
    MalformedPacketError,
    ProtocolError,
)
from tuya_ble_mesh.protocol import (
    COMMAND_PACKET_SIZE,
    MAX_PARAM_LEN,
    PAYLOAD_SIZE,
    CommandPacket,
    PairResponse,
    StatusResponse,
    build_nonce,
    build_notification_nonce,
    decode_command_packet,
    decode_dp_value,
    decode_dps_response,
    decode_status,
    decrypt_notification,
    encode_command_packet,
    encode_command_payload,
    encode_compact_dp,
    encode_dp_value,
    encode_dps_command,
    parse_pair_response,
)

# Test MAC address (DC:23:4D:21:43:A5)
TEST_MAC = bytes([0xDC, 0x23, 0x4D, 0x21, 0x43, 0xA5])
TEST_KEY = b"\xab" * 16


# --- build_nonce ---


class TestBuildNonce:
    """Test nonce construction."""

    def test_output_is_8_bytes(self) -> None:
        nonce = build_nonce(TEST_MAC, 0)
        assert len(nonce) == 8

    def test_mac_reversed_prefix(self) -> None:
        nonce = build_nonce(TEST_MAC, 0)
        # Reversed MAC: A5, 43, 21, 4D, 23, DC → first 4: A5, 43, 21, 4D
        assert nonce[:4] == bytes([0xA5, 0x43, 0x21, 0x4D])

    def test_separator_byte(self) -> None:
        nonce = build_nonce(TEST_MAC, 0)
        assert nonce[4] == 0x01

    def test_sequence_little_endian(self) -> None:
        nonce = build_nonce(TEST_MAC, 0x030201)
        assert nonce[5:8] == bytes([0x01, 0x02, 0x03])

    def test_sequence_zero(self) -> None:
        nonce = build_nonce(TEST_MAC, 0)
        assert nonce[5:8] == b"\x00\x00\x00"

    def test_sequence_max(self) -> None:
        nonce = build_nonce(TEST_MAC, 0xFFFFFF)
        assert nonce[5:8] == b"\xff\xff\xff"

    def test_wrong_mac_length(self) -> None:
        with pytest.raises(ProtocolError, match="6 bytes"):
            build_nonce(b"\x00" * 4, 0)

    def test_sequence_negative(self) -> None:
        with pytest.raises(ProtocolError, match="Sequence"):
            build_nonce(TEST_MAC, -1)

    def test_sequence_too_large(self) -> None:
        with pytest.raises(ProtocolError, match="Sequence"):
            build_nonce(TEST_MAC, 0x1000000)

    def test_deterministic(self) -> None:
        assert build_nonce(TEST_MAC, 42) == build_nonce(TEST_MAC, 42)


# --- encode_command_payload ---


class TestEncodeCommandPayload:
    """Test command payload construction."""

    def test_output_is_15_bytes(self) -> None:
        payload = encode_command_payload(0x0001, TELINK_CMD_POWER, b"\x01")
        assert len(payload) == PAYLOAD_SIZE

    def test_dest_id_little_endian(self) -> None:
        payload = encode_command_payload(0x0102, TELINK_CMD_POWER, b"")
        assert payload[:2] == struct.pack("<H", 0x0102)

    def test_opcode_at_byte_2(self) -> None:
        payload = encode_command_payload(0, TELINK_CMD_POWER, b"")
        assert payload[2] == TELINK_CMD_POWER

    def test_vendor_id_at_bytes_3_4(self) -> None:
        payload = encode_command_payload(0, TELINK_CMD_POWER, b"")
        assert payload[3:5] == TELINK_VENDOR_ID

    def test_params_after_vendor_id(self) -> None:
        payload = encode_command_payload(0, TELINK_CMD_POWER, b"\x01")
        assert payload[5] == 0x01

    def test_zero_padded(self) -> None:
        payload = encode_command_payload(0, TELINK_CMD_POWER, b"\x01")
        assert payload[6:] == b"\x00" * 9

    def test_max_params(self) -> None:
        params = b"\xff" * MAX_PARAM_LEN
        payload = encode_command_payload(0, 0, params)
        assert len(payload) == PAYLOAD_SIZE
        assert payload[5:] == params

    def test_params_too_long(self) -> None:
        with pytest.raises(ProtocolError, match="params too long"):
            encode_command_payload(0, 0, b"\x00" * (MAX_PARAM_LEN + 1))

    def test_dest_id_out_of_range(self) -> None:
        with pytest.raises(ProtocolError, match="dest_id"):
            encode_command_payload(0x10000, 0, b"")

    def test_opcode_out_of_range(self) -> None:
        with pytest.raises(ProtocolError, match="opcode"):
            encode_command_payload(0, 0x100, b"")

    def test_dest_id_negative(self) -> None:
        with pytest.raises(ProtocolError, match="dest_id"):
            encode_command_payload(-1, 0, b"")

    def test_custom_vendor_id(self) -> None:
        """Encoding with a custom vendor_id places it at bytes [3:5]."""
        awox_vendor = bytes([0x60, 0x01])
        payload = encode_command_payload(0, TELINK_CMD_POWER, b"", vendor_id=awox_vendor)
        assert payload[3:5] == awox_vendor

    def test_vendor_id_bad_length(self) -> None:
        """A vendor_id that is not exactly 2 bytes raises ProtocolError."""
        with pytest.raises(ProtocolError, match="vendor_id must be 2 bytes"):
            encode_command_payload(0, 0, b"", vendor_id=bytes([0x01, 0x02, 0x03]))


# --- encode_command_packet / decode_command_packet roundtrip ---


class TestCommandPacketRoundtrip:
    """Test encode → decrypt → decode roundtrip."""

    def test_roundtrip_power_on(self) -> None:
        packet = encode_command_packet(TEST_KEY, TEST_MAC, 1, 0x0001, TELINK_CMD_POWER, b"\x01")
        assert len(packet) == COMMAND_PACKET_SIZE

        decoded = decode_command_packet(TEST_KEY, TEST_MAC, packet)
        assert decoded.sequence == 1
        assert decoded.dest_id == 0x0001
        assert decoded.opcode == TELINK_CMD_POWER
        assert decoded.vendor_id == TELINK_VENDOR_ID
        assert decoded.params == b"\x01"

    def test_roundtrip_empty_params(self) -> None:
        packet = encode_command_packet(TEST_KEY, TEST_MAC, 0, 0, 0xE3, b"")
        decoded = decode_command_packet(TEST_KEY, TEST_MAC, packet)
        assert decoded.opcode == 0xE3
        assert decoded.params == b""

    def test_roundtrip_max_params(self) -> None:
        params = bytes(range(MAX_PARAM_LEN))
        packet = encode_command_packet(TEST_KEY, TEST_MAC, 100, 0xFFFF, 0xFF, params)
        decoded = decode_command_packet(TEST_KEY, TEST_MAC, packet)
        assert decoded.params == params
        assert decoded.dest_id == 0xFFFF

    def test_roundtrip_max_sequence(self) -> None:
        packet = encode_command_packet(TEST_KEY, TEST_MAC, 0xFFFFFF, 1, TELINK_CMD_POWER, b"\x00")
        decoded = decode_command_packet(TEST_KEY, TEST_MAC, packet)
        assert decoded.sequence == 0xFFFFFF

    def test_different_keys_fail_verification(self) -> None:
        packet = encode_command_packet(TEST_KEY, TEST_MAC, 1, 1, TELINK_CMD_POWER, b"\x01")
        wrong_key = b"\xcd" * 16
        with pytest.raises(AuthenticationError):
            decode_command_packet(wrong_key, TEST_MAC, packet)

    def test_wrong_size_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="20 bytes"):
            decode_command_packet(TEST_KEY, TEST_MAC, b"\x00" * 10)

    def test_tampered_payload_fails(self) -> None:
        packet = encode_command_packet(TEST_KEY, TEST_MAC, 1, 1, TELINK_CMD_POWER, b"\x01")
        tampered = bytearray(packet)
        tampered[10] ^= 0xFF  # Flip a byte in encrypted payload
        with pytest.raises(AuthenticationError):
            decode_command_packet(TEST_KEY, TEST_MAC, bytes(tampered))

    def test_roundtrip_custom_vendor_id(self) -> None:
        """Roundtrip with a non-default vendor_id preserves it."""
        awox_vendor = bytes([0x60, 0x01])
        packet = encode_command_packet(
            TEST_KEY, TEST_MAC, 1, 0x0001, TELINK_CMD_POWER, b"\x01", vendor_id=awox_vendor
        )
        decoded = decode_command_packet(TEST_KEY, TEST_MAC, packet)
        assert decoded.vendor_id == awox_vendor

    def test_encrypted_differs_from_plaintext(self) -> None:
        payload = encode_command_payload(1, TELINK_CMD_POWER, b"\x01")
        packet = encode_command_packet(TEST_KEY, TEST_MAC, 1, 1, TELINK_CMD_POWER, b"\x01")
        # Encrypted portion (bytes 5-19) should differ from plaintext
        assert packet[5:] != payload


# --- build_notification_nonce ---


class TestBuildNotificationNonce:
    """Test notification nonce construction."""

    def test_output_is_8_bytes(self) -> None:
        header = b"\x01\x02\x03\xaa\xbb"
        nonce = build_notification_nonce(TEST_MAC, header)
        assert len(nonce) == 8

    def test_uses_3_reversed_mac_bytes(self) -> None:
        header = b"\x00" * 5
        nonce = build_notification_nonce(TEST_MAC, header)
        # Reversed MAC: A5, 43, 21, 4D, 23, DC → first 3: A5, 43, 21
        assert nonce[:3] == bytes([0xA5, 0x43, 0x21])

    def test_uses_5_header_bytes(self) -> None:
        header = b"\x01\x02\x03\xaa\xbb"
        nonce = build_notification_nonce(TEST_MAC, header)
        assert nonce[3:] == header

    def test_wrong_mac_length(self) -> None:
        with pytest.raises(ProtocolError, match="6 bytes"):
            build_notification_nonce(b"\x00" * 4, b"\x00" * 5)

    def test_header_too_short(self) -> None:
        with pytest.raises(ProtocolError, match="raw_header"):
            build_notification_nonce(TEST_MAC, b"\x00" * 4)

    def test_uses_exactly_5_bytes_from_longer_input(self) -> None:
        """Extra bytes beyond 5 are ignored."""
        header_5 = b"\x01\x02\x03\x04\x05"
        header_7 = header_5 + b"\xaa\xbb"
        assert build_notification_nonce(TEST_MAC, header_5) == build_notification_nonce(
            TEST_MAC, header_7
        )

    def test_differs_from_command_nonce(self) -> None:
        """Notification nonce uses different format from command nonce."""
        seq = 42
        cmd_nonce = build_nonce(TEST_MAC, seq)
        header = seq.to_bytes(3, "little") + b"\x00\x00"
        notif_nonce = build_notification_nonce(TEST_MAC, header)
        assert cmd_nonce != notif_nonce


# --- decrypt_notification ---


def _build_notification_packet(
    key: bytes,
    mac_bytes: bytes,
    sequence: int,
    payload: bytes,
    header_extra: bytes = b"\x00\x00",
) -> bytes:
    """Build a notification packet encrypted with notification nonce.

    Simulates what a real device sends. The notification wire format is:
    ``[3B counter][2B header_extra][2B checksum][13B encrypted payload]``

    The nonce uses the first 5 bytes (counter + header_extra), so the
    checksum at bytes [5:7] is NOT part of the nonce — no circular dependency.

    Args:
        header_extra: 2 bytes of additional header data (e.g. source mesh ID).
    """
    if len(payload) != 13:
        msg = f"Notification payload must be 13 bytes, got {len(payload)}"
        raise AssertionError(msg)

    seq_bytes = sequence.to_bytes(3, "little")
    header = seq_bytes + header_extra[:2]  # 5 bytes
    nonce = build_notification_nonce(mac_bytes, header)
    checksum = make_checksum(key, nonce, payload)[:2]
    encrypted = crypt_payload(key, nonce, payload)
    return header + checksum + encrypted  # 5 + 2 + 13 = 20 bytes


class TestDecryptNotification:
    """Test notification decryption."""

    def test_roundtrip_with_notification_nonce(self) -> None:
        """A notification encrypted with notification nonce decrypts correctly."""
        # 13-byte payload (bytes [7:20] of 20-byte notification)
        payload = bytearray(13)
        payload[0] = 0x01  # example data byte
        payload[5] = 0x03  # mode field (at full packet offset 12)
        payload[6] = 0x7F  # white_brightness (at full packet offset 13)

        mesh_id_bytes = b"\x42\x00"  # mesh_id = 0x42 at header[3]
        packet = _build_notification_packet(
            TEST_KEY,
            TEST_MAC,
            42,
            bytes(payload),
            mesh_id_bytes,
        )
        decrypted = decrypt_notification(TEST_KEY, TEST_MAC, packet)
        assert len(decrypted) == COMMAND_PACKET_SIZE
        # Sequence preserved in header bytes 0-2
        assert int.from_bytes(decrypted[:3], "little") == 42
        # Mesh ID at byte 3 (from unencrypted header)
        assert decrypted[STATUS_OFFSET_MESH_ID] == 0x42
        # Mode at byte 12 (from decrypted payload[5])
        assert decrypted[STATUS_OFFSET_MODE] == 0x03
        # White brightness at byte 13 (from decrypted payload[6])
        assert decrypted[STATUS_OFFSET_WHITE_BRIGHTNESS] == 0x7F

    def test_returned_packet_is_20_bytes(self) -> None:
        """decrypt_notification returns full 20-byte packet matching awox format."""
        payload = b"\x00" * 13
        packet = _build_notification_packet(TEST_KEY, TEST_MAC, 0, payload)
        decrypted = decrypt_notification(TEST_KEY, TEST_MAC, packet)
        assert len(decrypted) == 20

    def test_header_preserved(self) -> None:
        """First 7 bytes (header + checksum) are unchanged in output."""
        payload = b"\x00" * 13
        packet = _build_notification_packet(TEST_KEY, TEST_MAC, 99, payload)
        decrypted = decrypt_notification(TEST_KEY, TEST_MAC, packet)
        # First 7 bytes should match the wire packet
        assert decrypted[:7] == packet[:7]

    def test_too_short_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="too short"):
            decrypt_notification(TEST_KEY, TEST_MAC, b"\x00" * 7)

    def test_wrong_key_fails(self) -> None:
        payload = b"\x00" * 13
        packet = _build_notification_packet(TEST_KEY, TEST_MAC, 1, payload)
        with pytest.raises(AuthenticationError):
            decrypt_notification(b"\xcd" * 16, TEST_MAC, packet)


# --- decode_status ---


class TestDecodeStatus:
    """Test status response parsing."""

    def test_parses_all_fields(self) -> None:
        # Build a 20-byte buffer with known values at status offsets
        data = bytearray(20)
        data[STATUS_OFFSET_MESH_ID] = 0x42
        data[STATUS_OFFSET_MODE] = 0x01
        data[STATUS_OFFSET_WHITE_BRIGHTNESS] = 0x7F
        data[STATUS_OFFSET_WHITE_TEMP] = 0x3F
        data[STATUS_OFFSET_COLOR_BRIGHTNESS] = 0x64
        data[STATUS_OFFSET_RED] = 0xFF
        data[STATUS_OFFSET_GREEN] = 0x80
        data[STATUS_OFFSET_BLUE] = 0x00

        status = decode_status(bytes(data))
        assert status.mesh_id == 0x42
        assert status.mode == 0x01
        assert status.white_brightness == 0x7F
        assert status.white_temp == 0x3F
        assert status.color_brightness == 0x64
        assert status.red == 0xFF
        assert status.green == 0x80
        assert status.blue == 0x00

    def test_returns_status_response(self) -> None:
        data = b"\x00" * 20
        assert isinstance(decode_status(data), StatusResponse)

    def test_too_short_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="too short"):
            decode_status(b"\x00" * 10)

    def test_minimum_size_accepted(self) -> None:
        """Exactly STATUS_OFFSET_BLUE + 1 bytes should work."""
        min_size = STATUS_OFFSET_BLUE + 1
        data = b"\x00" * min_size
        status = decode_status(data)
        assert status.blue == 0

    def test_frozen_dataclass(self) -> None:
        data = b"\x00" * 20
        status = decode_status(data)
        with pytest.raises(AttributeError):
            status.mode = 5  # type: ignore[misc]


# --- parse_pair_response ---


class TestParsePairResponse:
    """Test pair response parsing."""

    def test_success_response(self) -> None:
        device_random = b"\x11\x22\x33\x44\x55\x66\x77\x88"
        data = bytes([PAIR_OPCODE_SUCCESS]) + device_random
        resp = parse_pair_response(data)
        assert resp.opcode == PAIR_OPCODE_SUCCESS
        assert resp.device_random == device_random

    def test_success_with_extra_bytes(self) -> None:
        data = bytes([PAIR_OPCODE_SUCCESS]) + b"\xaa" * 19
        resp = parse_pair_response(data)
        assert resp.device_random == b"\xaa" * 8  # Only first 8

    def test_failure_response(self) -> None:
        data = bytes([PAIR_OPCODE_FAILURE])
        resp = parse_pair_response(data)
        assert resp.opcode == PAIR_OPCODE_FAILURE
        assert resp.device_random == b""

    def test_set_ok_response(self) -> None:
        data = bytes([PAIR_OPCODE_SET_OK])
        resp = parse_pair_response(data)
        assert resp.opcode == PAIR_OPCODE_SET_OK
        assert resp.device_random == b""

    def test_success_too_short(self) -> None:
        # Success opcode but not enough bytes for device random
        data = bytes([PAIR_OPCODE_SUCCESS]) + b"\x00" * 3
        with pytest.raises(MalformedPacketError, match="9 bytes"):
            parse_pair_response(data)

    def test_empty_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="empty"):
            parse_pair_response(b"")

    def test_unknown_opcode_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="0xFF"):
            parse_pair_response(bytes([0xFF]))

    def test_returns_pair_response(self) -> None:
        data = bytes([PAIR_OPCODE_FAILURE])
        assert isinstance(parse_pair_response(data), PairResponse)


# --- encode_dp_value ---


class TestEncodeDpValue:
    """Test Tuya DP TLV encoding."""

    def test_boolean_true(self) -> None:
        result = encode_dp_value(1, True)
        assert result == bytes([1, DP_TYPE_BOOLEAN, 0, 1, 1])

    def test_boolean_false(self) -> None:
        result = encode_dp_value(1, False)
        assert result == bytes([1, DP_TYPE_BOOLEAN, 0, 1, 0])

    def test_integer(self) -> None:
        result = encode_dp_value(3, 1000)
        dp_id, dp_type, length = struct.unpack(">BBH", result[:4])
        assert dp_id == 3
        assert dp_type == DP_TYPE_VALUE
        assert length == 4
        (val,) = struct.unpack(">i", result[4:])
        assert val == 1000

    def test_negative_integer(self) -> None:
        result = encode_dp_value(3, -100)
        (val,) = struct.unpack(">i", result[4:])
        assert val == -100

    def test_string(self) -> None:
        result = encode_dp_value(5, "hello")
        dp_id, dp_type, length = struct.unpack(">BBH", result[:4])
        assert dp_id == 5
        assert dp_type == DP_TYPE_STRING
        assert length == 5
        assert result[4:] == b"hello"

    def test_raw_bytes(self) -> None:
        result = encode_dp_value(6, b"\xde\xad")
        dp_id, dp_type, length = struct.unpack(">BBH", result[:4])
        assert dp_id == 6
        assert dp_type == DP_TYPE_RAW
        assert length == 2
        assert result[4:] == b"\xde\xad"

    def test_dp_id_zero_raises(self) -> None:
        with pytest.raises(ProtocolError, match="dp_id"):
            encode_dp_value(0, True)

    def test_dp_id_too_large_raises(self) -> None:
        with pytest.raises(ProtocolError, match="dp_id"):
            encode_dp_value(256, True)

    def test_int_overflow_raises(self) -> None:
        with pytest.raises(ProtocolError, match="32-bit"):
            encode_dp_value(1, 2**31)

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(ProtocolError, match="Unsupported"):
            encode_dp_value(1, 3.14)  # type: ignore[arg-type]


# --- decode_dp_value ---


class TestDecodeDpValue:
    """Test Tuya DP TLV decoding."""

    def test_boolean_true(self) -> None:
        data = bytes([1, DP_TYPE_BOOLEAN, 0, 1, 1])
        dp_id, dp_type, value = decode_dp_value(data)
        assert dp_id == 1
        assert dp_type == DP_TYPE_BOOLEAN
        assert value is True

    def test_boolean_false(self) -> None:
        data = bytes([1, DP_TYPE_BOOLEAN, 0, 1, 0])
        _, _, value = decode_dp_value(data)
        assert value is False

    def test_integer(self) -> None:
        data = struct.pack(">BBH", 3, DP_TYPE_VALUE, 4) + struct.pack(">i", 42)
        dp_id, _, value = decode_dp_value(data)
        assert dp_id == 3
        assert value == 42

    def test_string(self) -> None:
        text = b"test"
        data = struct.pack(">BBH", 5, DP_TYPE_STRING, len(text)) + text
        _, _, value = decode_dp_value(data)
        assert value == "test"

    def test_enum(self) -> None:
        data = bytes([2, DP_TYPE_ENUM, 0, 1, 3])
        dp_id, dp_type, value = decode_dp_value(data)
        assert dp_id == 2
        assert dp_type == DP_TYPE_ENUM
        assert value == 3

    def test_raw(self) -> None:
        raw = b"\xab\xcd"
        data = struct.pack(">BBH", 7, DP_TYPE_RAW, len(raw)) + raw
        _, _, value = decode_dp_value(data)
        assert value == raw

    def test_too_short_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="too short"):
            decode_dp_value(b"\x01\x01")

    def test_truncated_value_raises(self) -> None:
        # Header says 4 bytes but only 2 available
        data = struct.pack(">BBH", 1, DP_TYPE_VALUE, 4) + b"\x00\x00"
        with pytest.raises(MalformedPacketError, match="truncated"):
            decode_dp_value(data)

    def test_boolean_wrong_length_raises(self) -> None:
        data = bytes([1, DP_TYPE_BOOLEAN, 0, 2, 0, 0])
        with pytest.raises(MalformedPacketError, match="1 byte"):
            decode_dp_value(data)

    def test_value_wrong_length_raises(self) -> None:
        data = struct.pack(">BBH", 1, DP_TYPE_VALUE, 2) + b"\x00\x00"
        with pytest.raises(MalformedPacketError, match="4 bytes"):
            decode_dp_value(data)

    def test_enum_wrong_length_raises(self) -> None:
        data = bytes([1, DP_TYPE_ENUM, 0, 2, 0, 0])
        with pytest.raises(MalformedPacketError, match="1 byte"):
            decode_dp_value(data)


# --- encode_dps_command / decode_dps_response roundtrip ---


class TestDpsRoundtrip:
    """Test DP dict encoding/decoding roundtrip."""

    def test_single_boolean(self) -> None:
        dps = {1: True}
        encoded = encode_dps_command(dps)
        decoded = decode_dps_response(encoded)
        assert decoded[1] is True

    def test_multiple_dps(self) -> None:
        dps: dict[int, bool | int | str | bytes] = {
            1: True,
            3: 500,
            5: "warm",
        }
        encoded = encode_dps_command(dps)
        decoded = decode_dps_response(encoded)
        assert decoded[1] is True
        assert decoded[3] == 500
        assert decoded[5] == "warm"

    def test_empty_dict(self) -> None:
        encoded = encode_dps_command({})
        assert encoded == b""
        decoded = decode_dps_response(encoded)
        assert decoded == {}

    def test_sorted_by_dp_id(self) -> None:
        """DPs are encoded in sorted order."""
        dps: dict[int, bool | int | str | bytes] = {3: 100, 1: True}
        encoded = encode_dps_command(dps)
        # First DP should be ID 1
        assert encoded[0] == 1

    def test_trailing_bytes_raises(self) -> None:
        encoded = encode_dp_value(1, True)
        # Add incomplete trailing data
        with pytest.raises(MalformedPacketError, match="Trailing"):
            decode_dps_response(encoded + b"\x01")

    def test_truncated_dp_raises(self) -> None:
        encoded = encode_dp_value(1, True)
        # Truncate the value byte
        with pytest.raises(MalformedPacketError):
            decode_dps_response(encoded[:-1])


# --- Data class tests ---


class TestDataClasses:
    """Test data class properties."""

    def test_command_packet_frozen(self) -> None:
        pkt = CommandPacket(sequence=0, dest_id=0, opcode=0, vendor_id=b"", params=b"")
        with pytest.raises(AttributeError):
            pkt.sequence = 1  # type: ignore[misc]

    def test_status_response_frozen(self) -> None:
        sr = StatusResponse(
            mesh_id=0,
            mode=0,
            white_brightness=0,
            white_temp=0,
            color_brightness=0,
            red=0,
            green=0,
            blue=0,
        )
        with pytest.raises(AttributeError):
            sr.red = 255  # type: ignore[misc]

    def test_pair_response_frozen(self) -> None:
        pr = PairResponse(opcode=0x0D, device_random=b"")
        with pytest.raises(AttributeError):
            pr.opcode = 0  # type: ignore[misc]


# --- encode_compact_dp ---


class TestEncodeCompactDp:
    """Test compact DP encoding (Telink 0xD2 format)."""

    def test_power_on(self) -> None:
        result = encode_compact_dp(COMPACT_DP_POWER, DP_TYPE_VALUE, 1)
        assert result == bytes([121, DP_TYPE_VALUE, 4, 0, 0, 0, 1])

    def test_power_off(self) -> None:
        result = encode_compact_dp(COMPACT_DP_POWER, DP_TYPE_VALUE, 0)
        assert result == bytes([121, DP_TYPE_VALUE, 4, 0, 0, 0, 0])

    def test_brightness_100(self) -> None:
        result = encode_compact_dp(COMPACT_DP_BRIGHTNESS, DP_TYPE_VALUE, 100)
        assert result == bytes([122, DP_TYPE_VALUE, 4, 0, 0, 0, 100])

    def test_brightness_1(self) -> None:
        result = encode_compact_dp(COMPACT_DP_BRIGHTNESS, DP_TYPE_VALUE, 1)
        assert result == bytes([122, DP_TYPE_VALUE, 4, 0, 0, 0, 1])

    def test_boolean_type(self) -> None:
        result = encode_compact_dp(1, DP_TYPE_BOOLEAN, 1)
        assert result == bytes([1, DP_TYPE_BOOLEAN, 1, 1])

    def test_boolean_false(self) -> None:
        result = encode_compact_dp(1, DP_TYPE_BOOLEAN, 0)
        assert result == bytes([1, DP_TYPE_BOOLEAN, 1, 0])

    def test_header_is_3_bytes(self) -> None:
        """Compact DP uses 1-byte length, not 2-byte like standard Tuya."""
        result = encode_compact_dp(COMPACT_DP_POWER, DP_TYPE_VALUE, 1)
        # Header: [dp_id=121][dp_type=2][length=4]
        assert result[0] == 121
        assert result[1] == DP_TYPE_VALUE
        assert result[2] == 4  # 4 bytes for uint32

    def test_value_big_endian(self) -> None:
        result = encode_compact_dp(COMPACT_DP_BRIGHTNESS, DP_TYPE_VALUE, 0x01020304)
        assert result[3:] == bytes([0x01, 0x02, 0x03, 0x04])

    def test_dp_id_zero_raises(self) -> None:
        with pytest.raises(ProtocolError, match="dp_id"):
            encode_compact_dp(0, DP_TYPE_VALUE, 1)

    def test_dp_id_too_large_raises(self) -> None:
        with pytest.raises(ProtocolError, match="dp_id"):
            encode_compact_dp(256, DP_TYPE_VALUE, 1)

    def test_dp_type_negative_raises(self) -> None:
        with pytest.raises(ProtocolError, match="dp_type"):
            encode_compact_dp(1, -1, 1)

    def test_value_negative_raises(self) -> None:
        with pytest.raises(ProtocolError, match="Value must be"):
            encode_compact_dp(1, DP_TYPE_VALUE, -1)

    def test_value_overflow_raises(self) -> None:
        with pytest.raises(ProtocolError, match="Value must be"):
            encode_compact_dp(1, DP_TYPE_VALUE, 0x100000000)

    def test_unsupported_dp_type_raises(self) -> None:
        with pytest.raises(ProtocolError, match="Unsupported compact"):
            encode_compact_dp(1, DP_TYPE_STRING, 1)

    def test_compact_dp_constants(self) -> None:
        """Verify compact DP constants match confirmed values."""
        assert COMPACT_DP_POWER == 121
        assert COMPACT_DP_BRIGHTNESS == 122
