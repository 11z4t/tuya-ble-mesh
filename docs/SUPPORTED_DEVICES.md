# Supported Devices

This integration works with **two types** of Tuya BLE Mesh devices:

1. **Telink Proprietary Mesh** — Devices using the Telink firmware stack (service UUID `fe07`, GATT UUID `00010203-0405-0607-0809-0a0b0c0dXXXX`)
2. **SIG Mesh (Bluetooth Mesh)** — Standard Bluetooth Mesh devices (GATT services `0x1827` Provisioning, `0x1828` Proxy)

Both protocols are fully supported and can coexist on the same Home Assistant instance.

## Tested Devices

| Brand | Model | Product | Mesh Type | Vendor ID | Features | Status |
|-------|-------|---------|-----------|-----------|----------|--------|
| Malmbergs BT Smart | 9952126 | LED Driver | Telink Proprietary | `0x1001` | Power, brightness, color temp | ✅ Working |
| Malmbergs BT Smart | 9917072 | Smart Plug S17 | SIG Mesh | N/A | Power on/off | ✅ Working |
| Malmbergs BT Smart | 9917071 | Smart Plug S17 | SIG Mesh | N/A | Power on/off | ✅ Expected Working |
| Malmbergs BT Smart | 9917073 | Smart Plug S17 | SIG Mesh | N/A | Power on/off | ✅ Expected Working |

## Known Compatible Brands

These brands use the same Telink BLE Mesh protocol. They should work by
setting the correct vendor ID during setup, but are not hardware-verified.

| Brand | Vendor ID | Products | Reference |
|-------|-----------|----------|-----------|
| Malmbergs BT Smart | `0x1001` | LED drivers, bulbs | Tested in this project |
| AwoX / Eglo | `0x0160` | Mesh lights | [python-awox-mesh-light](https://github.com/fsaris/python-awox-mesh-light) |
| Dimond / retsimx | `0x0211` | Mesh lights | [tlsr8266_mesh](https://github.com/retsimx/tlsr8266_mesh) |

## How to Find Your Vendor ID

If your device is not listed above, you can find the vendor ID from a BLE
packet capture:

### Method 1: HCI Snoop Log (Android)

1. Enable Bluetooth HCI snoop log in Android Developer Options
2. Pair and control the device using its original app
3. Extract the HCI log and open in Wireshark
4. Filter for GATT writes to characteristic `1912`
5. In the encrypted command payload (after decryption), bytes at offset
   `[3:5]` are the vendor ID in little-endian

### Method 2: Known App Patterns

| App | Vendor ID |
|-----|-----------|
| Malmbergs BLE / BT Smart | `0x1001` |
| AwoX Smart Control | `0x0160` |
| Eglo Connect / Eglo BLE | `0x0160` |

### Method 3: Trial and Error

If your device uses the Telink mesh stack (same GATT service structure):

1. Add the device with default vendor ID (`0x1001`)
2. If commands don't work, try `0x0160` (AwoX) or `0x0211` (Dimond)
3. Check logs for encryption/authentication errors

## Adding a New Device

If you get a new device working with a different vendor ID:

1. Note the brand, model, vendor ID, and supported features
2. Open an issue or PR to add it to this table

## Detection Criteria

The integration **auto-detects** devices using multiple methods:

### Telink Proprietary Mesh
- BLE local name starts with `out_of_mesh` (unprovisioned)
- BLE local name starts with `tymesh` (provisioned)
- Service UUID `0xfe07` present

### SIG Mesh (Bluetooth Mesh)
- GATT service `0x1827` (Mesh Provisioning Service) present
- GATT service `0x1828` (Mesh Proxy Service) present
- BLE local name starts with `out_of_mesh` (common for unprovisioned devices)

Devices with other names may work via manual MAC address entry during config flow.

## S17 Smart Plug — Implementation Notes

Malmbergs sells S17 plug variants using **SIG Mesh (Bluetooth Mesh)** protocol:

| Article | Description | Protocol | Features |
|---------|-------------|----------|----------|
| 9917072 | Smart Plug S17 | SIG Mesh | On/off control (tested, working) |
| 9917071 | Smart Plug S17 | SIG Mesh | On/off control (expected working) |
| 9917073 | Smart Plug S17 | SIG Mesh | On/off control (expected working, energy monitoring not yet implemented) |

The S17 plug uses a **different protocol** than the LED driver:

- **Protocol:** Standard Bluetooth SIG Mesh (not Telink proprietary)
- **GATT Services:** `0x1827` (Provisioning), `0x1828` (Proxy)
- **Discovery:** Auto-detected via standard SIG Mesh service UUIDs
- **Provisioning:** Supports standard PB-GATT provisioning flow
- **Communication:** Via SIG Mesh Generic OnOff Server model (SIG Model ID `0x1000`)

**Status**: Fully supported as of v0.20.6. The integration auto-detects SIG Mesh devices and provisions them using standard Bluetooth Mesh protocol.

## Protocol Support Summary

| Protocol | Status | Example Devices | Bridge Required |
|----------|--------|-----------------|-----------------|
| **Telink Proprietary Mesh** | ✅ Fully Supported | Malmbergs 9952126 LED Driver | Yes (bridge daemon) |
| **SIG Mesh (Bluetooth Mesh)** | ✅ Fully Supported | Malmbergs 9917072 Smart Plug S17 | No (direct HA or ESPHome proxy) |

### Key Differences

**Telink Proprietary Mesh:**
- Requires **bridge daemon** running on a Raspberry Pi or similar device
- HA cannot communicate directly via Bluetooth (vendor-specific protocol)
- Fast provisioning with mesh name/password
- Compact DP commands for light control

**SIG Mesh (Bluetooth Mesh):**
- Works **directly with Home Assistant Bluetooth** or **ESPHome BLE proxies**
- Standard PB-GATT provisioning (no bridge needed)
- Uses standard SIG Mesh models (Generic OnOff, Generic Level, etc.)
- Wider ecosystem compatibility

## Known Limitations

- **Energy monitoring** for smart plugs (DP ID tracking) is not yet implemented
- **Color temperature DP ID** varies between Telink device models (vendor-dependent)
- The **compact DP format** (opcode 0xD2) is confirmed for Malmbergs Telink devices only; other brands may use standard Telink commands (0xD0, 0xF1, etc.)
- **RGB color control** is protocol-supported but minimally tested (lack of RGB hardware)
