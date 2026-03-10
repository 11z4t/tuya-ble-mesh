# Tuya BLE Mesh for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?logo=homeassistantcommunitystore)](https://github.com/hacs/integration)
[![CI](https://github.com/kvista-se/tuya-ble-mesh/actions/workflows/ci.yml/badge.svg)](https://github.com/kvista-se/tuya-ble-mesh/actions/workflows/ci.yml)
[![Version](https://img.shields.io/badge/version-0.20.6-blue.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![HA 2024.1+](https://img.shields.io/badge/HA-2024.1%2B-blue.svg)](https://www.home-assistant.io)
[![Tests](https://img.shields.io/badge/tests-passing-brightgreen.svg)](tests/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](docs/COVERAGE_REPORT.md)

A fully local Home Assistant integration for controlling Tuya BLE Mesh devices — including **Malmbergs BT Smart** lighting products. No cloud. No Tuya account required for daily use.

## What is this?

Many affordable smart lighting products (sold under brands like Malmbergs, AwoX, and others) use **Tuya BLE Mesh** firmware internally. They're typically controlled via the Tuya Smart app through Tuya's cloud servers.

This integration replaces cloud control with **direct BLE communication**, keeping everything local on your network. Your smart lights respond faster, work without internet, and don't depend on any external servers.

### How it works

There are two connection modes:

**Mode 1: Bridge daemon (RPi)**
```
Home Assistant  ←HTTP→  Bridge Daemon (RPi)  ←BLE Mesh→  Devices
```
1. A Raspberry Pi with Bluetooth runs the bridge daemon near your BLE mesh devices
2. The HA integration communicates with the bridge over your local network
3. The bridge translates commands to/from the BLE mesh protocol

**Mode 2: ESPHome BLE Proxy**
```
Home Assistant  ←API→  ESPHome BLE Proxy  ←BLE Mesh→  Devices
```
For SIG Mesh devices, any ESPHome device with BLE proxy enabled can be used instead of a dedicated RPi. This is simpler to set up and doesn't require a separate bridge daemon.

In both modes, Home Assistant itself doesn't need Bluetooth hardware.

## Tested Devices

| Device | Brand | Type | MAC | Status |
|--------|-------|------|-----|--------|
| LED Driver 9952126 | Malmbergs BT Smart | Dimmable LED driver | DC:23:4D:21:43:A5 | Tested — on/off, brightness |
| Smart Plug S17 | Malmbergs BT Smart | BLE Mesh relay plug | DC:23:4F:10:52:C4 | Tested — on/off, SIG Mesh provisioned |

### Potentially Compatible

Devices using the Tuya BLE Mesh / Telink stack with service UUID `fe07`:

| Brand | Example Products | Vendor ID | Status |
|-------|-----------------|-----------|--------|
| **Malmbergs BT Smart** | LED drivers, plugs | `0x1001` | Hardware tested |
| **AwoX** | Mesh lights | `0x0160` | Protocol compatible, untested |
| **Dimond/retsimx** | Mesh lights | `0x0211` | Protocol compatible, untested |

## Features

### Device Control
- **Power on/off** — instant local control, no cloud round-trip
- **Brightness** — 1–100% dimming with smooth transitions
- **Color temperature** — warm to cool white (CCT)
- **RGB color** — full color control on supported devices
- **Switch** — relay control for smart plugs

### Connectivity
- **Auto-discovery** — finds `out_of_mesh*` and `tymesh*` devices via BLE
- **ESPHome BLE proxy** — use any ESPHome device as a BLE bridge (SIG Mesh)
- **Auto-reconnect** — exponential backoff (5s → 5min) on connection loss
- **Keep-alive** — maintains BLE connections proactively to minimize latency
- **Command queue** — reliable delivery with TTL even under rapid HA automations

### Status & Monitoring
- **Push-based updates** — BLE notifications drive state changes (no polling)
- **RSSI sensor** — signal strength monitoring with adaptive polling
- **Firmware version** — sensor for device firmware tracking
- **Connection statistics** — visible in HA diagnostics

### Protocol Support
- **Tuya proprietary BLE Mesh** (Telink TLK8232 / TLK8258) — all light and plug features
- **SIG Mesh (Bluetooth Mesh)** — provisioning, proxy, segmentation/reassembly
- **Dual-stack** — both protocols work simultaneously on the same HA instance

## Installation

### Via HACS (recommended)

1. Open **HACS** in Home Assistant
2. Go to **Integrations** → three-dot menu → **Custom repositories**
3. Add URL: `https://github.com/11z4t/tuya-ble-mesh`
4. Category: **Integration**
5. Search for **"Tuya BLE Mesh"** and click **Download**
6. **Restart Home Assistant**

### Manual

1. Copy `custom_components/tuya_ble_mesh/` to your HA `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

### Adding a device

**Settings** → **Devices & Services** → **Add Integration** → search **"Tuya BLE Mesh"**

| Field | Description | Default |
|-------|-------------|---------|
| Device type | Light or Plug | Light |
| MAC Address | BLE MAC (XX:XX:XX:XX:XX:XX) | *required* |
| Bridge Host | IP/hostname of the bridge RPi | *required* |
| Bridge Port | Bridge daemon HTTP port | `8099` |
| Mesh Name | Mesh network name | `out_of_mesh` |
| Mesh Password | Mesh network password | `123456` |
| Vendor ID | Vendor identifier (hex) | `0x1001` |

### Bridge Daemon

The bridge daemon runs on a Raspberry Pi with Bluetooth, close to your mesh devices:

```bash
# On the RPi
cd ~/malmbergs-bt
source ~/malmbergs-ble/bin/activate
python scripts/ble_mesh_daemon.py --host 0.0.0.0 --port 8099
```

The daemon exposes a simple HTTP API that the HA integration uses to send commands and receive status.

### Vendor IDs

Different brands embed different vendor IDs in the Telink mesh protocol:

| Brand | Vendor ID |
|-------|-----------|
| Malmbergs BT Smart | `0x1001` (default) |
| AwoX | `0x0160` |
| Dimond/retsimx | `0x0211` |

If commands don't work with the default, check BLE snoop logs for vendor bytes at payload offset `[3:5]`.

## Entities

Each device creates:

| Entity | Type | Description |
|--------|------|-------------|
| `light.<name>` | Light | Power, brightness, color temperature |
| `switch.<name>` | Switch | Power on/off (plugs only) |
| `sensor.<name>_signal` | Sensor | BLE signal strength (RSSI) |

## Hardware Setup

### What you need

- **Home Assistant** 2024.1 or later (any installation method)
- **Raspberry Pi** (3B+ or 4) with built-in Bluetooth — runs the bridge daemon
- **Tuya BLE Mesh devices** — Malmbergs BT Smart or compatible

### Optional hardware (for development/debugging)

- **Adafruit nRF51822 BLE Sniffer** — passive packet capture via serial
- **Shelly Plug S** — remote power cycling for factory reset procedures

### Network diagram

```
┌──────────────┐     HTTP      ┌──────────────┐     BLE Mesh     ┌─────────┐
│ Home         │◄─────────────►│ Raspberry Pi │◄────────────────►│ Light 1 │
│ Assistant    │   (port 8099) │ (Bridge)     │                  ├─────────┤
│              │               │              │◄────────────────►│ Light 2 │
└──────────────┘               └──────────────┘                  ├─────────┤
                                                                 │ Plug 1  │
                                                                 └─────────┘
```

## Architecture

The codebase is split into two independent layers:

```
lib/tuya_ble_mesh/          ← Standalone BLE mesh library (no HA dependency)
├── protocol.py             ← Tuya BLE Mesh packet encoding/decoding
├── crypto.py               ← Mesh encryption (AES-based)
├── connection.py           ← BLE GATT connection management
├── device.py               ← High-level device abstraction
├── scanner.py              ← BLE device discovery
├── sig_mesh_protocol.py    ← SIG Mesh standard protocol
├── sig_mesh_crypto.py      ← SIG Mesh encryption
├── sig_mesh_device.py      ← SIG Mesh device with GATT proxy
└── sig_mesh_bridge.py      ← HTTP bridge for remote BLE access

custom_components/tuya_ble_mesh/   ← Home Assistant integration
├── __init__.py             ← Setup, config entry handling
├── config_flow.py          ← UI configuration wizard
├── coordinator.py          ← Data update coordinator
├── light.py                ← Light entity platform
├── switch.py               ← Switch entity platform (plugs)
├── sensor.py               ← Signal strength sensor
└── lib/                    ← Bundled copy of the core library
```

The core library can be used independently — for scripts, testing, or other platforms.

## Development

```bash
# Activate virtual environment
source ~/malmbergs-ble/bin/activate
cd ~/malmbergs-bt

# Run full check pipeline (must pass before committing)
bash scripts/run-checks.sh

# Scan for nearby BLE mesh devices
python scripts/scan.py

# Passive BLE sniffing (requires nRF51822 sniffer)
python scripts/sniff.py

# Power cycle device via Shelly plug
python scripts/power_cycle.py

# Run tests only
python -m pytest tests/unit/ -q
```

### Check pipeline

All checks must pass: **ruff** (lint + format), **mypy --strict**, **bandit**, **safety**, **detect-secrets**, **pytest**.

## Known Limitations

- **Bridge required** — HA cannot talk BLE mesh directly; the RPi bridge daemon must be running
- **Single LED driver tested** — color temperature DP ID not confirmed for all devices
- **Factory reset** — some devices need 5x rapid power cycling to enter provisioning mode; not all respond reliably
- **BlueZ quirks** — on older BlueZ versions, `bluetoothctl remove` may be needed between reconnects (handled automatically)
- **No OTA** — firmware updates are out of scope

## Contributing

1. Fork the repository
2. Create a feature branch
3. Ensure `bash scripts/run-checks.sh` passes
4. Submit a pull request

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release history.

## License

MIT
