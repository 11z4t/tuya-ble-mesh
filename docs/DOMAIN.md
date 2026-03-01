# Domain Knowledge — Tuya BLE Mesh

This document captures protocol knowledge for the Tuya BLE Mesh ecosystem
as it applies to Malmbergs BT Smart devices. It is a living document —
update it as reverse engineering and testing reveal new facts.

Confidence levels: **Confirmed** (verified on our hardware), **Expected**
(from Tuya docs, not yet verified), **Unknown** (requires investigation).

---

## 1. Tuya Mesh Variants

Tuya ships two distinct BLE mesh implementations. Identifying which one
our device uses is a Phase 1 priority.

### Tuya Mesh (Proprietary)

- Predates the Bluetooth SIG Mesh standard
- Authentication via mesh name + password
- Default unprovisioned credentials: name `out_of_mesh`, password `123456`
- Simpler provisioning: connect, authenticate, assign new mesh name/password
- Used by older Tuya modules (TYBT1, TLSR8266-based)

### Tuya SIG Mesh

- Based on the Bluetooth SIG Mesh standard (published July 2017)
- Uses standard PB-ADV and PB-GATT provisioning bearers
- Standard AES-128-CCM encryption with NetKey / AppKey / DevKey hierarchy
- Tuya layers its own Data Point (DP) model on top of standard mesh transport
- Uses both standard SIG Mesh models and a Tuya vendor model
- Tuya's direction since ~2020; newer devices likely use this variant

### How to Determine the Variant

1. Scan the device's GATT services after connecting:
   - `0x1827` (Mesh Provisioning Service) → SIG Mesh
   - `0x1828` (Mesh Proxy Service) → SIG Mesh
   - Custom Tuya service UUIDs → Proprietary Tuya Mesh
2. Check advertising data structure (see section 3)
3. Attempt standard SIG provisioning and observe response

**Status: Confirmed** — GATT enumeration (2026-03-01) shows Tuya Proprietary
Mesh with Telink-based UUIDs. No SIG Mesh services (0x1827, 0x1828) found.
See `docs/PROTOCOL.md` for full findings.

---

## 2. Target Device

### Malmbergs LED Driver 9952126

| Property | Value | Confidence |
|----------|-------|------------|
| Product name | Malmbergs Smart LED Driver | Confirmed |
| Model | 9952126 | Confirmed |
| MAC address | DC:23:4D:21:43:A5 | Confirmed |
| Tuya category | `dj` (light) | Expected |
| Mesh variant | Tuya Proprietary (Telink-based) | Confirmed |
| Mesh category | `0x1012` (CW) or `0x1015` (RGBCW) | Unknown |
| Capabilities | Dimming, color temperature | Expected |
| Chipset | Telink (confirmed via UUID base) | Confirmed |
| Firmware | 1.6 | Confirmed |
| Tuya Product ID | "model id 123" | Confirmed |

### Device States

| State | Advertising Name | Meaning | Confidence |
|-------|-----------------|---------|------------|
| Unprovisioned | `out_of_mesh` | Factory reset, ready for provisioning | Confirmed |
| Provisioned | `tymesh` + numeric suffix | Paired to a mesh network | Expected |

### Factory Reset Procedure

Power cycle the device 3–5 times in rapid succession. After reset, the
device advertises as `out_of_mesh`. **Confirmed** — implemented in
`lib/tuya_ble_mesh/power.py` via Shelly Plug S.

Tuya documentation states lights reset by "turn on and off consecutively
three times." Our implementation uses 5 cycles with 1-second intervals
as a safety margin.

---

## 3. BLE Advertising

### Detection Criteria

Implemented in `scripts/scan.py`:

```python
TUYA_SERVICE_UUID = "0000fe07-0000-1000-8000-00805f9b34fb"
TUYA_MESH_NAMES = ["out_of_mesh", "tymesh"]
```

A device is identified as Tuya BLE Mesh if:
1. Its name starts with `out_of_mesh` or `tymesh` (case-insensitive), OR
2. It advertises service UUID `0xFE07`

**Note on 0xFE07:** This UUID appears in scan results from the device.
Tuya's officially assigned 16-bit UUID is `0xFD50` (for Tuya BLE, not mesh).
`0xFE07` may be Telink-specific or legacy. Verify via packet capture.
**Status: Confirmed** in scan output, origin uncertain.

### SIG Mesh Standard Service UUIDs

| UUID | Service | Role |
|------|---------|------|
| `0x1827` | Mesh Provisioning Service | Used during provisioning (PB-GATT) |
| `0x1828` | Mesh Proxy Service | Used for post-provisioning communication |

