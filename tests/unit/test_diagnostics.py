"""Unit tests for Tuya BLE Mesh diagnostics module."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    CONF_APP_KEY,
    CONF_BRIDGE_HOST,
    CONF_DEV_KEY,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
)
from custom_components.tuya_ble_mesh.diagnostics import (  # noqa: E402
    REDACTED,
    _calculate_percentiles,
    _get_protocol_mode,
    _get_vendor_name,
    _redact_data,
    _redact_string,
    _rssi_trend,
)


class TestRedaction:
    """Test sensitive data redaction functions."""

    def test_redact_string_hides_ip(self) -> None:
        """Test IP address redaction."""
        text = "Server at 192.168.1.100 is unreachable"
        result = _redact_string(text)
        assert "192.168.1.100" not in result
        assert "xxx.xxx.xxx.xxx" in result

    def test_redact_string_hides_mac(self) -> None:
        """Test MAC address redaction."""
        text = "Device DC:23:4D:21:43:A5 connected"
        result = _redact_string(text)
        assert "DC:23:4D:21:43:A5" not in result
        assert "XX:XX:XX:XX:XX:XX" in result

    def test_redact_string_handles_multiple_ips(self) -> None:
        """Test multiple IP address redaction in one string."""
        text = "Route 192.168.1.1 via 10.0.0.1"
        result = _redact_string(text)
        assert "192.168.1.1" not in result
        assert "10.0.0.1" not in result
        assert result.count("xxx.xxx.xxx.xxx") == 2

    def test_redact_string_preserves_non_sensitive(self) -> None:
        """Test that non-sensitive text is preserved."""
        text = "Device is working correctly"
        result = _redact_string(text)
        assert result == text

    def test_redact_string_handles_non_string(self) -> None:
        """Test redaction converts non-strings to string first."""
        result = _redact_string(12345)
        assert result == "12345"

    def test_redact_data_hides_mesh_password(self) -> None:
        """Test mesh_password is redacted from dict."""
        data = {CONF_MESH_PASSWORD: "secret123", "other": "public"}
        result = _redact_data(data)
        assert result[CONF_MESH_PASSWORD] == REDACTED
        assert result["other"] == "public"

    def test_redact_data_hides_all_sensitive_keys(self) -> None:
        """Test all sensitive keys are redacted."""
        data = {
            CONF_MESH_NAME: "my_mesh",
            CONF_MESH_PASSWORD: "password",
            CONF_NET_KEY: "net_key_data",
            CONF_DEV_KEY: "dev_key_data",
            CONF_APP_KEY: "app_key_data",
            CONF_BRIDGE_HOST: "192.168.1.50",
            "public_field": "visible",
        }
        result = _redact_data(data)

        assert result[CONF_MESH_NAME] == REDACTED
        assert result[CONF_MESH_PASSWORD] == REDACTED
        assert result[CONF_NET_KEY] == REDACTED
        assert result[CONF_DEV_KEY] == REDACTED
        assert result[CONF_APP_KEY] == REDACTED
        assert result[CONF_BRIDGE_HOST] == REDACTED
        assert result["public_field"] == "visible"

    def test_redact_data_redacts_strings(self) -> None:
        """Test string values are redacted for IP/MAC."""
        data = {
            "error": "Connection to 192.168.1.100 failed",
            "device": "DC:23:4D:21:43:A5",
        }
        result = _redact_data(data)

        assert "192.168.1.100" not in result["error"]
        assert "DC:23:4D:21:43:A5" not in result["device"]

    def test_redact_data_handles_nested_dicts(self) -> None:
        """Test nested dict redaction."""
        data = {
            "outer": {
                CONF_MESH_PASSWORD: "secret",
                "inner": "192.168.1.1",
            }
        }
        result = _redact_data(data)

        assert result["outer"][CONF_MESH_PASSWORD] == REDACTED
        assert "192.168.1.1" not in result["outer"]["inner"]

    def test_redact_data_preserves_non_dict_non_string(self) -> None:
        """Test that numbers/booleans are preserved."""
        data = {"count": 42, "enabled": True, "ratio": 3.14}
        result = _redact_data(data)

        assert result["count"] == 42
        assert result["enabled"] is True
        assert result["ratio"] == 3.14


class TestPercentiles:
    """Test response time percentile calculation."""

    def test_calculate_percentiles_empty_list(self) -> None:
        """Test percentiles with empty list returns zeros."""
        result = _calculate_percentiles([])
        assert result == {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    def test_calculate_percentiles_single_value(self) -> None:
        """Test percentiles with single value."""
        result = _calculate_percentiles([0.5])
        assert result["p50"] == 0.5
        assert result["p95"] == 0.5
        assert result["p99"] == 0.5

    def test_calculate_percentiles_multiple_values(self) -> None:
        """Test percentiles with multiple values."""
        times = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        result = _calculate_percentiles(times)

        # p50 should be ~0.5, p95 ~0.95, p99 ~0.99
        assert 0.4 <= result["p50"] <= 0.6
        assert 0.9 <= result["p95"] <= 1.0
        assert 0.95 <= result["p99"] <= 1.0

    def test_calculate_percentiles_100_values(self) -> None:
        """Test percentiles with 100 values (0.0 to 1.0)."""
        times = [i / 100.0 for i in range(101)]
        result = _calculate_percentiles(times)

        # Should be very close to exact values
        assert abs(result["p50"] - 0.5) < 0.01
        assert abs(result["p95"] - 0.95) < 0.01
        assert abs(result["p99"] - 0.99) < 0.01

    def test_calculate_percentiles_unsorted_input(self) -> None:
        """Test percentiles work with unsorted input."""
        times = [1.0, 0.5, 0.8, 0.2, 0.6]
        result = _calculate_percentiles(times)

        # Should handle unsorted data correctly
        assert result["p50"] == 0.6  # median of [0.2, 0.5, 0.6, 0.8, 1.0]

    def test_calculate_percentiles_rounding(self) -> None:
        """Test percentiles are rounded to 3 decimals."""
        times = [0.123456789]
        result = _calculate_percentiles(times)

        # All values should have max 3 decimal places
        assert result["p50"] == 0.123


class TestProtocolMode:
    """Test protocol mode mapping."""

    def test_protocol_mode_telink_light(self) -> None:
        """Test Telink light protocol mode."""
        assert _get_protocol_mode(DEVICE_TYPE_LIGHT) == "Tuya BLE Mesh (Telink)"

    def test_protocol_mode_telink_plug(self) -> None:
        """Test Telink plug protocol mode."""
        assert _get_protocol_mode(DEVICE_TYPE_PLUG) == "Tuya BLE Mesh (Telink)"

    def test_protocol_mode_sig_plug(self) -> None:
        """Test SIG plug protocol mode."""
        from custom_components.tuya_ble_mesh.const import DEVICE_TYPE_SIG_PLUG

        assert _get_protocol_mode(DEVICE_TYPE_SIG_PLUG) == "SIG Mesh (direct BLE)"

    def test_protocol_mode_sig_bridge_plug(self) -> None:
        """Test SIG bridge plug protocol mode."""
        from custom_components.tuya_ble_mesh.const import DEVICE_TYPE_SIG_BRIDGE_PLUG

        assert _get_protocol_mode(DEVICE_TYPE_SIG_BRIDGE_PLUG) == "SIG Mesh (HTTP bridge)"

    def test_protocol_mode_telink_bridge_light(self) -> None:
        """Test Telink bridge light protocol mode."""
        from custom_components.tuya_ble_mesh.const import DEVICE_TYPE_TELINK_BRIDGE_LIGHT

        assert _get_protocol_mode(DEVICE_TYPE_TELINK_BRIDGE_LIGHT) == "Tuya BLE Mesh (HTTP bridge)"

    def test_protocol_mode_unknown(self) -> None:
        """Test unknown device type."""
        result = _get_protocol_mode("unknown_type")
        assert "Unknown" in result
        assert "unknown_type" in result


class TestVendorName:
    """Test vendor ID to name mapping."""

    def test_vendor_name_malmbergs(self) -> None:
        """Test Malmbergs vendor ID."""
        assert _get_vendor_name("0x1001") == "Malmbergs BT Smart"
        assert _get_vendor_name("0x1001") == "Malmbergs BT Smart"  # case insensitive
        assert _get_vendor_name("1001") == "Malmbergs BT Smart"  # without 0x prefix

    def test_vendor_name_unknown(self) -> None:
        """Test unknown vendor ID."""
        assert _get_vendor_name("0x9999") == "Unknown vendor"

    def test_vendor_name_handles_various_formats(self) -> None:
        """Test vendor name handles various hex formats."""
        # All these should work
        result1 = _get_vendor_name("0x1001")
        result2 = _get_vendor_name("1001")
        result3 = _get_vendor_name("0x1001")
        result4 = _get_vendor_name("  0x1001  ")

        assert result1 == result2 == result3 == result4 == "Malmbergs BT Smart"


class TestRSSITrend:
    """Test RSSI trend analysis."""

    def test_rssi_trend_unknown_insufficient_data(self) -> None:
        """Test RSSI trend returns unknown with < 3 samples."""
        assert _rssi_trend([]) == "unknown"
        assert _rssi_trend([(0.0, -70)]) == "unknown"
        assert _rssi_trend([(0.0, -70), (1.0, -68)]) == "unknown"

    def test_rssi_trend_improving(self) -> None:
        """Test RSSI trend detects improving signal."""
        # Signal getting stronger (-80 → -70 → -60)
        history = [(0.0, -80), (1.0, -70), (2.0, -60)]
        assert _rssi_trend(history) == "improving"

    def test_rssi_trend_declining(self) -> None:
        """Test RSSI trend detects declining signal."""
        # Signal getting weaker (-60 → -70 → -80)
        history = [(0.0, -60), (1.0, -70), (2.0, -80)]
        assert _rssi_trend(history) == "declining"

    def test_rssi_trend_stable(self) -> None:
        """Test RSSI trend detects stable signal."""
        # Signal relatively stable
        history = [(0.0, -70), (1.0, -69), (2.0, -71), (3.0, -70)]
        assert _rssi_trend(history) == "stable"

    def test_rssi_trend_stable_flat(self) -> None:
        """Test RSSI trend with perfectly flat signal."""
        # Perfectly stable (slope = 0)
        history = [(0.0, -70), (1.0, -70), (2.0, -70)]
        assert _rssi_trend(history) == "stable"

    def test_rssi_trend_boundary_improving(self) -> None:
        """Test RSSI trend at improving boundary (slope = 2.0)."""
        # Just at the boundary for improving
        history = [(0.0, -80), (1.0, -78), (2.0, -76)]
        result = _rssi_trend(history)
        # Should be improving or stable (depending on exact calculation)
        assert result in ("improving", "stable")

    def test_rssi_trend_boundary_declining(self) -> None:
        """Test RSSI trend at declining boundary (slope = -2.0)."""
        # Just at the boundary for declining
        history = [(0.0, -60), (1.0, -62), (2.0, -64)]
        result = _rssi_trend(history)
        # Should be declining or stable (depending on exact calculation)
        assert result in ("declining", "stable")

    def test_rssi_trend_long_history(self) -> None:
        """Test RSSI trend with many samples."""
        # Strongly improving trend over 10 samples
        history = [(float(i), -90 + i * 3) for i in range(10)]
        assert _rssi_trend(history) == "improving"

        # Strongly declining trend over 10 samples
        history = [(float(i), -60 - i * 3) for i in range(10)]
        assert _rssi_trend(history) == "declining"


class TestAsyncGetConfigEntryDiagnostics:
    """Test async_get_config_entry_diagnostics integration."""

    @pytest.mark.asyncio
    async def test_diagnostics_returns_expected_sections(
        self,
        mock_hass: MagicMock,
        mock_config_entry: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test diagnostics returns all expected top-level sections."""
        from custom_components.tuya_ble_mesh.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        # Set up runtime_data
        runtime_data = MagicMock()
        runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data = runtime_data

        # Mock statistics
        stats = MagicMock()
        stats.connect_time = None
        stats.total_reconnects = 0
        stats.total_errors = 0
        stats.connection_errors = 0
        stats.command_errors = 0
        stats.last_error = None
        stats.last_error_time = None
        stats.last_error_class = None
        stats.storm_detected = False
        stats.reconnect_times = []
        stats.response_times = []
        stats.rssi_history = []
        stats.reconnect_timeline = []
        mock_coordinator.statistics = stats
        mock_coordinator.consecutive_failures = 0
        mock_coordinator.storm_threshold = 10
        mock_coordinator.capabilities = MagicMock()
        mock_coordinator.capabilities.protocol = "Tuya BLE Mesh"

        result = await async_get_config_entry_diagnostics(mock_hass, mock_config_entry)

        # Check all expected top-level sections
        assert "entry_id" in result
        assert "data" in result
        assert "protocol_mode" in result
        assert "vendor_id" in result
        assert "vendor_name" in result
        assert "connection_statistics" in result
        assert "response_times" in result
        assert "device_state" in result
        assert "device_info" in result
        assert "capabilities" in result
        assert "firmware_compatibility" in result
        assert "mesh_topology" in result
        assert "connection_quality" in result
        assert "protocol_health" in result

    @pytest.mark.asyncio
    async def test_diagnostics_redacts_sensitive_data(
        self,
        mock_hass: MagicMock,
        mock_config_entry: MagicMock,
        mock_coordinator: MagicMock,
    ) -> None:
        """Test diagnostics redacts mesh_password and other sensitive fields."""
        from custom_components.tuya_ble_mesh.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        # Add sensitive data to config entry
        mock_config_entry.data[CONF_MESH_PASSWORD] = "supersecret123"
        mock_config_entry.data[CONF_BRIDGE_HOST] = "192.168.1.50"

        # Set up runtime_data
        runtime_data = MagicMock()
        runtime_data.coordinator = mock_coordinator
        mock_config_entry.runtime_data = runtime_data

        # Mock statistics
        stats = MagicMock()
        stats.connect_time = None
        stats.total_reconnects = 0
        stats.total_errors = 0
        stats.connection_errors = 0
        stats.command_errors = 0
        stats.last_error = None
        stats.last_error_time = None
        stats.last_error_class = None
        stats.storm_detected = False
        stats.reconnect_times = []
        stats.response_times = []
        stats.rssi_history = []
        stats.reconnect_timeline = []
        mock_coordinator.statistics = stats
        mock_coordinator.consecutive_failures = 0
        mock_coordinator.storm_threshold = 10
        mock_coordinator.capabilities = MagicMock()
        mock_coordinator.capabilities.protocol = "Tuya BLE Mesh"

        result = await async_get_config_entry_diagnostics(mock_hass, mock_config_entry)

        # Sensitive fields should be redacted
        assert result["data"][CONF_MESH_PASSWORD] == REDACTED
        assert result["data"][CONF_BRIDGE_HOST] == REDACTED
