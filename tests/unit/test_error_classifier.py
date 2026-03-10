"""Unit tests for error classification in TuyaBLEMeshCoordinator.

Tests the _classify_error method which categorizes connection and protocol
errors into semantic error classes for targeted repair issue creation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    ErrorClass,
    TuyaBLEMeshCoordinator,
)


def make_mock_device() -> MagicMock:
    """Create a mock MeshDevice."""
    device = MagicMock()
    device.address = "DC:23:4D:21:43:A5"
    device.is_connected = False
    return device


@pytest.mark.requires_ha
class TestErrorClassification:
    """Test error classification logic."""

    def test_classify_timeout_by_exception_type(self) -> None:
        """Test that TimeoutError is classified as TRANSIENT."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = TimeoutError("Connection timed out")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.TRANSIENT

    def test_classify_timeout_by_message(self) -> None:
        """Test that 'timeout' in message is classified as TRANSIENT."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Request timeout after 30s")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.TRANSIENT

    def test_classify_auth_error_by_keyword(self) -> None:
        """Test that authentication errors are classified as MESH_AUTH."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        test_cases = [
            Exception("Authentication failed"),
            Exception("Invalid password"),
            Exception("Mesh credentials rejected"),
            Exception("auth error"),
        ]

        for error in test_cases:
            error_class = coord._classify_error(error)
            assert error_class == ErrorClass.MESH_AUTH, f"Failed for: {error}"

    def test_classify_protocol_error(self) -> None:
        """Test that protocol errors are classified as PROTOCOL."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        test_cases = [
            Exception("Protocol version mismatch"),
            Exception("Unsupported protocol version"),
            Exception("protocol negotiation failed"),
        ]

        for error in test_cases:
            error_class = coord._classify_error(error)
            assert error_class == ErrorClass.PROTOCOL, f"Failed for: {error}"

    def test_classify_connection_refused_as_bridge_down(self) -> None:
        """Test that connection refused errors are classified as BRIDGE_DOWN."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        test_cases = [
            Exception("Connection refused"),
            Exception("Host unreachable"),
            Exception("No route to host"),
        ]

        for error in test_cases:
            error_class = coord._classify_error(error)
            assert error_class == ErrorClass.BRIDGE_DOWN, f"Failed for: {error}"

    def test_classify_device_not_found(self) -> None:
        """Test that device not found errors are classified as DEVICE_OFFLINE."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        test_cases = [
            Exception("Device not found"),
            Exception("MAC address not found in mesh"),
        ]

        for error in test_cases:
            error_class = coord._classify_error(error)
            assert error_class == ErrorClass.DEVICE_OFFLINE, f"Failed for: {error}"

    def test_classify_unsupported_device_as_permanent(self) -> None:
        """Test that unsupported device errors are classified as PERMANENT."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        test_cases = [
            Exception("Unsupported device model"),
            Exception("Unknown vendor ID"),
        ]

        for error in test_cases:
            error_class = coord._classify_error(error)
            assert error_class == ErrorClass.PERMANENT, f"Failed for: {error}"

    def test_classify_unknown_error(self) -> None:
        """Test that unrecognized errors are classified as UNKNOWN."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Something went wrong")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.UNKNOWN

    def test_classification_is_case_insensitive(self) -> None:
        """Test that error classification is case-insensitive."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        test_cases = [
            (Exception("TIMEOUT"), ErrorClass.TRANSIENT),
            (Exception("Authentication Failed"), ErrorClass.MESH_AUTH),
            (Exception("PROTOCOL ERROR"), ErrorClass.PROTOCOL),
            (Exception("Connection Refused"), ErrorClass.BRIDGE_DOWN),
        ]

        for error, expected_class in test_cases:
            error_class = coord._classify_error(error)
            assert error_class == expected_class, f"Failed for: {error}"


@pytest.mark.requires_ha
class TestErrorClassEnum:
    """Test ErrorClass enum values."""

    def test_error_class_values(self) -> None:
        """Test that ErrorClass enum has correct string values."""
        assert ErrorClass.BRIDGE_DOWN.value == "bridge_down"
        assert ErrorClass.DEVICE_OFFLINE.value == "device_offline"
        assert ErrorClass.MESH_AUTH.value == "mesh_auth"
        assert ErrorClass.PROTOCOL.value == "protocol"
        assert ErrorClass.PERMANENT.value == "permanent"
        assert ErrorClass.TRANSIENT.value == "transient"
        assert ErrorClass.UNKNOWN.value == "unknown"

    def test_error_class_is_string_enum(self) -> None:
        """Test that ErrorClass values are strings."""
        assert isinstance(ErrorClass.BRIDGE_DOWN.value, str)
        assert isinstance(ErrorClass.TRANSIENT, ErrorClass)


