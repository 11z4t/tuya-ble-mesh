"""Telink BLE Mesh protocol encoder/decoder.

This is the ONLY module that parses and constructs raw BLE protocol
bytes (Rule S3). All byte-access is bounds-checked and validated.

SECURITY: Packet data (hex bytes) may be logged for debugging, but
NEVER key material. Only protocol.py may perform byte slicing on
BLE wire data.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from tuya_ble_mesh.const import (
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
    TELINK_VENDOR_ID,
)
from tuya_ble_mesh.crypto import crypt_payload, make_checksum, verify_checksum
from tuya_ble_mesh.exceptions import MalformedPacketError, ProtocolError

# --- Packet structure constants ---

COMMAND_PACKET_SIZE = 20
PAYLOAD_SIZE = 15  # encrypted portion (bytes 5-19)
SEQUENCE_SIZE = 3
CHECKSUM_SIZE = 2
MAC_BYTES_SIZE = 6
MAX_PARAM_LEN = PAYLOAD_SIZE - 2 - 1 - len(TELINK_VENDOR_ID)  # 10

# Status notifications require at least up to the blue offset
_STATUS_MIN_SIZE = STATUS_OFFSET_BLUE + 1

# Pair response sizes
_PAIR_RESPONSE_MIN_SIZE = 1
_PAIR_SUCCESS_SIZE = 9  # [0x0D][8B device_random]
_SESSION_RANDOM_SIZE = 8

# DP TLV header: [dp_id 1B][dp_type 1B][length 2B BE]
_DP_HEADER_SIZE = 4


# --- Data classes ---


@dataclass(frozen=True)
class CommandPacket:
    """A decoded command packet."""

    sequence: int
    dest_id: int
    opcode: int
    vendor_id: bytes
    params: bytes


@dataclass(frozen=True)
class StatusResponse:
    """Parsed status notification from characteristic 1911.

    Field offsets are defined in const.py (STATUS_OFFSET_*) and
    applied to the full 20-byte notification with payload decrypted
    in place.
    """

    mesh_id: int
    mode: int
    white_brightness: int
    white_temp: int
    color_brightness: int
    red: int
    green: int
    blue: int


@dataclass(frozen=True)
class PairResponse:
    """Parsed pair response from characteristic 1914."""

    opcode: int
    device_random: bytes  # 8 bytes on success, empty on failure


# --- Nonce construction ---


def build_nonce(mac_bytes: bytes, sequence: int) -> bytes:
    """Build the 8-byte nonce for command encryption.

    Nonce format: ``[MAC_rev[0:4]][0x01][seq_lo][seq_mid][seq_hi]``

    The MAC bytes are reversed (Telink little-endian convention), then
    the first 4 bytes of the reversed MAC form the nonce prefix.

    SECURITY NOTE — nonce predictability:
    Both the MAC (public BLE advertisement) and the sequence counter
    (incrementing from 0) are deterministic. This is acceptable because
    CTR+CBC-MAC (AES-CCM) does NOT require nonce secrecy — only
    uniqueness within a session. The 24-bit counter supports 16M packets
    before wrapping; sessions reset on BLE reconnect so reuse across
    sessions uses a fresh session key.

    Args:
        mac_bytes: 6-byte BLE MAC address in standard order.
        sequence: 24-bit packet sequence counter.

    Returns:
        8-byte nonce.
    """
    if len(mac_bytes) != MAC_BYTES_SIZE:
        msg = f"MAC must be {MAC_BYTES_SIZE} bytes, got {len(mac_bytes)}"
        raise ProtocolError(msg)
    if not 0 <= sequence <= 0xFFFFFF:
        msg = f"Sequence must be 0..0xFFFFFF, got {sequence}"
        raise ProtocolError(msg)

    rev_mac = bytes(reversed(mac_bytes))
    seq_bytes = sequence.to_bytes(3, "little")
    return rev_mac[:4] + b"\x01" + seq_bytes


# --- Command packet encoding ---


def encode_command_payload(
    dest_id: int,
    opcode: int,
    params: bytes,
    *,
    vendor_id: bytes = TELINK_VENDOR_ID,
) -> bytes:
    """Build the 15-byte unencrypted command payload.

    Format: [dest_id LE 2B][opcode 1B][vendor_id 2B][params...][zero-pad to 15B]

    Args:
        dest_id: Destination mesh address (0..0xFFFF).
        opcode: Telink command code (e.g. 0xD0 for power).
        params: Command parameters (max 10 bytes).
        vendor_id: 2-byte vendor identifier (default: TELINK_VENDOR_ID).

    Returns:
        15-byte payload ready for encryption.
    """
    if len(vendor_id) != 2:
        msg = f"vendor_id must be 2 bytes, got {len(vendor_id)}"
        raise ProtocolError(msg)
    if not 0 <= dest_id <= 0xFFFF:
        msg = f"dest_id must be 0..0xFFFF, got {dest_id}"
        raise ProtocolError(msg)
    if not 0 <= opcode <= 0xFF:
        msg = f"opcode must be 0..0xFF, got {opcode}"
        raise ProtocolError(msg)
    if len(params) > MAX_PARAM_LEN:
        msg = f"params too long: {len(params)} > {MAX_PARAM_LEN}"
        raise ProtocolError(msg)

    payload = struct.pack("<HB", dest_id, opcode)
    payload += vendor_id
    payload += params
    payload += b"\x00" * (PAYLOAD_SIZE - len(payload))
    return payload


def encode_command_packet(
    key: bytes,
    mac_bytes: bytes,
    sequence: int,
    dest_id: int,
    opcode: int,
    params: bytes,
    *,
    vendor_id: bytes = TELINK_VENDOR_ID,
) -> bytes:
    """Build a complete 20-byte encrypted command packet.

    Structure: [3B sequence][2B truncated MAC][15B encrypted payload]

    Args:
        key: 16-byte session key.
        mac_bytes: 6-byte BLE MAC address.
        sequence: 24-bit packet sequence counter.
        dest_id: Destination mesh address.
        opcode: Telink command code.
        params: Command parameters.
        vendor_id: 2-byte vendor identifier (default: TELINK_VENDOR_ID).

    Returns:
        20-byte encrypted command packet.
    """
    seq_bytes = sequence.to_bytes(3, "little")
    nonce = build_nonce(mac_bytes, sequence)

    payload = encode_command_payload(dest_id, opcode, params, vendor_id=vendor_id)

    # CBC-MAC on plaintext, then CTR encrypt
    checksum = make_checksum(key, nonce, payload)
    encrypted = crypt_payload(key, nonce, payload)

    return seq_bytes + checksum[:CHECKSUM_SIZE] + encrypted


# --- Command packet decoding ---


def decode_command_packet(
    key: bytes,
    mac_bytes: bytes,
    data: bytes,
) -> CommandPacket:
    """Decrypt and parse a 20-byte command packet.

    Args:
        key: 16-byte session key.
        mac_bytes: 6-byte BLE MAC address.
        data: 20-byte encrypted command packet.

    Returns:
        Parsed command fields.

    Raises:
        MalformedPacketError: If packet size is wrong.
        AuthenticationError: If MAC verification fails.
    """
    if len(data) != COMMAND_PACKET_SIZE:
        msg = f"Command packet must be {COMMAND_PACKET_SIZE} bytes, got {len(data)}"
        raise MalformedPacketError(msg)

    seq_bytes = data[:SEQUENCE_SIZE]
    sequence = int.from_bytes(seq_bytes, "little")
    expected_mac = data[SEQUENCE_SIZE : SEQUENCE_SIZE + CHECKSUM_SIZE]
    encrypted = data[SEQUENCE_SIZE + CHECKSUM_SIZE :]

    nonce = build_nonce(mac_bytes, sequence)
    payload = crypt_payload(key, nonce, encrypted)
    verify_checksum(key, nonce, payload, expected_mac)

    # Parse decrypted payload
    dest_id = struct.unpack("<H", payload[:2])[0]
    opcode = payload[2]
    vendor_id = payload[3:5]
    raw_params = payload[5:]
    params = raw_params.rstrip(b"\x00")

    return CommandPacket(
        sequence=sequence,
        dest_id=dest_id,
        opcode=opcode,
        vendor_id=vendor_id,
        params=params,
    )


# --- Notification nonce and decryption ---


_NOTIFICATION_HEADER_SIZE = 5  # First 5 bytes of notification used in nonce
_NOTIFICATION_CHECKSUM_OFFSET = 5  # Checksum at bytes [5:7]
_NOTIFICATION_PAYLOAD_OFFSET = 7  # Encrypted payload starts at byte 7
_NOTIFICATION_MIN_SIZE = _NOTIFICATION_PAYLOAD_OFFSET + 1


def build_notification_nonce(mac_bytes: bytes, raw_header: bytes) -> bytes:
    """Build the 8-byte nonce for notification decryption.

    Notification nonce format (per python-awox-mesh-light reference):
    ``[rev_mac[0:3]][packet[0:5]]``

    This differs from the command nonce which uses 4 MAC bytes + 0x01 + seq.
    The notification nonce uses 3 reversed MAC bytes followed by the first
    5 bytes of the raw notification packet.

    Notification wire format (20 bytes):
    ``[3B counter][2B header_extra][2B checksum][13B encrypted payload]``

    The first 5 bytes (counter + header_extra) form part of the nonce.
    Bytes [3:5] contain additional unencrypted data (e.g. source mesh ID).
    The 2-byte checksum is at bytes [5:7], NOT at [3:5].

    SECURITY NOTE — nonce predictability:
    The header bytes (counter + header_extra) are unencrypted and
    observable by any BLE listener. This is acceptable: AES-CCM
    requires nonce uniqueness, not secrecy. The 3-byte counter provides
    uniqueness within a session; session key changes on reconnect.

    Args:
        mac_bytes: 6-byte BLE MAC address in standard order.
        raw_header: First 5 bytes of the raw notification packet.

    Returns:
        8-byte nonce for notification decryption.
    """
    if len(mac_bytes) != MAC_BYTES_SIZE:
        msg = f"MAC must be {MAC_BYTES_SIZE} bytes, got {len(mac_bytes)}"
        raise ProtocolError(msg)
    if len(raw_header) < _NOTIFICATION_HEADER_SIZE:
        msg = (
            f"raw_header must be at least {_NOTIFICATION_HEADER_SIZE} bytes, got {len(raw_header)}"
        )
        raise ProtocolError(msg)

    rev_mac = bytes(reversed(mac_bytes))
    return rev_mac[:3] + raw_header[:_NOTIFICATION_HEADER_SIZE]


def decrypt_notification(
    key: bytes,
    mac_bytes: bytes,
    data: bytes,
) -> bytes:
    """Decrypt a notification from characteristic 1911.

    Returns the full 20-byte packet with the payload portion decrypted
    in place (matching python-awox-mesh-light ``decrypt_packet``).
    The status offsets from const.py apply to this returned buffer.

    Wire format: ``[5B header][2B checksum][13B encrypted payload]``
    Returned:    ``[5B header][2B checksum][13B decrypted payload]``

    Uses the notification-specific nonce: ``[rev_mac[0:3]][packet[0:5]]``

    Args:
        key: 16-byte session key.
        mac_bytes: 6-byte BLE MAC address.
        data: Raw 20-byte notification from characteristic 1911.

    Returns:
        20-byte packet with payload decrypted in place.

    Raises:
        MalformedPacketError: If notification is too short.
        AuthenticationError: If MAC verification fails.
    """
    if len(data) < _NOTIFICATION_MIN_SIZE:
        msg = f"Notification too short: {len(data)} < {_NOTIFICATION_MIN_SIZE}"
        raise MalformedPacketError(msg)

    nonce_header = data[:_NOTIFICATION_HEADER_SIZE]
    expected_mac = data[
        _NOTIFICATION_CHECKSUM_OFFSET : _NOTIFICATION_CHECKSUM_OFFSET + CHECKSUM_SIZE
    ]
    encrypted = data[_NOTIFICATION_PAYLOAD_OFFSET:]

    nonce = build_notification_nonce(mac_bytes, nonce_header)
    payload = crypt_payload(key, nonce, encrypted)
    verify_checksum(key, nonce, payload, expected_mac)

    return data[:_NOTIFICATION_PAYLOAD_OFFSET] + payload


# --- Status parsing ---


def decode_status(data: bytes) -> StatusResponse:
    """Parse a decrypted status notification.

    Applies the STATUS_OFFSET_* constants from const.py to extract
    light state fields from the full notification buffer.

    Args:
        data: Full notification data with payload decrypted in place.
              Must be at least 19 bytes (up to blue at offset 18).

    Returns:
        Parsed status fields.

    Raises:
        MalformedPacketError: If data is too short.
    """
    if len(data) < _STATUS_MIN_SIZE:
        msg = f"Status data too short: {len(data)} < {_STATUS_MIN_SIZE}"
        raise MalformedPacketError(msg)

    return StatusResponse(
        mesh_id=data[STATUS_OFFSET_MESH_ID],
        mode=data[STATUS_OFFSET_MODE],
        white_brightness=data[STATUS_OFFSET_WHITE_BRIGHTNESS],
        white_temp=data[STATUS_OFFSET_WHITE_TEMP],
        color_brightness=data[STATUS_OFFSET_COLOR_BRIGHTNESS],
        red=data[STATUS_OFFSET_RED],
        green=data[STATUS_OFFSET_GREEN],
        blue=data[STATUS_OFFSET_BLUE],
    )


# --- Pair response parsing ---


def parse_pair_response(data: bytes) -> PairResponse:
    """Parse pair response from characteristic 1914.

    Response opcodes:
    - 0x0D + 8B device_random = pairing success
    - 0x0E = authentication failure
    - 0x07 = credential set acknowledged

    Args:
        data: Raw bytes read from pairing characteristic.

    Returns:
        Parsed pair response.

    Raises:
        MalformedPacketError: If response is malformed or unknown.
    """
    if len(data) < _PAIR_RESPONSE_MIN_SIZE:
        msg = "Pair response empty"
        raise MalformedPacketError(msg)

    opcode = data[0]

    if opcode == PAIR_OPCODE_SUCCESS:
        if len(data) < _PAIR_SUCCESS_SIZE:
            msg = f"Pair success response must be >= {_PAIR_SUCCESS_SIZE} bytes, got {len(data)}"
            raise MalformedPacketError(msg)
        return PairResponse(opcode=opcode, device_random=data[1:9])

    if opcode == PAIR_OPCODE_FAILURE:
        return PairResponse(opcode=opcode, device_random=b"")

    if opcode == PAIR_OPCODE_SET_OK:
        return PairResponse(opcode=opcode, device_random=b"")

    msg = f"Unknown pair opcode: 0x{opcode:02X}"
    raise MalformedPacketError(msg)


# --- Tuya DP (Data Point) encoding/decoding ---


def encode_dp_value(dp_id: int, value: bool | int | str | bytes) -> bytes:
    """Encode a single Tuya data point as TLV bytes.

    TLV format: [dp_id 1B][dp_type 1B][length 2B BE][value NB]

    Type is auto-detected from the Python type:
    - bool → DP_TYPE_BOOLEAN (1 byte)
    - int → DP_TYPE_VALUE (4 bytes, big-endian)
    - str → DP_TYPE_STRING (N bytes, UTF-8)
    - bytes → DP_TYPE_RAW (N bytes)

    Args:
        dp_id: Data point ID (1..255).
        value: Data point value.

    Returns:
        TLV-encoded bytes.

    Raises:
        ProtocolError: If dp_id is out of range or value type unsupported.
    """
    if not 1 <= dp_id <= 0xFF:
        msg = f"dp_id must be 1..255, got {dp_id}"
        raise ProtocolError(msg)

    if isinstance(value, bool):
        dp_type = DP_TYPE_BOOLEAN
        encoded = b"\x01" if value else b"\x00"
    elif isinstance(value, int):
        dp_type = DP_TYPE_VALUE
        if not -(2**31) <= value <= 2**31 - 1:
            msg = f"int value out of 32-bit range: {value}"
            raise ProtocolError(msg)
        encoded = struct.pack(">i", value)
    elif isinstance(value, str):
        dp_type = DP_TYPE_STRING
        encoded = value.encode("utf-8")
    elif isinstance(value, bytes):
        dp_type = DP_TYPE_RAW
        encoded = value
    else:
        msg = f"Unsupported DP value type: {type(value).__name__}"
        raise ProtocolError(msg)

    header = struct.pack(">BBH", dp_id, dp_type, len(encoded))
    return header + encoded


def decode_dp_value(data: bytes) -> tuple[int, int, bool | int | str | bytes]:
    """Decode a single Tuya data point TLV.

    Args:
        data: TLV bytes starting at the dp_id byte.

    Returns:
        Tuple of (dp_id, dp_type, value).

    Raises:
        MalformedPacketError: If data is too short or truncated.
    """
    if len(data) < _DP_HEADER_SIZE:
        msg = f"DP TLV too short: {len(data)} < {_DP_HEADER_SIZE}"
        raise MalformedPacketError(msg)

    dp_id, dp_type, length = struct.unpack(">BBH", data[:_DP_HEADER_SIZE])

    if len(data) < _DP_HEADER_SIZE + length:
        msg = f"DP TLV truncated: need {_DP_HEADER_SIZE + length} bytes, got {len(data)}"
        raise MalformedPacketError(msg)

    raw = data[_DP_HEADER_SIZE : _DP_HEADER_SIZE + length]

    if dp_type == DP_TYPE_BOOLEAN:
        if length != 1:
            msg = f"Boolean DP must be 1 byte, got {length}"
            raise MalformedPacketError(msg)
        return dp_id, dp_type, raw[0] != 0

    if dp_type == DP_TYPE_VALUE:
        if length != 4:
            msg = f"Value DP must be 4 bytes, got {length}"
            raise MalformedPacketError(msg)
        (val,) = struct.unpack(">i", raw)
        return dp_id, dp_type, val

    if dp_type == DP_TYPE_STRING:
        return dp_id, dp_type, raw.decode("utf-8", errors="replace")

    if dp_type == DP_TYPE_ENUM:
        if length != 1:
            msg = f"Enum DP must be 1 byte, got {length}"
            raise MalformedPacketError(msg)
        return dp_id, dp_type, raw[0]

    # RAW or unknown type — return as bytes
    return dp_id, dp_type, raw


def encode_dps_command(dps: dict[int, bool | int | str | bytes]) -> bytes:
    """Encode multiple data points into concatenated TLV bytes.

    Args:
        dps: Mapping of dp_id → value.

    Returns:
        Concatenated TLV bytes for all data points. Returns empty bytes if dps is empty.
    """
    result = bytearray()
    for dp_id in sorted(dps):
        result.extend(encode_dp_value(dp_id, dps[dp_id]))
    return bytes(result)


def decode_dps_response(data: bytes) -> dict[int, bool | int | str | bytes]:
    """Decode concatenated Tuya DP TLVs into a dict.

    Args:
        data: Concatenated TLV bytes.

    Returns:
        Mapping of dp_id → decoded value.

    Raises:
        MalformedPacketError: If any TLV is malformed.
    """
    result: dict[int, bool | int | str | bytes] = {}
    offset = 0

    while offset < len(data):
        remaining = data[offset:]
        if len(remaining) < _DP_HEADER_SIZE:
            msg = f"Trailing bytes in DP response at offset {offset}"
            raise MalformedPacketError(msg)

        _, _, length = struct.unpack(">BBH", remaining[:_DP_HEADER_SIZE])
        tlv_size = _DP_HEADER_SIZE + length

        if len(remaining) < tlv_size:
            msg = f"DP TLV truncated at offset {offset}"
            raise MalformedPacketError(msg)

        dp_id, _, value = decode_dp_value(remaining[:tlv_size])
        result[dp_id] = value
        offset += tlv_size

    return result


# --- Compact DP encoding (Telink BLE Mesh 0xD2 format) ---

# Compact TLV header: [dp_id 1B][dp_type 1B][length 1B]
# NOTE: Standard Tuya uses 2-byte BE length; compact uses 1-byte length.
_COMPACT_DP_HEADER_SIZE = 3


def encode_compact_dp(dp_id: int, dp_type: int, value: int) -> bytes:
    """Encode a single data point in Telink compact DP format.

    Compact DP format (used with opcode 0xD2): ``[dp_id 1B][dp_type 1B][dp_len 1B][value NB]``

    This differs from standard Tuya DP TLV which uses 2-byte BE length.
    Confirmed from HCI snoop capture of Malmbergs BLE app (2026-03-03).

    Args:
        dp_id: Data point ID (1..255).
        dp_type: DP type code (e.g. DP_TYPE_VALUE=0x02).
        value: Integer value to encode (big-endian, variable length based on dp_type).

    Returns:
        Compact DP TLV bytes.

    Raises:
        ProtocolError: If dp_id or dp_type is out of range, or value
            cannot be encoded for the given dp_type.
    """
    if not 1 <= dp_id <= 0xFF:
        msg = f"dp_id must be 1..255, got {dp_id}"
        raise ProtocolError(msg)
    if not 0 <= dp_type <= 0xFF:
        msg = f"dp_type must be 0..255, got {dp_type}"
        raise ProtocolError(msg)

    if dp_type == DP_TYPE_BOOLEAN:
        encoded = bytes([1 if value else 0])
    elif dp_type == DP_TYPE_VALUE:
        if not 0 <= value <= 0xFFFFFFFF:
            msg = f"Value must be 0..0xFFFFFFFF for dp_type VALUE, got {value}"
            raise ProtocolError(msg)
        encoded = struct.pack(">I", value)
    else:
        msg = f"Unsupported compact dp_type: 0x{dp_type:02X}"
        raise ProtocolError(msg)

    return struct.pack("BBB", dp_id, dp_type, len(encoded)) + encoded
