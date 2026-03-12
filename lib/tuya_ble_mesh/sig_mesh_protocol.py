"""SIG Mesh protocol encoder/decoder.

Implements the Bluetooth Mesh network, transport, and access layers:

- Network PDU encryption/decryption with privacy obfuscation
- Lower transport: unsegmented and segmented access messages
- Upper transport: AES-CCM with application/device nonces
- Proxy PDU wrapping (GATT Proxy Service)
- Config model messages (Composition Get, AppKey Add, Model App Bind)
- Generic OnOff model messages
- Access layer opcode parsing and status formatting

This module complements ``protocol.py`` (Telink proprietary) with standard
SIG Mesh protocol. Rule S3: raw BLE bytes parsed only in protocol modules.

SECURITY: Key material is NEVER logged, printed, or included in
exception messages. Only lengths and opcodes are safe to log.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

from tuya_ble_mesh.exceptions import CryptoError, MalformedPacketError, ProtocolError
from tuya_ble_mesh.sig_mesh_crypto import (
    aes_ecb,
    k2,
    k3,
    k4,
    mesh_aes_ccm_decrypt,
    mesh_aes_ccm_encrypt,
)

_LOGGER = logging.getLogger(__name__)

# --- Proxy PDU constants ---
PROXY_SAR_COMPLETE = 0x00
PROXY_TYPE_NETWORK = 0x00

# --- Transport constants ---
MAX_UNSEG_ACCESS_PAYLOAD = 11  # 15 byte upper transport - 4 byte TransMIC
SEG_DATA_SIZE = 12  # max bytes per segment chunk


# ============================================================
# Mesh Keys
# ============================================================


@dataclass
class MeshKeys:
    """Derived mesh cryptographic key set.

    Holds all keys needed for SIG Mesh communication:
    network key derivatives (NID, encryption key, privacy key),
    device key, and optional application key with AID.

    SECURITY: Key bytes stored in memory only. Never serialized
    to logs or exception messages.
    """

    net_key: bytes
    dev_key: bytes
    app_key: bytes | None
    iv_index: int
    nid: int
    enc_key: bytes
    priv_key: bytes
    network_id: bytes
    aid: int

    def __init__(
        self,
        net_key_hex: str,
        dev_key_hex: str,
        app_key_hex: str | None = None,
        iv_index: int = 0,
    ) -> None:
        self.net_key = bytes.fromhex(net_key_hex)
        self.dev_key = bytes.fromhex(dev_key_hex)
        self.app_key = bytes.fromhex(app_key_hex) if app_key_hex else None
        self.iv_index = iv_index

        # Derive network-layer keys
        self.nid, self.enc_key, self.priv_key = k2(self.net_key, b"\x00")
        self.network_id = k3(self.net_key)

        # Derive app key AID
        self.aid = k4(self.app_key) if self.app_key else 0

        _LOGGER.debug(
            "Keys derived: NID=0x%02X AID=0x%02X ivIdx=%d",
            self.nid,
            self.aid,
            self.iv_index,
        )


# ============================================================
# Network Layer (Mesh Profile 3.4.4)
# ============================================================


def _make_network_nonce(
    ctl_ttl: int,
    seq: int,
    src: int,
    iv_index: int,
) -> bytes:
    """Build 13-byte network nonce (Mesh Profile 3.8.5.1).

    Format: [0x00][CTL|TTL 1B][SEQ 3B BE][SRC 2B BE][0x0000][IVindex 4B BE]
    """
    return (
        bytes([0x00, ctl_ttl])
        + struct.pack(">I", seq)[1:]
        + struct.pack(">H", src)
        + b"\x00\x00"
        + struct.pack(">I", iv_index)
    )


def encrypt_network_pdu(
    enc_key: bytes,
    priv_key: bytes,
    nid: int,
    *,
    ctl: int,
    ttl: int,
    seq: int,
    src: int,
    dst: int,
    transport_pdu: bytes,
    iv_index: int = 0,
) -> bytes:
    """Encrypt and obfuscate a mesh network PDU (Mesh Profile 3.4.4).

    Args:
        enc_key: 16-byte network encryption key (from k2).
        priv_key: 16-byte network privacy key (from k2).
        nid: 7-bit Network ID (from k2).
        ctl: Control flag (1 for control messages, 0 for access).
        ttl: Time-to-live (0-127).
        seq: 24-bit sequence number.
        src: 16-bit source unicast address.
        dst: 16-bit destination address.
        transport_pdu: Lower transport PDU bytes.
        iv_index: IV Index (default 0).

    Returns:
        Complete network PDU with IVI/NID header, obfuscated fields,
        and encrypted payload.
    """
    ctl_ttl = ((ctl & 1) << 7) | (ttl & 0x7F)
    nonce = _make_network_nonce(ctl_ttl, seq, src, iv_index)
    plaintext = struct.pack(">H", dst) + transport_pdu
    mic_len = 8 if ctl else 4
    encrypted = mesh_aes_ccm_encrypt(enc_key, nonce, plaintext, mic_len)

    ivi_nid = ((iv_index & 1) << 7) | (nid & 0x7F)
    header = bytes([ivi_nid, ctl_ttl]) + struct.pack(">I", seq)[1:] + struct.pack(">H", src)

    # Privacy obfuscation (Mesh Profile 3.8.7.3)
    privacy_random = encrypted[:7]
    pecb_input = b"\x00\x00\x00\x00\x00" + struct.pack(">I", iv_index) + privacy_random
    pecb = aes_ecb(priv_key, pecb_input)
    obfuscated = bytes(a ^ b for a, b in zip(header[1:7], pecb[:6], strict=True))

    return bytes([ivi_nid]) + obfuscated + encrypted


@dataclass(frozen=True)
class NetworkPDU:
    """Decoded network PDU fields."""

    ctl: int
    ttl: int
    seq: int
    src: int
    dst: int
    transport_pdu: bytes


def decrypt_network_pdu(
    enc_key: bytes,
    priv_key: bytes,
    nid: int,
    pdu: bytes,
    iv_index: int = 0,
) -> NetworkPDU | None:
    """Decrypt a mesh network PDU (Mesh Profile 3.4.4).

    Args:
        enc_key: 16-byte network encryption key.
        priv_key: 16-byte network privacy key.
        nid: Expected 7-bit NID.
        pdu: Raw network PDU bytes.
        iv_index: IV Index.

    Returns:
        Decoded NetworkPDU or None if NID mismatch or decryption fails.
    """
    if len(pdu) < 10:
        return None

    if pdu[0] & 0x7F != nid:
        return None

    encrypted_data = pdu[7:]

    # De-obfuscate (Mesh Profile 3.8.7.3)
    privacy_random = encrypted_data[:7]
    pecb_input = b"\x00\x00\x00\x00\x00" + struct.pack(">I", iv_index) + privacy_random
    pecb = aes_ecb(priv_key, pecb_input)
    deobfuscated = bytes(a ^ b for a, b in zip(pdu[1:7], pecb[:6], strict=True))

    ctl_ttl = deobfuscated[0]
    ctl = (ctl_ttl >> 7) & 1
    ttl = ctl_ttl & 0x7F
    seq = (deobfuscated[1] << 16) | (deobfuscated[2] << 8) | deobfuscated[3]
    src = (deobfuscated[4] << 8) | deobfuscated[5]

    nonce = _make_network_nonce(ctl_ttl, seq, src, iv_index)
    mic_len = 8 if ctl else 4

    try:
        plaintext = mesh_aes_ccm_decrypt(enc_key, nonce, encrypted_data, mic_len)
    except CryptoError:
        _LOGGER.debug("Network decryption failed")
        return None

    dst = (plaintext[0] << 8) | plaintext[1]
    return NetworkPDU(
        ctl=ctl,
        ttl=ttl,
        seq=seq,
        src=src,
        dst=dst,
        transport_pdu=plaintext[2:],
    )


# ============================================================
# Transport Layer (Mesh Profile 3.5)
# ============================================================


def _make_app_nonce(
    akf: int,
    szmic: int,
    seq: int,
    src: int,
    dst: int,
    iv_index: int,
) -> bytes:
    """Build 13-byte application/device nonce (Mesh Profile 3.8.5.2/3.8.5.3).

    Nonce type 0x01 for application key, 0x02 for device key.
    """
    nonce_type = 0x01 if akf else 0x02
    return (
        bytes([nonce_type, szmic << 7])
        + struct.pack(">I", seq)[1:]
        + struct.pack(">H", src)
        + struct.pack(">H", dst)
        + struct.pack(">I", iv_index)
    )


def make_access_unsegmented(
    key: bytes,
    src: int,
    dst: int,
    seq: int,
    iv_index: int,
    access_payload: bytes,
    *,
    akf: int = 0,
    aid: int = 0,
) -> bytes:
    """Create unsegmented access message lower transport PDU.

    Max access payload: 11 bytes (15 upper transport - 4 byte TransMIC).

    Args:
        key: 16-byte encryption key (device key or app key).
        src: Source unicast address.
        dst: Destination address.
        seq: Sequence number.
        iv_index: IV Index.
        access_payload: Access layer payload (max 11 bytes).
        akf: Application Key Flag (0=device key, 1=app key).
        aid: Application key ID (from k4, 0 for device key).

    Returns:
        Lower transport PDU bytes.

    Raises:
        ProtocolError: If access payload exceeds 11 bytes.
    """
    if len(access_payload) > MAX_UNSEG_ACCESS_PAYLOAD:
        msg = (
            f"Unsegmented access payload max {MAX_UNSEG_ACCESS_PAYLOAD} bytes, "
            f"got {len(access_payload)}"
        )
        raise ProtocolError(msg)

    nonce = _make_app_nonce(akf, 0, seq, src, dst, iv_index)
    encrypted = mesh_aes_ccm_encrypt(key, nonce, access_payload, 4)
    hdr = (akf << 6) | (aid & 0x3F)  # SEG=0
    return bytes([hdr]) + encrypted


def make_access_segmented(
    key: bytes,
    src: int,
    dst: int,
    seq_start: int,
    iv_index: int,
    access_payload: bytes,
    *,
    akf: int = 0,
    aid: int = 0,
    szmic: int = 0,
) -> list[tuple[int, bytes]]:
    """Create segmented access message lower transport PDUs.

    Splits the encrypted upper transport into segments of up to 12 bytes.

    Args:
        key: 16-byte encryption key.
        src: Source unicast address.
        dst: Destination address.
        seq_start: Starting sequence number (increments per segment).
        iv_index: IV Index.
        access_payload: Access layer payload (any length).
        akf: Application Key Flag.
        aid: Application key ID.
        szmic: Size of MIC flag (0=32-bit, 1=64-bit).

    Returns:
        List of (sequence_number, transport_pdu) tuples, one per segment.
    """
    nonce = _make_app_nonce(akf, szmic, seq_start, src, dst, iv_index)
    mic_len = 8 if szmic else 4
    upper_transport = mesh_aes_ccm_encrypt(key, nonce, access_payload, mic_len)

    n_segs = (len(upper_transport) + SEG_DATA_SIZE - 1) // SEG_DATA_SIZE
    seg_n = n_segs - 1
    seq_zero = seq_start & 0x1FFF

    segments: list[tuple[int, bytes]] = []
    for seg_o in range(n_segs):
        chunk = upper_transport[seg_o * SEG_DATA_SIZE : (seg_o + 1) * SEG_DATA_SIZE]
        hdr = 0x80 | (akf << 6) | (aid & 0x3F)  # SEG=1
        # SZMIC(1) | SeqZero(13) | SegO(5) | SegN(5) = 24 bits
        info = (szmic << 23) | (seq_zero << 10) | (seg_o << 5) | seg_n
        transport_pdu = bytes([hdr]) + struct.pack(">I", info)[1:] + chunk
        segments.append((seq_start + seg_o, transport_pdu))

    return segments


@dataclass(frozen=True)
class SegmentHeader:
    """Parsed segmented access message header fields."""

    akf: int
    aid: int
    szmic: int
    seq_zero: int
    seg_o: int
    seg_n: int
    segment_data: bytes


def parse_segment_header(transport_pdu: bytes) -> SegmentHeader:
    """Parse a segmented access message lower transport PDU header.

    Extracts AKF, AID, SZMIC, SeqZero, SegO, SegN and segment data
    from a segmented transport PDU (Mesh Profile 3.5.2.2).

    Args:
        transport_pdu: Lower transport PDU bytes (at least 4 bytes header).

    Returns:
        Parsed SegmentHeader.

    Raises:
        MalformedPacketError: If PDU is too short.
    """
    if len(transport_pdu) < 4:
        msg = f"Segmented transport PDU too short: {len(transport_pdu)} bytes"
        raise MalformedPacketError(msg)

    hdr = transport_pdu[0]
    if not (hdr & 0x80):
        msg = "Not a segmented PDU (SEG bit not set)"
        raise MalformedPacketError(msg)

    akf = (hdr >> 6) & 1
    aid = hdr & 0x3F

    # 3 bytes: SZMIC(1) | SeqZero(13) | SegO(5) | SegN(5) = 24 bits
    info = (transport_pdu[1] << 16) | (transport_pdu[2] << 8) | transport_pdu[3]
    szmic = (info >> 23) & 1
    seq_zero = (info >> 10) & 0x1FFF
    seg_o = (info >> 5) & 0x1F
    seg_n = info & 0x1F

    return SegmentHeader(
        akf=akf,
        aid=aid,
        szmic=szmic,
        seq_zero=seq_zero,
        seg_o=seg_o,
        seg_n=seg_n,
        segment_data=transport_pdu[4:],
    )


def reassemble_and_decrypt_segments(
    keys: MeshKeys,
    src: int,
    dst: int,
    segments: dict[int, bytes],
    seg_n: int,
    szmic: int,
    seq_zero: int,
    akf: int,
) -> bytes | None:
    """Reassemble segmented transport PDU chunks and decrypt.

    Concatenates all segment data in order (0..seg_n), then decrypts
    the upper transport PDU using the application or device nonce
    with seq_zero as the sequence number.

    Args:
        keys: Mesh key set.
        src: Source unicast address.
        dst: Destination address.
        segments: Dict mapping seg_o index to segment data bytes.
        seg_n: Last segment index.
        szmic: Size of MIC flag (0=32-bit, 1=64-bit).
        seq_zero: 13-bit sequence number from segment header.
        akf: Application Key Flag (0=device key, 1=app key).

    Returns:
        Decrypted access payload, or None if decryption fails.
    """
    # Concatenate in order
    upper_transport = b""
    for i in range(seg_n + 1):
        if i not in segments:
            return None
        upper_transport += segments[i]

    key = keys.app_key if akf else keys.dev_key
    if key is None:
        _LOGGER.debug("No %s key for segmented decryption", "app" if akf else "dev")
        return None

    mic_len = 8 if szmic else 4
    nonce = _make_app_nonce(akf, szmic, seq_zero, src, dst, keys.iv_index)

    try:
        return mesh_aes_ccm_decrypt(key, nonce, upper_transport, mic_len)
    except CryptoError:
        _LOGGER.debug("Segmented upper transport decryption failed (akf=%d)", akf)
        return None


@dataclass(frozen=True)
class AccessMessage:
    """Decoded access layer message."""

    seg: bool
    akf: int
    aid: int
    access_payload: bytes | None  # None if segmented or decryption failed
    raw: bytes  # Original transport PDU


def decrypt_access_payload(
    keys: MeshKeys,
    src: int,
    dst: int,
    seq: int,
    transport_pdu: bytes,
) -> AccessMessage | None:
    """Decrypt upper transport from a received lower transport PDU.

    Handles unsegmented messages only. Segmented messages are returned
    with ``seg=True`` and ``access_payload=None`` for reassembly by caller.

    Args:
        keys: Mesh key set.
        src: Source address of the message.
        dst: Destination address.
        seq: Network layer sequence number.
        transport_pdu: Lower transport PDU from network layer.

    Returns:
        Decoded AccessMessage, or None if decryption fails.
    """
    if not transport_pdu:
        return None

    hdr = transport_pdu[0]
    seg = bool((hdr >> 7) & 1)
    akf = (hdr >> 6) & 1
    aid = hdr & 0x3F

    if seg:
        return AccessMessage(seg=True, akf=akf, aid=aid, access_payload=None, raw=transport_pdu)

    encrypted_upper = transport_pdu[1:]
    key = keys.app_key if akf else keys.dev_key
    if key is None:
        _LOGGER.debug("No %s key for decryption", "app" if akf else "dev")
        return None

    nonce = _make_app_nonce(akf, 0, seq, src, dst, keys.iv_index)
    try:
        access_payload = mesh_aes_ccm_decrypt(key, nonce, encrypted_upper, 4)
    except CryptoError:
        _LOGGER.debug("Upper transport decryption failed (akf=%d)", akf)
        return None

    return AccessMessage(
        seg=False, akf=akf, aid=aid, access_payload=access_payload, raw=transport_pdu
    )


# ============================================================
# Proxy PDU (Mesh Profile 6.3)
# ============================================================


def make_proxy_pdu(network_pdu: bytes) -> bytes:
    """Wrap a network PDU in a Mesh Proxy PDU (SAR=complete, type=network).

    Args:
        network_pdu: Complete network PDU.

    Returns:
        Proxy PDU with SAR/type header byte prepended.
    """
    return bytes([(PROXY_SAR_COMPLETE << 6) | PROXY_TYPE_NETWORK]) + network_pdu


@dataclass(frozen=True)
class ProxyPDU:
    """Parsed Mesh Proxy PDU."""

    sar: int
    pdu_type: int
    payload: bytes


def parse_proxy_pdu(data: bytes) -> ProxyPDU:
    """Parse a Mesh Proxy PDU.

    Args:
        data: Raw proxy PDU bytes from GATT characteristic.

    Returns:
        Parsed ProxyPDU with SAR, type, and payload.

    Raises:
        MalformedPacketError: If data is empty.
    """
    if not data:
        msg = "Empty proxy PDU"
        raise MalformedPacketError(msg)
    return ProxyPDU(
        sar=(data[0] >> 6) & 0x03,
        pdu_type=data[0] & 0x3F,
        payload=data[1:],
    )


# ============================================================
# Config Model Messages (Mesh Profile 4.3)
# ============================================================


def config_composition_get(page: int = 0) -> bytes:
    """Config Composition Data Get (opcode 0x8008).

    Args:
        page: Composition data page number.

    Returns:
        Access layer payload.
    """
    if not 0 <= page <= 0xFF:
        msg = f"Page must be 0..255, got {page}"
        raise ProtocolError(msg)
    return b"\x80\x08" + bytes([page])


def config_appkey_add(
    net_idx: int,
    app_idx: int,
    app_key: bytes,
) -> bytes:
    """Config AppKey Add (opcode 0x00).

    20-byte payload — requires segmented transport.

    Args:
        net_idx: Network key index (12-bit).
        app_idx: Application key index (12-bit).
        app_key: 16-byte application key.

    Returns:
        Access layer payload.
    """
    if not 0 <= net_idx <= 0xFFF:
        msg = f"net_idx must be 0..0xFFF, got {net_idx}"
        raise ProtocolError(msg)
    if not 0 <= app_idx <= 0xFFF:
        msg = f"app_idx must be 0..0xFFF, got {app_idx}"
        raise ProtocolError(msg)
    if len(app_key) != 16:
        msg = f"app_key must be 16 bytes, got {len(app_key)}"
        raise ProtocolError(msg)
    # NetKeyIndex(12) + AppKeyIndex(12) packed into 3 bytes LE
    idx = (net_idx & 0xFFF) | ((app_idx & 0xFFF) << 12)
    return b"\x00" + struct.pack("<I", idx)[:3] + app_key


def config_model_app_bind(
    element_addr: int,
    app_idx: int,
    model_id: int,
) -> bytes:
    """Config Model App Bind (opcode 0x803D).

    8-byte payload — fits unsegmented transport.

    Note: Only SIG Model IDs (16-bit, 0x0000-0xFFFF) are supported.
    Vendor Model IDs (32-bit, company code + model ID) use a different
    10-byte payload format and are not supported by this function.
    Passing a value > 0xFFFF raises ProtocolError rather than truncating.

    Args:
        element_addr: Element unicast address.
        app_idx: Application key index.
        model_id: SIG Model ID (16-bit, 0x0000-0xFFFF).

    Returns:
        Access layer payload.
    """
    if not 0 <= element_addr <= 0xFFFF:
        msg = f"element_addr must be 0..0xFFFF, got {element_addr}"
        raise ProtocolError(msg)
    if not 0 <= app_idx <= 0xFFF:
        msg = f"app_idx must be 0..0xFFF, got {app_idx}"
        raise ProtocolError(msg)
    if not 0 <= model_id <= 0xFFFF:
        msg = f"model_id must be 0..0xFFFF, got {model_id}"
        raise ProtocolError(msg)
    return b"\x80\x3d" + struct.pack("<HHH", element_addr, app_idx, model_id)


# ============================================================
# Generic OnOff Model Messages (Mesh Model 3.2)
# ============================================================


def generic_onoff_set(on: bool, tid: int = 0) -> bytes:
    """Generic OnOff Set (opcode 0x8202).

    Args:
        on: Target state (True=ON, False=OFF).
        tid: Transaction identifier (0-255, should increment per command).

    Returns:
        Access layer payload.
    """
    return b"\x82\x02" + bytes([0x01 if on else 0x00, tid & 0xFF])


def generic_onoff_get() -> bytes:
    """Generic OnOff Get (opcode 0x8201).

    Returns:
        Access layer payload.
    """
    return b"\x82\x01"


# ============================================================
# Access Layer Opcode Parsing (Mesh Profile 3.7.3)
# ============================================================


def parse_access_opcode(data: bytes) -> tuple[int, bytes]:
    """Parse a SIG Mesh access layer opcode.

    Opcodes are 1, 2, or 3 bytes:
    - 0xxxxxxx: 1-byte SIG opcode
    - 10xxxxxx: 2-byte SIG opcode
    - 11xxxxxx: 3-byte vendor opcode

    Args:
        data: Access layer payload starting with opcode.

    Returns:
        Tuple of (opcode, remaining parameters).

    Raises:
        MalformedPacketError: If data is too short.
    """
    if not data:
        msg = "Empty access payload"
        raise MalformedPacketError(msg)

    if data[0] & 0x80 == 0:
        # 1-byte opcode
        return data[0], data[1:]
    elif data[0] & 0xC0 == 0x80:
        # 2-byte opcode
        if len(data) < 2:
            msg = "2-byte opcode truncated"
            raise MalformedPacketError(msg)
        return (data[0] << 8) | data[1], data[2:]
    else:
        # 3-byte vendor opcode
        if len(data) < 3:
            msg = "3-byte vendor opcode truncated"
            raise MalformedPacketError(msg)
        return (data[0] << 16) | (data[1] << 8) | data[2], data[3:]


# ============================================================
# Tuya Vendor Model (CID 0x07D0)
# ============================================================

# Tuya vendor opcodes (3-byte: opcode_byte + D0 07 for CID 0x07D0)
TUYA_VENDOR_OPCODE = 0xCDD007  # DATA: device → client status/report
TUYA_VENDOR_WRITE_ACK = 0xC9D007  # WRITE: client → device with ACK
TUYA_VENDOR_WRITE_UNACK = 0xCAD007  # WRITE: client → device no ACK

# Tuya vendor frame command types
TUYA_CMD_DP_DATA = 0x01
TUYA_CMD_TIMESTAMP_SYNC = 0x02

# Tuya DP IDs (tentative — log raw values for HW verification)
DP_ID_SWITCH = 1
DP_ID_ENERGY_KWH = 17
DP_ID_POWER_W = 18
DP_ID_CURRENT_MA = 19
DP_ID_VOLTAGE_V = 20


@dataclass(frozen=True)
class TuyaVendorDP:
    """A single Tuya vendor Data Point from a vendor message."""

    dp_id: int
    dp_type: int
    value: bytes


@dataclass(frozen=True)
class TuyaVendorFrame:
    """Parsed Tuya vendor message frame.

    Tuya vendor messages use a frame header: ``[command 1B][data_length 1B][data NB]``
    where command identifies the payload type (DP data, timestamp sync, etc.).

    Attributes:
        command: Frame command byte (0x01=DP data, 0x02=timestamp sync, 0=unknown/raw).
        data: Raw data bytes after the frame header.
        dps: Parsed data points (populated only when command is TUYA_CMD_DP_DATA).
    """

    command: int
    data: bytes
    dps: list[TuyaVendorDP]


def parse_tuya_vendor_frame(params: bytes) -> TuyaVendorFrame:
    """Parse a Tuya vendor message with frame header.

    Tuya vendor format: ``[command 1B][data_length 1B][data NB]``
    Falls back to raw DP parsing when the frame header looks invalid
    (for compatibility with devices that omit the frame header).

    Args:
        params: Raw parameter bytes after the vendor opcode.

    Returns:
        Parsed TuyaVendorFrame with command type and extracted DPs.
    """
    if len(params) < 2:
        _LOGGER.debug("Vendor frame too short (%d bytes)", len(params))
        return TuyaVendorFrame(command=0, data=params, dps=[])

    command = params[0]
    data = params[2:]

    if command == TUYA_CMD_TIMESTAMP_SYNC:
        _LOGGER.debug(
            "Tuya timestamp sync request (%d data bytes): %s",
            len(data),
            data.hex(),
        )
        return TuyaVendorFrame(command=command, data=data, dps=[])

    if command == TUYA_CMD_DP_DATA:
        dps = _parse_dp_bytes(data)
        return TuyaVendorFrame(command=command, data=data, dps=dps)

    # Unknown command or no frame header — try raw DP parse on full params
    _LOGGER.debug(
        "Unknown vendor command 0x%02X, trying raw DP parse on full params",
        command,
    )
    dps = _parse_dp_bytes(params)
    return TuyaVendorFrame(command=command, data=params, dps=dps)


def tuya_vendor_timestamp_response() -> bytes:
    """Build a Tuya vendor WRITE_UNACK payload with current UTC timestamp.

    Format: 3-byte opcode (0xCA D0 07) + frame header + timestamp data.
    Frame: ``[cmd=0x02][len=0x08][timestamp_utc 4B BE][tz_offset 1B signed][pad 3B]``

    Returns:
        Complete access layer payload for a timestamp sync response.
    """
    import time

    now = int(time.time())
    opcode_bytes = TUYA_VENDOR_WRITE_UNACK.to_bytes(3, "big")
    ts_bytes = now.to_bytes(4, "big")

    # Signed timezone offset in hours from UTC
    tz_offset = time.timezone // -3600 if not time.daylight else time.altzone // -3600
    tz_byte = (
        tz_offset.to_bytes(1, "big", signed=True) if -12 <= tz_offset <= 14 else b"\x00"
    )
    # Pad to 8 data bytes (observed in Tuya app traces)
    data = ts_bytes + tz_byte + b"\x00\x00\x00"
    frame = bytes([TUYA_CMD_TIMESTAMP_SYNC, len(data)]) + data
    return opcode_bytes + frame


def parse_tuya_vendor_dps(params: bytes) -> list[TuyaVendorDP]:
    """Parse Tuya vendor DP values from access layer parameters.

    This is the **raw DP parser** — it expects bare DP TLV bytes without
    a frame header.  For framed messages (with command+length prefix),
    use :func:`parse_tuya_vendor_frame` instead.

    Format: ``[dp_id 1B][dp_type 1B][dp_len 1B][value NB]...``

    Args:
        params: Raw parameter bytes after the vendor opcode.

    Returns:
        List of parsed TuyaVendorDP.
    """
    return _parse_dp_bytes(params)


def _parse_dp_bytes(data: bytes) -> list[TuyaVendorDP]:
    """Parse raw DP bytes: ``[dp_id 1B][dp_type 1B][dp_len 1B][value NB]...``

    Args:
        data: Raw bytes containing concatenated DP TLV entries.

    Returns:
        List of parsed TuyaVendorDP. Truncated entries are silently skipped.
    """
    dps: list[TuyaVendorDP] = []
    offset = 0
    while offset < len(data):
        if offset + 3 > len(data):
            _LOGGER.debug("Truncated DP header at offset %d", offset)
            break
        dp_id = data[offset]
        dp_type = data[offset + 1]
        dp_len = data[offset + 2]
        offset += 3
        if offset + dp_len > len(data):
            _LOGGER.debug(
                "Truncated DP value: dp_id=%d, need %d bytes, have %d",
                dp_id,
                dp_len,
                len(data) - offset,
            )
            break
        value = data[offset : offset + dp_len]
        offset += dp_len
        dps.append(TuyaVendorDP(dp_id=dp_id, dp_type=dp_type, value=value))
    return dps


# ============================================================
# Composition Data (Mesh Profile 4.2.1)
# ============================================================

_OPCODE_COMPOSITION_STATUS = 0x02


@dataclass(frozen=True)
class CompositionData:
    """Parsed Composition Data Page 0 header."""

    cid: int  # Company ID
    pid: int  # Product ID
    vid: int  # Version ID
    crpl: int  # Replay protection list size
    features: int  # Features bitmask
    raw_elements: bytes  # Unparsed element data


def parse_composition_data(params: bytes) -> CompositionData:
    """Parse Composition Data Status page 0 parameters.

    Args:
        params: Parameters after opcode 0x02 (page byte + data).

    Returns:
        Parsed CompositionData.

    Raises:
        MalformedPacketError: If data is too short.
    """
    # params[0] = page, params[1:] = composition data
    if len(params) < 11:
        msg = f"Composition Data too short: {len(params)} bytes (need >= 11)"
        raise MalformedPacketError(msg)

    data = params[1:]  # Skip page byte
    cid = struct.unpack_from("<H", data, 0)[0]
    pid = struct.unpack_from("<H", data, 2)[0]
    vid = struct.unpack_from("<H", data, 4)[0]
    crpl = struct.unpack_from("<H", data, 6)[0]
    features = struct.unpack_from("<H", data, 8)[0]

    return CompositionData(
        cid=cid,
        pid=pid,
        vid=vid,
        crpl=crpl,
        features=features,
        raw_elements=data[10:],
    )


# ============================================================
# Status Response Formatting
# ============================================================

# Config status code names
_CONFIG_STATUS_NAMES: dict[int, str] = {
    0x00: "Success",
    0x01: "InvalidAddress",
    0x02: "InvalidModel",
    0x03: "InvalidAppKeyIndex",
    0x04: "InvalidNetKeyIndex",
    0x05: "InsufficientResources",
    0x06: "KeyIndexAlreadyStored",
}


def format_status_response(opcode: int, params: bytes) -> str:
    """Format a mesh status response for display.

    Args:
        opcode: Access layer opcode.
        params: Opcode parameters.

    Returns:
        Human-readable status string.
    """
    if opcode == 0x8003:  # Config AppKey Status
        status = params[0] if params else 0xFF
        name = _CONFIG_STATUS_NAMES.get(status, f"Unknown(0x{status:02X})")
        return f"AppKey Status: {name}"

    if opcode == 0x803E:  # Config Model App Status
        status = params[0] if params else 0xFF
        bind_status: dict[int, str] = {
            0x00: "Success",
            0x02: "InvalidModel",
            0x03: "InvalidAppKeyIndex",
            0x04: "InvalidNetKeyIndex",
            0x06: "ModelAppAlreadyBound",
        }
        name = bind_status.get(status, f"Unknown(0x{status:02X})")
        return f"Model App Status: {name}"

    if opcode == 0x02:  # Config Composition Data Status
        page = params[0] if params else 0xFF
        return f"Composition Data: page={page} ({len(params) - 1} bytes)"

    if opcode == 0x8204:  # Generic OnOff Status
        state = params[0] if params else 0xFF
        msg = f"OnOff Status: {'ON' if state else 'OFF'}"
        if len(params) >= 3:
            target = "ON" if params[1] else "OFF"
            msg += f" (target={target}, remaining={params[2]})"
        return msg

    return f"Opcode 0x{opcode:04X}: {len(params)} bytes"
