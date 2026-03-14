# Code Review Findings — Security & Functionality Hardening

**Reviewed by:** Claude Opus 4.6 (security + functionality agents)
**Date:** 2026-03-07

## CRITICAL — Must Fix

### C1. `/tmp/mesh_keys.json` — World-readable key file
**File:** `config_flow.py:54-75`
**Fix:** Remove `_MESH_KEYS_PATH` and `_load_mesh_key_defaults()` entirely. Generate random keys in the config flow instead. Never read keys from filesystem.

### C2. SIG Mesh Sequence Number Overflow — Nonce Reuse
**File:** `lib/tuya_ble_mesh/sig_mesh_device.py:444-447`
**Fix:** Add 24-bit wraparound check. When seq > 0xFFFFFF, raise an error or increment IV Index. Match the Telink side (`connection.py:146`) which correctly wraps at `_MAX_SEQUENCE`.
```python
async def _next_seq(self) -> int:
    async with self._seq_lock:
        if self._seq > 0xFFFFFF:
            raise SIGMeshError("Sequence number exhausted — reconnect required")
        seq = self._seq
        self._seq += 1
        return seq
```

### C3. Bluetooth Discovery Missing `_abort_if_unique_id_configured()`
**File:** `config_flow.py:159`
**Fix:** Add `self._abort_if_unique_id_configured()` after `await self.async_set_unique_id(address)` in `async_step_bluetooth`.

## HIGH — Should Fix

### H1. SIG Mesh Keys Not Zeroed on Disconnect
**File:** `lib/tuya_ble_mesh/sig_mesh_device.py:319-330`
**Fix:** Zero-fill key material before setting to None (like Telink side in `connection.py:304-308`):
```python
if self._keys:
    # Zero-fill before clearing
    if self._keys.enc_key:
        self._keys = self._keys._replace(enc_key=b"\x00"*16, priv_key=b"\x00"*16, ...)
    self._keys = None
```

### H2. Bridge HTTP — No Auth, Plaintext, Header Injection
**File:** `lib/tuya_ble_mesh/sig_mesh_bridge.py`
**Fix:** 
- Validate `bridge_host` (reject CRLF characters)
- Add `await writer.wait_closed()` after `writer.close()`
- Document that bridge should be on trusted network only

### H3. RSSI Polling Bypasses HA Bluetooth Stack
**File:** `coordinator.py:406-425`
**Fix:** Use `async_ble_device_from_address()` for RSSI instead of raw BleakScanner. Or remove RSSI loop entirely — HA bluetooth integration tracks RSSI already.

### H4. Telink Notification Path Broken
**File:** `__init__.py` (Telink device setup), `device.py`
**Fix:** Wire `MeshDevice._handle_notification` to `BLEConnection` notification handler. Without this, Telink devices never receive status updates in HA.

### H5. `subprocess` MAC Validation
**File:** `connection.py:279`, `sig_mesh_device.py:731`
**Fix:** Validate MAC format in `BLEConnection.__init__()` constructor (not just config_flow):
```python
import re
if not re.match(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", address.upper()):
    raise ValueError(f"Invalid MAC: {address}")
```

### H6. `writer.wait_closed()` Missing Everywhere
**File:** `sig_mesh_bridge.py:245,275,519,549`, `config_flow.py:126`
**Fix:** Add `await writer.wait_closed()` after every `writer.close()`.

## MEDIUM — Nice to Fix

### M1. No Replay Protection on Telink Protocol
**File:** `protocol.py:222-267`
**Fix:** Track last-seen sequence per source address, reject replays.

### M2. Unbounded Segment Reassembly Buffer
**File:** `sig_mesh_device.py:537-582`
**Fix:** Add max buffer count (e.g., 32). Drop oldest when exceeded.

### M3. Proxy PDU SAR Not Implemented
**File:** `sig_mesh_device.py:500`
**Fix:** Handle SAR first/continuation/last in `_on_notify`. Reassemble before network decryption.

### M4. `sys.path` Mutation
**File:** `__init__.py:47-52`
**Fix:** Use relative imports or proper package installation instead of `sys.path.insert`.

### M5. `asyncio.get_event_loop()` Deprecated
**File:** `device.py:315`
**Fix:** Use `asyncio.get_running_loop().create_future()`.

### M6. Transition Task Exception Lost
**File:** `light.py:248-251`
**Fix:** Add `task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)`.

### M7. `ConnectionError` Shadows Built-in
**File:** `exceptions.py:15`
**Fix:** Rename to `MeshConnectionError`.

### M8. Config Flow `_test_bridge` Uses Raw Sockets
**File:** `config_flow.py:97-129`
**Fix:** Use `aiohttp` instead of raw `asyncio.open_connection`.

## Positive Observations
- Consistent `[REDACTED]` logging — keys never logged
- `hmac.compare_digest` for constant-time MAC comparison
- Session key zeroing on Telink disconnect
- `yaml.safe_load` for profiles
- `subprocess_exec` (not `shell=True`)
- `os.urandom` for CSPRNG
- Well-documented AES-ECB rationale
- SIG Mesh crypto uses `cryptography` library AESCCM (not hand-rolled)
