#!/usr/bin/env python3
"""Send SIG Mesh commands via GATT Proxy connection.

Connects to a provisioned SIG Mesh device via its Mesh Proxy Service
(UUID 0x1828) and sends encrypted mesh messages.

Supported commands:
  setup       — Config AppKey Add + Model App Bind in one session
  appkey-add  — Send Config AppKey Add (segmented, device key)
  bind        — Send Config Model App Bind (unsegmented, device key)
  on / off    — Send Generic OnOff Set (unsegmented, app key)
  status      — Send Generic OnOff Get (unsegmented, app key)
  composition — Send Config Composition Data Get (unsegmented, device key)

Requires mesh keys from bluetooth-meshd provisioning.

SECURITY: Key material is loaded from meshd config files only —
never printed to stdout. Only lengths and opcodes are logged.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import struct
import sys
from pathlib import Path

from bleak import BleakClient, BleakScanner
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

_LOGGER = logging.getLogger(__name__)

# --- BLE characteristics ---
MESH_PROXY_DATA_IN = "00002add-0000-1000-8000-00805f9b34fb"
MESH_PROXY_DATA_OUT = "00002ade-0000-1000-8000-00805f9b34fb"

# --- Mesh constants ---
PROXY_SAR_COMPLETE = 0x00
PROXY_TYPE_NETWORK = 0x00
SEQ_FILE = Path("/tmp/mesh_seq_tracker.json")
MAX_UNSEG_ACCESS_PAYLOAD = 11  # 15 bytes upper transport - 4 byte TransMIC
SEG_DATA_SIZE = 12  # max bytes per segment chunk


# ============================================================
# SIG Mesh Crypto (minimal, for network + transport layers)
# ============================================================


def aes_ecb(key: bytes, plaintext: bytes) -> bytes:
    """Single-block AES-128-ECB encrypt."""
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    enc = cipher.encryptor()
    return enc.update(plaintext) + enc.finalize()


def aes_cmac(key: bytes, msg: bytes) -> bytes:
    """AES-CMAC (RFC 4493)."""
    zero = b"\x00" * 16
    l_val = aes_ecb(key, zero)
    k1 = _shift_left(l_val)
    if l_val[0] & 0x80:
        k1 = _xor(k1, b"\x00" * 15 + b"\x87")
    k2 = _shift_left(k1)
    if k1[0] & 0x80:
        k2 = _xor(k2, b"\x00" * 15 + b"\x87")

    n = max(1, (len(msg) + 15) // 16)
    flag = len(msg) > 0 and len(msg) % 16 == 0

    if flag:
        m_last = _xor(msg[(n - 1) * 16 :], k1)
    else:
        padded = msg[(n - 1) * 16 :] + b"\x80" + b"\x00" * (15 - len(msg) % 16)
        m_last = _xor(padded[:16], k2)

    x = b"\x00" * 16
    for i in range(n - 1):
        x = aes_ecb(key, _xor(x, msg[i * 16 : (i + 1) * 16]))
    x = aes_ecb(key, _xor(x, m_last))
    return x


def _shift_left(b: bytes) -> bytes:
    result = bytearray(16)
    for i in range(15):
        result[i] = ((b[i] << 1) & 0xFF) | (b[i + 1] >> 7)
    result[15] = (b[15] << 1) & 0xFF
    return bytes(result)


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b, strict=True))


def s1(m: bytes) -> bytes:
    """s1 salt generation function."""
    return aes_cmac(b"\x00" * 16, m)


def k1(n: bytes, salt: bytes, p: bytes) -> bytes:
    """k1 key derivation function."""
    t = aes_cmac(salt, n)
    return aes_cmac(t, p)


def k2(n: bytes, p: bytes) -> tuple[int, bytes, bytes]:
    """k2 — derive NID, encryption key, privacy key from NetKey."""
    salt = s1(b"smk2")
    t = aes_cmac(salt, n)
    t1 = aes_cmac(t, p + b"\x01")
    t2 = aes_cmac(t, t1 + p + b"\x02")
    t3 = aes_cmac(t, t2 + p + b"\x03")
    return t1[15] & 0x7F, t2, t3


def k3(n: bytes) -> bytes:
    """k3 — derive Network ID from NetKey."""
    salt = s1(b"smk3")
    t = aes_cmac(salt, n)
    return aes_cmac(t, b"id64\x01")[8:]


def k4(n: bytes) -> int:
    """k4 — derive AID from AppKey."""
    salt = s1(b"smk4")
    t = aes_cmac(salt, n)
    return aes_cmac(t, b"id6\x01")[15] & 0x3F


def mesh_aes_ccm_encrypt(key: bytes, nonce: bytes, payload: bytes, mic_len: int = 4) -> bytes:
    """AES-CCM encrypt for mesh (MIC appended)."""
    aesccm = AESCCM(key, tag_length=mic_len)
    return aesccm.encrypt(nonce, payload, b"")


def mesh_aes_ccm_decrypt(key: bytes, nonce: bytes, ct_and_mic: bytes, mic_len: int = 4) -> bytes:
    """AES-CCM decrypt for mesh."""
    aesccm = AESCCM(key, tag_length=mic_len)
    return aesccm.decrypt(nonce, ct_and_mic, b"")


# ============================================================
# Mesh Network Layer
# ============================================================


def _make_network_nonce(ctl_ttl: int, seq: int, src: int, iv_index: int) -> bytes:
    """Build 13-byte network nonce."""
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
    ctl: int,
    ttl: int,
    seq: int,
    src: int,
    dst: int,
    transport_pdu: bytes,
    iv_index: int = 0,
) -> bytes:
    """Encrypt and obfuscate a mesh network PDU."""
    ctl_ttl = ((ctl & 1) << 7) | (ttl & 0x7F)
    nonce = _make_network_nonce(ctl_ttl, seq, src, iv_index)
    plaintext = struct.pack(">H", dst) + transport_pdu
    mic_len = 8 if ctl else 4
    encrypted = mesh_aes_ccm_encrypt(enc_key, nonce, plaintext, mic_len)

    ivi_nid = ((iv_index & 1) << 7) | (nid & 0x7F)
    header = bytes([ivi_nid, ctl_ttl]) + struct.pack(">I", seq)[1:] + struct.pack(">H", src)

    privacy_random = encrypted[:7]
    pecb_input = b"\x00\x00\x00\x00\x00" + struct.pack(">I", iv_index) + privacy_random
    pecb = aes_ecb(priv_key, pecb_input)
    obfuscated = bytes(a ^ b for a, b in zip(header[1:7], pecb[:6], strict=True))

    return bytes([ivi_nid]) + obfuscated + encrypted


def decrypt_network_pdu(
    enc_key: bytes,
    priv_key: bytes,
    nid: int,
    pdu: bytes,
    iv_index: int = 0,
) -> dict | None:
    """Decrypt a mesh network PDU."""
    if pdu[0] & 0x7F != nid:
        return None

    encrypted_data = pdu[7:]
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
    except Exception:
        _LOGGER.debug("Network decryption failed")
        return None

    return {
        "ctl": ctl,
        "ttl": ttl,
        "seq": seq,
        "src": src,
        "dst": (plaintext[0] << 8) | plaintext[1],
        "transport_pdu": plaintext[2:],
    }


# ============================================================
# Mesh Transport Layer
# ============================================================


def _make_app_nonce(
    akf: int,
    szmic: int,
    seq: int,
    src: int,
    dst: int,
    iv_index: int,
) -> bytes:
    """Build 13-byte application/device nonce."""
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
    akf: int = 0,
    aid: int = 0,
) -> bytes:
    """Create unsegmented access message lower transport PDU.

    Max 11-byte access payload (15 - 4 TransMIC).
    """
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
    akf: int = 0,
    aid: int = 0,
    szmic: int = 0,
) -> list[tuple[int, bytes]]:
    """Create segmented access message lower transport PDUs.

    Returns [(seq, transport_pdu), ...] — one per segment.
    Each transport_pdu includes the 1-byte header + 3-byte seg info + chunk.
    """
    nonce = _make_app_nonce(akf, szmic, seq_start, src, dst, iv_index)
    mic_len = 8 if szmic else 4
    upper_transport = mesh_aes_ccm_encrypt(key, nonce, access_payload, mic_len)

    n_segs = (len(upper_transport) + SEG_DATA_SIZE - 1) // SEG_DATA_SIZE
    seg_n = n_segs - 1
    seq_zero = seq_start & 0x1FFF

    segments = []
    for seg_o in range(n_segs):
        chunk = upper_transport[seg_o * SEG_DATA_SIZE : (seg_o + 1) * SEG_DATA_SIZE]
        hdr = 0x80 | (akf << 6) | (aid & 0x3F)  # SEG=1
        # SZMIC(1) | SeqZero(13) | SegO(5) | SegN(5) = 24 bits
        info = (szmic << 23) | (seq_zero << 10) | (seg_o << 5) | seg_n
        transport_pdu = bytes([hdr]) + struct.pack(">I", info)[1:] + chunk
        segments.append((seq_start + seg_o, transport_pdu))

    return segments


def decrypt_access_payload(
    keys: "MeshKeys",
    src: int,
    dst: int,
    seq: int,
    transport_pdu: bytes,
) -> dict | None:
    """Decrypt upper transport from a received lower transport PDU."""
    hdr = transport_pdu[0]
    seg = (hdr >> 7) & 1
    akf = (hdr >> 6) & 1
    aid = hdr & 0x3F

    if seg:
        # Segmented — return raw for reassembly
        return {"seg": True, "akf": akf, "aid": aid, "raw": transport_pdu}

    encrypted_upper = transport_pdu[1:]
    key = keys.app_key if akf else keys.dev_key
    if key is None:
        _LOGGER.debug("No %s key for decryption", "app" if akf else "dev")
        return None

    nonce = _make_app_nonce(akf, 0, seq, src, dst, keys.iv_index)
    try:
        access_payload = mesh_aes_ccm_decrypt(key, nonce, encrypted_upper, 4)
    except Exception:
        _LOGGER.debug("Upper transport decryption failed (akf=%d)", akf)
        return None

    return {"seg": False, "akf": akf, "aid": aid, "access": access_payload}


# ============================================================
# Proxy PDU
# ============================================================


def make_proxy_pdu(network_pdu: bytes) -> bytes:
    """Wrap a network PDU in a Mesh Proxy PDU (SAR=complete, type=network)."""
    return bytes([(PROXY_SAR_COMPLETE << 6) | PROXY_TYPE_NETWORK]) + network_pdu


def parse_proxy_pdu(data: bytes) -> tuple[int, int, bytes]:
    """Parse Mesh Proxy PDU. Returns (sar, pdu_type, payload)."""
    return (data[0] >> 6) & 0x03, data[0] & 0x3F, data[1:]


# ============================================================
# Mesh Keys
# ============================================================


class MeshKeys:
    """Holds derived mesh keys for a network."""

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

        _LOGGER.info(
            "Keys derived: NID=0x%02X AID=0x%02X ivIdx=%d",
            self.nid,
            self.aid,
            self.iv_index,
        )


# ============================================================
# Config Model Messages
# ============================================================


def config_composition_get(page: int = 0) -> bytes:
    """Config Composition Data Get (opcode 0x8008)."""
    return b"\x80\x08" + bytes([page])


def config_appkey_add(net_idx: int, app_idx: int, app_key: bytes) -> bytes:
    """Config AppKey Add (opcode 0x00). 20 bytes — needs segmented transport."""
    # NetKeyIndex(12) + AppKeyIndex(12) packed into 3 bytes LE
    idx = (net_idx & 0xFFF) | ((app_idx & 0xFFF) << 12)
    return b"\x00" + struct.pack("<I", idx)[:3] + app_key


def config_model_app_bind(
    element_addr: int,
    app_idx: int,
    model_id: int,
) -> bytes:
    """Config Model App Bind (opcode 0x803D). 8 bytes — fits unsegmented."""
    return b"\x80\x3d" + struct.pack("<HHH", element_addr, app_idx, model_id)


# ============================================================
# Generic OnOff Model Messages
# ============================================================


def generic_onoff_set(on: bool, tid: int = 0) -> bytes:
    """Generic OnOff Set (opcode 0x8202)."""
    return b"\x82\x02" + bytes([0x01 if on else 0x00, tid & 0xFF])


def generic_onoff_get() -> bytes:
    """Generic OnOff Get (opcode 0x8201)."""
    return b"\x82\x01"


# ============================================================
# Response Parsing
# ============================================================


def parse_access_opcode(data: bytes) -> tuple[int, bytes]:
    """Parse SIG Mesh access layer opcode. Returns (opcode, params)."""
    if data[0] & 0x80 == 0:
        return data[0], data[1:]
    elif data[0] & 0xC0 == 0x80:
        return (data[0] << 8) | data[1], data[2:]
    else:
        return (data[0] << 16) | (data[1] << 8) | data[2], data[3:]


def format_status_response(opcode: int, params: bytes) -> str:
    """Format a mesh status response for display."""
    if opcode == 0x8003:  # Config AppKey Status
        status = params[0] if params else 0xFF
        status_names = {
            0: "Success",
            1: "InvalidAddress",
            2: "InvalidModel",
            3: "InvalidAppKeyIndex",
            4: "InvalidNetKeyIndex",
            5: "InsufficientResources",
            6: "KeyIndexAlreadyStored",
        }
        name = status_names.get(status, f"Unknown(0x{status:02X})")
        return f"AppKey Status: {name}"
    elif opcode == 0x803E:  # Config Model App Status
        status = params[0] if params else 0xFF
        status_names = {
            0: "Success",
            2: "InvalidModel",
            3: "InvalidAppKeyIndex",
            4: "InvalidNetKeyIndex",
            6: "ModelAppAlreadyBound",
        }
        name = status_names.get(status, f"Unknown(0x{status:02X})")
        return f"Model App Status: {name}"
    elif opcode == 0x02:  # Config Composition Data Status
        page = params[0] if params else 0xFF
        return f"Composition Data: page={page} ({len(params) - 1} bytes)"
    elif opcode == 0x8204:  # Generic OnOff Status
        state = params[0] if params else 0xFF
        msg = f"OnOff Status: {'ON' if state else 'OFF'}"
        if len(params) >= 3:
            msg += f" (target={'ON' if params[1] else 'OFF'}, remaining={params[2]})"
        return msg
    return f"Opcode 0x{opcode:04X}: {len(params)} bytes"


# ============================================================
# Sequence Number Tracking
# ============================================================


def next_seq(count: int = 1) -> int:
    """Allocate next sequence number(s). Persists to file."""
    if SEQ_FILE.exists():
        data = json.loads(SEQ_FILE.read_text())
        seq = data["seq"]
    else:
        # Start well above any previous usage (meshd seq=108, previous runs ~208+)
        seq = 500
    SEQ_FILE.write_text(json.dumps({"seq": seq + count}))
    _LOGGER.debug("Allocated seq %d..%d", seq, seq + count - 1)
    return seq


# ============================================================
# BLE Connection
# ============================================================


async def ble_remove(mac: str) -> None:
    """Remove device from BlueZ cache."""
    with contextlib.suppress(Exception):
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            "remove",
            mac,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5)


async def connect_with_retry(
    mac: str,
    retries: int = 7,
    timeout: float = 20,
) -> BleakClient:
    """Connect to device with retry (removes BlueZ cache between attempts)."""
    last_error: str = "unknown"
    scan_failures = 0
    connect_failures = 0

    for attempt in range(1, retries + 1):
        try:
            # Clear BlueZ cache to avoid stale connection state
            await ble_remove(mac)
            await asyncio.sleep(2)

            # Scan for device
            _LOGGER.debug("Scanning for %s (timeout=%.1fs)", mac, timeout)
            dev = await asyncio.wait_for(
                BleakScanner.find_device_by_address(mac, timeout=timeout),
                timeout=timeout + 5.0,
            )

            if not dev:
                scan_failures += 1
                print(f"  [{attempt}/{retries}] Device not found in BLE scan")
                _LOGGER.warning("Scan attempt %d/%d failed for %s", attempt, retries, mac)
                # Exponential backoff
                backoff = min(3.0 * (1.5 ** (attempt - 1)), 15.0)
                await asyncio.sleep(backoff)
                continue

            # Connect to device
            _LOGGER.debug("Device %s found, connecting...", mac)
            client = BleakClient(dev, timeout=timeout)
            await asyncio.wait_for(client.connect(), timeout=timeout)

            # Verify connection
            if not client.is_connected:
                connect_failures += 1
                raise ConnectionError("BleakClient reported connected but is_connected=False")

            print(f"  [{attempt}/{retries}] Connected! (MTU={client.mtu_size})")
            _LOGGER.info("Connected to %s (MTU=%d)", mac, client.mtu_size)
            return client

        except asyncio.TimeoutError:
            last_error = f"timeout after {timeout}s"
            connect_failures += 1
            print(f"  [{attempt}/{retries}] Connection timeout")
            _LOGGER.warning("Connect timeout for %s (attempt %d/%d)", mac, attempt, retries)
            await asyncio.sleep(3)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            connect_failures += 1
            print(f"  [{attempt}/{retries}] {type(exc).__name__}: {exc}")
            _LOGGER.warning("Connect failed for %s: %s", mac, last_error)
            await asyncio.sleep(3)

    error_msg = (
        f"Failed to connect to {mac} after {retries} attempts "
        f"(scan_failures={scan_failures}, connect_failures={connect_failures}). "
        f"Last error: {last_error}. "
        f"Check device is powered on, in range, and not connected to another client."
    )
    raise ConnectionError(error_msg)


# ============================================================
# Proxy Session (manages BLE subscription + send/receive)
# ============================================================


class ProxySession:
    """Manages a GATT Proxy connection for sending mesh commands.

    Subscribes to Mesh Proxy Data Out once and reuses the subscription
    across multiple commands in the same session.
    """

    def __init__(
        self,
        client: BleakClient,
        keys: MeshKeys,
        our_addr: int,
    ) -> None:
        self.client = client
        self.keys = keys
        self.our_addr = our_addr
        self.responses: list[dict] = []
        self._subscribed = False

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        """Handle Mesh Proxy Data Out notifications."""
        _sar, pdu_type, payload = parse_proxy_pdu(bytes(data))
        if pdu_type != 0:
            _LOGGER.debug("Non-network proxy PDU type=%d", pdu_type)
            return

        result = decrypt_network_pdu(
            self.keys.enc_key,
            self.keys.priv_key,
            self.keys.nid,
            payload,
            self.keys.iv_index,
        )
        if not result:
            return

        print(
            f"  << src=0x{result['src']:04X} dst=0x{result['dst']:04X} "
            f"seq={result['seq']} ctl={result['ctl']}"
        )

        if result["ctl"]:
            if result["transport_pdu"]:
                opcode = result["transport_pdu"][0] & 0x7F
                _LOGGER.debug("Control opcode=0x%02X", opcode)
                if opcode == 0x00:
                    print("     [Segment Acknowledgment]")
            return

        decoded = decrypt_access_payload(
            self.keys,
            result["src"],
            result["dst"],
            result["seq"],
            result["transport_pdu"],
        )
        if decoded and not decoded.get("seg") and "access" in decoded:
            opcode, params = parse_access_opcode(decoded["access"])
            msg = format_status_response(opcode, params)
            print(f"     {msg}")
            self.responses.append({"opcode": opcode, "params": params, "msg": msg})
        elif decoded and decoded.get("seg"):
            print("     [segmented response — not yet reassembled]")
        else:
            print("     [decryption failed]")

    async def _ensure_subscribed(self) -> None:
        """Subscribe to Mesh Proxy Data Out (idempotent)."""
        if self._subscribed:
            return
        await self.client.start_notify(MESH_PROXY_DATA_OUT, self._on_notify)
        self._subscribed = True
        _LOGGER.debug("Subscribed to Mesh Proxy Data Out")

    async def send(
        self,
        dst: int,
        access_payload: bytes,
        use_dev_key: bool = True,
        wait_secs: float = 5.0,
    ) -> list[dict]:
        """Send a mesh command and collect responses.

        Automatically uses segmented transport for payloads > 11 bytes.
        Returns list of decoded access-layer responses.
        """
        key = self.keys.dev_key if use_dev_key else self.keys.app_key
        akf = 0 if use_dev_key else 1
        aid = 0 if use_dev_key else self.keys.aid

        if key is None:
            print("ERROR: Required key not available")
            return []

        self.responses.clear()
        await self._ensure_subscribed()

        needs_segmented = len(access_payload) > MAX_UNSEG_ACCESS_PAYLOAD

        if needs_segmented:
            mic_len = 4  # szmic=0
            upper_len = len(access_payload) + mic_len
            n_segs = (upper_len + SEG_DATA_SIZE - 1) // SEG_DATA_SIZE
            seq = next_seq(n_segs)
            segments = make_access_segmented(
                key,
                self.our_addr,
                dst,
                seq,
                self.keys.iv_index,
                access_payload,
                akf=akf,
                aid=aid,
            )
            print(f"  Sending {len(segments)} segments (seq {seq}..{seq + len(segments) - 1})...")
            for seg_seq, transport_pdu in segments:
                network_pdu = encrypt_network_pdu(
                    self.keys.enc_key,
                    self.keys.priv_key,
                    self.keys.nid,
                    ctl=0,
                    ttl=4,
                    seq=seg_seq,
                    src=self.our_addr,
                    dst=dst,
                    transport_pdu=transport_pdu,
                    iv_index=self.keys.iv_index,
                )
                proxy_pdu = make_proxy_pdu(network_pdu)
                await self.client.write_gatt_char(
                    MESH_PROXY_DATA_IN,
                    proxy_pdu,
                    response=False,
                )
                await asyncio.sleep(0.05)
            print("  All segments sent")
        else:
            seq = next_seq(1)
            transport_pdu = make_access_unsegmented(
                key,
                self.our_addr,
                dst,
                seq,
                self.keys.iv_index,
                access_payload,
                akf=akf,
                aid=aid,
            )
            network_pdu = encrypt_network_pdu(
                self.keys.enc_key,
                self.keys.priv_key,
                self.keys.nid,
                ctl=0,
                ttl=4,
                seq=seq,
                src=self.our_addr,
                dst=dst,
                transport_pdu=transport_pdu,
                iv_index=self.keys.iv_index,
            )
            proxy_pdu = make_proxy_pdu(network_pdu)
            await self.client.write_gatt_char(
                MESH_PROXY_DATA_IN,
                proxy_pdu,
                response=False,
            )
            print(f"  Sent (seq={seq})")

        print(f"  Waiting {wait_secs}s for response...")
        await asyncio.sleep(wait_secs)
        return list(self.responses)


# ============================================================
# Key Loading
# ============================================================


def load_mesh_keys_from_json(keys_file: Path, our_addr: int) -> tuple["MeshKeys", int]:
    """Load mesh keys from pb_gatt_provision.py output JSON.

    Returns (MeshKeys, our_unicast_address).
    """
    if not keys_file.exists():
        print(f"ERROR: Keys file not found: {keys_file}")
        sys.exit(1)

    data = json.loads(keys_file.read_text())
    keys = MeshKeys(
        net_key_hex=data["net_key"],
        dev_key_hex=data["dev_key"],
        app_key_hex=data.get("app_key"),
        iv_index=data.get("iv_index", 0),
    )
    return keys, our_addr


def load_mesh_keys(target: int) -> tuple["MeshKeys", int]:
    """Load mesh keys from bluetooth-meshd config files.

    Returns (MeshKeys, our_unicast_address).
    """
    # Try pb_gatt_provision.py output first
    json_keys = Path("/tmp/mesh_keys.json")
    if json_keys.exists():
        print(f"Loading keys from {json_keys}")
        return load_mesh_keys_from_json(json_keys, our_addr=0x0001)

    meshd_dir = Path("/var/lib/bluetooth/mesh")
    node_dirs = list(meshd_dir.glob("*/node.json"))
    if not node_dirs:
        print("ERROR: No meshd node config found. Run provisioning first.")
        sys.exit(1)

    node_json = node_dirs[0]
    node_dir = node_json.parent
    config = json.loads(node_json.read_text())

    net_key_hex = config["netKeys"][0]["key"]
    iv_index = config["IVindex"]
    our_addr = int(config["unicastAddress"], 16)

    # Device key for target
    dev_key_file = node_dir / "dev_keys" / f"{target:04x}"
    if not dev_key_file.exists():
        print(f"ERROR: Device key for 0x{target:04X} not found at {dev_key_file}")
        sys.exit(1)
    dev_key_hex = dev_key_file.read_bytes().hex()

    # App key (from meshd app_keys/000 binary file)
    # Format: 4 bytes header + 16 bytes current key + 16 bytes updated key
    app_key_hex = None
    app_key_file = node_dir / "app_keys" / "000"
    if app_key_file.exists():
        raw = app_key_file.read_bytes()
        if len(raw) >= 20:
            app_key_hex = raw[4:20].hex()
            _LOGGER.info("App key loaded (%d bytes file)", len(raw))
        else:
            _LOGGER.warning("App key file too short (%d bytes)", len(raw))

    keys = MeshKeys(net_key_hex, dev_key_hex, app_key_hex, iv_index)
    return keys, our_addr


# ============================================================
# Command Handlers
# ============================================================


async def cmd_appkey_add(session: ProxySession, args: argparse.Namespace) -> list[dict]:
    """Send Config AppKey Add to target (segmented, device key)."""
    print("\n=== Config AppKey Add ===")
    if session.keys.app_key is None:
        print("ERROR: No app key loaded from meshd")
        return []
    access = config_appkey_add(0, 0, session.keys.app_key)
    print(f"  Payload: {len(access)} bytes (will be segmented)")
    return await session.send(args.target, access, use_dev_key=True, wait_secs=args.wait)


async def cmd_bind(session: ProxySession, args: argparse.Namespace) -> list[dict]:
    """Send Config Model App Bind for GenericOnOff Server (device key)."""
    print("\n=== Config Model App Bind ===")
    print(f"  Element: 0x{args.target:04X}, AppKey: 0, Model: 0x1000 (GenericOnOff)")
    access = config_model_app_bind(args.target, 0, 0x1000)
    return await session.send(args.target, access, use_dev_key=True, wait_secs=args.wait)


async def cmd_onoff(
    session: ProxySession,
    args: argparse.Namespace,
    on: bool,
) -> list[dict]:
    """Send Generic OnOff Set (app key)."""
    state_str = "ON" if on else "OFF"
    print(f"\n=== Generic OnOff Set {state_str} ===")
    access = generic_onoff_set(on, tid=1 if on else 2)
    return await session.send(args.target, access, use_dev_key=False, wait_secs=args.wait)


async def cmd_status(session: ProxySession, args: argparse.Namespace) -> list[dict]:
    """Send Generic OnOff Get (app key)."""
    print("\n=== Generic OnOff Get ===")
    access = generic_onoff_get()
    return await session.send(args.target, access, use_dev_key=False, wait_secs=args.wait)


async def cmd_composition(session: ProxySession, args: argparse.Namespace) -> list[dict]:
    """Send Config Composition Data Get (device key)."""
    print("\n=== Config Composition Data Get ===")
    access = config_composition_get()
    return await session.send(args.target, access, use_dev_key=True, wait_secs=args.wait)


async def cmd_setup(session: ProxySession, args: argparse.Namespace) -> None:
    """Run AppKey Add + Model App Bind in one session."""
    print("\n========== SETUP: AppKey Add + Model App Bind ==========")

    # Step 1: AppKey Add
    results = await cmd_appkey_add(session, args)
    appkey_ok = False
    for r in results:
        if r["opcode"] == 0x8003:
            status = r["params"][0]
            # 0x00=Success, 0x06=KeyIndexAlreadyStored (both OK)
            appkey_ok = status in (0x00, 0x06)
            if not appkey_ok:
                print(f"\n  AppKey Add failed (status=0x{status:02X}), aborting")
                return

    if not appkey_ok and not results:
        print("\n  No AppKey Status response — continuing anyway (may already be added)")

    await asyncio.sleep(2)

    # Step 2: Model App Bind
    results = await cmd_bind(session, args)
    for r in results:
        if r["opcode"] == 0x803E:
            status = r["params"][0]
            if status == 0x00:
                print("\n*** SETUP COMPLETE — ready for on/off commands ***")
            else:
                print(f"\n  Model App Bind failed (status=0x{status:02X})")
            return

    if not results:
        print("\n  No Model App Status response — check BLE connection")


# ============================================================
# Main
# ============================================================


async def run(args: argparse.Namespace) -> None:
    """Main entry point."""
    keys, our_addr = load_mesh_keys(args.target)
    print(
        f"Mesh: NID=0x{keys.nid:02X} our=0x{our_addr:04X} "
        f"target=0x{args.target:04X} AID=0x{keys.aid:02X}"
    )
    print(f"App key: {'loaded' if keys.app_key else 'NOT AVAILABLE'}")

    # Validate app key available for model commands
    if args.command in ("on", "off", "status") and keys.app_key is None:
        print("ERROR: App key required for GenericOnOff commands.")
        print("  Run 'appkey-add' and 'bind' first, or run 'setup'.")
        sys.exit(1)

    print(f"\nConnecting to {args.mac}...")
    client = await connect_with_retry(args.mac)
    session = ProxySession(client, keys, our_addr)

    try:
        if args.command == "setup":
            await cmd_setup(session, args)
        elif args.command == "appkey-add":
            await cmd_appkey_add(session, args)
        elif args.command == "bind":
            await cmd_bind(session, args)
        elif args.command == "on":
            await cmd_onoff(session, args, on=True)
        elif args.command == "off":
            await cmd_onoff(session, args, on=False)
        elif args.command == "status":
            await cmd_status(session, args)
        elif args.command == "composition":
            await cmd_composition(session, args)
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()
        print("\nDisconnected.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SIG Mesh GATT Proxy commander",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  %(prog)s setup              # Configure S17 (appkey + bind)
  %(prog)s on                 # Turn plug ON
  %(prog)s off                # Turn plug OFF
  %(prog)s status             # Query on/off state
  %(prog)s composition        # Get device composition data
""",
    )
    parser.add_argument(
        "command",
        choices=["setup", "appkey-add", "bind", "on", "off", "status", "composition"],
        help="Command to send",
    )
    parser.add_argument("--mac", default="DC:23:4F:10:52:C4", help="BLE MAC address (default: S17)")
    parser.add_argument(
        "--target",
        type=lambda x: int(x, 16),
        default=0x00AA,
        help="Target unicast address in hex (default: 00aa)",
    )
    parser.add_argument(
        "--wait", type=float, default=5.0, help="Wait time for response in seconds (default: 5)"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except ConnectionError as exc:
        print(f"\nConnection failed: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\nFATAL: {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
