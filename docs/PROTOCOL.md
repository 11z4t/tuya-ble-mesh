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

## 6. Next Steps (Provisioning)

Based on the confirmed proprietary Tuya mesh variant:

1. Write default mesh name (`out_of_mesh`) + password (`123456`) to
   pairing characteristic (1913)
2. Subscribe to command notify characteristic (1911) for responses
3. Upon success, assign new mesh credentials
4. Monitor with `sniff.py` in parallel for packet capture

Key reference: [python-awox-mesh-light](https://github.com/Leiaz/python-awox-mesh-light)
uses a similar Telink-based proprietary mesh protocol with name/password auth.
