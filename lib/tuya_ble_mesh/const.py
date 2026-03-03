"""BLE Mesh constants for Tuya BLE Mesh devices.

UUIDs, opcodes, DP types, mesh categories, and device defaults.
All 128-bit UUIDs are lowercase strings matching bleak conventions.
"""

# --- Bluetooth SIG Base UUID ---
# 16-bit UUIDs expand as: 0000XXXX-0000-1000-8000-00805f9b34fb

BT_BASE_UUID_FMT = "0000{:04x}-0000-1000-8000-00805f9b34fb"

# --- Tuya Mesh Service (Advertising) ---

TUYA_MESH_SERVICE_UUID = "0000fe07-0000-1000-8000-00805f9b34fb"

# --- SIG Mesh Standard Services ---

SIG_MESH_PROVISIONING_SERVICE = "00001827-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROXY_SERVICE = "00001828-0000-1000-8000-00805f9b34fb"

# SIG Mesh Provisioning Characteristics
SIG_MESH_PROV_DATA_IN = "00002adb-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROV_DATA_OUT = "00002adc-0000-1000-8000-00805f9b34fb"

# SIG Mesh Proxy Characteristics
SIG_MESH_PROXY_DATA_IN = "00002add-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROXY_DATA_OUT = "00002ade-0000-1000-8000-00805f9b34fb"

# --- Device Information Service (0x180A) ---

DEVICE_INFORMATION_SERVICE = "0000180a-0000-1000-8000-00805f9b34fb"
DIS_MANUFACTURER_NAME = "00002a29-0000-1000-8000-00805f9b34fb"
DIS_MODEL_NUMBER = "00002a24-0000-1000-8000-00805f9b34fb"
DIS_FIRMWARE_REVISION = "00002a26-0000-1000-8000-00805f9b34fb"
DIS_HARDWARE_REVISION = "00002a27-0000-1000-8000-00805f9b34fb"
DIS_SOFTWARE_REVISION = "00002a28-0000-1000-8000-00805f9b34fb"

# Set for quick membership tests
DIS_CHARACTERISTICS: frozenset[str] = frozenset(
    {
        DIS_MANUFACTURER_NAME,
        DIS_MODEL_NUMBER,
        DIS_FIRMWARE_REVISION,
        DIS_HARDWARE_REVISION,
        DIS_SOFTWARE_REVISION,
    }
)

# --- Tuya Custom GATT Service (Proprietary Mesh) ---
# Expected UUIDs from documentation (standard BT SIG base):
TUYA_CUSTOM_SERVICE = "00001910-0000-1000-8000-00805f9b34fb"
TUYA_CHAR_COMMAND_TX = "00001911-0000-1000-8000-00805f9b34fb"
TUYA_CHAR_COMMAND_RX = "00001912-0000-1000-8000-00805f9b34fb"
TUYA_CHAR_PAIRING = "00001913-0000-1000-8000-00805f9b34fb"
TUYA_CHAR_OTA = "00001914-0000-1000-8000-00805f9b34fb"

# --- Telink-based Tuya GATT Service (Confirmed on Malmbergs 9952126) ---
# Telink BLE mesh uses a custom base UUID: 00010203-0405-0607-0809-0a0b0c0dXXXX
# Same suffix pattern (1910-1914), different base from BT SIG standard.
# Role mapping confirmed via python-awox-mesh-light reference analysis.
TELINK_BASE_UUID_PREFIX = "00010203-0405-0607-0809-0a0b0c0d"
TELINK_CUSTOM_SERVICE = "00010203-0405-0607-0809-0a0b0c0d1910"
TELINK_CHAR_STATUS = "00010203-0405-0607-0809-0a0b0c0d1911"  # notify + write(enable)
TELINK_CHAR_COMMAND = "00010203-0405-0607-0809-0a0b0c0d1912"  # write-without-response
TELINK_CHAR_OTA = "00010203-0405-0607-0809-0a0b0c0d1913"  # write-without-response
TELINK_CHAR_PAIRING = "00010203-0405-0607-0809-0a0b0c0d1914"  # write + read

# Short ID suffix used to identify Telink mesh characteristics
TUYA_CHAR_SUFFIX_SERVICE = "1910"
TUYA_CHAR_SUFFIX_STATUS = "1911"
TUYA_CHAR_SUFFIX_COMMAND = "1912"
TUYA_CHAR_SUFFIX_OTA = "1913"
TUYA_CHAR_SUFFIX_PAIRING = "1914"

# --- Telink Mesh Pairing Opcodes ---
# Prefix bytes for pair characteristic (1914) read/write operations.
PAIR_OPCODE_REQUEST = 0x0C  # Controller → device: pair request
PAIR_OPCODE_SUCCESS = 0x0D  # Device → controller: pair accepted
PAIR_OPCODE_FAILURE = 0x0E  # Device → controller: auth failure
PAIR_OPCODE_SET_NAME = 0x04  # Controller → device: set new mesh name
PAIR_OPCODE_SET_PASS = 0x05  # Controller → device: set new mesh password
PAIR_OPCODE_SET_LTK = 0x06  # Controller → device: set long-term key
PAIR_OPCODE_SET_OK = 0x07  # Device → controller: credentials accepted