Characteristics for provisioning service:
- `0x2ADB` — Data In (Write Without Response)
- `0x2ADC` — Data Out (Notify)

Characteristics for proxy service:
- `0x2ADD` — Data In (Write Without Response)
- `0x2ADE` — Data Out (Notify)

### Tuya Custom GATT Service (Proprietary Variant)

The device exposes a proprietary Tuya service using the **Telink BLE base UUID**
(`00010203-0405-0607-0809-0a0b0c0dXXXX`) instead of the standard BT SIG base:

| UUID Suffix | Full UUID | Role | Properties |
|-------------|-----------|------|------------|
| `1910` | `00010203-...-0d1910` | Custom Tuya Service | — |
| `1911` | `00010203-...-0d1911` | Command (notify channel) | read, write, notify |
| `1912` | `00010203-...-0d1912` | Command RX | read, write, write-without-response |
| `1913` | `00010203-...-0d1913` | Pairing | read, write-without-response |
| `1914` | `00010203-...-0d1914` | Status / OTA | read, write |

**Note:** The notify capability is on characteristic 1911 (not 1912 as some
documentation suggests). This means 1911 is the response/data channel.

**Status: Confirmed** — Verified via GATT enumeration on 2026-03-01.

### Device UUID Structure (SIG Mesh, 16 bytes)

| Offset | Length | Content |
|--------|--------|---------|
| 0 | 6 bytes | Device MAC address |
| 6 | 2 bytes | Mesh category (see below) |
| 8 | 8 bytes | Product ID (PID) |

### Mesh Category Byte (SIG Mesh)

Bits 7–4 encode the product category:

| Value | Category |
|-------|----------|
| 0x01 | Lights |
| 0x02 | Electrical (switches, sockets) |
| 0x04 | Sensors |
| 0x05 | Remotes |
| 0x06 | Wireless switches |

Bits 3–0 encode the product type within lights:

| Mesh Category | Light Type |
|---------------|------------|
| `0x1011` | Cool white (C) |
| `0x1012` | Cool + warm white (CW) |
| `0x1013` | RGB |
| `0x1014` | RGBC |
| `0x1015` | RGBCW |

---

## 4. Provisioning

### SIG Mesh Provisioning Flow

1. Device broadcasts unprovisioned device beacons (PB-ADV or PB-GATT)
2. Provisioner discovers device and initiates provisioning
3. ECDH key exchange establishes a shared secret
4. Session key derived from ECDH shared secret via AES-CMAC
5. Provisioner sends provisioning data over AES-CCM encrypted link:
   - Network Key (NetKey)
   - Device Key (DevKey)
   - IV Index
   - Unicast Address
6. Configuration stage: AppKey distribution, model-to-AppKey bindings
7. Device transitions from `out_of_mesh` to `tymesh*`

Timeout: 30 seconds standard, 90 seconds for bulk provisioning.

### Proprietary Tuya Mesh Provisioning Flow

1. Scan for devices with name `out_of_mesh`
2. Connect and authenticate using mesh name (`out_of_mesh`) + password (`123456`)
3. Assign new mesh name and password to join the target network
4. Device restarts with new identity

### Critical Unknown: Cloud Dependency

**Does local provisioning require a Tuya Cloud token?**

This is the highest-risk unknown. Possible outcomes:

| Scenario | Impact | Mitigation |
|----------|--------|------------|
| Fully local provisioning | Ideal — no cloud needed | Implement standard SIG provisioner |
| Cloud token required for key exchange | Blocker for cloud-free goal | MITM Tuya app to extract keys |
| Keys derived locally during ECDH | Good — keys stay local | Capture and store in 1Password |

**Status: Unknown** — Phase 1 priority.

---

## 5. Encryption

### SIG Mesh Encryption (AES-128-CCM)

The Bluetooth SIG Mesh standard mandates AES-128 in CCM mode
(Counter with CBC-MAC) at two layers:

#### Key Hierarchy

| Key | Size | Scope | Purpose |
|-----|------|-------|---------|
| Network Key (NetKey) | 128-bit | Entire network | Encrypts network layer |
| Application Key (AppKey) | 128-bit | Per application | Encrypts application payload |
| Device Key (DevKey) | 128-bit | Per device (unique) | Configuration messages only |

- NetKey is shared across all nodes in the mesh network
- AppKey is bound to specific models (e.g., Light Lightness Server)
- DevKey is unique per device, used only during configuration

#### Network Layer Encryption

