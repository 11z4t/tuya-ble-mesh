# Protocol Findings — Malmbergs LED Driver 9952126

GATT exploration performed on 2026-03-01 against device DC:23:4D:21:43:A5.

---

## 1. GATT Service Enumeration

3 services discovered after connecting via bleak:

### Service 1: Generic Access Profile (0x1800)

| Characteristic | UUID | Properties |
|----------------|------|------------|
| Device Name | 0x2A00 | read |
| Appearance | 0x2A01 | read |

Standard GAP service. Device Name reads as 12 bytes, Appearance as 2 bytes.

### Service 2: Tuya Custom Service (Telink Base)

**UUID:** `00010203-0405-0607-0809-0a0b0c0d1910`

This is the proprietary Tuya mesh service using the **Telink BLE base UUID**
(`00010203-0405-0607-0809-0a0b0c0dXXXX`) instead of the standard BT SIG base.
The 16-bit suffix pattern (1910-1914) matches documented Tuya characteristics.

| Role | UUID Suffix | Full UUID | Properties | Bytes Read |
|------|-------------|-----------|------------|------------|
| Status / Notify | 1911 | `...0d1911` | read, write, notify | 1 |
| Command TX | 1912 | `...0d1912` | read, write-without-response, write | 16 |
| OTA | 1913 | `...0d1913` | read, write-without-response | 16 |
| Pairing | 1914 | `...0d1914` | read, write | 20 |

**Key observations:**
- Characteristic 1911 has `notify` — this is the status/response channel
- Characteristic 1912 has `write-without-response` — this is the command TX channel
- Characteristic 1914 is the **pairing** channel (write pair packet, read response)
- Characteristic 1913 is OTA firmware update (write-without-response for data chunks)

**CORRECTION (2026-03-01):** Phase 1 initially labeled 1913 as "Pairing" and
1914 as "OTA/Status". Reference analysis of `python-awox-mesh-light` (same
Telink chipset) confirmed the roles are swapped: **1914 = Pairing, 1913 = OTA**.
Evidence: pairing requires write-with-response + read (matching 1914 properties),
while OTA uses write-without-response for streaming chunks (matching 1913).

### Service 3: Device Information Service (0x180A)

| Characteristic | UUID | Value | Bytes |
|----------------|------|-------|-------|
| Manufacturer Name (0x2A29) | `00002a29-...` | `out_of_mesh ` | 12 |
| Model Number (0x2A24) | `00002a24-...` | `model id 123 ` | 13 |
| Firmware Revision (0x2A26) | `00002a26-...` | `1.6         ` | 12 |
| Hardware Revision (0x2A27) | `00002a27-...` | `""""` | 4 |

**Notes:**
- Manufacturer Name is `out_of_mesh` (the mesh name, not actual manufacturer)
- Model Number is `model id 123` (Tuya product ID, not Malmbergs model)
- Firmware version is `1.6`
- Hardware Revision is 4 bytes of `"` characters (0x22) — likely unset/placeholder
- Software Revision (0x2A28) is NOT present on this device

---

## 2. Mesh Variant Determination

**Result: Tuya Proprietary Mesh (Telink-based)**

Evidence:
- No SIG Mesh Provisioning Service (0x1827) present
- No SIG Mesh Proxy Service (0x1828) present
- Tuya custom service present with Telink UUID base
- Characteristic pattern matches documented Tuya proprietary mesh (1910-1914)
- Device advertises as `out_of_mesh` (proprietary mesh default name)
- No 0xFE07 service UUID in advertising (confirmed: device advertises via
  manufacturer data with company ID 0x43A5, not standard Tuya service data)

**Implication:** Provisioning uses mesh name + password authentication,
not SIG Mesh PB-GATT/PB-ADV flow. Default credentials: name `out_of_mesh`,
password `123456`.

---

## 3. Advertising Data

From `scan.py` output:
- **Name:** `out_of_mesh`
- **MAC:** `DC:23:4D:21:43:A5` (public address)
- **RSSI:** -36 to -53 dBm (strong signal)
- **Manufacturer ID:** `0x43A5` (derived from MAC bytes)
- **Manufacturer Data:** `214d` (2 bytes)
- **Service UUIDs:** None advertised (no 0xFE07 in advertisement)
- **Service Data:** None

**Note:** The 0xFE07 UUID was not in this device's advertisements, contradicting
the general Tuya detection logic. Detection relied on name pattern matching
(`out_of_mesh`). Other Tuya devices on the same network do advertise 0xFE07.

---

## 4. Notification Activity

No unsolicited notifications received during a 5-second listen period.
This is expected for an unprovisioned device — it should start sending
data only after a successful pairing/provisioning handshake.

---

## 5. Connection Behavior

- First connection attempt timed out (20s) — device may need to be freshly
  power-cycled before accepting GATT connections
- Second attempt succeeded after power cycle via Shelly
- Connection MTU: 23 (default, no MTU negotiation observed)
- All readable characteristics were accessible without authentication

