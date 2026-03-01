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
| Command (notify+write) | 1911 | `...0d1911` | read, write, notify | 1 |
| Command RX | 1912 | `...0d1912` | read, write-without-response, write | 16 |
| Pairing | 1913 | `...0d1913` | read, write-without-response | 16 |
| OTA / Status | 1914 | `...0d1914` | read, write | 20 |

**Key observations:**
- Characteristic 1911 has `notify` — this is the response/data channel
- Characteristic 1912 has `write-without-response` — this is the command TX channel
- Characteristic 1913 is the pairing channel (write mesh name + password here)
- Characteristic 1914 likely status/OTA (20 bytes readable, may contain device info)
- The 1911 properties differ from documentation (notify is on 1911, not 1912)

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

### Key Observation

The `python-awox-mesh-light` project (Telink mesh, same UUID base) uses a
3-step pairing handshake:

1. **Random exchange:** Host sends 8 random bytes to 1913, device responds
   with 8 random bytes on 1911
2. **Session key derivation:** Both sides derive a session key from the
   random values + mesh name + mesh password via AES-ECB
3. **Encrypted credential write:** Mesh credentials encrypted with session
   key, then written to 1913

This explains both failures: the device expected encrypted data, not
plaintext, so it rejected the write and dropped the connection.

---

## 7. Next Steps

Based on the provisioning failure analysis:

1. **Study `python-awox-mesh-light` pairing flow** — Implement the 3-step
   random exchange + session key + encrypted credential handshake
2. **Capture Tuya app pairing** with `sniff.py` — Compare our handshake
   attempt with the official app's GATT writes
3. **Implement crypto module** — AES-ECB key derivation from random values
   + mesh name + password (to be placed in `lib/tuya_ble_mesh/crypto.py`)
4. **Retry provisioning** — With proper encrypted handshake

Key references:
- [python-awox-mesh-light](https://github.com/Leiaz/python-awox-mesh-light)
  — Telink mesh pairing with AES session key
- [retsimx/tlsr8266_mesh](https://github.com/retsimx/tlsr8266_mesh)
  — Reverse-engineered Tuya mesh firmware (Ghidra)
