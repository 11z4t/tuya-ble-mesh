"""Exception hierarchy for Malmbergs BT / Tuya BLE Mesh.

All exceptions inherit from ``MalmbergsBTError``. Callers can catch the
base class for broad handling or specific subclasses for targeted recovery.

SECURITY: Exception messages MUST NEVER contain secret material
(keys, passwords, tokens). Use length/type descriptions only.
"""


class MalmbergsBTError(Exception):
    """Base exception for all Malmbergs BT operations."""


class ConnectionError(MalmbergsBTError):
    """Failed to establish or maintain a BLE connection."""


class DeviceNotFoundError(MalmbergsBTError):
    """Target BLE device was not discovered during scanning."""


class TimeoutError(MalmbergsBTError):
    """BLE operation exceeded the allowed time limit."""


class ProvisioningError(MalmbergsBTError):
    """Provisioning handshake failed."""


class ProtocolError(MalmbergsBTError):
    """Wire-level protocol violation."""


class MalformedPacketError(ProtocolError):
    """Received packet failed structural validation."""


class CryptoError(MalmbergsBTError):
    """Cryptographic operation failed."""


class AuthenticationError(CryptoError):
    """Mesh authentication (session key / pair proof) failed."""


class SecretAccessError(MalmbergsBTError):
    """Failed to read or write a secret via 1Password."""


class PowerControlError(MalmbergsBTError):
    """Shelly power control operation failed."""


# --- Backward-compatible aliases (used by Phase 1 scripts) ---

BLEError = MalmbergsBTError
BLEConnectionError = ConnectionError
BLEDeviceNotFoundError = DeviceNotFoundError
BLETimeoutError = TimeoutError
BLEServiceError = ProtocolError
BLECharacteristicError = ProtocolError
BLENotificationError = ConnectionError