---

## 6. Provisioning Attempt (2026-03-01)

Attempted local provisioning using `scripts/provision_attempt.py` with default
mesh credentials (name `out_of_mesh`, password `123456`).

### Procedure

1. Factory reset device via Shelly power cycle
2. Scan and connect (succeeded after power cycle)
3. Read characteristic state before write
4. Write pairing data (32 bytes) to characteristic 1913
5. Subscribe to notifications on 1911
6. Wait for response

### Results

| Step | Outcome |
|------|---------|
| Scan | Found `out_of_mesh` at RSSI -43 dBm |
| Connect | Succeeded (MTU: 23) |
| Read 1911 | 1 byte (read succeeded) |
| Read 1913 | 16 bytes (read succeeded) |
| Read 1914 | 20 bytes (read succeeded) |
| Write 1913 | **Failed** — `BleakDBusError` |
| Subscribe 1911 | **Failed** — `EOFError` (device disconnected) |
| Notifications | 0 received |

### Failure Analysis

**Write failure (1913):** The pairing characteristic has `write-without-response`
property. The 32-byte payload (16-byte name + 16-byte password, null-padded)
exceeded the characteristic's value size or MTU constraints. BlueZ rejected
the write via D-Bus.

**Subscribe failure (1911):** The device dropped the BLE connection after
the failed write attempt. The `start_notify` call encountered an `EOFError`
because the D-Bus connection to BlueZ reported the device as disconnected.

### Hypotheses

1. **Payload format incorrect:** The credentials may need AES encryption
   before writing (reference: `python-awox-mesh-light` encrypts with a
   session key derived from random + device data).
2. **Payload too large:** The characteristic reads as 16 bytes, suggesting
   the write should also be 16 bytes. The name and password may be written
   separately or combined differently.
3. **Missing handshake:** The Telink mesh protocol may require a multi-step
   handshake (random exchange → session key → encrypted credentials) rather
   than a single plaintext write.
4. **Cloud dependency:** The device may require a cloud-derived token for
   provisioning. This is the highest-risk scenario.

### Root Cause

**We wrote to the wrong characteristic.** The provisioning attempt targeted
1913 (OTA), not 1914 (Pairing). Additionally, the payload was unencrypted
plaintext, but the protocol requires an encrypted 3-step handshake. See
section 7 below.

---

## 7. Reference Analysis — python-awox-mesh-light

