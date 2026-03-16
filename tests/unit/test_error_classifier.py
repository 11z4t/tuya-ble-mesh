"""Unit tests for the error_classifier module (PLAT-668).

Tests the standalone ``classify_error()`` function and ``ErrorClass`` enum
extracted from ConnectionManager / coordinator.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from custom_components.tuya_ble_mesh.error_classifier import (  # noqa: E402
    ErrorClass,
    classify_error,
)
from tuya_ble_mesh.exceptions import (  # noqa: E402
    AuthenticationError,
    CryptoError,
    DeviceNotFoundError,
    MeshConnectionError,
    MeshTimeoutError,
    ProtocolError,
    SIGMeshKeyError,
)


# ---------------------------------------------------------------------------
# ErrorClass enum
# ---------------------------------------------------------------------------


class TestErrorClassEnum:
    """Test ErrorClass enum values and membership."""

    def test_has_seven_members(self) -> None:
        """ErrorClass must have exactly 7 members."""
        assert len(ErrorClass) == 7

    def test_error_class_values(self) -> None:
        """Each member must equal its documented string value."""
        assert ErrorClass.BRIDGE_DOWN.value == "bridge_down"
        assert ErrorClass.DEVICE_OFFLINE.value == "device_offline"
        assert ErrorClass.MESH_AUTH.value == "mesh_auth"
        assert ErrorClass.PROTOCOL.value == "protocol"
        assert ErrorClass.PERMANENT.value == "permanent"
        assert ErrorClass.TRANSIENT.value == "transient"
        assert ErrorClass.UNKNOWN.value == "unknown"

    def test_error_class_is_string_enum(self) -> None:
        """ErrorClass values are usable as plain strings."""
        assert isinstance(ErrorClass.BRIDGE_DOWN.value, str)
        assert isinstance(ErrorClass.TRANSIENT, ErrorClass)
        # StrEnum means the member itself is also a str
        assert isinstance(ErrorClass.TRANSIENT, str)


# ---------------------------------------------------------------------------
# Stage 1 — lib exception hierarchy (isinstance)
# ---------------------------------------------------------------------------


class TestClassifyLibExceptions:
    """Tests for Stage 1: isinstance-based classification."""

    def test_authentication_error(self) -> None:
        assert classify_error(AuthenticationError("bad key")) == ErrorClass.MESH_AUTH

    def test_crypto_error(self) -> None:
        assert classify_error(CryptoError("decrypt failed")) == ErrorClass.MESH_AUTH

    def test_sig_mesh_key_error(self) -> None:
        assert classify_error(SIGMeshKeyError("missing key")) == ErrorClass.MESH_AUTH

    def test_mesh_timeout_error(self) -> None:
        assert classify_error(MeshTimeoutError("10s")) == ErrorClass.TRANSIENT

    def test_protocol_error(self) -> None:
        assert classify_error(ProtocolError("bad frame")) == ErrorClass.PROTOCOL

    def test_device_not_found_error(self) -> None:
        assert classify_error(DeviceNotFoundError("DC:23")) == ErrorClass.DEVICE_OFFLINE

    def test_mesh_connection_error_refused(self) -> None:
        assert (
            classify_error(MeshConnectionError("Connection refused"))
            == ErrorClass.BRIDGE_DOWN
        )

    def test_mesh_connection_error_unreachable(self) -> None:
        assert (
            classify_error(MeshConnectionError("Host unreachable"))
            == ErrorClass.BRIDGE_DOWN
        )

    def test_mesh_connection_error_no_route(self) -> None:
        assert (
            classify_error(MeshConnectionError("No route to host"))
            == ErrorClass.BRIDGE_DOWN
        )

    def test_mesh_connection_error_generic(self) -> None:
        """Generic MeshConnectionError (no bridge keyword) → TRANSIENT."""
        assert (
            classify_error(MeshConnectionError("BLE link lost"))
            == ErrorClass.TRANSIENT
        )


# ---------------------------------------------------------------------------
# Stage 2 — string heuristic fallback
# ---------------------------------------------------------------------------


class TestClassifyStringHeuristics:
    """Tests for Stage 2: message-based fallback classification."""

    def test_timeout_by_exception_type(self) -> None:
        assert classify_error(TimeoutError("timed out")) == ErrorClass.TRANSIENT

    def test_asyncio_timeout(self) -> None:
        assert classify_error(asyncio.TimeoutError()) == ErrorClass.TRANSIENT

    def test_timeout_by_message(self) -> None:
        assert classify_error(Exception("Request timeout after 30s")) == ErrorClass.TRANSIENT

    def test_auth_keyword(self) -> None:
        assert classify_error(Exception("Authentication failed")) == ErrorClass.MESH_AUTH

    def test_password_keyword(self) -> None:
        assert classify_error(Exception("Invalid password")) == ErrorClass.MESH_AUTH

    def test_credential_keyword(self) -> None:
        assert classify_error(Exception("Mesh credentials rejected")) == ErrorClass.MESH_AUTH

    def test_protocol_keyword(self) -> None:
        assert classify_error(Exception("protocol negotiation failed")) == ErrorClass.PROTOCOL

    def test_version_keyword(self) -> None:
        assert classify_error(Exception("Unsupported protocol version")) == ErrorClass.PROTOCOL

    def test_unsupported_device(self) -> None:
        assert classify_error(Exception("Unsupported device model")) == ErrorClass.PERMANENT

    def test_unsupported_generic(self) -> None:
        assert classify_error(Exception("unsupported feature")) == ErrorClass.PERMANENT

    def test_unknown_vendor(self) -> None:
        assert classify_error(Exception("Unknown vendor ID")) == ErrorClass.PERMANENT

    def test_connection_refused(self) -> None:
        assert classify_error(Exception("Connection refused")) == ErrorClass.BRIDGE_DOWN

    def test_host_unreachable(self) -> None:
        assert classify_error(Exception("Host unreachable")) == ErrorClass.BRIDGE_DOWN

    def test_no_route(self) -> None:
        assert classify_error(Exception("No route to host")) == ErrorClass.BRIDGE_DOWN

    def test_not_found(self) -> None:
        assert classify_error(Exception("Device not found")) == ErrorClass.DEVICE_OFFLINE

    def test_unknown_error(self) -> None:
        assert classify_error(Exception("Something went wrong")) == ErrorClass.UNKNOWN


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestClassifyEdgeCases:
    """Edge cases and precedence in classification."""

    def test_empty_error_message(self) -> None:
        assert classify_error(Exception("")) == ErrorClass.UNKNOWN

    def test_no_args_error(self) -> None:
        assert classify_error(Exception()) == ErrorClass.UNKNOWN

    def test_case_insensitive(self) -> None:
        assert classify_error(Exception("TIMEOUT")) == ErrorClass.TRANSIENT
        assert classify_error(Exception("Authentication Failed")) == ErrorClass.MESH_AUTH
        assert classify_error(Exception("PROTOCOL ERROR")) == ErrorClass.PROTOCOL
        assert classify_error(Exception("Connection Refused")) == ErrorClass.BRIDGE_DOWN

    def test_multiple_keywords_timeout_wins(self) -> None:
        """Timeout keyword appears first in the heuristic chain."""
        assert classify_error(Exception("timeout during authentication")) == ErrorClass.TRANSIENT

    def test_generic_connection_error_with_refused_message(self) -> None:
        """Non-lib ConnectionError classified by message content."""

        class CustomConnError(Exception):
            pass

        assert classify_error(CustomConnError("Connection refused")) == ErrorClass.BRIDGE_DOWN


# ---------------------------------------------------------------------------
# Real-world BLE / HTTP error scenarios
# ---------------------------------------------------------------------------


class TestRealWorldScenarios:
    """Regression tests with production-like error messages."""

    def test_bleak_timeout(self) -> None:
        err = TimeoutError("Bluetooth operation timed out after 10.0 seconds")
        assert classify_error(err) == ErrorClass.TRANSIENT

    def test_http_bridge_connection_pool(self) -> None:
        err = Exception(
            "HTTPConnectionPool: Failed to establish connection - Connection refused"
        )
        assert classify_error(err) == ErrorClass.BRIDGE_DOWN

    def test_mesh_credential_mismatch(self) -> None:
        err = Exception("Mesh authentication failed: password incorrect")
        assert classify_error(err) == ErrorClass.MESH_AUTH

    def test_device_powered_off(self) -> None:
        err = Exception("Device DC:23:4D:21:43:A5 not found in mesh network")
        assert classify_error(err) == ErrorClass.DEVICE_OFFLINE

    def test_firmware_incompatibility(self) -> None:
        err = Exception("Protocol version 2.5 not supported, requires 3.0+")
        assert classify_error(err) == ErrorClass.PROTOCOL


# ---------------------------------------------------------------------------
# Backward-compat: ErrorClass still importable from connection_manager
# ---------------------------------------------------------------------------


class TestBackwardCompat:
    """Verify ErrorClass is re-exported from connection_manager."""

    def test_import_from_connection_manager(self) -> None:
        from custom_components.tuya_ble_mesh.connection_manager import ErrorClass as CM_EC

        assert CM_EC is ErrorClass

    def test_classify_via_connection_manager(self) -> None:
        from custom_components.tuya_ble_mesh.connection_manager import (
            classify_error as cm_classify,
        )

        assert cm_classify is classify_error
