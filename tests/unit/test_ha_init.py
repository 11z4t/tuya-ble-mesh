"""Unit tests for HA integration setup and teardown."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root so custom_components is importable
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh import async_setup_entry, async_unload_entry  # noqa: E402
from custom_components.tuya_ble_mesh.const import DOMAIN, PLATFORMS  # noqa: E402


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
        "mesh_password": "123456",  # pragma: allowlist secret
    }
    return entry


_PATCH_MESH_DEVICE = "tuya_ble_mesh.device.MeshDevice"
_PATCH_COORDINATOR = "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator"


def _make_patches() -> tuple[MagicMock, MagicMock]:
    """Create mock MeshDevice and Coordinator classes."""
    mock_device_instance = MagicMock()
    mock_device_instance.address = "DC:23:4D:21:43:A5"

    mock_coord_instance = MagicMock()
    mock_coord_instance.async_start = AsyncMock()
    mock_coord_instance.async_stop = AsyncMock()
    mock_coord_instance.device = mock_device_instance

    return mock_device_instance, mock_coord_instance


class TestAsyncSetupEntry:
    """Test async_setup_entry."""

    @pytest.mark.asyncio
    async def test_setup_creates_device_and_coordinator(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device) as device_cls,
            patch(_PATCH_COORDINATOR, return_value=mock_coord) as coord_cls,
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        device_cls.assert_called_once_with(
            "DC:23:4D:21:43:A5",
            b"out_of_mesh",
            b"123456",
        )
        coord_cls.assert_called_once_with(mock_device)

    @pytest.mark.asyncio
    async def test_setup_starts_coordinator(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        mock_coord.async_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_setup_stores_coordinator_in_hass_data(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]
        assert hass.data[DOMAIN][entry.entry_id]["coordinator"] is mock_coord

    @pytest.mark.asyncio
    async def test_setup_forwards_platforms(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        hass.config_entries.async_forward_entry_setups.assert_called_once_with(entry, PLATFORMS)

    @pytest.mark.asyncio
    async def test_setup_preserves_existing_entries(self) -> None:
        hass = make_mock_hass()
        hass.data[DOMAIN] = {"existing_entry": {}}
        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        assert "existing_entry" in hass.data[DOMAIN]
        assert entry.entry_id in hass.data[DOMAIN]


class TestAsyncUnloadEntry:
    """Test async_unload_entry."""

    @pytest.mark.asyncio
    async def test_unload_stops_coordinator(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_coord = MagicMock()
        mock_coord.async_stop = AsyncMock()
        hass.data[DOMAIN] = {entry.entry_id: {"coordinator": mock_coord}}

        await async_unload_entry(hass, entry)

        mock_coord.async_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_unload_removes_entry_data(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_coord = MagicMock()
        mock_coord.async_stop = AsyncMock()
        hass.data[DOMAIN] = {entry.entry_id: {"coordinator": mock_coord}}

        result = await async_unload_entry(hass, entry)

        assert result is True
        assert entry.entry_id not in hass.data.get(DOMAIN, {})

    @pytest.mark.asyncio
    async def test_unload_removes_domain_when_last_entry(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_coord = MagicMock()
        mock_coord.async_stop = AsyncMock()
        hass.data[DOMAIN] = {entry.entry_id: {"coordinator": mock_coord}}

        await async_unload_entry(hass, entry)

        assert DOMAIN not in hass.data

    @pytest.mark.asyncio
    async def test_unload_preserves_domain_with_other_entries(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_coord = MagicMock()
        mock_coord.async_stop = AsyncMock()
        hass.data[DOMAIN] = {entry.entry_id: {"coordinator": mock_coord}, "other_entry": {}}

        await async_unload_entry(hass, entry)

        assert DOMAIN in hass.data
        assert "other_entry" in hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_unload_calls_async_unload_platforms(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_coord = MagicMock()
        mock_coord.async_stop = AsyncMock()
        hass.data[DOMAIN] = {entry.entry_id: {"coordinator": mock_coord}}

        await async_unload_entry(hass, entry)

        hass.config_entries.async_unload_platforms.assert_called_once_with(entry, PLATFORMS)

    @pytest.mark.asyncio
    async def test_unload_returns_false_on_failure(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_coord = MagicMock()
        mock_coord.async_stop = AsyncMock()
        hass.data[DOMAIN] = {entry.entry_id: {"coordinator": mock_coord}}
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        result = await async_unload_entry(hass, entry)

        assert result is False
        # Data should NOT be removed on failure
        assert entry.entry_id in hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_unload_handles_missing_coordinator(self) -> None:
        """Unload should handle entries without a coordinator gracefully."""
        hass = make_mock_hass()
        entry = make_mock_entry()
        hass.data[DOMAIN] = {entry.entry_id: {}}

        result = await async_unload_entry(hass, entry)

        assert result is True
