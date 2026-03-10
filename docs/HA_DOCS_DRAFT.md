---
title: Tuya BLE Mesh
description: Instructions for integrating Tuya BLE Mesh devices with Home Assistant.
ha_category:
  - Light
  - Switch
ha_release: 2024.1
ha_iot_class: Local Push
ha_quality_scale: platinum
ha_codeowners:
  - "@11z4t"
ha_domain: tuya_ble_mesh
ha_config_flow: true
ha_platforms:
  - light
  - switch
  - sensor
ha_dhcp: false
ha_bluetooth:
  - local_name: out_of_mesh*
  - local_name: tymesh*
  - service_uuid: "00001828-0000-1000-8000-00805f9b34fb"
  - service_uuid: "00001827-0000-1000-8000-00805f9b34fb"
ha_integration_type: hub
---

The **Tuya BLE Mesh** integration allows local control of BLE Mesh lighting and switch devices that use the Tuya firmware stack — including devices sold under **Malmbergs BT Smart** and compatible brands. No cloud connection is required for daily use.

{% include integrations/config_flow.md %}

## Overview

Many affordable smart lighting products use **Tuya BLE Mesh** firmware (based on the Telink TLK8232/TLK8258 SoC or the Bluetooth SIG Mesh standard). These devices are typically controlled through the Tuya Smart app via Tuya's cloud servers.

This integration provides a fully **local** alternative, communicating directly with the devices over Bluetooth — resulting in faster response times and independence from external servers.

### Connection modes

**Telink BLE Mesh (via bridge daemon)**

The most common configuration: a Raspberry Pi or other Linux host with Bluetooth runs the bridge daemon near your devices. Home Assistant communicates with the bridge over your local network via HTTP.

```
Home Assistant  ←HTTP→  Bridge Daemon  ←BLE Mesh→  Devices
```

**SIG Mesh (via ESPHome BLE proxy)**