Studied the [python-awox-mesh-light](https://github.com/fsaris/python-awox-mesh-light)
repository (MIT license, Copyright 2017 Leiaz). This project implements the
same Telink BLE mesh protocol for AwoX/Eglo lights. The protocol is identical
at the GATT and crypto layer — same UUID base, same AES operations, same
packet formats.

### 7.1 Characteristic Role Mapping (Corrected)

| UUID Suffix | awox Constant | Role | Properties |
|-------------|---------------|------|------------|
| 1911 | `STATUS_CHAR_UUID` | Status/notify (receive data) | notify, write (`0x01` to enable) |
| 1912 | `COMMAND_CHAR_UUID` | Command TX (send encrypted commands) | write-without-response |
| 1913 | `OTA_CHAR_UUID` | OTA firmware update | write-without-response |
| 1914 | `PAIR_CHAR_UUID` | Pairing handshake | write + read |

### 7.2 Three-Step Pairing Handshake

```
Controller                              Device ("out_of_mesh")
    |                                        |
    |  Step 1: Pair packet to 1914           |
    |  [0x0C][8B random][8B encrypted proof] |
    |--------------------------------------->|
    |                                        |
    |  Step 2: Read response from 1914       |
    |  [0x0D][8B device random] = success    |
    |  [0x0E] = auth failure                 |
    |<---------------------------------------|
    |                                        |
    |  [Derive session key locally]          |
    |  AES-ECB(key=name^pass, pt=rand||rand) |
    |                                        |
    |  Enable notifications on 1911:         |
    |  write 0x01 to 1911                    |
    |--------------------------------------->|
    |                                        |
    |  Step 3: Set mesh credentials via 1914 |
    |  [0x04][encrypted new name]            |
    |  [0x05][encrypted new password]        |
    |  [0x06][encrypted new LTK]             |
    |--------------------------------------->|
    |                                        |
    |  Read confirmation from 1914           |
    |  [0x07] = success                      |
    |<---------------------------------------|
```

### 7.3 Pair Packet Construction (Step 1)

```
make_pair_packet(mesh_name, mesh_password, session_random):
  1. Pad name to 16 bytes (null-padded)
  2. Pad password to 16 bytes (null-padded)
  3. XOR name with password → name_pass (16 bytes)
  4. Pad session_random to 16 bytes (used as AES key)
  5. Encrypt: AES-ECB(key=session_random_padded, plaintext=name_pass)
  6. Packet: [0x0C] + session_random[0:8] + encrypted[0:8]
  Total: 17 bytes
```

### 7.4 Session Key Derivation (Step 2)

```
make_session_key(mesh_name, mesh_password, client_random, device_random):
  1. Concatenate: client_random[8] + device_random[8] → combined[16]
  2. Pad name to 16 bytes, pad password to 16 bytes
  3. XOR name with password → name_pass (16 bytes)
  4. Encrypt: AES-ECB(key=name_pass, plaintext=combined)
  Result: 16-byte session key
```

### 7.5 AES-ECB with Telink Byte Reversal

All AES operations use a Telink-specific convention: **both key and plaintext
are byte-reversed before AES-ECB, and the ciphertext is byte-reversed after.**
This is characteristic of Telink semiconductor's little-endian AES implementation.

```python
# Telink AES-ECB (from python-awox-mesh-light)
def encrypt(key, value):
    k = bytearray(key); k.reverse()
    v = bytearray(value.ljust(16, b'\x00')); v.reverse()
    cipher = AES.new(bytes(k), AES.MODE_ECB)
    result = bytearray(cipher.encrypt(bytes(v)))
    result.reverse()
    return result
```

This is NOT standard AES-ECB. The byte reversal is required for
interoperability with Telink BLE mesh firmware.

### 7.6 Command Packet Format (20 bytes)

After pairing, encrypted commands are sent to characteristic 1912:

```
[3B seq][2B MAC][15B encrypted payload]

Unencrypted payload (15 bytes):
[2B dest_id LE][1B command][0x60][0x01][data...][padding to 15B]
```

Nonce construction (8 bytes):
```
[4B reversed MAC prefix][0x01][3B random sequence]
```

Encryption uses manual CTR mode + CBC-MAC (effectively AES-CCM) built on
top of the Telink AES-ECB primitive. The CBC-MAC is truncated to 2 bytes.

### 7.7 Command Codes (Telink Mesh)

| Command | Code | Data | Description |
|---------|------|------|-------------|
| Power | `0xD0` | `0x01`/`0x00` | On / Off |
| Color | `0xE2` | `0x04, R, G, B` | Set RGB color |
| White brightness | `0xF1` | 1 byte (1–0x7F) | Brightness (1–127) |
| White temperature | `0xF0` | 1 byte (0–0x7F) | Color temp (0–127) |
| Color brightness | `0xF2` | 1 byte (0x0A–0x64) | Color brightness |
| Mesh address | `0xE0` | 2B LE uint16 | Set mesh ID |
| Mesh reset | `0xE3` | `0x00` | Factory reset |
| Mesh group | `0xD7` | 3 bytes | Set mesh group |
| Light mode | `0x33` | 1 byte | Set mode |

These are **Telink mesh** command codes, NOT standard Tuya DP commands.
The Malmbergs device may use these directly, or Tuya may layer its own
DP protocol on top. To be verified during provisioning.

### 7.8 Status Message Format

Decrypted status notifications (from 1911) contain:

| Offset | Size | Field |
|--------|------|-------|
| 3 | 1B | Mesh ID |
| 12 | 1B | Mode (1=white-on, 2=white-off, 3=color-on) |
| 13 | 1B | White brightness (1–0x7F) |
| 14 | 1B | White temperature (0–0x7F) |
| 15 | 1B | Color brightness |
| 16–18 | 3B | R, G, B |

### 7.9 Key Differences: AwoX vs Tuya/Malmbergs

| Aspect | AwoX/Eglo | Tuya/Malmbergs |
|--------|-----------|----------------|
| Default mesh name | `"unpaired"` | `"out_of_mesh"` |
| Default password | `"1234"` | `"123456"` |
| UUID base | Telink (identical) | Telink (identical) |
| AES convention | Byte-reversed ECB | Byte-reversed ECB (assumed) |
| Command codes | Telink standard | Telink standard (assumed) |
| Cloud dependency | None | Unknown (likely none) |
| DP layer | None (raw Telink) | Possibly Tuya DP on top |

### 7.10 License

MIT license (Copyright 2017 Leiaz). Safe to use as reference. Our
implementation will be independently written based on the protocol
understanding gained from this analysis.

---

## 8. Next Steps

1. **Fix UUID mapping** in `const.py`: swap 1913 (OTA) and 1914 (Pairing)
2. **Implement `crypto.py`**: Telink AES-ECB with byte reversal, session
   key derivation, CTR + CBC-MAC
3. **Implement `protocol.py`**: Pair packet, command packet, status decode
4. **Implement `provisioner.py`**: 3-step handshake targeting char 1914
5. **Retry provisioning** with corrected characteristic and encrypted packets

Key references:
- [python-awox-mesh-light](https://github.com/fsaris/python-awox-mesh-light)
  — MIT license, Telink mesh pairing + commands
- [retsimx/tlsr8266_mesh](https://github.com/retsimx/tlsr8266_mesh)
  — Reverse-engineered Tuya mesh firmware (Ghidra)
