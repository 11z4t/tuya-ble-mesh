"""Constants for the Tuya BLE Mesh integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "tuya_ble_mesh"

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.SENSOR, Platform.SWITCH, Platform.UPDATE]

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

# Advanced options keys
CONF_DEBUG_LEVEL = "debug_level"
CONF_COMMAND_TIMEOUT = "command_timeout"
CONF_MAX_RECONNECTS = "max_reconnects"
CONF_RECONNECT_STORM_THRESHOLD = "reconnect_storm_threshold"

DEFAULT_DEBUG_LEVEL = "info"
DEFAULT_COMMAND_TIMEOUT = 10
DEFAULT_MAX_RECONNECTS = 0  # 0 = unlimited
DEFAULT_RECONNECT_STORM_THRESHOLD = 10  # per 5-minute window

# BLE command retry settings
CONF_MAX_COMMAND_RETRIES = "max_command_retries"
DEFAULT_MAX_COMMAND_RETRIES = 3  # Retry up to 3 times on transient failure
DEFAULT_COMMAND_RETRY_BASE_DELAY = 0.5  # seconds — doubles each retry

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

# Error classification constants (used by coordinator and repairs)
ERROR_BRIDGE_UNREACHABLE = "bridge_unreachable"
ERROR_AUTH_OR_MESH_MISMATCH = "auth_or_mesh_mismatch"
ERROR_UNSUPPORTED_VENDOR = "unsupported_vendor"
ERROR_DEVICE_NOT_FOUND = "device_not_found"
ERROR_TIMEOUT = "timeout"
ERROR_RECONNECT_STORM = "reconnect_storm"
ERROR_PROTOCOL_MISMATCH = "protocol_mismatch"
ERROR_UNKNOWN = "unknown"

# SIG Mesh key config keys (stored in config entry data)
CONF_NET_KEY = "net_key"
CONF_DEV_KEY = "dev_key"
CONF_APP_KEY = "app_key"

DEVICE_TYPE_SIG_BRIDGE_PLUG = "sig_bridge_plug"
DEVICE_TYPE_TELINK_BRIDGE_LIGHT = "telink_bridge_light"

PLUG_DEVICE_TYPES = {DEVICE_TYPE_PLUG, DEVICE_TYPE_SIG_PLUG, DEVICE_TYPE_SIG_BRIDGE_PLUG}
LIGHT_DEVICE_TYPES = {DEVICE_TYPE_LIGHT, DEVICE_TYPE_TELINK_BRIDGE_LIGHT}

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

# --- Mesh Scene Registry ---
# Maps scene_id → human-readable effect name shown in HA UI.
# scene_id=0 is reserved for "no scene" (custom mode).
# These IDs align with Telink Mesh scene slot numbers (1-based).
MESH_SCENES: dict[int, str] = {
    1: "Warm Candlelight",
    2: "Cool Daylight",
    3: "RGB Sunset",
    4: "Ocean Breeze",
    5: "Forest Green",
    6: "Party Flash",
    7: "Soft Glow",
}

# --- Vendor ID Registry (single source of truth) ---
# Maps vendor ID hex string to human-readable brand name.
# These are BLE SIG company IDs or Telink mesh vendor bytes (LE uint16 as 0xNNNN).
# Used by config_flow, diagnostics, and validation.
#
# Known devices confirmed via HCI snoop / BLE scan:
#   Malmbergs BT Smart  : 0x1001  (TELINK_VENDOR_ID = bytes([0x01, 0x10]))
#   AwoX                : 0x0160
#   Dimond/retsimx      : 0x0211
KNOWN_VENDOR_IDS: dict[str, str] = {
    "0x1001": "Malmbergs BT Smart",
    "0x0160": "AwoX",
    "0x0211": "Dimond/retsimx",
}
