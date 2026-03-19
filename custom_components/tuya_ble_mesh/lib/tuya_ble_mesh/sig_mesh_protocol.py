"""SIG Mesh protocol — state machine, keys, and crypto operations.

Implements the cryptographic layers of Bluetooth Mesh:

- Network PDU encryption/decryption with privacy obfuscation
- Lower transport: unsegmented and segmented access messages
- Upper transport: AES-CCM with application/device nonces
- Mesh key derivation (MeshKeys)

Packet encoding/decoding lives in ``sig_mesh_protocol_codec.py``.

This module complements ``protocol.py`` (Telink proprietary) with standard
SIG Mesh protocol. Rule S3: raw BLE bytes parsed only in protocol modules.

SECURITY: Key material is NEVER logged, printed, or included in
exception messages. Only lengths and opcodes are safe to log.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

from tuya_ble_mesh.exceptions import CryptoError, ProtocolError
from tuya_ble_mesh.sig_mesh_crypto import (
    aes_ecb,
    k2,
    k3,
    k4,
    mesh_aes_ccm_decrypt,
    mesh_aes_ccm_encrypt,
)
from tuya_ble_mesh.sig_mesh_protocol_codec import (  # noqa: F401  — re-exports
    _OPCODE_COMPOSITION_STATUS,
    DP_ID_CURRENT_MA,
    DP_ID_ENERGY_KWH,
    DP_ID_POWER_W,
    DP_ID_SWITCH,
    DP_ID_VOLTAGE_V,
    MAX_UNSEG_ACCESS_PAYLOAD,
    MESH_AID_MASK,
    MESH_AKF_SHIFT,
    MESH_CTL_SHIFT,
    MESH_IVI_SHIFT,
    MESH_NID_MASK,
    MESH_OPCODE_1BYTE_MASK,
    MESH_OPCODE_2BYTE_MASK,
    MESH_OPCODE_2BYTE_VALUE,
    MESH_SEG_BIT,
    MESH_SEG_MASK,
    MESH_SEG_O_SHIFT,
    MESH_SEQ_ZERO_MASK,
    MESH_SEQ_ZERO_SHIFT,
    MESH_SZMIC_SHIFT,
    MESH_TTL_MASK,
    MIC_LEN_ACCESS,
    MIC_LEN_CONTROL,
    OP_CONFIG_APPKEY_ADD,
    OP_CONFIG_APPKEY_STATUS,
    OP_CONFIG_COMPOSITION_GET,
    OP_CONFIG_COMPOSITION_STATUS,
    OP_CONFIG_MODEL_APP_BIND,
    OP_CONFIG_MODEL_APP_STATUS,
    OP_GENERIC_ONOFF_GET,
    OP_GENERIC_ONOFF_SET,
    OP_GENERIC_ONOFF_STATUS,
    PROXY_SAR_COMPLETE,
    PROXY_SAR_MASK,
    PROXY_TYPE_MASK,
    PROXY_TYPE_NETWORK,
    SEG_DATA_SIZE,
    TUYA_CMD_DP_DATA,
    TUYA_CMD_TIMESTAMP_SYNC,
    TUYA_VENDOR_OPCODE,
    TUYA_VENDOR_WRITE_ACK,
    TUYA_VENDOR_WRITE_UNACK,
    CompositionData,
    ProxyPDU,
    SegmentHeader,
    TuyaVendorDP,
    TuyaVendorFrame,
    config_appkey_add,
    config_composition_get,
    config_model_app_bind,
    format_status_response,
    generic_onoff_get,
    generic_onoff_set,
    make_proxy_pdu,
    parse_access_opcode,
    parse_composition_data,
    parse_proxy_pdu,
    parse_segment_header,
    parse_tuya_vendor_dps,
    parse_tuya_vendor_frame,
    tuya_vendor_timestamp_response,
)

_LOGGER = logging.getLogger(__name__)


# ============================================================
# Mesh Keys
# ============================================================


@dataclass
class MeshKeys:
    """Derived mesh cryptographic key set.

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
        self.nid, self.enc_key, self.priv_key = k2(self.net_key, b"\x00")
        self.network_id = k3(self.net_key)
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