- Key: derived from NetKey via AES-CMAC (k2 function)
- Nonce: source address + sequence number + IV Index
- Encrypts: destination address + transport PDU
- MIC: 32-bit or 64-bit

#### Application Layer Encryption

- Key: AppKey (or DevKey for config messages)
- Nonce: source unicast address + sequence number + IV Index
- Encrypts: access layer payload (DP data)

#### Replay Protection

- Each node caches (source address → latest sequence number)
- Messages with SEQ ≤ cached value are discarded
- IV Index (32-bit, network-wide) increments when 24-bit SEQ space exhausts

### Proprietary Tuya Mesh Encryption

Less documented. Known to use AES, likely simpler key management
based on mesh name/password derivation.

---

## 6. Data Points (DPs)

Tuya devices expose functionality through numbered Data Points (DPs).
Each DP has an ID, type, and value range.

### DP Data Types

| Type Code | Name | Size | Range |
|-----------|------|------|-------|
| 0x00 | Raw | N bytes | Arbitrary |
| 0x01 | Boolean | 1 byte | 0x00 or 0x01 |
| 0x02 | Value | 4 bytes | Big-endian integer |
| 0x03 | String | N bytes | UTF-8 |
| 0x04 | Enum | 1 byte | 0x00–0xFF |
| 0x05 | Bitmap | 4 bytes | Big-endian bitmask |

### DP Wire Format (per segment)

| Field | Size | Description |
|-------|------|-------------|
| dpid | 1 byte | DP identifier |
| type | 1 byte | Data type code (see above) |
| len | 1 byte | Data length |
| data | N bytes | Big-endian value |

### Expected Light DPs (category `dj`)

| DP ID | Function | Type | Range | HA Mapping | Status |
|-------|----------|------|-------|------------|--------|
| 1 | Power on/off | Boolean | true/false | `light.is_on` | Expected |
| 2 | Mode | Enum | 0=white, 1=color, 2=scene, 3=music | Attribute | Expected |
| 3 | Brightness | Value | 10–1000 | `light.brightness` (map to 0–255) | Expected |
| 4 | Color temperature | Value | 0–1000 | `light.color_temp_kelvin` (map to mireds) | Expected |
| 5 | Color (HSV) | String | "HHHHSSSSSVVVV" hex | `light.hs_color` | Expected |
| 6 | Scene data | String | Compressed 34-byte | Not mapped | Expected |
| 8 | Music sync | Raw | Compressed single packet | Not mapped | Expected |

**Status: Expected** — Standard Tuya lighting DPs. Must be verified against
the actual Malmbergs LED Driver 9952126, which may use a subset (likely
DPs 1, 3, 4 for a CW driver without RGB).

---

## 7. Mesh Command Protocol

### SIG Mesh Models Used by Tuya Lights

| Model | Model ID | DPs Handled | Standard? |
|-------|----------|-------------|-----------|
| Generic OnOff Server | 0x1000 | DP 1 (power) | Yes |
| Light Lightness Server | 0x1300 | DP 3 (brightness) | Yes |
| Light CTL Server | 0x1306 | DP 4 (color temp) | Yes |
| Light HSL Server | 0x1307 | DP 5 (color) | Yes |
| Tuya Vendor Server | 0x07D00004 | DPs 2, 6, 8 | No (Tuya CID 0x07D0) |

Standard DPs (1, 3, 4, 5) use SIG Mesh model opcodes.
Non-standard DPs (2, 6, 8) use the Tuya vendor model.

### Tuya Vendor Model Opcodes

| Operation | Opcode | Direction |
|-----------|--------|-----------|
| Write (acknowledged) | `0xC9D007` | Client → Server |
| Write (unacknowledged) | `0xCAD007` | Client → Server |
| Status (reserved) | `0xCBD007` | — |
| Read (query) | `0xCCD007` | Client → Server |
| Data (response/report) | `0xCDD007` | Server → Client |

### Vendor Model Message Frame

| Field | Size | Description |
|-------|------|-------------|
| Command | 1 byte | 0x01 = DP data, 0x02 = timestamp sync |
| Data length | 1 byte | Payload length |
| Data | N bytes | DP payload (see DP wire format above) |

---

## 8. Device Categories

Tuya product category codes relevant to this project:

### Lighting

| Code | Device Type |
|------|-------------|
| `dj` | Light (general) — **our device** |
| `dd` | Light strip |
| `dc` | Light string |
| `xdd` | Ceiling light |
| `fsd` | Ceiling fan light |
| `tgq` | Dimmer |
| `tgkg` | Dimmer switch |

