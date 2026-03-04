"""Constants for the Tuya BLE Mesh integration."""

from __future__ import annotations

DOMAIN = "tuya_ble_mesh"

PLATFORMS: list[str] = ["light", "sensor", "switch"]

# Config entry data keys
CONF_DEVICE_TYPE = "device_type"
DEVICE_TYPE_LIGHT = "light"
DEVICE_TYPE_PLUG = "plug"
CONF_MESH_NAME = "mesh_name"
CONF_MESH_PASSWORD = "mesh_password"  # pragma: allowlist secret
CONF_MAC_ADDRESS = "mac_address"
CONF_VENDOR_ID = "vendor_id"
DEFAULT_VENDOR_ID = "0x1001"

# Brightness mapping: device 1-100 ↔ HA 1-255
DEVICE_BRIGHTNESS_MIN = 1
DEVICE_BRIGHTNESS_MAX = 100
HA_BRIGHTNESS_MIN = 1
HA_BRIGHTNESS_MAX = 255

# Color temperature mapping: device 0(warm)-127(cool) ↔ mireds 370(warm)-153(cool)
DEVICE_COLOR_TEMP_MIN = 0  # warmest
DEVICE_COLOR_TEMP_MAX = 127  # coolest
HA_MIRED_MIN = 153  # coolest (6536K)
HA_MIRED_MAX = 370  # warmest (2703K)

# Color brightness mapping: device 0-255 ↔ HA 0-255 (same scale)
DEVICE_COLOR_BRIGHTNESS_MIN = 0
DEVICE_COLOR_BRIGHTNESS_MAX = 255
