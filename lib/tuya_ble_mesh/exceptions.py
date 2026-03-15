"""Exception hierarchy for Tuya BLE Mesh.

All exceptions inherit from ``TuyaBLEMeshError``. Callers can catch the
base class for broad handling or specific subclasses for targeted recovery.

SECURITY: Exception messages MUST NEVER contain secret material
(keys, passwords, tokens). Use length/type descriptions only.
"""

from __future__ import annotations


class TuyaBLEMeshError(Exception):
    """Base exception for all Tuya BLE Mesh operations."""


class MeshConnectionError(TuyaBLEMeshError):
    """Failed to establish or maintain a BLE connection."""


# Backward-compatible alias — internal lib code imports this by name.
ConnectionError = MeshConnectionError


class DeviceNotFoundError(TuyaBLEMeshError):
    """Target BLE device was not discovered during scanning."""


class MeshTimeoutError(TuyaBLEMeshError):
    """BLE operation exceeded the allowed time limit."""


# DEPRECATED: kept for backward compat, use MeshTimeoutError
TimeoutError = MeshTimeoutError


class ProvisioningError(TuyaBLEMeshError):
    """Provisioning handshake failed."""


class ProtocolError(TuyaBLEMeshError):
    """Wire-level protocol violation."""


class MalformedPacketError(ProtocolError):
    """Received packet failed structural validation."""


class CryptoError(TuyaBLEMeshError):
    """Cryptographic operation failed."""


class AuthenticationError(CryptoError):
    """Mesh authentication (session key / pair proof) failed."""


class SecretAccessError(TuyaBLEMeshError):
    """Failed to read or write a secret via 1Password."""


class SIGMeshError(TuyaBLEMeshError):
    """SIG Mesh protocol or configuration error."""


class SIGMeshKeyError(SIGMeshError):
    """Required SIG Mesh key not available."""


class PowerControlError(TuyaBLEMeshError):
    """Bridge power control operation failed."""


class DisconnectedError(MeshConnectionError):
    """Operation attempted while device is disconnected."""


class CommandQueueFullError(TuyaBLEMeshError):
    """Command queue has reached its maximum capacity."""


class CommandExpiredError(TuyaBLEMeshError):
    """Command expired before it could be sent (TTL exceeded)."""


class InvalidRequestError(TuyaBLEMeshError):
    """Invalid command request parameters."""


class InvalidResultError(TuyaBLEMeshError):
    """Invalid command result data."""


class CorrelationConflictError(TuyaBLEMeshError):
    """Correlation key conflict — request_id already registered."""


# --- Backward-compatible aliases ---

# Phase 2 → Phase 3 rename
MalmbergsBTError = TuyaBLEMeshError

# Phase 1 legacy aliases
BLEError = TuyaBLEMeshError
BLEConnectionError = MeshConnectionError
BLEDeviceNotFoundError = DeviceNotFoundError
BLETimeoutError = MeshTimeoutError
BLEServiceError = ProtocolError
BLECharacteristicError = ProtocolError
BLENotificationError = MeshConnectionError
