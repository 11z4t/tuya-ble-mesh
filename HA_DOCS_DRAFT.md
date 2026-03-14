# Home Assistant Documentation Draft — Tuya BLE Mesh

**Integration Name:** Tuya BLE Mesh
**Domain:** `tuya_ble_mesh`
**Category:** Light, Switch
**Config Flow:** Yes
**Discovery:** Bluetooth

---

## Overview

The Tuya BLE Mesh integration allows you to control Tuya BLE Mesh devices (lights, switches, plugs) directly via Bluetooth, without requiring a cloud connection or Tuya account.

Popular brands using Tuya BLE Mesh include:
- **Malmbergs BT Smart** (Sweden)
- **AwoX** (France)
- **Dimond** / **retsimx** (various regions)

This integration communicates directly with devices using the Tuya proprietary BLE Mesh protocol or the SIG Mesh standard.

---

## Supported Devices

### Tested Hardware
- **Malmbergs BT Smart LED Driver 9952126** — Dimmable LED driver
- **Malmbergs BT Smart Plug S17** — BLE Mesh relay plug

### Potentially Compatible
Any device advertising Tuya BLE Mesh services (`fe07`, `0x1827`, `0x1828`) should work. Check manufacturer specifications for "Bluetooth Mesh" or "Tuya Mesh" support.

---

## Prerequisites

### Option 1: Built-in Bluetooth (Recommended)
If your Home Assistant installation has built-in Bluetooth support (e.g., Home Assistant Yellow, Raspberry Pi with Bluetooth adapter), devices will be auto-discovered.

### Option 2: ESPHome BLE Proxy
Use ESP32 devices as Bluetooth proxies to extend range:
- Install ESPHome on ESP32 boards
- Enable `bluetooth_proxy` component
- Place ESP32 devices near Tuya BLE Mesh lights

### Option 3: Bridge Daemon (Advanced)
For installations without Bluetooth, run a bridge daemon on a separate Raspberry Pi:
```bash
python scripts/ble_mesh_daemon.py --host 0.0.0.0 --port 8787
```

---

## Configuration

### Automatic Discovery
Devices advertising as `out_of_mesh*` or `tymesh*` are automatically discovered. Click **Configure** in the integration notification to add them.

### Manual Addition
1. Go to **Settings** → **Devices & Services**
2. Click **Add Integration**
3. Search for **Tuya BLE Mesh**
4. Select device type (Light or Plug)
5. Enter device MAC address and mesh credentials

---

## Options

| Option | Description | Default |
|--------|-------------|---------|
| **Mesh Name** | Network name for Telink Mesh devices | `out_of_mesh` |
| **Mesh Password** | Network password for Telink Mesh devices | `123456` |
| **Vendor ID** | Manufacturer vendor ID (hex) | `0x1001` (Malmbergs) |
| **Bridge Host** | IP of bridge daemon (if using Option 3) | — |
| **Bridge Port** | Port of bridge daemon | `8787` |

---

## Entities

Each device creates the following entities:

### Lights
- **`light.<device_name>`** — Power, brightness, color temperature control

### Switches (Plugs Only)
- **`switch.<device_name>`** — Power on/off

### Sensors
- **`sensor.<device_name>_signal`** — BLE signal strength (RSSI)

---

## Services

### `tuya_ble_mesh.identify`
Flash the device LED to visually identify it.

```yaml
service: tuya_ble_mesh.identify
data:
  device_id: <device_id>
```

### `tuya_ble_mesh.set_log_level`
Change BLE mesh logging verbosity without restarting Home Assistant.

```yaml
service: tuya_ble_mesh.set_log_level
data:
  level: debug  # debug | info | warning | error
```

---

## Troubleshooting

### Device not discovered
- Ensure device is powered on and in pairing mode (some devices need factory reset via 5× power cycles)
- Check Bluetooth adapter is working: **Settings** → **System** → **Hardware** → **Bluetooth**
- Move device closer to Home Assistant or add ESPHome BLE proxy

### Commands not working
- **Vendor ID mismatch:** Try `0x0160` (AwoX) or `0x0211` (Dimond) if default `0x1001` doesn't work
- **Mesh credentials:** Verify mesh name and password match what was set in the original Tuya app (if previously paired)

### Connection drops
- BLE mesh has limited range (5-10 meters). Add ESPHome BLE proxies for larger homes.
- Reduce concurrent BLE connections (max 3-5 devices per adapter recommended)

### Factory Reset
Most Tuya BLE Mesh devices reset via rapid power cycling:
1. Turn off device
2. Turn on for 1 second, turn off for 1 second
3. Repeat 5-10 times until device flashes rapidly
4. Device will advertise as `out_of_mesh` or similar

---

## Data Privacy

This integration operates **entirely locally**. No data is sent to Tuya servers or any cloud service. All communication happens over Bluetooth within your local network.

---

## Removal

To remove devices:
1. Go to **Settings** → **Devices & Services** → **Tuya BLE Mesh**
2. Click on the device
3. Click **Delete**

To remove the integration completely:
1. Remove all devices first
2. Click **…** → **Delete** on the integration card

---

## Links

- [GitHub Repository](https://github.com/11z4t/tuya-ble-mesh)
- [Issue Tracker](https://github.com/11z4t/tuya-ble-mesh/issues)
- [HACS Installation](https://hacs.xyz)
