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
    CONF_MAC_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
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
    async def test_redacted_placeholder_present(self) -> None:
        """Verify redacted fields use the REDACTED constant, not empty/None."""
        entry = make_mock_entry()
        hass = MagicMock()

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["data"][CONF_MESH_NAME] == REDACTED
        assert result["data"][CONF_MESH_PASSWORD] == REDACTED
        assert REDACTED != ""
        assert REDACTED is not None
