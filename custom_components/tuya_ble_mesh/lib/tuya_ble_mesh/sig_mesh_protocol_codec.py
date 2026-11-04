"""SIG Mesh protocol codec — packet encoding/decoding.

Pure encoding and decoding of SIG Mesh packets, config model messages,
access layer opcodes, Tuya vendor frames, composition data, and proxy PDUs.

No cryptographic operations — those live in ``sig_mesh_protocol.py``.

SECURITY: Key material is NEVER logged, printed, or included in
exception messages. Only lengths and opcodes are safe to log.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass

from tuya_ble_mesh.exceptions import MalformedPacketError, ProtocolError

_LOGGER = logging.getLogger(__name__)

# --- Proxy PDU constants ---
PROXY_SAR_COMPLETE = 0x00
PROXY_TYPE_NETWORK = 0x00

# --- Transport constants ---
MAX_UNSEG_ACCESS_PAYLOAD = 11  # 15 byte upper transport - 4 byte TransMIC
SEG_DATA_SIZE = 12  # max bytes per segment chunk

# --- Network PDU field masks and lengths ---
MESH_NID_MASK = 0x7F
MESH_IVI_SHIFT = 7
MESH_TTL_MASK = 0x7F
MESH_CTL_SHIFT = 7
MIC_LEN_ACCESS = 4
MIC_LEN_CONTROL = 8

# --- Lower transport header masks ---
MESH_SEG_BIT = 0x80
MESH_AKF_SHIFT = 6
MESH_AID_MASK = 0x3F

# --- Segmented transport header bit positions (24-bit info field) ---
MESH_SZMIC_SHIFT = 23
MESH_SEQ_ZERO_SHIFT = 10
MESH_SEQ_ZERO_MASK = 0x1FFF
MESH_SEG_O_SHIFT = 5
MESH_SEG_MASK = 0x1F

# --- Proxy PDU header masks ---
PROXY_SAR_MASK = 0x03
PROXY_TYPE_MASK = 0x3F

# --- Access layer opcode format bits (Mesh Profile 3.7.3) ---
MESH_OPCODE_1BYTE_MASK = 0x80
MESH_OPCODE_2BYTE_MASK = 0xC0
MESH_OPCODE_2BYTE_VALUE = 0x80

# --- Config model opcodes (SIG Mesh Profile 4.3) ---
OP_CONFIG_COMPOSITION_GET = 0x8008
OP_CONFIG_COMPOSITION_STATUS = 0x02
OP_CONFIG_APPKEY_ADD = 0x0000
OP_CONFIG_APPKEY_STATUS = 0x8003
OP_CONFIG_MODEL_APP_BIND = 0x803D
OP_CONFIG_MODEL_APP_STATUS = 0x803E
OP_CONFIG_MODEL_PUB_SET = 0x03
OP_CONFIG_MODEL_PUB_STATUS = 0x8019

# Node feature state Get/Status (Mesh Profile 4.3.4) — read-only probes
OP_CONFIG_BEACON_GET = 0x8009
OP_CONFIG_BEACON_STATUS = 0x800B
OP_CONFIG_DEFAULT_TTL_GET = 0x800C
OP_CONFIG_DEFAULT_TTL_STATUS = 0x800E
OP_CONFIG_FRIEND_GET = 0x800F
OP_CONFIG_FRIEND_STATUS = 0x8011
OP_CONFIG_GATT_PROXY_GET = 0x8012
OP_CONFIG_GATT_PROXY_STATUS = 0x8014
OP_CONFIG_NETWORK_TRANSMIT_GET = 0x8023
OP_CONFIG_NETWORK_TRANSMIT_STATUS = 0x8025
OP_CONFIG_RELAY_GET = 0x8026
OP_CONFIG_RELAY_STATUS = 0x8028
OP_CONFIG_NODE_RESET = 0x8049
OP_CONFIG_NODE_RESET_STATUS = 0x804A

# Feature state values (Mesh Profile 4.2.8 / 4.2.10-4.2.12)
FEATURE_STATE_NAMES: dict[int, str] = {0x00: "disabled", 0x01: "enabled", 0x02: "not_supported"}

# --- Generic OnOff model opcodes (Mesh Model 3.2) ---
OP_GENERIC_ONOFF_GET = 0x8201
OP_GENERIC_ONOFF_SET = 0x8202
OP_GENERIC_ONOFF_SET_UNACK = 0x8203
OP_GENERIC_ONOFF_STATUS = 0x8204

# --- Light Lightness model opcodes (Mesh Model 6.3.1) ---
OP_LIGHT_LIGHTNESS_GET = 0x824B
OP_LIGHT_LIGHTNESS_SET = 0x824C
OP_LIGHT_LIGHTNESS_SET_UNACK = 0x824D
OP_LIGHT_LIGHTNESS_STATUS = 0x824E

# --- Light CTL model opcodes (Mesh Model 6.3.2) ---
OP_LIGHT_CTL_GET = 0x825D
OP_LIGHT_CTL_SET = 0x825E
OP_LIGHT_CTL_SET_UNACK = 0x825F
OP_LIGHT_CTL_STATUS = 0x8260
OP_LIGHT_CTL_TEMP_RANGE_GET = 0x8262
OP_LIGHT_CTL_TEMP_RANGE_STATUS = 0x8263
OP_LIGHT_CTL_TEMP_SET = 0x8264
OP_LIGHT_CTL_TEMP_SET_UNACK = 0x8265
OP_LIGHT_CTL_TEMP_STATUS = 0x8266

# --- Light HSL model opcodes (Mesh Model 6.3.3) ---
OP_LIGHT_HSL_GET = 0x826D
OP_LIGHT_HSL_SET = 0x8276
OP_LIGHT_HSL_SET_UNACK = 0x8277
OP_LIGHT_HSL_STATUS = 0x8278

# --- SIG model IDs (Mesh Model 7.3) ---
MODEL_GENERIC_ONOFF_SERVER = 0x1000
MODEL_GENERIC_LEVEL_SERVER = 0x1002
MODEL_LIGHT_LIGHTNESS_SERVER = 0x1300
MODEL_LIGHT_CTL_SERVER = 0x1303
MODEL_LIGHT_CTL_TEMP_SERVER = 0x1306
MODEL_LIGHT_HSL_SERVER = 0x1307
MODEL_LIGHT_HSL_HUE_SERVER = 0x130A
MODEL_LIGHT_HSL_SAT_SERVER = 0x130B

# Foundation models. Config Server/Client use the device key and are never
# app-bound; Health Server/Client use an application key and are bound.
MODEL_CONFIG_SERVER = 0x0000
MODEL_CONFIG_CLIENT = 0x0001
MODEL_HEALTH_SERVER = 0x0002
MODEL_HEALTH_CLIENT = 0x0003
FOUNDATION_MODELS = frozenset({MODEL_CONFIG_SERVER, MODEL_CONFIG_CLIENT})

# Light CTL temperature valid range (Mesh Model 6.1.3.1)
CTL_TEMP_MIN_K = 800
CTL_TEMP_MAX_K = 20000

# --- Health model opcodes (Mesh Profile 4.3.3) ---
OP_HEALTH_ATTENTION_GET = 0x8004
OP_HEALTH_ATTENTION_SET = 0x8005
OP_HEALTH_ATTENTION_SET_UNACK = 0x8006
OP_HEALTH_ATTENTION_STATUS = 0x8007

# Transition Time encoding (Mesh Model 3.1.3)
_TT_MAX_STEPS = 0x3E  # 62; 0x3F means "unknown"
_TT_RESOLUTIONS_S = (0.1, 1.0, 10.0, 600.0)  # step resolution per 2-bit field

# --- Lower transport control: Segment Acknowledgment (Mesh Profile 3.5.2.3.1) ---
CTL_OPCODE_SEGMENT_ACK = 0x00

# --- Tuya Vendor Model (CID 0x07D0) ---
TUYA_VENDOR_OPCODE = 0xCDD007
TUYA_VENDOR_WRITE_ACK = 0xC9D007
TUYA_VENDOR_WRITE_UNACK = 0xCAD007
TUYA_CMD_DP_DATA = 0x01
TUYA_CMD_TIMESTAMP_SYNC = 0x02
DP_ID_SWITCH = 1
DP_ID_ENERGY_KWH = 17
DP_ID_POWER_W = 18
DP_ID_CURRENT_MA = 19
DP_ID_VOLTAGE_V = 20

# Internal alias used by segments module
_OPCODE_COMPOSITION_STATUS = OP_CONFIG_COMPOSITION_STATUS


# ============================================================
# Segment Header Parsing (Mesh Profile 3.5.2.2)
# ============================================================


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
    """Parse a segmented access message lower transport PDU header."""
    if len(transport_pdu) < 4:
        msg = f"Segmented transport PDU too short: {len(transport_pdu)} bytes"
        raise MalformedPacketError(msg)

    hdr = transport_pdu[0]
    if not (hdr & MESH_SEG_BIT):
        msg = "Not a segmented PDU (SEG bit not set)"
        raise MalformedPacketError(msg)

    akf = (hdr >> MESH_AKF_SHIFT) & 1
    aid = hdr & MESH_AID_MASK
    info = (transport_pdu[1] << 16) | (transport_pdu[2] << 8) | transport_pdu[3]

    return SegmentHeader(
        akf=akf,
        aid=aid,
        szmic=(info >> MESH_SZMIC_SHIFT) & 1,
        seq_zero=(info >> MESH_SEQ_ZERO_SHIFT) & MESH_SEQ_ZERO_MASK,
        seg_o=(info >> MESH_SEG_O_SHIFT) & MESH_SEG_MASK,
        seg_n=info & MESH_SEG_MASK,
        segment_data=transport_pdu[4:],
    )


# ============================================================
# Proxy PDU (Mesh Profile 6.3)
# ============================================================


def make_proxy_pdu(network_pdu: bytes) -> bytes:
    """Wrap a network PDU in a Mesh Proxy PDU (SAR=complete, type=network)."""
    return bytes([(PROXY_SAR_COMPLETE << 6) | PROXY_TYPE_NETWORK]) + network_pdu


@dataclass(frozen=True)
class ProxyPDU:
    """Parsed Mesh Proxy PDU."""

    sar: int
    pdu_type: int
    payload: bytes


def parse_proxy_pdu(data: bytes) -> ProxyPDU:
    """Parse a Mesh Proxy PDU from GATT characteristic bytes."""
    if not data:
        msg = "Empty proxy PDU"
        raise MalformedPacketError(msg)
    return ProxyPDU(
        sar=(data[0] >> 6) & PROXY_SAR_MASK,
        pdu_type=data[0] & PROXY_TYPE_MASK,
        payload=data[1:],
    )


# ============================================================
# Config Model Messages (Mesh Profile 4.3)
# ============================================================


def config_composition_get(page: int = 0) -> bytes:
    """Config Composition Data Get (opcode 0x8008)."""
    if not 0 <= page <= 0xFF:
        msg = f"Page must be 0..255, got {page}"
        raise ProtocolError(msg)
    return struct.pack(">H", OP_CONFIG_COMPOSITION_GET) + bytes([page])


def config_state_get(opcode: int) -> bytes:
    """Encode a parameterless Config ``* Get`` message (Beacon, TTL, Relay, …)."""
    if not 0x8000 <= opcode <= 0xBFFF:
        msg = f"Not a 2-byte opcode: 0x{opcode:04X}"
        raise ProtocolError(msg)
    return struct.pack(">H", opcode)


@dataclass(frozen=True)
class TransmitState:
    """Relay Retransmit / Network Transmit state (count + interval)."""

    transmissions: int  # total sends = count + 1
    interval_ms: int  # (steps + 1) * 10 ms


def parse_transmit_byte(value: int) -> TransmitState:
    """Decode a packed transmit byte: count in bits 0-2, interval steps in bits 3-7."""
    return TransmitState(transmissions=(value & 0x07) + 1, interval_ms=((value >> 3) + 1) * 10)


@dataclass(frozen=True)
class RelayStatus:
    """Config Relay Status."""

    relay: int
    retransmit: TransmitState


def parse_relay_status(params: bytes) -> RelayStatus:
    """Parse Config Relay Status params (relay, retransmit)."""
    if len(params) < 2:
        msg = f"Relay Status too short: {len(params)} bytes"
        raise MalformedPacketError(msg)
    return RelayStatus(relay=params[0], retransmit=parse_transmit_byte(params[1]))


def parse_single_byte_status(params: bytes, name: str) -> int:
    """Parse a one-byte Config Status (Beacon, Default TTL, Friend, GATT Proxy)."""
    if len(params) < 1:
        msg = f"{name} Status empty"
        raise MalformedPacketError(msg)
    return params[0]


def config_appkey_add(net_idx: int, app_idx: int, app_key: bytes) -> bytes:
    """Config AppKey Add (opcode 0x00). 20-byte payload — requires segmented transport."""
    if not 0 <= net_idx <= 0xFFF:
        msg = f"net_idx must be 0..0xFFF, got {net_idx}"
        raise ProtocolError(msg)
    if not 0 <= app_idx <= 0xFFF:
        msg = f"app_idx must be 0..0xFFF, got {app_idx}"
        raise ProtocolError(msg)
    if len(app_key) != 16:
        msg = f"app_key must be 16 bytes, got {len(app_key)}"
        raise ProtocolError(msg)
    idx = (net_idx & 0xFFF) | ((app_idx & 0xFFF) << 12)
    return bytes([OP_CONFIG_APPKEY_ADD]) + struct.pack("<I", idx)[:3] + app_key


def config_model_app_bind(element_addr: int, app_idx: int, model_id: int) -> bytes:
    """Config Model App Bind (opcode 0x803D). SIG Model IDs only (16-bit)."""
    if not 0 <= element_addr <= 0xFFFF:
        msg = f"element_addr must be 0..0xFFFF, got {element_addr}"
        raise ProtocolError(msg)
    if not 0 <= app_idx <= 0xFFF:
        msg = f"app_idx must be 0..0xFFF, got {app_idx}"
        raise ProtocolError(msg)
    if not 0 <= model_id <= 0xFFFF:
        msg = f"model_id must be 0..0xFFFF, got {model_id}"
        raise ProtocolError(msg)
    return struct.pack(">H", OP_CONFIG_MODEL_APP_BIND) + struct.pack(
        "<HHH", element_addr, app_idx, model_id
    )


def config_model_app_bind_vendor(element_addr: int, app_idx: int, cid: int, model_id: int) -> bytes:
    """Config Model App Bind (opcode 0x803D) for a vendor model (4-byte model identifier)."""
    if not 0 <= element_addr <= 0xFFFF:
        msg = f"element_addr must be 0..0xFFFF, got {element_addr}"
        raise ProtocolError(msg)
    if not 0 <= app_idx <= 0xFFF:
        msg = f"app_idx must be 0..0xFFF, got {app_idx}"
        raise ProtocolError(msg)
    if not (0 <= cid <= 0xFFFF and 0 <= model_id <= 0xFFFF):
        msg = f"cid/model_id must be 0..0xFFFF, got 0x{cid:X}/0x{model_id:X}"
        raise ProtocolError(msg)
    return struct.pack(">H", OP_CONFIG_MODEL_APP_BIND) + struct.pack(
        "<HHHH", element_addr, app_idx, cid, model_id
    )


def config_model_pub_set(
    element_addr: int,
    publish_addr: int,
    app_idx: int,
    model_id: int,
    *,
    ttl: int = 5,
    period: int = 0,
    retransmit: int = 0,
) -> bytes:
    """Config Model Publication Set (opcode 0x03) for a SIG model.

    12-byte payload — requires segmented transport.
    """
    if not (0 <= element_addr <= 0xFFFF and 0 <= publish_addr <= 0xFFFF):
        msg = "element_addr/publish_addr must be 0..0xFFFF"
        raise ProtocolError(msg)
    if not 0 <= app_idx <= 0xFFF:
        msg = f"app_idx must be 0..0xFFF, got {app_idx}"
        raise ProtocolError(msg)
    if not 0 <= model_id <= 0xFFFF:
        msg = f"model_id must be 0..0xFFFF, got {model_id}"
        raise ProtocolError(msg)
    return (
        bytes([OP_CONFIG_MODEL_PUB_SET])
        + struct.pack("<HHH", element_addr, publish_addr, app_idx & 0xFFF)
        + bytes([ttl & 0xFF, period & 0xFF, retransmit & 0xFF])
        + struct.pack("<H", model_id)
    )


def segment_ack(seq_zero: int, block_ack: int, *, obo: bool = False) -> bytes:
    """Build a Segment Acknowledgment lower transport control PDU (unsegmented, CTL=1)."""
    info = ((1 if obo else 0) << 15) | ((seq_zero & MESH_SEQ_ZERO_MASK) << 2)
    return bytes([CTL_OPCODE_SEGMENT_ACK]) + struct.pack(">HI", info, block_ack & 0xFFFFFFFF)


# ============================================================
# Generic OnOff Model Messages (Mesh Model 3.2)
# ============================================================


def encode_transition_time(seconds: float) -> int:
    """Encode a duration as a mesh Transition Time byte (Mesh Model 3.1.3).

    Uses the finest resolution (100 ms, 1 s, 10 s, 10 min) that can hold the
    duration; longer values are clamped to 62 x 10 min.
    """
    if seconds <= 0:
        return 0
    for res_index, resolution in enumerate(_TT_RESOLUTIONS_S):
        steps = round(seconds / resolution)
        if steps <= _TT_MAX_STEPS:
            return (res_index << 6) | max(1, steps)
    return (3 << 6) | _TT_MAX_STEPS


def decode_transition_time(value: int) -> float:
    """Decode a mesh Transition Time byte to seconds ("unknown" -> 0.0)."""
    steps = value & 0x3F
    if steps == 0x3F:
        return 0.0
    return steps * _TT_RESOLUTIONS_S[(value >> 6) & 0x03]


def _transition_suffix(transition_s: float | None) -> bytes:
    """Optional Transition Time + Delay (0) trailer for Set messages."""
    if transition_s is None:
        return b""
    return bytes([encode_transition_time(transition_s), 0])


def generic_onoff_set(
    on: bool, tid: int = 0, *, transition_s: float | None = None, ack: bool = True
) -> bytes:
    """Generic OnOff Set / Set Unacknowledged (0x8202 / 0x8203)."""
    opcode = OP_GENERIC_ONOFF_SET if ack else OP_GENERIC_ONOFF_SET_UNACK
    return (
        struct.pack(">H", opcode)
        + bytes([0x01 if on else 0x00, tid & 0xFF])
        + _transition_suffix(transition_s)
    )


def generic_onoff_get() -> bytes:
    """Generic OnOff Get (opcode 0x8201)."""
    return struct.pack(">H", OP_GENERIC_ONOFF_GET)


# ============================================================
# Light Lightness / CTL / HSL Messages (Mesh Model 6.3)
# ============================================================


def _check_u16(name: str, value: int) -> None:
    if not 0 <= value <= 0xFFFF:
        msg = f"{name} must be 0..65535, got {value}"
        raise ProtocolError(msg)


def light_lightness_set(
    lightness: int, tid: int, *, ack: bool = True, transition_s: float | None = None
) -> bytes:
    """Light Lightness Set / Set Unacknowledged (0x824C / 0x824D)."""
    _check_u16("lightness", lightness)
    opcode = OP_LIGHT_LIGHTNESS_SET if ack else OP_LIGHT_LIGHTNESS_SET_UNACK
    return (
        struct.pack(">H", opcode)
        + struct.pack("<HB", lightness, tid & 0xFF)
        + _transition_suffix(transition_s)
    )


def light_lightness_get() -> bytes:
    """Light Lightness Get (0x824B)."""
    return struct.pack(">H", OP_LIGHT_LIGHTNESS_GET)


def light_ctl_set(
    lightness: int,
    temperature_k: int,
    tid: int,
    *,
    delta_uv: int = 0,
    ack: bool = True,
    transition_s: float | None = None,
) -> bytes:
    """Light CTL Set / Set Unacknowledged (0x825E / 0x825F)."""
    _check_u16("lightness", lightness)
    if not CTL_TEMP_MIN_K <= temperature_k <= CTL_TEMP_MAX_K:
        msg = f"temperature must be {CTL_TEMP_MIN_K}..{CTL_TEMP_MAX_K} K, got {temperature_k}"
        raise ProtocolError(msg)
    opcode = OP_LIGHT_CTL_SET if ack else OP_LIGHT_CTL_SET_UNACK
    return (
        struct.pack(">H", opcode)
        + struct.pack("<HHhB", lightness, temperature_k, delta_uv, tid & 0xFF)
        + _transition_suffix(transition_s)
    )


def light_ctl_temperature_set(
    temperature_k: int,
    tid: int,
    *,
    delta_uv: int = 0,
    ack: bool = True,
    transition_s: float | None = None,
) -> bytes:
    """Light CTL Temperature Set / Set Unacknowledged (0x8264 / 0x8265).

    Tuya SIG Mesh lights change colour temperature only via this message
    (their DP 4 maps to it); the combined CTL Set's temperature is ignored.
    """
    if not CTL_TEMP_MIN_K <= temperature_k <= CTL_TEMP_MAX_K:
        msg = f"temperature must be {CTL_TEMP_MIN_K}..{CTL_TEMP_MAX_K} K, got {temperature_k}"
        raise ProtocolError(msg)
    opcode = OP_LIGHT_CTL_TEMP_SET if ack else OP_LIGHT_CTL_TEMP_SET_UNACK
    return (
        struct.pack(">H", opcode)
        + struct.pack("<HhB", temperature_k, delta_uv, tid & 0xFF)
        + _transition_suffix(transition_s)
    )


def light_ctl_get() -> bytes:
    """Light CTL Get (0x825D)."""
    return struct.pack(">H", OP_LIGHT_CTL_GET)


def light_ctl_temp_range_get() -> bytes:
    """Light CTL Temperature Range Get (0x8262)."""
    return struct.pack(">H", OP_LIGHT_CTL_TEMP_RANGE_GET)


def light_hsl_set(
    lightness: int,
    hue: int,
    saturation: int,
    tid: int,
    *,
    ack: bool = True,
    transition_s: float | None = None,
) -> bytes:
    """Light HSL Set / Set Unacknowledged (0x8276 / 0x8277). All values 0..65535."""
    _check_u16("lightness", lightness)
    _check_u16("hue", hue)
    _check_u16("saturation", saturation)
    opcode = OP_LIGHT_HSL_SET if ack else OP_LIGHT_HSL_SET_UNACK
    return (
        struct.pack(">H", opcode)
        + struct.pack("<HHHB", lightness, hue, saturation, tid & 0xFF)
        + _transition_suffix(transition_s)
    )


def health_attention_set(seconds: int, *, ack: bool = True) -> bytes:
    """Health Attention Set / Set Unacknowledged (0x8005 / 0x8006), 0-255 s."""
    if not 0 <= seconds <= 0xFF:
        msg = f"attention must be 0..255 s, got {seconds}"
        raise ProtocolError(msg)
    opcode = OP_HEALTH_ATTENTION_SET if ack else OP_HEALTH_ATTENTION_SET_UNACK
    return struct.pack(">H", opcode) + bytes([seconds])


def light_hsl_get() -> bytes:
    """Light HSL Get (0x826D)."""
    return struct.pack(">H", OP_LIGHT_HSL_GET)


@dataclass(frozen=True)
class LightnessStatus:
    """Light Lightness Status (present value)."""

    lightness: int


@dataclass(frozen=True)
class CTLStatus:
    """Light CTL Status (present values)."""

    lightness: int
    temperature_k: int


@dataclass(frozen=True)
class CTLTempRangeStatus:
    """Light CTL Temperature Range Status."""

    status: int
    min_k: int
    max_k: int


@dataclass(frozen=True)
class HSLStatus:
    """Light HSL Status (present values, all 0..65535)."""

    lightness: int
    hue: int
    saturation: int


def parse_lightness_status(params: bytes) -> LightnessStatus:
    """Parse Light Lightness Status params (present[, target, remaining])."""
    if len(params) < 2:
        msg = f"Lightness Status too short: {len(params)} bytes"
        raise MalformedPacketError(msg)
    return LightnessStatus(lightness=struct.unpack_from("<H", params, 0)[0])


def parse_ctl_status(params: bytes) -> CTLStatus:
    """Parse Light CTL Status params (present lightness, present temp[, targets, remaining])."""
    if len(params) < 4:
        msg = f"CTL Status too short: {len(params)} bytes"
        raise MalformedPacketError(msg)
    lightness, temp = struct.unpack_from("<HH", params, 0)
    return CTLStatus(lightness=lightness, temperature_k=temp)


@dataclass(frozen=True)
class CTLTemperatureStatus:
    """Light CTL Temperature Status (present values)."""

    temperature_k: int
    delta_uv: int


def parse_ctl_temperature_status(params: bytes) -> CTLTemperatureStatus:
    """Parse Light CTL Temperature Status params (present temp, present Δuv[, ...])."""
    if len(params) < 4:
        msg = f"CTL Temperature Status too short: {len(params)} bytes"
        raise MalformedPacketError(msg)
    temp, duv = struct.unpack_from("<Hh", params, 0)
    return CTLTemperatureStatus(temperature_k=temp, delta_uv=duv)


def parse_ctl_temp_range_status(params: bytes) -> CTLTempRangeStatus:
    """Parse Light CTL Temperature Range Status params."""
    if len(params) < 5:
        msg = f"CTL Temperature Range Status too short: {len(params)} bytes"
        raise MalformedPacketError(msg)
    status = params[0]
    min_k, max_k = struct.unpack_from("<HH", params, 1)
    return CTLTempRangeStatus(status=status, min_k=min_k, max_k=max_k)


def parse_hsl_status(params: bytes) -> HSLStatus:
    """Parse Light HSL Status params (lightness, hue, saturation[, remaining])."""
    if len(params) < 6:
        msg = f"HSL Status too short: {len(params)} bytes"
        raise MalformedPacketError(msg)
    lightness, hue, sat = struct.unpack_from("<HHH", params, 0)
    return HSLStatus(lightness=lightness, hue=hue, saturation=sat)


# ============================================================
# Access Layer Opcode Parsing (Mesh Profile 3.7.3)
# ============================================================


def parse_access_opcode(data: bytes) -> tuple[int, bytes]:
    """Parse a SIG Mesh access layer opcode (1, 2, or 3 bytes)."""
    if not data:
        msg = "Empty access payload"
        raise MalformedPacketError(msg)

    if data[0] & MESH_OPCODE_1BYTE_MASK == 0:
        return data[0], data[1:]
    elif data[0] & MESH_OPCODE_2BYTE_MASK == MESH_OPCODE_2BYTE_VALUE:
        if len(data) < 2:
            msg = "2-byte opcode truncated"
            raise MalformedPacketError(msg)
        return (data[0] << 8) | data[1], data[2:]
    else:
        if len(data) < 3:
            msg = "3-byte vendor opcode truncated"
            raise MalformedPacketError(msg)
        return (data[0] << 16) | (data[1] << 8) | data[2], data[3:]


# ============================================================
# Tuya Vendor Model (CID 0x07D0)
# ============================================================


@dataclass(frozen=True)
class TuyaVendorDP:
    """A single Tuya vendor Data Point from a vendor message."""

    dp_id: int
    dp_type: int
    value: bytes


@dataclass(frozen=True)
class TuyaVendorFrame:
    """Parsed Tuya vendor message frame.

    Attributes:
        command: Frame command byte (0x01=DP data, 0x02=timestamp sync, 0=unknown/raw).
        data: Raw data bytes after the frame header.
        dps: Parsed data points (populated only when command is TUYA_CMD_DP_DATA).
    """

    command: int
    data: bytes
    dps: list[TuyaVendorDP]


def parse_tuya_vendor_frame(params: bytes) -> TuyaVendorFrame:
    """Parse a Tuya vendor message with frame header ``[command 1B][data_length 1B][data NB]``."""
    if len(params) < 2:
        _LOGGER.debug("Vendor frame too short (%d bytes)", len(params))
        return TuyaVendorFrame(command=0, data=params, dps=[])

    command = params[0]
    data = params[2:]

    if command == TUYA_CMD_TIMESTAMP_SYNC:
        _LOGGER.debug("Tuya timestamp sync request (%d data bytes)", len(data))
        return TuyaVendorFrame(command=command, data=data, dps=[])

    if command == TUYA_CMD_DP_DATA:
        return TuyaVendorFrame(command=command, data=data, dps=_parse_dp_bytes(data))

    # Decrypted application payload (not key material) — logged so unknown
    # Tuya commands can be identified.
    _LOGGER.info(
        "Unknown Tuya vendor command 0x%02X (%d bytes): %s — trying raw DP parse",
        command,
        len(params),
        params.hex(),
    )
    return TuyaVendorFrame(command=command, data=params, dps=_parse_dp_bytes(params))


def tuya_vendor_timestamp_response() -> bytes:
    """Build a Tuya vendor WRITE_UNACK payload with current UTC timestamp."""
    import time

    now = int(time.time())
    opcode_bytes = TUYA_VENDOR_WRITE_UNACK.to_bytes(3, "big")
    ts_bytes = now.to_bytes(4, "big")
    # tm_gmtoff reflects whether DST is currently in effect
    tz_offset = time.localtime(now).tm_gmtoff // 3600
    tz_byte = tz_offset.to_bytes(1, "big", signed=True) if -12 <= tz_offset <= 14 else b"\x00"
    data = ts_bytes + tz_byte + b"\x00\x00\x00"
    frame = bytes([TUYA_CMD_TIMESTAMP_SYNC, len(data)]) + data
    return opcode_bytes + frame


def parse_tuya_vendor_dps(params: bytes) -> list[TuyaVendorDP]:
    """Parse Tuya vendor DP values (raw TLV, no frame header)."""
    return _parse_dp_bytes(params)


def _parse_dp_bytes(data: bytes) -> list[TuyaVendorDP]:
    """Parse raw DP bytes: ``[dp_id 1B][dp_type 1B][dp_len 1B][value NB]...``"""
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
        dps.append(TuyaVendorDP(dp_id=dp_id, dp_type=dp_type, value=data[offset : offset + dp_len]))
        offset += dp_len
    return dps


# ============================================================
# Composition Data (Mesh Profile 4.2.1)
# ============================================================


@dataclass(frozen=True)
class CompositionElement:
    """One element from Composition Data Page 0.

    Attributes:
        index: Element index (0 = primary). Element address = primary unicast + index.
        location: GATT Namespace Descriptor location.
        sig_models: 16-bit SIG model IDs.
        vendor_models: (company_id, model_id) pairs.
    """

    index: int
    location: int
    sig_models: tuple[int, ...]
    vendor_models: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class CompositionData:
    """Parsed Composition Data Page 0."""

    cid: int  # Company ID
    pid: int  # Product ID
    vid: int  # Version ID
    crpl: int  # Replay protection list size
    features: int  # Features bitmask
    raw_elements: bytes  # Element data as received
    elements: tuple[CompositionElement, ...] = ()

    def find_model(self, model_id: int) -> int | None:
        """Return the index of the first element containing a SIG model, or None."""
        for element in self.elements:
            if model_id in element.sig_models:
                return element.index
        return None


def _parse_composition_elements(data: bytes) -> tuple[CompositionElement, ...]:
    """Parse the element list of Composition Data Page 0 (Mesh Profile 4.2.1.1).

    Stops at the first truncated element rather than raising, so a partially
    malformed page still yields the elements that were complete.
    """
    elements: list[CompositionElement] = []
    offset = 0
    while offset + 4 <= len(data):
        loc = struct.unpack_from("<H", data, offset)[0]
        num_s = data[offset + 2]
        num_v = data[offset + 3]
        end = offset + 4 + num_s * 2 + num_v * 4
        if end > len(data):
            _LOGGER.debug("Truncated composition element at offset %d", offset)
            break
        pos = offset + 4
        sig = struct.unpack_from(f"<{num_s}H", data, pos) if num_s else ()
        pos += num_s * 2
        vendor: list[tuple[int, int]] = []
        for _ in range(num_v):
            cid, mid = struct.unpack_from("<HH", data, pos)
            vendor.append((cid, mid))
            pos += 4
        elements.append(
            CompositionElement(
                index=len(elements),
                location=loc,
                sig_models=tuple(sig),
                vendor_models=tuple(vendor),
            )
        )
        offset = end
    return tuple(elements)


def parse_composition_data(params: bytes) -> CompositionData:
    """Parse Composition Data Status page 0 parameters."""
    if len(params) < 11:
        msg = f"Composition Data too short: {len(params)} bytes (need >= 11)"
        raise MalformedPacketError(msg)

    data = params[1:]  # Skip page byte
    raw_elements = data[10:]
    return CompositionData(
        cid=struct.unpack_from("<H", data, 0)[0],
        pid=struct.unpack_from("<H", data, 2)[0],
        vid=struct.unpack_from("<H", data, 4)[0],
        crpl=struct.unpack_from("<H", data, 6)[0],
        features=struct.unpack_from("<H", data, 8)[0],
        raw_elements=raw_elements,
        elements=_parse_composition_elements(raw_elements),
    )


# ============================================================
# Status Response Formatting
# ============================================================

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
    """Format a mesh status response for human-readable display."""
    if opcode == OP_CONFIG_APPKEY_STATUS:
        status = params[0] if params else 0xFF
        return f"AppKey Status: {_CONFIG_STATUS_NAMES.get(status, f'Unknown(0x{status:02X})')}"

    if opcode == OP_CONFIG_MODEL_APP_STATUS:
        status = params[0] if params else 0xFF
        bind_status: dict[int, str] = {
            0x00: "Success",
            0x02: "InvalidModel",
            0x03: "InvalidAppKeyIndex",
            0x04: "InvalidNetKeyIndex",
            0x06: "ModelAppAlreadyBound",
        }
        return f"Model App Status: {bind_status.get(status, f'Unknown(0x{status:02X})')}"

    if opcode == OP_CONFIG_COMPOSITION_STATUS:
        page = params[0] if params else 0xFF
        return f"Composition Data: page={page} ({len(params) - 1} bytes)"

    if opcode == OP_GENERIC_ONOFF_STATUS:
        state = params[0] if params else 0xFF
        msg = f"OnOff Status: {'ON' if state else 'OFF'}"
        if len(params) >= 3:
            msg += f" (target={'ON' if params[1] else 'OFF'}, remaining={params[2]})"
        return msg

    return f"Opcode 0x{opcode:04X}: {len(params)} bytes"
