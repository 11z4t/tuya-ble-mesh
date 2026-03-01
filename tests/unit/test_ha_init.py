"""Unit tests for HA integration setup and teardown."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root so custom_components is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from custom_components.tuya_ble_mesh import async_setup_entry, async_unload_entry
from custom_components.tuya_ble_mesh.const import DOMAIN, PLATFORMS


def make_mock_hass() -> MagicMock:
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    return hass


def make_mock_entry(entry_id: str = "test_entry_id", title: str = "Test Device") -> MagicMock:
    """Create a mock ConfigEntry."""
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    entry.data = {
        "mac_address": "DC:23:4D:21:43:A5",
        "mesh_name": "out_of_mesh",
        "mesh_password": "123456",
    }
    return entry


class TestAsyncSetupEntry:
    """Test async_setup_entry."""

    @pytest.mark.asyncio
    async def test_setup_creates_domain_data(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()

        result = await async_setup_entry(hass, entry)

        assert result is True
        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_setup_forwards_platforms(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()

        await async_setup_entry(hass, entry)

        hass.config_entries.async_forward_entry_setups.assert_called_once_with(entry, PLATFORMS)

    @pytest.mark.asyncio
    async def test_setup_preserves_existing_entries(self) -> None:
        hass = make_mock_hass()
        hass.data[DOMAIN] = {"existing_entry": {}}
        entry = make_mock_entry()

        await async_setup_entry(hass, entry)

        assert "existing_entry" in hass.data[DOMAIN]
        assert entry.entry_id in hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_setup_returns_true(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()

        result = await async_setup_entry(hass, entry)

        assert result is True


class TestAsyncUnloadEntry:
    """Test async_unload_entry."""

    @pytest.mark.asyncio
    async def test_unload_removes_entry_data(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        hass.data[DOMAIN] = {entry.entry_id: {}}

        result = await async_unload_entry(hass, entry)

        assert result is True
        assert entry.entry_id not in hass.data.get(DOMAIN, {})

    @pytest.mark.asyncio
    async def test_unload_removes_domain_when_last_entry(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        hass.data[DOMAIN] = {entry.entry_id: {}}

        await async_unload_entry(hass, entry)

        assert DOMAIN not in hass.data

    @pytest.mark.asyncio
    async def test_unload_preserves_domain_with_other_entries(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        hass.data[DOMAIN] = {entry.entry_id: {}, "other_entry": {}}

        await async_unload_entry(hass, entry)

        assert DOMAIN in hass.data
        assert "other_entry" in hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_unload_calls_async_unload_platforms(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        hass.data[DOMAIN] = {entry.entry_id: {}}

        await async_unload_entry(hass, entry)

        hass.config_entries.async_unload_platforms.assert_called_once_with(entry, PLATFORMS)

    @pytest.mark.asyncio
    async def test_unload_returns_false_on_failure(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        hass.data[DOMAIN] = {entry.entry_id: {}}
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        result = await async_unload_entry(hass, entry)

        assert result is False
        # Data should NOT be removed on failure
        assert entry.entry_id in hass.data[DOMAIN]