def _make_network_nonce(ctl_ttl: int, seq: int, src: int, iv_index: int) -> bytes:
    """Build 13-byte network nonce (Mesh Profile 3.8.5.1)."""
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
    """Encrypt and obfuscate a mesh network PDU (Mesh Profile 3.4.4)."""
    ctl_ttl = ((ctl & 1) << MESH_CTL_SHIFT) | (ttl & MESH_TTL_MASK)
    nonce = _make_network_nonce(ctl_ttl, seq, src, iv_index)
    plaintext = struct.pack(">H", dst) + transport_pdu
    mic_len = MIC_LEN_CONTROL if ctl else MIC_LEN_ACCESS
    encrypted = mesh_aes_ccm_encrypt(enc_key, nonce, plaintext, mic_len)

    ivi_nid = ((iv_index & 1) << MESH_IVI_SHIFT) | (nid & MESH_NID_MASK)
    header = bytes([ivi_nid, ctl_ttl]) + struct.pack(">I", seq)[1:] + struct.pack(">H", src)

    # Privacy obfuscation (Mesh Profile 3.8.7.3)
    privacy_random = encrypted[:7]
    pecb_input = b"\x00\x00\x00\x00\x00" + struct.pack(">I", iv_index) + privacy_random
    pecb = aes_ecb(priv_key, pecb_input)
    obfuscated = bytes(a ^ b for a, b in zip(header[1:7], pecb[:6], strict=True))

    return bytes(bytes([ivi_nid]) + obfuscated + encrypted)


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
    """Decrypt a mesh network PDU (Mesh Profile 3.4.4)."""
    if len(pdu) < 10:
        return None
    if pdu[0] & MESH_NID_MASK != nid:
        return None

    encrypted_data = pdu[7:]

    # De-obfuscate (Mesh Profile 3.8.7.3)
    privacy_random = encrypted_data[:7]
    pecb_input = b"\x00\x00\x00\x00\x00" + struct.pack(">I", iv_index) + privacy_random
    pecb = aes_ecb(priv_key, pecb_input)
    deobfuscated = bytes(a ^ b for a, b in zip(pdu[1:7], pecb[:6], strict=True))

    ctl_ttl = deobfuscated[0]
    ctl = (ctl_ttl >> MESH_CTL_SHIFT) & 1
    ttl = ctl_ttl & MESH_TTL_MASK
    seq = (deobfuscated[1] << 16) | (deobfuscated[2] << 8) | deobfuscated[3]
    src = (deobfuscated[4] << 8) | deobfuscated[5]

    nonce = _make_network_nonce(ctl_ttl, seq, src, iv_index)
    mic_len = MIC_LEN_CONTROL if ctl else MIC_LEN_ACCESS

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
    """Build 13-byte application/device nonce (Mesh Profile 3.8.5.2/3.8.5.3)."""
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
    """Create unsegmented access message lower transport PDU (max 11 bytes payload)."""
    if len(access_payload) > MAX_UNSEG_ACCESS_PAYLOAD:
        msg = (
            f"Unsegmented access payload max {MAX_UNSEG_ACCESS_PAYLOAD} bytes, "
            f"got {len(access_payload)}"
        )
        raise ProtocolError(msg)

    nonce = _make_app_nonce(akf, 0, seq, src, dst, iv_index)
    encrypted = mesh_aes_ccm_encrypt(key, nonce, access_payload, MIC_LEN_ACCESS)
    hdr = (akf << MESH_AKF_SHIFT) | (aid & MESH_AID_MASK)
    return bytes(bytes([hdr]) + encrypted)


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
    """Create segmented access message lower transport PDUs."""
    nonce = _make_app_nonce(akf, szmic, seq_start, src, dst, iv_index)
    mic_len = MIC_LEN_CONTROL if szmic else MIC_LEN_ACCESS
    upper_transport = mesh_aes_ccm_encrypt(key, nonce, access_payload, mic_len)

    n_segs = (len(upper_transport) + SEG_DATA_SIZE - 1) // SEG_DATA_SIZE
    seg_n = n_segs - 1
    seq_zero = seq_start & MESH_SEQ_ZERO_MASK

    segments: list[tuple[int, bytes]] = []
    for seg_o in range(n_segs):
        chunk = upper_transport[seg_o * SEG_DATA_SIZE : (seg_o + 1) * SEG_DATA_SIZE]
        hdr = MESH_SEG_BIT | (akf << MESH_AKF_SHIFT) | (aid & MESH_AID_MASK)
        info = (
            (szmic << MESH_SZMIC_SHIFT)
            | (seq_zero << MESH_SEQ_ZERO_SHIFT)
            | (seg_o << MESH_SEG_O_SHIFT)
            | seg_n
        )
        transport_pdu = bytes([hdr]) + struct.pack(">I", info)[1:] + chunk
        segments.append((seq_start + seg_o, transport_pdu))

    return segments


@dataclass(frozen=True)
class AccessMessage:
    """Decoded access layer message."""

    seg: bool
    akf: int
    aid: int
    access_payload: bytes | None
    raw: bytes


def decrypt_access_payload(
    keys: MeshKeys,
    src: int,
    dst: int,
    seq: int,
    transport_pdu: bytes,
) -> AccessMessage | None:
    """Decrypt upper transport from a received lower transport PDU."""
    if not transport_pdu:
        return None

    hdr = transport_pdu[0]
    seg = bool(hdr & MESH_SEG_BIT)
    akf = (hdr >> MESH_AKF_SHIFT) & 1
    aid = hdr & MESH_AID_MASK

    if seg:
        return AccessMessage(seg=True, akf=akf, aid=aid, access_payload=None, raw=transport_pdu)

    encrypted_upper = transport_pdu[1:]
    key = keys.app_key if akf else keys.dev_key
    if key is None:
        _LOGGER.debug("No %s key for decryption", "app" if akf else "dev")
        return None

    nonce = _make_app_nonce(akf, 0, seq, src, dst, keys.iv_index)
    try:
        access_payload = mesh_aes_ccm_decrypt(key, nonce, encrypted_upper, MIC_LEN_ACCESS)
    except CryptoError:
        _LOGGER.debug("Upper transport decryption failed (akf=%d)", akf)
        return None

    return AccessMessage(
        seg=False,
        akf=akf,
        aid=aid,
        access_payload=access_payload,
        raw=transport_pdu,
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
    """Reassemble segmented transport PDU chunks and decrypt."""
    segments_snapshot = dict(segments)

    upper_transport = b""
    for i in range(seg_n + 1):
        if i not in segments_snapshot:
            return None
        upper_transport += segments_snapshot[i]

    key = keys.app_key if akf else keys.dev_key
    if key is None:
        _LOGGER.debug("No %s key for segmented decryption", "app" if akf else "dev")
        return None

    mic_len = MIC_LEN_CONTROL if szmic else MIC_LEN_ACCESS
    nonce = _make_app_nonce(akf, szmic, seq_zero, src, dst, keys.iv_index)

    try:
        return bytes(mesh_aes_ccm_decrypt(key, nonce, upper_transport, mic_len))
    except CryptoError:
        _LOGGER.debug("Segmented upper transport decryption failed (akf=%d)", akf)
        return None
