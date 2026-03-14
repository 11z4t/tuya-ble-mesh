# Supported Functions

This document details the **functions and features** supported by the Tuya BLE Mesh integration, broken down by device type and entity platform.

## Table of Contents

- [Overview by Device Type](#overview-by-device-type)
- [Light Entities](#light-entities)
- [Switch Entities](#switch-entities)
- [Sensor Entities](#sensor-entities)
- [Update Entities](#update-entities)
- [Services](#services)
- [Data Points (DP) Reference](#data-points-dp-reference)

---

## Overview by Device Type

| Device Type | Entity Platforms | Supported Functions |
|-------------|------------------|---------------------|
| **Light (Telink Mesh)** | Light, Sensor, Update | On/Off, Brightness (1-100%), Color Temperature (2500-6500K), RGB Color¹, RSSI, Firmware |
| **Light (SIG Mesh)** | Light, Sensor, Update | On/Off, Brightness (0-100%), RSSI, Firmware |
| **Switch/Plug (Telink Mesh)** | Switch, Sensor, Update | On/Off, RSSI, Firmware |
| **Switch/Plug (SIG Mesh)** | Switch, Sensor, Update | On/Off, RSSI, Firmware |

¹ RGB color control is protocol-supported but **minimally tested** due to lack of RGB hardware.

---

## Light Entities

Light entities represent dimmable lights, LED drivers, and smart bulbs.

### Supported Features

#### All Light Devices

| Feature | Platform | HA Attribute | Range/Values | Notes |
|---------|----------|--------------|--------------|-------|
| **Power On/Off** | `light` | `state` | `on` / `off` | Instant local control |
| **Brightness** | `light` | `brightness` | 1-255 (HA scale) | Maps to device 1-100% or 0-255 depending on mode |
| **Transition** | `light` | `transition` | Seconds (float) | Smooth fade in/out |

#### Telink Mesh Lights (Color Temperature)

| Feature | Platform | HA Attribute | Range/Values | Notes |
|---------|----------|--------------|--------------|-------|
| **Color Temperature** | `light` | `color_temp_kelvin` | 2500K - 6500K | Warm to cool white (CCT) |
| **Color Mode** | `light` | `color_mode` | `color_temp`, `rgb` | Auto-detected based on last command |

#### Telink Mesh Lights (RGB)

| Feature | Platform | HA Attribute | Range/Values | Notes |
|---------|----------|--------------|--------------|-------|
| **RGB Color** | `light` | `rgb_color` | (R, G, B) 0-255 each | Full color spectrum control² |
| **Color Brightness** | `light` | `brightness` | 0-255 | Separate brightness scale for RGB mode |

² RGB support is **protocol-documented** but **minimally tested**. Tested devices (Malmbergs 9952126) are CCT-only. RGB commands may not work on all devices.

#### SIG Mesh Lights

| Feature | Platform | HA Attribute | Range/Values | Notes |
|---------|----------|--------------|--------------|-------|
| **Generic Level** | `light` | `brightness` | 0-100% | Uses SIG Mesh Generic Level Server model |

### Supported Color Modes

| Mode | Telink Mesh | SIG Mesh | Description |
|------|-------------|----------|-------------|
| `ColorMode.ONOFF` | ❌ | ✅ | On/Off only (no dimming) |
| `ColorMode.BRIGHTNESS` | ❌ | ✅ | Dimmable white light |
| `ColorMode.COLOR_TEMP` | ✅ | ❌ | Color temperature (warm/cool white) |
| `ColorMode.RGB` | ✅³ | ❌ | Full RGB color control |

³ RGB mode is supported for Telink Mesh devices but **not validated** on real RGB hardware.

### Data Points (Telink Mesh)

| DP ID | Name | Type | Values | Confirmed |
|-------|------|------|--------|-----------|
| `0x79` (121) | Power | value | 0=OFF, 1=ON | ✅ Hardware tested |
| `0x7A` (122) | Brightness | value | 1-100 (%) | ✅ Hardware tested |
| `0x7B` (123) | Color Temp | value | 0-127 (warm→cool) | ✅ Hardware tested |
| `0x7C` (124) | RGB Color | string | 6-char hex (RRGGBB) | ⚠️ Protocol documented, untested |

---

## Switch Entities

Switch entities represent smart plugs and relay switches.

### Supported Features

| Feature | Platform | HA Attribute | Range/Values | Notes |
|---------|----------|--------------|--------------|-------|
| **Power On/Off** | `switch` | `state` | `on` / `off` | Relay control |

### Data Points (Telink Mesh)

| DP ID | Name | Type | Values | Confirmed |
|-------|------|------|--------|-----------|
| `0x79` (121) | Power | value | 0=OFF, 1=ON | ✅ Hardware tested |

### SIG Mesh Models

| Model ID | Model Name | Supported Operations |
|----------|------------|----------------------|
| `0x1000` | Generic OnOff Server | Set On, Set Off, Get Status |

---

## Sensor Entities

Sensor entities provide monitoring and diagnostic data.

### RSSI (Signal Strength)

| Attribute | Value | Notes |
|-----------|-------|-------|
| **Platform** | `sensor` | — |
| **Device Class** | `signal_strength` | — |
| **Unit** | dBm (decibels-milliwatt) | — |
| **State Class** | `measurement` | For statistics/graphing |
| **Entity Category** | `diagnostic` | Not shown on main dashboard by default |
| **Update Interval** | Adaptive 30-300 seconds | Based on connection stability |
| **Typical Range** | -40 dBm (excellent) to -90 dBm (poor) | < -80 dBm indicates weak signal |

**Usage**:
- Monitor BLE connection quality
- Identify devices that need repositioning or closer proxy/bridge
- Trigger automations on signal degradation (see `docs/EXAMPLES.md`)

### Firmware Version

| Attribute | Value | Notes |
|-----------|-------|-------|
| **Platform** | `sensor` | — |
| **Device Class** | None | Plain text sensor |
| **Entity Category** | `diagnostic` | — |
| **Entity Registry Enabled** | `False` (disabled by default) | Enable manually if needed |
| **Format** | Varies by device | Examples: `1.0.3`, `v2.1.5` |

**Usage**:
- Track device firmware versions
- Pair with `update` entity for upgrade notifications (read-only)

### Power (Smart Plugs) — PLANNED

| Attribute | Value | Notes |
|-----------|-------|-------|
| **Status** | 🚧 Not yet implemented | Planned for v0.22+ |
| **Platform** | `sensor` | — |
| **Device Class** | `power` | — |
| **Unit** | Watts (W) | — |
| **DP ID (Telink)** | `0x04` (expected) | Not confirmed |

### Energy (Smart Plugs) — PLANNED

| Attribute | Value | Notes |
|-----------|-------|-------|
| **Status** | 🚧 Not yet implemented | Planned for v0.22+ |
| **Platform** | `sensor` | — |
| **Device Class** | `energy` | — |
| **Unit** | kWh (kilowatt-hours) | — |
| **State Class** | `total_increasing` | For energy dashboard integration |

---

## Update Entities

Update entities track firmware versions and notify when updates are available (read-only).

### Supported Features

| Feature | Platform | HA Attribute | Values | Notes |
|---------|----------|--------------|--------|-------|
| **Current Version** | `update` | `installed_version` | String (e.g., `1.0.3`) | Read from device |
| **Latest Version** | `update` | `latest_version` | String | Currently mirrors `installed_version` |
| **Update Available** | `update` | `state` | `on` / `off` | Always `off` (no OTA support) |
| **Entity Category** | `update` | `entity_category` | `diagnostic` | Not shown on main dashboard |
| **Entity Registry Enabled** | `update` | `entity_registry_enabled_default` | `False` | Disabled by default |

**Limitations**:
- **No OTA (Over-The-Air) updates** — The integration cannot install firmware updates
- Firmware version is tracked for monitoring only
- To update firmware, use the **Tuya Smart app** temporarily

**Why No OTA?**:
- Tuya's OTA protocol uses proprietary encryption and vendor SDK
- Staging servers and signing keys are not publicly documented
- Risk of bricking devices with incorrect firmware images

---

## Services

The integration provides custom services for advanced control. See `docs/SERVICES.md` for full details.

### Quick Reference

| Service | Description | Target | Example Use Case |
|---------|-------------|--------|------------------|
| `tuya_ble_mesh.provision_device` | Provision a new SIG Mesh device | MAC address | Add device to mesh network |
| `tuya_ble_mesh.factory_reset` | Factory reset a device | `entity_id` | Remove device from mesh |
| `tuya_ble_mesh.set_mesh_address` | Change device unicast address | `entity_id` | Reassign mesh address |
| `tuya_ble_mesh.refresh_rssi` | Force RSSI update | `entity_id` | Debug signal strength |

**Standard HA Services** (also supported):

| Service | Platform | Description |
|---------|----------|-------------|
| `light.turn_on` | `light` | Turn on light, set brightness/color/CCT |
| `light.turn_off` | `light` | Turn off light with optional transition |
| `switch.turn_on` | `switch` | Turn on switch/plug |
| `switch.turn_off` | `switch` | Turn off switch/plug |
| `homeassistant.update_entity` | All | Force state refresh |

---

## Data Points (DP) Reference

Data Points (DPs) are Tuya's internal protocol for device state and control. This section documents the known DP IDs for Telink Mesh devices.

### Telink Mesh DP IDs

| DP ID (Hex) | DP ID (Dec) | Name | Type | Values | Device Type | Status |
|-------------|-------------|------|------|--------|-------------|--------|
| `0x79` | 121 | Power | value | 0=OFF, 1=ON | Light, Switch | ✅ Confirmed |
| `0x7A` | 122 | Brightness | value | 1-100 (%) | Light | ✅ Confirmed |
| `0x7B` | 123 | Color Temp | value | 0-127 (warm→cool) | Light (CCT) | ✅ Confirmed |
| `0x7C` | 124 | RGB Color | string | 6-char hex (RRGGBB) | Light (RGB) | ⚠️ Untested |
| `0x04` | 4 | Power (W) | value | Integer (watts) | Switch/Plug | 🚧 Planned |
| `0x?? ` | ?? | Energy (kWh) | value | Float (kilowatt-hours) | Switch/Plug | 🚧 Planned |

### SIG Mesh Models

SIG Mesh devices use **standard Bluetooth Mesh models** instead of Tuya DPs.

| Model ID | Model Name | Elements | Supported Opcodes |
|----------|------------|----------|-------------------|
| `0x1000` | Generic OnOff Server | Switch, Light | Get, Set, Set Unacknowledged, Status |
| `0x1002` | Generic Level Server | Light (brightness) | Get, Set, Set Unacknowledged, Status, Delta Set, Move Set |

**Generic OnOff Server Operations**:
- `0x8201` — Generic OnOff Get (query current state)
- `0x8202` — Generic OnOff Set (acknowledged)
- `0x8203` — Generic OnOff Set Unacknowledged (fire-and-forget)
- `0x8204` — Generic OnOff Status (response)

**Generic Level Server Operations**:
- `0x8205` — Generic Level Get
- `0x8206` — Generic Level Set
- `0x8207` — Generic Level Set Unacknowledged

---

## Feature Matrix by Protocol

| Feature | Telink Mesh | SIG Mesh | Notes |
|---------|-------------|----------|-------|
| **On/Off Control** | ✅ | ✅ | Both protocols |
| **Brightness (1-100%)** | ✅ | ✅ | Telink uses DP 122, SIG uses Generic Level |
| **Color Temperature (CCT)** | ✅ | ❌ | Telink DP 123, SIG Mesh lacks standard CCT model |
| **RGB Color** | ⚠️ Untested | ❌ | Telink DP 124 exists but untested |
| **RSSI Monitoring** | ✅ | ✅ | Both protocols |
| **Firmware Tracking** | ✅ | ✅ | Both protocols (read-only) |
| **Energy Monitoring** | 🚧 Planned | 🚧 Planned | Protocol-supported, not yet parsed |
| **Group Addressing** | ❌ | ❌ | Mesh groups not implemented (use HA light groups) |
| **Scene Recall** | ❌ | ❌ | Device scenes not accessible (use HA scenes) |
| **Effects/Animations** | ❌ | ❌ | Not supported (use HA automation scripts) |

---

## Integration Features

### Push-Based Updates

All state changes are **push-based** via BLE notifications. No polling.

- **Telink Mesh**: Status updates via GATT characteristic `0x1911` notifications
- **SIG Mesh**: Status updates via Proxy PDUs (GATT characteristic `0x2ADE`)

### Auto-Reconnect

Exponential backoff reconnection on BLE connection loss:
- Initial retry: 5 seconds
- Max retry interval: 5 minutes
- Unlimited retries while HA is running

### Command Queue

Reliable command delivery with TTL (Time-To-Live):
- Commands queued during disconnection are delivered when reconnected
- TTL: 5 minutes (configurable)
- Prevents automation failures during temporary BLE drops

### Keep-Alive

Proactive BLE connection maintenance:
- Periodic GATT reads to prevent idle disconnection
- Interval: 60 seconds (adaptive based on device behavior)

### Diagnostics

Full diagnostics data available via HA's built-in diagnostics downloader:
- Connection statistics (uptime, reconnect count)
- RSSI trends and history
- Response time percentiles (p50, p90, p99)
- Multi-layer secret redaction (MAC addresses, keys, tokens)

See `docs/SERVICES.md` for `get_diagnostics` usage.

---

## Unsupported Functions

The following functions are **not supported** due to protocol or hardware limitations:

| Function | Reason | Workaround |
|----------|--------|------------|
| **OTA Firmware Updates** | Proprietary Tuya protocol, no public SDK | Use Tuya Smart app temporarily |
| **Energy Monitoring** | DP parsing not yet implemented | Use separate energy meter plug |
| **Scene Recall** | Proprietary Tuya scene model, undocumented | Use Home Assistant scenes |
| **Music Sync / Effects** | App-only feature, no BLE protocol | Use HA automation scripts for basic effects |
| **Cloud Account Sync** | Integration is fully local by design | Manually add devices via MAC address |
| **Group Addressing (Mesh Groups)** | Not implemented (single-device control only) | Use Home Assistant light groups |

See `docs/KNOWN_LIMITATIONS.md` for full details.

---

## Related Documentation

- [Services Reference](SERVICES.md) — Full service call documentation
- [Examples](EXAMPLES.md) — Automation examples using these functions
- [Known Limitations](KNOWN_LIMITATIONS.md) — What's not supported and why
- [Protocol Documentation](PROTOCOL.md) — Low-level protocol details
- [User Guide](USER_GUIDE.md) — Setup and configuration