For SIG Mesh devices, any [ESPHome device with BLE proxy](https://esphome.io/components/bluetooth_proxy.html) can serve as the bridge — no dedicated Raspberry Pi needed. Home Assistant provisions the device automatically and communicates via the SIG Mesh GATT proxy.

```
Home Assistant  ←Bluetooth API→  ESPHome BLE Proxy  ←SIG Mesh→  Devices
```

## Prerequisites

### Telink BLE Mesh devices

- A **Raspberry Pi** (3B+, 4, or Zero 2W) with built-in Bluetooth, or a Linux host with a USB Bluetooth adapter
- Python 3.11+ on the bridge host
- The bridge daemon from the [tuya-ble-mesh repository](https://github.com/11z4t/tuya-ble-mesh)

### SIG Mesh devices

- An **ESPHome** device with BLE proxy enabled (`bluetooth_proxy:` component)
- Home Assistant 2024.1 or later
- The device must be in factory-reset state (advertising Provisioning Service UUID `0x1827`)

## Configuration

{% include integrations/config_flow.md %}

When you add the integration, a discovery scan finds nearby Tuya BLE Mesh devices automatically. You can also add devices manually by entering their BLE MAC address.

### Configuration options

| Option | Description | Default |
|--------|-------------|---------|
| **Device Type** | Light, Plug (Telink), SIG Bridge Light, SIG Plug | Light |
| **MAC Address** | BLE MAC address of the device (XX:XX:XX:XX:XX:XX) | — |
| **Mesh Name** | Network credential name (max 16 bytes) | `out_of_mesh` |
| **Mesh Password** | Network credential password (max 16 bytes) | `123456` |
| **Vendor ID** | Manufacturer identifier in hex, e.g. `0x1001` | `0x1001` |
| **Bridge Host** | IP address or hostname of the bridge daemon host | — |
| **Bridge Port** | TCP port of the bridge daemon | `8787` |
| **Mesh Address** | Target device address (0 = automatic) | `0` |

### SIG Mesh auto-provisioning

For SIG Mesh plugs and lights, the integration handles provisioning automatically:

1. Add the integration and choose **SIG Mesh Plug** device type
2. Put the device in factory-reset mode (power-cycle 5 times quickly)
3. Click **Submit** — the integration generates random NetKey, AppKey, and derives DevKey via ECDH
4. All keys are stored securely in the Home Assistant config database

## Supported devices

| Device | Brand | Type | Features |
|--------|-------|------|----------|
| LED Driver 9952126 | Malmbergs BT Smart | Dimmable LED driver | On/off, brightness |
| Smart Plug S17 | Malmbergs BT Smart | BLE relay plug | On/off, SIG Mesh |

### Potentially compatible

Other devices using the Tuya BLE Mesh / Telink stack with service UUID `0xfe07`:

| Brand | Products | Vendor ID |
|-------|----------|-----------|
| AwoX | Mesh lights | `0x0160` |
| Dimond/retsimx | Mesh lights | `0x0211` |

If your device is not listed but uses Tuya BLE Mesh firmware, it may work with the correct Vendor ID. See the [supported devices list](https://github.com/11z4t/tuya-ble-mesh/blob/main/docs/SUPPORTED_DEVICES.md).

## Entities

Each configured device creates the following entities:

| Entity | Platform | Description |
|--------|----------|-------------|
| `light.<name>` | Light | Power, brightness, color temperature, RGB |
| `switch.<name>` | Switch | On/off relay (plugs only) |
| `sensor.<name>_signal` | Sensor | BLE signal strength (RSSI, dBm) |
| `sensor.<name>_firmware` | Sensor | Firmware version string |

Signal strength and firmware sensors are **disabled by default** and can be enabled in the entity settings.

## Actions

### `tuya_ble_mesh.identify`

Flash the device LED to make it easy to identify physically.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `device_id` | string | yes | The device to identify |

### `tuya_ble_mesh.set_log_level`

Change BLE mesh logging verbosity at runtime.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `level` | select | yes | `debug`, `info`, `warning`, or `error` |

## Bridge daemon setup

Run the bridge daemon on the Raspberry Pi:

```bash
# Install dependencies
pip install bleak aiohttp

# Clone the repository
git clone https://github.com/11z4t/tuya-ble-mesh.git
cd tuya-ble-mesh

# Start the daemon
python scripts/ble_mesh_daemon.py --host 0.0.0.0 --port 8787
```

The daemon can also be run as a systemd service for automatic startup.

## Troubleshooting

### Device not found after adding

- Ensure the device is powered on and within BLE range of the bridge
- For Telink devices: verify the bridge daemon is running (`curl http://bridge-host:8787/health`)
- Check that the Vendor ID matches your device brand

### Cannot connect to bridge

- Verify the bridge host IP and port in the integration options
- Check that the bridge daemon process is running
- Review bridge daemon logs for errors

### Provisioning failed (SIG Mesh)

- Factory-reset the device: power cycle it 5 times rapidly (1s on, 1s off)
- Verify the device advertises the Provisioning Service UUID (`0x1827`)
- Ensure the ESPHome BLE proxy is within 5m of the device during provisioning
- Check Home Assistant logs for detailed error messages

### Authentication failed (re-auth required)

If mesh credentials change (e.g., after factory reset), HA prompts for re-authentication. Open the integration and follow the re-authentication flow to update credentials.

## Known limitations

- **Bridge required** — HA cannot communicate BLE mesh directly without either a bridge daemon or ESPHome proxy
- **Factory reset** — some devices need exactly 5 rapid power cycles to enter provisioning mode
- **Color temperature range** — device-specific, may differ from stated specs
- **No OTA firmware updates** — out of scope for this integration
- **SIG Mesh key rotation** — requires re-pairing the device

## Security

Mesh credentials are:
- Never logged (even at DEBUG level)
- Stored encrypted in the Home Assistant credential store
- Redacted in diagnostic exports
- Not exposed in entity state attributes

The bridge connection is over plain HTTP within your local network. For sensitive environments, place the bridge on a dedicated VLAN or use a VPN.

See [SECURITY.md](https://github.com/11z4t/tuya-ble-mesh/blob/main/SECURITY.md) for the full security policy and vulnerability reporting instructions.
