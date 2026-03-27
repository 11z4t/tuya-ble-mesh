"""Constants for the Tuya BLE Mesh integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "tuya_ble_mesh"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]

# Config entry data keys
CONF_DEVICE_TYPE = "device_type"
DEVICE_TYPE_LIGHT = "light"
DEVICE_TYPE_PLUG = "plug"
DEVICE_TYPE_SIG_PLUG = "sig_plug"
CONF_MESH_NAME = "mesh_name"
CONF_MESH_PASSWORD = "mesh_password"  # pragma: allowlist secret
CONF_MAC_ADDRESS = "mac_address"
CONF_VENDOR_ID = "vendor_id"
DEFAULT_VENDOR_ID = "0x1001"
CONF_MESH_ADDRESS = "mesh_address"
DEFAULT_MESH_ADDRESS = 0  # 0 = connected device itself

# SIG Mesh config keys
CONF_UNICAST_TARGET = "unicast_target"
CONF_UNICAST_OUR = "unicast_our"
CONF_OP_ITEM_PREFIX = "op_item_prefix"
CONF_IV_INDEX = "iv_index"
CONF_BRIDGE_HOST = "bridge_host"
CONF_BRIDGE_PORT = "bridge_port"

DEFAULT_OP_ITEM_PREFIX = "s17"
DEFAULT_IV_INDEX = 0
DEFAULT_BRIDGE_PORT = 8099

# SIG Mesh key config keys (stored in config entry data)
CONF_NET_KEY = "net_key"
CONF_DEV_KEY = "dev_key"
CONF_APP_KEY = "app_key"

DEVICE_TYPE_SIG_BRIDGE_PLUG = "sig_bridge_plug"
DEVICE_TYPE_TELINK_BRIDGE_LIGHT = "telink_bridge_light"

PLUG_DEVICE_TYPES = {DEVICE_TYPE_PLUG, DEVICE_TYPE_SIG_PLUG, DEVICE_TYPE_SIG_BRIDGE_PLUG}
LIGHT_DEVICE_TYPES = {DEVICE_TYPE_LIGHT, DEVICE_TYPE_TELINK_BRIDGE_LIGHT}

# Human-readable model names shown in the HA device registry
DEVICE_MODEL_NAMES: dict[str, str] = {
    DEVICE_TYPE_LIGHT: "LED Light",
    DEVICE_TYPE_PLUG: "Smart Plug",
    DEVICE_TYPE_SIG_PLUG: "Smart Plug",
    DEVICE_TYPE_SIG_BRIDGE_PLUG: "Smart Plug (Bridge)",
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT: "LED Light (Bridge)",
}

# SIG Mesh service UUIDs (Bluetooth SIG assigned)
SIG_MESH_PROV_UUID = "00001827-0000-1000-8000-00805f9b34fb"  # Provisioning Service
SIG_MESH_PROXY_UUID = "00001828-0000-1000-8000-00805f9b34fb"  # Proxy Service

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

# Command retry defaults
DEFAULT_MAX_COMMAND_RETRIES = 3
DEFAULT_COMMAND_RETRY_BASE_DELAY = 0.5

# Mesh scene/effect presets: scene_id (1-based) → display name
MESH_SCENES: dict[int, str] = {
    1: "Warm Candlelight",
    2: "Ocean Breeze",
    3: "Forest Dawn",
    4: "Sunset Glow",
    5: "Arctic Aurora",
}

# Known vendor IDs: normalized hex string (no '0x', lowercase) → manufacturer name
KNOWN_VENDOR_IDS: dict[str, str] = {
    "1001": "Tuya / Telink",
    "1002": "Malmbergs",
    "1003": "Tuya",
    "1004": "Telink",
}
