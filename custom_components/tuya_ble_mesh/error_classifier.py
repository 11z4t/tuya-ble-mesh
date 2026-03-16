"""Error classification for connection and protocol failures.

Classifies exceptions into categories for targeted error handling,
repair issue creation, and reconnection strategy decisions.

Extracted from ConnectionManager (PLAT-668).
"""

from __future__ import annotations

import asyncio
from enum import StrEnum


class ErrorClass(StrEnum):
    """Classification of connection/protocol errors for repair creation."""

    BRIDGE_DOWN = "bridge_down"
    DEVICE_OFFLINE = "device_offline"
    MESH_AUTH = "mesh_auth"
    PROTOCOL = "protocol"
    PERMANENT = "permanent"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


def classify_error(err: Exception) -> ErrorClass:
    """Classify a connection error into a category.

    Two-stage classification:
    1. **isinstance checks** for lib exception hierarchy (precise)
    2. **String heuristics** for generic exceptions (fallback)

    Args:
        err: The exception to classify.

    Returns:
        ErrorClass indicating the failure category.

    Examples:
        >>> from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.exceptions import AuthenticationError
        >>> classify_error(AuthenticationError("bad key"))
        ErrorClass.MESH_AUTH

        >>> classify_error(TimeoutError("Connection timeout"))
        ErrorClass.TRANSIENT

        >>> classify_error(Exception("Invalid mesh password"))
        ErrorClass.MESH_AUTH
    """
    # --- Stage 1: isinstance checks for lib exception hierarchy ---
    # Import locally to avoid circular dependencies
    try:
        from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.exceptions import (
            AuthenticationError,
            CryptoError,
            DeviceNotFoundError,
            MeshConnectionError,
            MeshTimeoutError,
            ProtocolError,
            SIGMeshKeyError,
        )

        # Auth and crypto errors
        if isinstance(err, (AuthenticationError, CryptoError, SIGMeshKeyError)):
            return ErrorClass.MESH_AUTH

        # Timeout errors (transient)
        if isinstance(err, MeshTimeoutError):
            return ErrorClass.TRANSIENT

        # Protocol errors
        if isinstance(err, ProtocolError):
            return ErrorClass.PROTOCOL

        # Device not found
        if isinstance(err, DeviceNotFoundError):
            return ErrorClass.DEVICE_OFFLINE

        # MeshConnectionError — check message for bridge-specific patterns
        if isinstance(err, MeshConnectionError):
            err_msg = str(err).lower()
            if any(
                keyword in err_msg
                for keyword in ["refused", "unreachable", "no route"]
            ):
                return ErrorClass.BRIDGE_DOWN
            # Generic connection error → transient
            return ErrorClass.TRANSIENT

    except ImportError:
        # If lib imports fail, fall through to string heuristics
        pass

    # --- Stage 2: String heuristics for generic exceptions ---
    err_type = type(err).__name__.lower()
    err_msg = str(err).lower()

    # Timeout errors are usually transient network issues
    if isinstance(err, (TimeoutError, asyncio.TimeoutError)) or "timeout" in err_msg:
        return ErrorClass.TRANSIENT

    # Authentication and credential failures
    if any(
        keyword in err_type or keyword in err_msg
        for keyword in ["auth", "password", "credential"]
    ):
        return ErrorClass.MESH_AUTH

    # Protocol version mismatches and negotiation failures
    if any(keyword in err_msg for keyword in ["protocol", "version"]):
        return ErrorClass.PROTOCOL

    # Bridge connectivity issues (HTTP/network)
    if any(
        keyword in err_msg
        for keyword in ["connection refused", "unreachable", "no route"]
    ):
        return ErrorClass.BRIDGE_DOWN

    # Device not found on the mesh
    if "not found" in err_msg:
        return ErrorClass.DEVICE_OFFLINE

    # Unsupported device or vendor (no retry useful)
    if any(keyword in err_msg for keyword in ["vendor", "unsupported"]):
        return ErrorClass.PERMANENT

    # Default to unknown if no pattern matches
    return ErrorClass.UNKNOWN