### Electrical

| Code | Device Type |
|------|-------------|
| `kg` | Switch |
| `cz` | Socket |
| `pc` | Power strip |
| `dlq` | Circuit breaker |

### Sensors

| Code | Device Type |
|------|-------------|
| `wsdcg` | Temperature / humidity sensor |
| `mcs` | Door / window sensor |
| `pir` | PIR motion sensor |
| `hps` | Human presence sensor |

---

## 9. Reference Projects

Open-source projects relevant to understanding Tuya BLE Mesh:

| Project | Relevance |
|---------|-----------|
| [retsimx/tlsr8266_mesh](https://github.com/retsimx/tlsr8266_mesh) | Rust firmware replacement for Tuya BLE mesh lights. Reverse-engineered via Ghidra. Most detailed low-level protocol reference. |
| [dominikberse/homeassistant-bluetooth-mesh](https://github.com/dominikberse/homeassistant-bluetooth-mesh) | HA integration for SIG Mesh via BlueZ + `bluetooth_mesh` library. Closest architecture to our goal. |
| [PlusPlus-ua/ha_tuya_ble](https://github.com/PlusPlus-ua/ha_tuya_ble) | Tuya BLE integration for HA. Requires cloud credentials for initial key extraction. |
| [fsaris/home-assistant-awox](https://github.com/fsaris/home-assistant-awox) | AwoX/Eglo BLE mesh lights for HA. Telink mesh variant (similar but not identical to Tuya). |
| [Leiaz/python-awox-mesh-light](https://github.com/Leiaz/python-awox-mesh-light) | Python library for Telink mesh. Good reference for proprietary mesh name/password auth. |
| [make-all/tuya-local](https://github.com/make-all/tuya-local) | Local Tuya control for HA (WiFi). Good reference for DP model and device profiles. |

### Key Python Libraries

| Library | Use |
|---------|-----|
| `bleak` | BLE GATT client (scanning, connecting, read/write characteristics) |
| `bluetooth_mesh` | Python SIG Mesh protocol implementation |
| `cryptography` | AES-CCM, ECDH, key derivation |

---

## 10. Phase 1 Investigation Plan

These unknowns must be resolved before protocol implementation can begin:

| # | Question | Method | Risk |
|---|----------|--------|------|
| 1 | SIG Mesh or proprietary Tuya Mesh? | GATT service enumeration after connecting | Low |
| 2 | Does provisioning require cloud token? | Attempt local provisioning, analyze failure | High |
| 3 | Are encryption keys cloud-derived? | Analyze provisioning key exchange | High |
| 4 | Exact GATT services and characteristics? | `bleak` service discovery | Low |
| 5 | Which DPs does this device support? | Send DP queries after provisioning | Low |
| 6 | What is the advertising data format? | Passive sniff + decode | Low |
| 7 | Mesh category bytes for this device? | Parse advertising data | Low |
| 8 | Chipset and firmware version? | Read Device Information Service (0x180A) | Low |

### Investigation Tools

- `scripts/scan.py` — BLE discovery, advertising data capture
- `scripts/sniff.py` — Passive packet capture (nRF Sniffer)
- `bleak` REPL — Interactive GATT exploration
- Wireshark/tshark — Packet analysis with BLE dissector

---

## 11. Tuya Developer Resources

- [BLE Mesh Common Solution](https://developer.tuya.com/en/docs/iot/hardware?id=K95ykh7lc390c)
- [BLE Mesh Light Protocol](https://developer.tuya.com/en/docs/iot/hardware?id=K9pieafepp3q7)
- [Vendor Model Pass-Through](https://developer.tuya.com/en/docs/iot-device-dev/tuya-sigmesh-device-vendor-model-access-standard?id=K9pikwhoo3gux)
- [SIG Mesh App SDK](https://developer.tuya.com/en/docs/app-development/sigmesh?id=Ka5vdjp2tlb23)
- [Tuya Mesh App SDK](https://developer.tuya.com/en/docs/app-development/mesh?id=Ka5vdjp3ikagz)
- [Device Pairing (TuyaOS)](https://developer.tuya.com/en/docs/iot-device-dev/bluetooth_software_map_mesh_provision?id=Kd5wkuunhsjtq)
- [Device Reset (TuyaOS)](https://developer.tuya.com/en/docs/iot-device-dev/bluetooth_software_map_mesh_reset?id=Kd5wkznhwupfc)
- [Light Standard Instructions](https://developer.tuya.com/en/docs/iot/dj?id=K9i5ql3v98hn3)
