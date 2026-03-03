# Supported Devices

This integration works with Tuya BLE Mesh devices using the Telink firmware
stack. These devices advertise the `fe07` BLE service UUID and use GATT
characteristics with the Telink base UUID (`00010203-0405-0607-0809-0a0b0c0dXXXX`).

## Tested Devices

| Brand | Model | Product | Vendor ID | Features | Status |
|-------|-------|---------|-----------|----------|--------|
| Malmbergs BT Smart | 9952126 | LED Driver | `0x1001` | Power, brightness, color temp | Working |

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

The integration discovers devices automatically if they match:

- BLE local name starts with `out_of_mesh` (unprovisioned)
- BLE local name starts with `tymesh` (provisioned)

Devices with other names may work via manual MAC address entry.

## Known Limitations

- Only Telink-based proprietary Tuya Mesh is supported (not SIG Mesh)
- Color temperature DP ID is not confirmed for all devices
- Some devices may use different DP IDs or command formats
- The compact DP format (opcode 0xD2) is confirmed for Malmbergs only;
  other brands may use standard Telink commands (0xD0, 0xF1, etc.)
