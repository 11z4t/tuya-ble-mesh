# API Reference — Tuya BLE Mesh Integration

This document describes the public API surfaces for the Tuya BLE Mesh integration.

## Table of Contents

1. [Home Assistant Services](#home-assistant-services)
2. [Entity Platforms](#entity-platforms)
3. [Configuration](#configuration)
4. [Core Library API](#core-library-api)
5. [Events](#events)
6. [Diagnostics](#diagnostics)

---

## Home Assistant Services

### `tuya_ble_mesh.identify`

Flash the device LED to visually identify it.

**Service Data:**
```yaml
service: tuya_ble_mesh.identify
data:
  device_id: <device_id>
```

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `device_id` | string | Yes | Home Assistant device ID |

**Example:**
```yaml
service: tuya_ble_mesh.identify
data:
  device_id: 1234567890abcdef
```

**Behavior:**
- Device LED will flash 3 times over 3 seconds
- Works for both lights and switches
- Uses standard BLE mesh identify command

---

### `tuya_ble_mesh.set_log_level`

Change BLE mesh logging verbosity without restarting Home Assistant.

**Service Data:**
```yaml
service: tuya_ble_mesh.set_log_level
data:
  level: debug  # debug | info | warning | error
```

**Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `level` | string | Yes | Logging level: `debug`, `info`, `warning`, `error` |

**Example:**
```yaml
service: tuya_ble_mesh.set_log_level
data:
  level: debug
```

**Behavior:**
- Changes logging level for `tuya_ble_mesh` logger
- Affects all BLE mesh protocol and connection logs
- Persists until Home Assistant restart

---

## Entity Platforms

### Light Platform

**Entity ID Format:** `light.<device_name>`

**Supported Features:**
- `SUPPORT_BRIGHTNESS` — 1-100% dimming
- `SUPPORT_COLOR_TEMP` — Warm to cool white (CCT)
- `SUPPORT_COLOR` — RGB color (device-dependent)
- `SUPPORT_TRANSITION` — Smooth transitions

**Attributes:**
| Attribute | Type | Description |
|-----------|------|-------------|
| `brightness` | int | 0-255 brightness level |
| `color_temp` | int | Color temperature in mireds (153-500) |
| `rgb_color` | tuple | RGB color (r, g, b) |
| `hs_color` | tuple | Hue/saturation color |
| `supported_color_modes` | list | List of supported color modes |

**Methods:**

#### `turn_on()`
```python
await hass.services.async_call(
    "light",
    "turn_on",
    {
        "entity_id": "light.malmbergs_led_driver",
        "brightness_pct": 75,
        "color_temp": 300,
        "transition": 2
    }
)
```

#### `turn_off()`
```python
await hass.services.async_call(
    "light",
    "turn_off",
    {
        "entity_id": "light.malmbergs_led_driver",
        "transition": 1
    }
)
```

---

### Switch Platform

**Entity ID Format:** `switch.<device_name>`

**Supported Features:**
- Power on/off

**Attributes:**
| Attribute | Type | Description |
|-----------|------|-------------|
| `is_on` | bool | Current power state |

**Methods:**

#### `turn_on()`
```python
await hass.services.async_call(
    "switch",
    "turn_on",
    {"entity_id": "switch.malmbergs_plug_s17"}
)
```

#### `turn_off()`
```python
await hass.services.async_call(
    "switch",
    "turn_off",
    {"entity_id": "switch.malmbergs_plug_s17"}
)
```

---

### Sensor Platform

**Entity ID Format:** `sensor.<device_name>_signal`

**Sensor Types:**

#### RSSI Signal Strength
- **Unit:** dBm
- **State Class:** `measurement`
- **Device Class:** `signal_strength`
- **Update Interval:** 30 seconds (adaptive based on state)

**Attributes:**
| Attribute | Type | Description |
|-----------|------|-------------|
| `state` | int | Signal strength in dBm (-100 to 0) |
| `unit_of_measurement` | string | `dBm` |

**Interpretation:**
- `-30 to -50 dBm`: Excellent signal
- `-50 to -70 dBm`: Good signal
- `-70 to -85 dBm`: Fair signal (may drop)
- `< -85 dBm`: Poor signal (add BLE proxy)

---

## Configuration

### Config Flow Options

**Initial Setup:**
```python
{
    "device_type": "light",  # or "plug"
    "mac_address": "DC:23:4D:21:43:A5",
    "mesh_name": "out_of_mesh",
    "mesh_password": "123456",
    "vendor_id": "0x1001",
    "bridge_host": None,  # optional
    "bridge_port": 8787   # optional
}
```

**Reconfigure Options:**
```python
{
    "mesh_name": "out_of_mesh",
    "mesh_password": "123456",
    "vendor_id": "0x1001"
}
```

### Configuration Schema

```python
CONFIG_SCHEMA = vol.Schema({
    vol.Required("device_type"): vol.In(["light", "plug"]),
    vol.Required("mac_address"): cv.string,
    vol.Optional("mesh_name", default="out_of_mesh"): cv.string,
    vol.Optional("mesh_password", default="123456"): cv.string,
    vol.Optional("vendor_id", default="0x1001"): cv.string,
    vol.Optional("bridge_host"): cv.string,
    vol.Optional("bridge_port", default=8787): cv.port,
})
```

---

## Core Library API

The integration uses the standalone `lib/tuya_ble_mesh/` library.

### Device Control

```python
from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh import TuyaBLEMeshDevice

# Initialize device
device = TuyaBLEMeshDevice(
    mac_address="DC:23:4D:21:43:A5",
    mesh_name="out_of_mesh",
    mesh_password="123456",
    vendor_id=0x1001
)

# Connect
await device.connect()

# Turn on
await device.turn_on()

# Set brightness (0-100)
await device.set_brightness(75)

# Set color temperature (0-100, warm to cool)
await device.set_color_temp(50)

# Turn off
await device.turn_off()

# Disconnect
await device.disconnect()
```

### Scanner

```python
from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh import TuyaBLEMeshScanner

# Scan for devices
scanner = TuyaBLEMeshScanner()
devices = await scanner.scan(timeout=10)

for device in devices:
    print(f"Found: {device.name} ({device.address})")
```

### Protocol Encoding

```python
from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.protocol import encode_command

# Encode a power command
packet = encode_command(
    command_type="power",
    value=True,
    mesh_name="out_of_mesh",
    mesh_password="123456",
    vendor_id=0x1001
)

# Send via BLE GATT
await device.write_gatt_char(CHARACTERISTIC_UUID, packet)
```

---

## Events

### Device State Changes

The integration uses push-based updates via BLE notifications. When a device state changes:

1. BLE notification received
2. Coordinator decodes state
3. Entity state updated
4. Home Assistant fires `state_changed` event

**Event Data:**
```python
{
    "entity_id": "light.malmbergs_led_driver",
    "old_state": {
        "state": "off",
        "attributes": {}
    },
    "new_state": {
        "state": "on",
        "attributes": {
            "brightness": 192,
            "color_temp": 300
        }
    }
}
```

---

## Diagnostics

### Device Diagnostics Data

Access via **Settings** → **Devices & Services** → **Tuya BLE Mesh** → Device → **Download Diagnostics**.

**Data Structure:**
```python
{
    "device_info": {
        "mac_address": "DC:23:4D:21:43:A5",
        "device_type": "light",
        "vendor_id": "0x1001",
        "firmware_version": "1.2.3"
    },
    "connection_stats": {
        "connected": True,
        "rssi": -65,
        "connection_attempts": 12,
        "reconnect_count": 2,
        "last_seen": "2026-03-10T12:00:00Z"
    },
    "mesh_config": {
        "mesh_name": "out_of_mesh",
        "mesh_password": "******",  # redacted
        "bridge_host": None
    },
    "entity_states": {
        "light.malmbergs_led_driver": {
            "state": "on",
            "brightness": 192,
            "color_temp": 300
        },
        "sensor.malmbergs_led_driver_signal": {
            "state": -65,
            "unit": "dBm"
        }
    }
}
```

---

## Integration Lifecycle

### Setup Flow

```python
async def async_setup_entry(hass, entry):
    """Set up Tuya BLE Mesh from a config entry."""

    # 1. Initialize coordinator
    coordinator = TuyaBLEMeshCoordinator(hass, entry)

    # 2. Connect to device
    await coordinator.async_config_entry_first_refresh()

    # 3. Store coordinator
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # 4. Forward to platforms
    await hass.config_entries.async_forward_entry_setups(
        entry, ["light", "switch", "sensor"]
    )

    return True
```

### Unload Flow

```python
async def async_unload_entry(hass, entry):
    """Unload a config entry."""

    # 1. Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry, ["light", "switch", "sensor"]
    )

    # 2. Disconnect device
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_shutdown()

    # 3. Remove from hass.data
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
```

---

## Error Handling

### Common Exceptions

```python
from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.exceptions import (
    TuyaBLEMeshError,
    ConnectionError,
    CommandError,
    EncryptionError
)

try:
    await device.turn_on()
except ConnectionError:
    # Device not reachable
    _LOGGER.error("Failed to connect to device")
except CommandError:
    # Command rejected (wrong vendor ID, etc.)
    _LOGGER.error("Device rejected command")
except EncryptionError:
    # Wrong mesh credentials
    _LOGGER.error("Mesh encryption failed")
except TuyaBLEMeshError as e:
    # Generic error
    _LOGGER.error("BLE mesh error: %s", e)
```

---

## Performance Considerations

### Connection Management
- **Auto-reconnect:** Exponential backoff (5s → 5min)
- **Keep-alive:** Ping every 30s when idle
- **Command queue:** TTL-based with overflow protection

### Update Frequency
- **State changes:** Immediate (push-based via BLE notifications)
- **RSSI sensor:** 30s when on, 5min when off

### Resource Usage
- **BLE connections:** 1 per device (multiplexed via coordinator)
- **Memory:** ~1 MB per device (includes protocol buffers)
- **CPU:** Minimal (event-driven, not polling)

---

## Version Compatibility

| Integration Version | HA Minimum | Python | BLE Stack |
|---------------------|-----------|---------|-----------|
| 0.19.x | 2024.1 | 3.11+ | BlueZ 5.50+ |
| 0.18.x | 2024.1 | 3.11+ | BlueZ 5.50+ |
| 0.17.x | 2024.1 | 3.11+ | BlueZ 5.50+ |

---

## References

- [Home Assistant Developer Docs](https://developers.home-assistant.io/)
- [Bluetooth Integration](https://www.home-assistant.io/integrations/bluetooth/)
- [Tuya BLE Mesh Protocol](PROTOCOL.md)
- [Architecture Overview](ARCHITECTURE.md)
