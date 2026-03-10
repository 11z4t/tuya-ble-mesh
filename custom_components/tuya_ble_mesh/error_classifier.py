"""Error classification for connection and protocol failures.

Classifies exceptions into categories for targeted error handling,
repair issue creation, and reconnection strategy decisions.
"""

from __future__ import annotations

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


class ErrorClassifier:
    """Classifies connection and protocol errors into categories.

    Uses exception type names and message keywords to determine the root cause
    of failures. This enables targeted error handling (e.g., no retry for
    permanent errors, create mesh auth repair for credential issues).
    """

    @staticmethod
    def classify(err: Exception) -> ErrorClass:
        """Classify a connection error into a category.

        Args:
            err: The exception to classify.

        Returns:
            ErrorClass indicating the failure category.

        Examples:
            >>> classify(TimeoutError("Connection timeout"))
            ErrorClass.TRANSIENT

            >>> classify(ValueError("Invalid mesh password"))
            ErrorClass.MESH_AUTH

            >>> classify(RuntimeError("Unsupported device"))
            ErrorClass.PERMANENT
        """
        err_type = type(err).__name__.lower()
        err_msg = str(err).lower()

        # Timeout errors are usually transient network issues
        if "timeout" in err_type or "timeout" in err_msg:
            return ErrorClass.TRANSIENT

        # Authentication and credential failures
        if (
            "auth" in err_type
            or "auth" in err_msg
            or "password" in err_msg
            or "credential" in err_msg
        ):
            return ErrorClass.MESH_AUTH

        # Protocol version mismatches and negotiation failures
        if "protocol" in err_type or "protocol" in err_msg or "version" in err_msg:
            return ErrorClass.PROTOCOL

        # Bridge connectivity issues (HTTP/network)
        if "connection refused" in err_msg or "unreachable" in err_msg or "no route" in err_msg:
            return ErrorClass.BRIDGE_DOWN

        # Device not found on the mesh
        if "not found" in err_msg or "device not found" in err_msg:
            return ErrorClass.DEVICE_OFFLINE

        # Unsupported device or vendor (no retry useful)
        if "vendor" in err_msg or "unsupported" in err_msg:
            return ErrorClass.PERMANENT

        # Default to unknown if no pattern matches
        return ErrorClass.UNKNOWN
