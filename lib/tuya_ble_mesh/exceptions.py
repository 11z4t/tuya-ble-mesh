"""BLE exception hierarchy for Tuya BLE Mesh operations.

Each exception carries a descriptive message. Callers can catch the
base ``BLEError`` for general handling or specific subclasses for
targeted recovery.

SECURITY: Exception messages MUST NEVER contain secret material
(keys, passwords, tokens). Use length/type descriptions only.
"""


class BLEError(Exception):
    """Base exception for all BLE operations."""


class BLEConnectionError(BLEError):
    """Failed to establish or maintain a BLE connection."""


class BLEDeviceNotFoundError(BLEError):
    """Target BLE device was not discovered during scanning."""


class BLEServiceError(BLEError):
    """Expected GATT service not found on the device."""


class BLECharacteristicError(BLEError):
    """Failed to read, write, or interact with a GATT characteristic."""


class BLETimeoutError(BLEError):
    """BLE operation exceeded the allowed time limit."""


class BLENotificationError(BLEError):
    """Failed to subscribe to or receive GATT notifications."""
