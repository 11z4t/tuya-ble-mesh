"""Unit tests for the Tuya BLE Mesh diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)

from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    CONF_APP_KEY,
    CONF_DEV_KEY,
    CONF_MAC_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
)
from custom_components.tuya_ble_mesh.diagnostics import (  # noqa: E402
    REDACTED,
    _calculate_percentiles,
    _redact_data,
    async_get_config_entry_diagnostics,
)


def make_mock_entry(
    *,
    entry_id: str = "test_entry_id",
    mac: str = "DC:23:4D:21:43:A5",
    mesh_name: str = "my_mesh",
    mesh_password: str = "secret123",  # pragma: allowlist secret
    with_coordinator: bool = False,
) -> MagicMock:
    """Create a mock config entry.

    Args:
        entry_id: The entry ID
        mac: MAC address
        mesh_name: Mesh network name
        mesh_password: Mesh password
        with_coordinator: If True, add a mocked coordinator with statistics
    """
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {
        CONF_MAC_ADDRESS: mac,
        CONF_MESH_NAME: mesh_name,
        CONF_MESH_PASSWORD: mesh_password,  # pragma: allowlist secret
    }

    if with_coordinator:
        stats = MagicMock()
        stats.connect_time = None
        stats.total_reconnects = 0
        stats.total_errors = 0
        stats.connection_errors = 0
        stats.command_errors = 0
        stats.last_error = None
        stats.last_error_time = None
        stats.response_times = []

        coordinator = MagicMock()
        coordinator.state = MagicMock()
        coordinator.statistics = stats
        coordinator.device = MagicMock()
        coordinator.device.address = mac
        type(coordinator.device).__name__ = "SIGMeshDevice"

        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = coordinator

    return entry


@pytest.mark.requires_ha
class TestRedactData:
    """Test _redact_data helper."""

    def test_redacts_mesh_name(self) -> None:
        data = {CONF_MESH_NAME: "my_mesh", CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF"}
        result = _redact_data(data)
        assert result[CONF_MESH_NAME] == REDACTED

    def test_redacts_mesh_password(self) -> None:
        pw = "secret"  # pragma: allowlist secret
        data = {CONF_MESH_PASSWORD: pw, CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF"}
        result = _redact_data(data)
        assert result[CONF_MESH_PASSWORD] == REDACTED

    def test_redacts_mac_address(self) -> None:
        """MAC addresses should be redacted for privacy."""
        data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "mesh",
            CONF_MESH_PASSWORD: "pass",  # pragma: allowlist secret
        }
        result = _redact_data(data)
        assert result[CONF_MAC_ADDRESS] == "XX:XX:XX:XX:XX:XX"

    def test_does_not_modify_original(self) -> None:
        data = {CONF_MESH_NAME: "original", CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF"}
        _redact_data(data)
        assert data[CONF_MESH_NAME] == "original"

    def test_redacts_net_key(self) -> None:
        data = {CONF_NET_KEY: "aabbccdd", CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF"}
        result = _redact_data(data)
        assert result[CONF_NET_KEY] == REDACTED

    def test_redacts_dev_key(self) -> None:
        data = {CONF_DEV_KEY: "aabbccdd", CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF"}
        result = _redact_data(data)
        assert result[CONF_DEV_KEY] == REDACTED

    def test_redacts_app_key(self) -> None:
        data = {CONF_APP_KEY: "aabbccdd", CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF"}
        result = _redact_data(data)
        assert result[CONF_APP_KEY] == REDACTED

    def test_empty_dict(self) -> None:
        assert _redact_data({}) == {}

    def test_redacts_nested_dict(self) -> None:
        """Test that nested dictionaries are redacted recursively."""
        data = {
            "outer": {
                CONF_MESH_NAME: "secret_mesh",
                CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF",
            }
        }
        result = _redact_data(data)
        assert result["outer"][CONF_MESH_NAME] == REDACTED
        assert result["outer"][CONF_MAC_ADDRESS] == "XX:XX:XX:XX:XX:XX"

    def test_preserves_non_string_non_dict_values(self) -> None:
        """Test that non-string, non-dict values are preserved as-is."""
        data = {
            "number": 42,
            "boolean": True,
            "none": None,
            "list": [1, 2, 3],
        }
        result = _redact_data(data)
        assert result["number"] == 42
        assert result["boolean"] is True
        assert result["none"] is None
        assert result["list"] == [1, 2, 3]


@pytest.mark.requires_ha
class TestCalculatePercentiles:
    """Test _calculate_percentiles helper."""

    def test_empty_list_returns_zeros(self) -> None:
        """Empty list should return all zeros."""
        result = _calculate_percentiles([])
        assert result == {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    def test_calculates_percentiles_correctly(self) -> None:
        """Test percentile calculation with a known dataset."""
        times = [float(i) for i in range(1, 101)]  # 1 to 100
        result = _calculate_percentiles(times)

        # p50 should be around 50.5
        assert 49.0 < result["p50"] < 52.0
        # p95 should be around 95.05
        assert 94.0 < result["p95"] < 96.0
        # p99 should be around 99.01
        assert 98.0 < result["p99"] < 100.0


@pytest.mark.requires_ha
class TestAsyncGetDiagnostics:
    """Test async_get_config_entry_diagnostics."""

    @pytest.mark.asyncio
    async def test_returns_entry_id(self) -> None:
        entry = make_mock_entry(entry_id="my_entry", with_coordinator=True)
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["entry_id"] == "my_entry"

    @pytest.mark.asyncio
    async def test_redacts_sensitive_fields(self) -> None:
        entry = make_mock_entry(
            mesh_name="secret_mesh",
            mesh_password="secret_pass",  # pragma: allowlist secret
            with_coordinator=True,
        )
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["data"][CONF_MESH_NAME] == REDACTED
        assert result["data"][CONF_MESH_PASSWORD] == REDACTED

    @pytest.mark.asyncio
    async def test_redacts_mac_in_diagnostics(self) -> None:
        """MAC addresses should be redacted in diagnostics output."""
        entry = make_mock_entry(mac="DC:23:4D:21:43:A5", with_coordinator=True)
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["data"][CONF_MAC_ADDRESS] == "XX:XX:XX:XX:XX:XX"

    @pytest.mark.asyncio
    async def test_no_coordinator_key_without_coordinator(self) -> None:
        """Without runtime_data, result has no 'coordinator' key."""
        entry = make_mock_entry()
        # Remove runtime_data to simulate entry with no coordinator
        del entry.runtime_data
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert "coordinator" not in result
        assert "device" not in result
        assert "entry_id" in result
        assert "data" in result

    @pytest.mark.asyncio
    async def test_includes_coordinator_state(self) -> None:
        """Coordinator state fields are included when coordinator exists."""
        entry = make_mock_entry(entry_id="coord_entry")
        state = MagicMock()
        state.available = True
        state.is_on = True
        state.brightness = 80
        state.color_temp = 42
        state.mode = "white"
        state.rssi = -55
        state.firmware_version = "1.2.3"
        state.power_w = 5.2
        state.energy_kwh = 12.3

        stats = MagicMock()
        stats.connect_time = None
        stats.total_reconnects = 0
        stats.total_errors = 0
        stats.connection_errors = 0
        stats.command_errors = 0
        stats.last_error = None
        stats.last_error_time = None
        stats.response_times = []

        coordinator = MagicMock()
        coordinator.state = state
        coordinator.statistics = stats
        coordinator.device = MagicMock()
        coordinator.device.address = "DC:23:4F:10:52:C4"
        type(coordinator.device).__name__ = "SIGMeshDevice"

        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = coordinator
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["device_state"]["available"] is True
        assert result["device_state"]["is_on"] is True
        assert result["device_state"]["brightness"] == 80
        assert result["device_state"]["color_temp"] == 42
        assert result["device_state"]["mode"] == "white"
        assert result["device_state"]["rssi"] == -55
        assert result["device_state"]["firmware_version"] == "1.2.3"
        assert result["device_state"]["power_w"] == 5.2
        assert result["device_state"]["energy_kwh"] == 12.3

    @pytest.mark.asyncio
    async def test_includes_device_info(self) -> None:
        """Device type name is included, but address is redacted."""
        entry = make_mock_entry(entry_id="dev_entry")

        stats = MagicMock()
        stats.connect_time = None
        stats.total_reconnects = 0
        stats.total_errors = 0
        stats.connection_errors = 0
        stats.command_errors = 0
        stats.last_error = None
        stats.last_error_time = None
        stats.response_times = []

        coordinator = MagicMock()
        coordinator.state = MagicMock()
        coordinator.statistics = stats
        coordinator.device = MagicMock()
        coordinator.device.address = "AA:BB:CC:DD:EE:FF"
        type(coordinator.device).__name__ = "SIGMeshDevice"

        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = coordinator
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["device_info"]["type"] == "SIGMeshDevice"
        # Address should be redacted for privacy
        assert result["device_info"]["address"] == "XX:XX:XX:XX:XX:XX"

    @pytest.mark.asyncio
    async def test_includes_response_time_percentiles(self) -> None:
        """Test that response time percentiles are calculated correctly."""
        entry = make_mock_entry(entry_id="perf_entry")

        stats = MagicMock()
        stats.connect_time = None
        stats.total_reconnects = 0
        stats.total_errors = 0
        stats.connection_errors = 0
        stats.command_errors = 0
        stats.last_error = None
        stats.last_error_time = None
        # Add response times to trigger percentile calculation
        stats.response_times = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

        coordinator = MagicMock()
        coordinator.state = MagicMock()
        coordinator.statistics = stats
        coordinator.device = MagicMock()
        coordinator.device.address = "AA:BB:CC:DD:EE:FF"
        type(coordinator.device).__name__ = "SIGMeshDevice"

        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = coordinator
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert "response_times" in result
        assert result["response_times"]["sample_count"] == 10
        assert result["response_times"]["avg_seconds"] > 0
        assert result["response_times"]["p50_seconds"] > 0
        assert result["response_times"]["p95_seconds"] > 0
        assert result["response_times"]["p99_seconds"] > 0

    @pytest.mark.asyncio
    async def test_mesh_topology_direct_ble(self) -> None:
        """Test mesh topology for direct BLE devices (no bridge)."""
        entry = make_mock_entry(entry_id="direct_entry")

        stats = MagicMock()
        stats.connect_time = None
        stats.total_reconnects = 0
        stats.total_errors = 0
        stats.connection_errors = 0
        stats.command_errors = 0
        stats.last_error = None
        stats.last_error_time = None
        stats.response_times = []

        coordinator = MagicMock()
        coordinator.state = MagicMock()
        coordinator.statistics = stats
        coordinator.device = MagicMock()
        coordinator.device.address = "AA:BB:CC:DD:EE:FF"
        type(coordinator.device).__name__ = "SIGMeshDevice"
        # Ensure device doesn't have bridge_url attribute
        del coordinator.device.bridge_url

        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = coordinator
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["mesh_topology"]["mode"] == "direct_ble"
        assert result["mesh_topology"]["local_ble"] is True


@pytest.mark.requires_ha
class TestSecurityVerification:
    """Security verification: ensure secrets NEVER leak in diagnostics."""

    @pytest.mark.asyncio
    async def test_no_plaintext_secrets_in_output(self) -> None:
        """Verify that secret values never appear anywhere in the diagnostics output."""
        secret_mesh = "super_secret_mesh_name"  # pragma: allowlist secret
        secret_pass = "ultra_secret_password_123"  # pragma: allowlist secret
        entry = make_mock_entry(
            mesh_name=secret_mesh,
            mesh_password=secret_pass,  # pragma: allowlist secret
            with_coordinator=True,
        )
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        # Convert entire result to string and verify no secret values leak
        result_str = str(result)
        assert secret_mesh not in result_str
        assert secret_pass not in result_str

    @pytest.mark.asyncio
    async def test_no_plaintext_sig_keys_in_output(self) -> None:
        """Verify SIG Mesh keys never appear in diagnostics output."""
        secret_net = "aa11bb22cc33dd44ee55ff6677889900"  # pragma: allowlist secret
        secret_dev = "11223344556677889900aabbccddeeff"  # pragma: allowlist secret
        secret_app = "ffeeddccbbaa99887766554433221100"  # pragma: allowlist secret
        entry = MagicMock()
        entry.entry_id = "sig_entry"
        entry.data = {
            CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF",
            CONF_NET_KEY: secret_net,
            CONF_DEV_KEY: secret_dev,
            CONF_APP_KEY: secret_app,
        }

        # Add coordinator with statistics
        stats = MagicMock()
        stats.connect_time = None
        stats.total_reconnects = 0
        stats.total_errors = 0
        stats.connection_errors = 0
        stats.command_errors = 0
        stats.last_error = None
        stats.last_error_time = None
        stats.response_times = []

        coordinator = MagicMock()
        coordinator.state = MagicMock()
        coordinator.statistics = stats
        coordinator.device = MagicMock()
        coordinator.device.address = "AA:BB:CC:DD:EE:FF"
        type(coordinator.device).__name__ = "SIGMeshDevice"

        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = coordinator

        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        result_str = str(result)
        assert secret_net not in result_str
        assert secret_dev not in result_str
        assert secret_app not in result_str

    @pytest.mark.asyncio
    async def test_redacted_placeholder_present(self) -> None:
        """Verify redacted fields use the REDACTED constant, not empty/None."""
        entry = make_mock_entry(with_coordinator=True)
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["data"][CONF_MESH_NAME] == REDACTED
        assert result["data"][CONF_MESH_PASSWORD] == REDACTED
        assert REDACTED != ""
        assert REDACTED is not None
