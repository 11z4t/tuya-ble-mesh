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
    _redact_data,
    async_get_config_entry_diagnostics,
)


def make_mock_entry(
    *,
    entry_id: str = "test_entry_id",
    mac: str = "DC:23:4D:21:43:A5",
    mesh_name: str = "my_mesh",
    mesh_password: str = "secret123",  # pragma: allowlist secret
) -> MagicMock:
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {
        CONF_MAC_ADDRESS: mac,
        CONF_MESH_NAME: mesh_name,
        CONF_MESH_PASSWORD: mesh_password,  # pragma: allowlist secret
    }
    return entry


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

    def test_preserves_mac_address(self) -> None:
        data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "mesh",
            CONF_MESH_PASSWORD: "pass",  # pragma: allowlist secret
        }
        result = _redact_data(data)
        assert result[CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"

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


class TestAsyncGetDiagnostics:
    """Test async_get_config_entry_diagnostics."""

    @pytest.mark.asyncio
    async def test_returns_entry_id(self) -> None:
        entry = make_mock_entry(entry_id="my_entry")
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["entry_id"] == "my_entry"

    @pytest.mark.asyncio
    async def test_redacts_sensitive_fields(self) -> None:
        entry = make_mock_entry(
            mesh_name="secret_mesh",
            mesh_password="secret_pass",  # pragma: allowlist secret
        )
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["data"][CONF_MESH_NAME] == REDACTED
        assert result["data"][CONF_MESH_PASSWORD] == REDACTED

    @pytest.mark.asyncio
    async def test_preserves_non_sensitive_fields(self) -> None:
        entry = make_mock_entry(mac="DC:23:4D:21:43:A5")
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"

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

        coordinator = MagicMock()
        coordinator.state = state
        coordinator.device = MagicMock()
        coordinator.device.address = "DC:23:4F:10:52:C4"
        type(coordinator.device).__name__ = "SIGMeshDevice"

        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = coordinator
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"]["available"] is True
        assert result["coordinator"]["is_on"] is True
        assert result["coordinator"]["brightness"] == 80
        assert result["coordinator"]["color_temp"] == 42
        assert result["coordinator"]["mode"] == "white"
        assert result["coordinator"]["rssi"] == -55
        assert result["coordinator"]["firmware_version"] == "1.2.3"
        assert result["coordinator"]["power_w"] == 5.2
        assert result["coordinator"]["energy_kwh"] == 12.3

    @pytest.mark.asyncio
    async def test_includes_device_info(self) -> None:
        """Device type name and address are included."""
        entry = make_mock_entry(entry_id="dev_entry")
        coordinator = MagicMock()
        coordinator.state = MagicMock()
        coordinator.device = MagicMock()
        coordinator.device.address = "AA:BB:CC:DD:EE:FF"
        type(coordinator.device).__name__ = "SIGMeshDevice"

        entry.runtime_data = MagicMock()
        entry.runtime_data.coordinator = coordinator
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["device"]["type"] == "SIGMeshDevice"
        assert result["device"]["address"] == "AA:BB:CC:DD:EE:FF"


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
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        result_str = str(result)
        assert secret_net not in result_str
        assert secret_dev not in result_str
        assert secret_app not in result_str

    @pytest.mark.asyncio
    async def test_redacted_placeholder_present(self) -> None:
        """Verify redacted fields use the REDACTED constant, not empty/None."""
        entry = make_mock_entry()
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["data"][CONF_MESH_NAME] == REDACTED
        assert result["data"][CONF_MESH_PASSWORD] == REDACTED
        assert REDACTED != ""
        assert REDACTED is not None
