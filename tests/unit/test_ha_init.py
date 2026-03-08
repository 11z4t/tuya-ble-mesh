"""Unit tests for HA integration setup and teardown."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

# Add project root so custom_components is importable
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh import async_setup_entry, async_unload_entry  # noqa: E402
from custom_components.tuya_ble_mesh.const import PLATFORMS  # noqa: E402


def make_mock_hass() -> MagicMock:
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.async_add_import_executor_job = AsyncMock()
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


@pytest.mark.requires_ha
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
            mesh_id=0,
            vendor_id=b"\x01\x10",
            ble_device_callback=ANY,
        )
        coord_cls.assert_called_once_with(mock_device, hass=hass, entry_id=entry.entry_id)

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
    async def test_setup_stores_runtime_data_on_entry(self) -> None:
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        assert entry.runtime_data.coordinator is mock_coord

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
    async def test_setup_registers_services(self) -> None:
        """Setup should register identify and set_log_level services."""
        hass = make_mock_hass()
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        hass.services.async_register = MagicMock()
        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        assert hass.services.async_register.call_count >= 2


@pytest.mark.requires_ha
class TestAsyncSetupEntrySIGMesh:
    """Test async_setup_entry with SIG Mesh device type."""

    @pytest.mark.asyncio
    async def test_setup_sig_plug_creates_sig_mesh_device(self) -> None:
        hass = make_mock_hass()
        entry = MagicMock()
        entry.entry_id = "sig_entry_id"
        entry.title = "SIG Mesh Plug"
        entry.data = {
            "mac_address": "AA:BB:CC:DD:EE:FF",
            "device_type": "sig_plug",
            "unicast_target": "00aa",
            "unicast_our": "0001",
            "op_item_prefix": "s17",
            "iv_index": 0,
        }

        mock_device = MagicMock()
        mock_device.address = "AA:BB:CC:DD:EE:FF"
        mock_coord = MagicMock()
        mock_coord.async_start = AsyncMock()
        mock_coord.async_stop = AsyncMock()
        mock_coord.device = mock_device

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_device.SIGMeshDevice",
                return_value=mock_device,
            ) as sig_cls,
            patch(
                "tuya_ble_mesh.secrets.SecretsManager",
            ),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        sig_cls.assert_called_once()
        call_kwargs = sig_cls.call_args
        assert call_kwargs[0][0] == "AA:BB:CC:DD:EE:FF"
        assert call_kwargs[0][1] == 0x00AA
        assert call_kwargs[0][2] == 0x0001


def _make_entry_with_runtime(
    entry_id: str = "test_entry_id",
    cancel_listeners: list | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Create a mock entry with runtime_data and a mock coordinator."""
    mock_coord = MagicMock()
    mock_coord.async_stop = AsyncMock()

    entry = make_mock_entry(entry_id=entry_id)
    entry.runtime_data = MagicMock()
    entry.runtime_data.coordinator = mock_coord
    entry.runtime_data.cancel_listeners = cancel_listeners or []

    return entry, mock_coord


@pytest.mark.requires_ha
class TestAsyncUnloadEntry:
    """Test async_unload_entry."""

    @pytest.mark.asyncio
    async def test_unload_stops_coordinator(self) -> None:
        hass = make_mock_hass()
        entry, mock_coord = _make_entry_with_runtime()

        await async_unload_entry(hass, entry)

        mock_coord.async_stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_unload_returns_true_on_success(self) -> None:
        hass = make_mock_hass()
        entry, _ = _make_entry_with_runtime()

        result = await async_unload_entry(hass, entry)

        assert result is True

    @pytest.mark.asyncio
    async def test_unload_calls_cancel_listeners(self) -> None:
        """Cancel listeners should be called during unload."""
        hass = make_mock_hass()
        cancel_fn = MagicMock()
        entry, _ = _make_entry_with_runtime(cancel_listeners=[cancel_fn])

        await async_unload_entry(hass, entry)

        cancel_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_unload_calls_async_unload_platforms(self) -> None:
        hass = make_mock_hass()
        entry, _ = _make_entry_with_runtime()

        await async_unload_entry(hass, entry)

        hass.config_entries.async_unload_platforms.assert_called_once_with(entry, PLATFORMS)

    @pytest.mark.asyncio
    async def test_unload_returns_false_on_failure(self) -> None:
        hass = make_mock_hass()
        entry, _ = _make_entry_with_runtime()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        result = await async_unload_entry(hass, entry)

        assert result is False

    @pytest.mark.asyncio
    async def test_unload_handles_missing_runtime_data(self) -> None:
        """Unload should handle entries without runtime_data gracefully."""
        hass = make_mock_hass()
        entry = make_mock_entry()
        # Ensure runtime_data is not set (simulate partial setup)
        del entry.runtime_data

        result = await async_unload_entry(hass, entry)

        assert result is True