# --- Telink Mesh Command Codes ---
# Sent as the command byte in encrypted command packets via char 1912.
TELINK_CMD_POWER = 0xD0
TELINK_CMD_LIGHT_MODE = 0x33
TELINK_CMD_COLOR = 0xE2
TELINK_CMD_WHITE_TEMP = 0xF0
TELINK_CMD_WHITE_BRIGHTNESS = 0xF1
TELINK_CMD_COLOR_BRIGHTNESS = 0xF2
TELINK_CMD_MESH_ADDRESS = 0xE0
TELINK_CMD_MESH_RESET = 0xE3
TELINK_CMD_MESH_GROUP = 0xD7
TELINK_CMD_PRESET = 0xC8
TELINK_CMD_SEQ_COLOR_DURATION = 0xF5
TELINK_CMD_SEQ_FADE_DURATION = 0xF6
TELINK_CMD_TIME = 0xE4
TELINK_CMD_ALARMS = 0xE5

# Telink command packet fixed bytes (vendor/application identifier)
# Confirmed from HCI snoop capture of Malmbergs BLE app (2026-03-03):
# App sends vendor bytes 01 10 (LE uint16: 0x1001), NOT 60 01 (0x0160).
TELINK_VENDOR_ID = bytes([0x01, 0x10])

# --- Telink Mesh Status Offsets ---
# Byte offsets within a decrypted status notification from char 1911.
STATUS_OFFSET_MESH_ID = 3
STATUS_OFFSET_MODE = 12
STATUS_OFFSET_WHITE_BRIGHTNESS = 13
STATUS_OFFSET_WHITE_TEMP = 14
STATUS_OFFSET_COLOR_BRIGHTNESS = 15
STATUS_OFFSET_RED = 16
STATUS_OFFSET_GREEN = 17
STATUS_OFFSET_BLUE = 18

# --- Tuya Vendor Model (SIG Mesh) ---

TUYA_VENDOR_COMPANY_ID = 0x07D0

# 3-byte vendor opcodes (little-endian company ID + opcode byte)
TUYA_OPCODE_WRITE_ACK = 0xC9D007
TUYA_OPCODE_WRITE_UNACK = 0xCAD007
TUYA_OPCODE_STATUS = 0xCBD007
TUYA_OPCODE_READ = 0xCCD007
TUYA_OPCODE_DATA = 0xCDD007

# Vendor model command types
TUYA_CMD_DP_DATA = 0x01
TUYA_CMD_TIMESTAMP_SYNC = 0x02

# --- Data Point Types ---

DP_TYPE_RAW = 0x00
DP_TYPE_BOOLEAN = 0x01
DP_TYPE_VALUE = 0x02
DP_TYPE_STRING = 0x03
DP_TYPE_ENUM = 0x04
DP_TYPE_BITMAP = 0x05

DP_TYPE_NAMES: dict[int, str] = {
    DP_TYPE_RAW: "raw",
    DP_TYPE_BOOLEAN: "boolean",
    DP_TYPE_VALUE: "value",
    DP_TYPE_STRING: "string",
    DP_TYPE_ENUM: "enum",
    DP_TYPE_BITMAP: "bitmap",
}

# --- Expected Light DPs (category dj) ---

DP_POWER = 1
DP_MODE = 2
DP_BRIGHTNESS = 3
DP_COLOR_TEMP = 4
DP_COLOR_HSV = 5
DP_SCENE_DATA = 6
DP_MUSIC_SYNC = 8

# --- Mesh Category Codes ---

# Product category (upper nibble of category byte)
MESH_CATEGORY_LIGHTS = 0x01
MESH_CATEGORY_ELECTRICAL = 0x02
MESH_CATEGORY_SENSORS = 0x04
MESH_CATEGORY_REMOTES = 0x05
MESH_CATEGORY_WIRELESS_SWITCHES = 0x06

# Light type (full 16-bit mesh category)
MESH_LIGHT_C = 0x1011  # Cool white
MESH_LIGHT_CW = 0x1012  # Cool + warm white
MESH_LIGHT_RGB = 0x1013  # RGB
MESH_LIGHT_RGBC = 0x1014  # RGBC
MESH_LIGHT_RGBCW = 0x1015  # RGBCW

MESH_LIGHT_TYPE_NAMES: dict[int, str] = {
    MESH_LIGHT_C: "C (cool white)",
    MESH_LIGHT_CW: "CW (cool + warm white)",
    MESH_LIGHT_RGB: "RGB",
    MESH_LIGHT_RGBC: "RGBC",
    MESH_LIGHT_RGBCW: "RGBCW",
}

# --- SIG Mesh Standard Model IDs ---

SIG_MODEL_GENERIC_ONOFF = 0x1000
SIG_MODEL_LIGHT_LIGHTNESS = 0x1300
SIG_MODEL_LIGHT_CTL = 0x1306
SIG_MODEL_LIGHT_HSL = 0x1307
TUYA_VENDOR_MODEL_ID = 0x07D00004

# --- Device Defaults ---

# Documented Tuya public defaults for unprovisioned devices.
# These are NOT secrets — they are published in Tuya developer documentation
# and advertised openly by unprovisioned devices.
TUYA_MESH_DEFAULT_NAME = "out_of_mesh"
TUYA_MESH_DEFAULT_PASSWORD = "123456"  # nosec B105 — documented public default

# Name patterns for detecting Tuya mesh devices
TUYA_MESH_NAME_PATTERNS: tuple[str, ...] = ("out_of_mesh", "tymesh")

# --- Target Device ---

TARGET_DEVICE_MAC = "DC:23:4D:21:43:A5"
TARGET_DEVICE_MODEL = "9952126"
TARGET_DEVICE_NAME = "Malmbergs LED Driver"
TARGET_DEVICE_CATEGORY = "dj"