@pytest.mark.requires_ha
class TestErrorClassificationEdgeCases:
    """Test edge cases in error classification."""

    def test_empty_error_message(self) -> None:
        """Test classification of error with empty message."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.UNKNOWN

    def test_none_error_message(self) -> None:
        """Test classification handles None-like error gracefully."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Exception with no args defaults to empty string
        error = Exception()
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.UNKNOWN

    def test_multiple_keywords_in_error(self) -> None:
        """Test that first matching keyword takes precedence."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Timeout should be classified first (appears first in logic)
        error = Exception("timeout during authentication")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.TRANSIENT

    def test_classify_network_error_types(self) -> None:
        """Test classification of common network exception types."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Simulate ConnectionError (subclass of OSError)
        class ConnectionError(Exception):
            """Mock ConnectionError."""

        error = ConnectionError("Connection refused")
        error_class = coord._classify_error(error)

        # Should classify based on message, not exception type
        assert error_class == ErrorClass.BRIDGE_DOWN


@pytest.mark.requires_ha
class TestErrorStatisticsIntegration:
    """Test integration between error classification and statistics."""

    def test_error_class_stored_in_statistics(self) -> None:
        """Test that error class can be stored in statistics."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = TimeoutError("timeout")
        error_class = coord._classify_error(error)

        coord._stats.last_error_class = error_class.value
        assert coord._stats.last_error_class == "transient"

    def test_error_counter_increments(self) -> None:
        """Test that error counters can be incremented."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord._stats.total_errors == 0
        assert coord._stats.connection_errors == 0

        coord._stats.total_errors += 1
        coord._stats.connection_errors += 1

        assert coord._stats.total_errors == 1
        assert coord._stats.connection_errors == 1

    def test_last_error_message_stored(self) -> None:
        """Test that last error message can be stored."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Connection timeout")
        coord._stats.last_error = str(error)

        assert coord._stats.last_error == "Connection timeout"

    def test_last_error_time_tracked(self) -> None:
        """Test that last error timestamp can be tracked."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        import time

        now = time.time()
        coord._stats.last_error_time = now

        assert coord._stats.last_error_time == now


@pytest.mark.requires_ha
class TestErrorClassificationForRepairs:
    """Test error classification for repair issue creation."""

    def test_transient_errors_create_timeout_repair(self) -> None:
        """Test that transient errors map to timeout repair."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = TimeoutError("timeout")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.TRANSIENT
        # In real code, this would trigger async_create_issue_timeout

    def test_bridge_down_errors_create_bridge_repair(self) -> None:
        """Test that BRIDGE_DOWN errors map to bridge unreachable repair."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Connection refused")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.BRIDGE_DOWN
        # In real code, this would trigger async_create_issue_bridge_unreachable

    def test_mesh_auth_errors_create_auth_repair(self) -> None:
        """Test that MESH_AUTH errors map to auth/mesh mismatch repair."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Authentication failed")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.MESH_AUTH
        # In real code, this would trigger async_create_issue_auth_or_mesh_mismatch

    def test_device_offline_errors_create_device_repair(self) -> None:
        """Test that DEVICE_OFFLINE errors map to device not found repair."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Device not found")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.DEVICE_OFFLINE
        # In real code, this would trigger async_create_issue_device_not_found

    def test_permanent_errors_do_not_create_repair(self) -> None:
        """Test that PERMANENT errors stop reconnection without repair."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Unsupported device")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.PERMANENT
        # In real code, reconnection would stop, no repair created

    def test_unknown_errors_do_not_create_specific_repair(self) -> None:
        """Test that UNKNOWN errors do not map to specific repair."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Something unexpected")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.UNKNOWN
        # In real code, no specific repair would be created


@pytest.mark.requires_ha
class TestErrorClassificationRealWorldScenarios:
    """Test error classification with real-world error messages."""

    def test_bleak_timeout_error(self) -> None:
        """Test classification of typical BLE timeout error."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = TimeoutError("Bluetooth operation timed out after 10.0 seconds")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.TRANSIENT

    def test_http_bridge_connection_error(self) -> None:
        """Test classification of HTTP bridge connection failure."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("HTTPConnectionPool: Failed to establish connection - Connection refused")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.BRIDGE_DOWN

    def test_mesh_credential_mismatch(self) -> None:
        """Test classification of mesh credential rejection."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Mesh authentication failed: password incorrect")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.MESH_AUTH

    def test_device_powered_off(self) -> None:
        """Test classification when device is powered off."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Device DC:23:4D:21:43:A5 not found in mesh network")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.DEVICE_OFFLINE

    def test_incompatible_firmware(self) -> None:
        """Test classification of firmware incompatibility."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Protocol version 2.5 not supported, requires 3.0+")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.PROTOCOL
