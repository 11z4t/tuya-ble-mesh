# Tuya BLE Mesh for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![HA 2024.1+](https://img.shields.io/badge/HA-2024.1%2B-blue.svg)](https://www.home-assistant.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Fully local BLE mesh control of Tuya/Telink-based devices from Home Assistant.
No cloud dependency. No Tuya account required.

## Supported Devices

Devices that advertise the Tuya BLE Mesh service (`fe07`) with Telink firmware:

| Brand | Example Products | Vendor ID | Status |
|-------|-----------------|-----------|--------|
| **Malmbergs BT Smart** | LED Driver 9952126 | `0x1001` | Tested |
| **AwoX** | Mesh lights | `0x0160` | Untested |
| **Dimond/retsimx** | Mesh lights | `0x0211` | Untested |

Other Tuya BLE Mesh devices using the Telink stack may work by specifying the
correct vendor ID during setup.

## Features

- Power on/off
- Brightness control (1-100%)
- Color temperature (warm-cool)
- Automatic BLE discovery (`out_of_mesh*`, `tymesh*`)
- Push-based status updates via BLE notifications
- Configurable vendor ID for multi-brand support
- Auto-reconnect with exponential backoff
- Keep-alive to maintain BLE connection

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Go to **Integrations** > **Custom repositories**
3. Add `https://git.malmgrens.me/bormal/malmbergs-bt` as an **Integration**
4. Search for "Tuya BLE Mesh" and install
5. Restart Home Assistant

### Manual

1. Copy `custom_components/tuya_ble_mesh/` and `lib/tuya_ble_mesh/` to your
   Home Assistant config directory
2. Restart Home Assistant

## Configuration

### Automatic (Bluetooth discovery)

If your device advertises as `out_of_mesh*` or `tymesh*`, Home Assistant will
detect it automatically and prompt you to confirm.

### Manual setup

Go to **Settings** > **Devices & Services** > **Add Integration** > **Tuya BLE Mesh**.

| Field | Description | Default |
|-------|-------------|---------|
| MAC Address | BLE address (XX:XX:XX:XX:XX:XX) | *required* |
| Mesh Name | Mesh network name | `out_of_mesh` |
| Mesh Password | Mesh network password | `123456` |
| Vendor ID | Vendor identifier in hex | `0x1001` |

### Vendor ID

Different brands use different vendor IDs in the Telink mesh protocol.
If your device is not Malmbergs, set the correct vendor ID:

| Brand | Vendor ID |
|-------|-----------|
| Malmbergs BT Smart | `0x1001` (default) |
| AwoX | `0x0160` |
| Dimond/retsimx | `0x0211` |

The vendor ID is written as little-endian bytes into every command packet.
If commands don't work with the default, try the vendor ID from your device's
original app (check BLE snoop logs for the bytes at payload offset [3:5]).

## Entities

Each device creates:

| Entity | Type | Description |
|--------|------|-------------|
| `light.<name>` | Light | Power, brightness, color temperature |
| `sensor.<name>_signal` | Sensor | BLE signal strength (RSSI) |

## Requirements

- Home Assistant 2024.1 or later
- Bluetooth adapter (built-in or USB)
- Python 3.11+
- bleak >= 0.21.0

## Architecture

The integration is split into two layers:

- **`lib/tuya_ble_mesh/`** - Standalone BLE mesh library (no HA dependency)
- **`custom_components/tuya_ble_mesh/`** - Home Assistant integration wrapper

This separation allows the core library to be used independently of Home
Assistant (scripts, testing, other platforms).

## Development

```bash
# Activate venv
source ~/malmbergs-ble/bin/activate && cd ~/malmbergs-bt

# Run all checks (must pass before committing)
bash scripts/run-checks.sh

# Scan for BLE devices
python scripts/scan.py

# Run tests only
python -m pytest tests/unit/ -q
```

### Check pipeline

All 7 checks must pass: ruff, ruff format, mypy --strict, bandit, safety,
detect-secrets, pytest.

## Known Limitations

- Color temperature DP ID not yet confirmed for all devices
- Only one device tested end-to-end (Malmbergs LED Driver 9952126)
- BlueZ 5.x may require `bluetoothctl remove` between reconnects (handled
  automatically)
- AwoX and Dimond vendor IDs are known but not hardware-verified

## License

MIT
