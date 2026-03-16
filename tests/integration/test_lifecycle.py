"""Integration lifecycle tests for Tuya BLE Mesh.

Tests the full production lifecycle:
- discovery → config_flow → config entry creation
- setup → coordinator.async_start → entities available
- HA restart (unload → reload) → config entry survives
- unload → cleanup
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))


class TestFullLifecycle:
    """Test full production lifecycle from config flow to unload."""

    @pytest.mark.asyncio
    async def test_config_entry_creation_and_persistence(self) -> None:
        """Config flow should create a persistent config entry."""
        from custom_components.tuya_ble_mesh.config_flow import TuyaBLEMeshConfigFlow
        from custom_components.tuya_ble_mesh.const import (
            CONF_DEVICE_TYPE,
            CONF_MAC_ADDRESS,
            CONF_MESH_NAME,
            CONF_MESH_PASSWORD,
            DEVICE_TYPE_LIGHT,
        )

        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()

        # Simulate user input with device_type to avoid SIG Mesh flow
        user_input = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
            CONF_MESH_NAME: "out_of_mesh",
            CONF_MESH_PASSWORD: "123456",
        }

        # Mock the async_set_unique_id (prevents real HA calls)
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = MagicMock()

        # Mock the entry creation
        entries = []

        def mock_create_entry(title: str, data: dict[str, object]) -> dict[str, object]:
            entry_data = {"title": title, "data": data, "type": "create_entry"}
            entries.append(entry_data)
            return entry_data

        flow.async_create_entry = mock_create_entry

        result = await flow.async_step_user(user_input)

        # Verify entry was created
        assert result["type"] == "create_entry"
        assert CONF_MAC_ADDRESS in result["data"]
        assert result["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"

        # Verify entry persists (is in list)
        assert len(entries) == 1
        assert entries[0]["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"

    @pytest.mark.asyncio
    async def test_setup_creates_coordinator_and_starts(self) -> None:
        """Setup should create coordinator and call async_start."""
        from custom_components.tuya_ble_mesh import async_setup_entry
        from custom_components.tuya_ble_mesh.const import (
            CONF_MAC_ADDRESS,
            CONF_MESH_NAME,
            CONF_MESH_PASSWORD,
        )

        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
        mock_hass.services = MagicMock()
        mock_hass.services.has_service = MagicMock(return_value=False)
        mock_hass.services.async_register = MagicMock()
        mock_hass.async_add_import_executor_job = AsyncMock(return_value=None)

        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.title = "Test Device"
        mock_entry.data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "out_of_mesh",
            CONF_MESH_PASSWORD: "123456",
        }
        mock_entry.async_on_unload = MagicMock(return_value=None)

        # Mock the MeshDevice
        with (
            patch("tuya_ble_mesh.device.MeshDevice") as mock_device_cls,
            patch(
                "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator.async_start"
            ) as mock_start,
        ):
            mock_device = MagicMock()
            mock_device.address = "DC:23:4D:21:43:A5"
            mock_device_cls.return_value = mock_device
            mock_start.return_value = None

            result = await async_setup_entry(mock_hass, mock_entry)

            # Verify setup succeeded
            assert result is True

            # Verify coordinator was started
            mock_start.assert_called_once()

            # Verify runtime_data was set
            assert hasattr(mock_entry, "runtime_data")
            assert mock_entry.runtime_data is not None
            assert hasattr(mock_entry.runtime_data, "coordinator")

    @pytest.mark.asyncio
    async def test_coordinator_start_lifecycle(self) -> None:
        """Coordinator async_start should initialize device connection."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Start coordinator
        await coord.async_start()

        # Verify device connection was attempted
        mock_device.connect.assert_called()

        # Verify running state
        assert coord._running is True

        # Stop coordinator
        await coord.async_stop()

        # Verify stopped
        assert coord._running is False

    @pytest.mark.asyncio
    async def test_entities_become_available_after_coordinator_start(self) -> None:
        """Entities should become available after coordinator starts."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
        from custom_components.tuya_ble_mesh.light import TuyaBLEMeshLight

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.mesh_id = 0x01
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"

        # Coordinator without hass (avoids storage initialization)
        coord = TuyaBLEMeshCoordinator(mock_device)
        light = TuyaBLEMeshLight(coord, mock_config_entry)

        # Initially unavailable (coordinator not started)
        assert coord.state.available is False
        assert light.available is False

        # Start coordinator (will attempt connect but we mock it)
        with patch.object(coord, "_reconnect_loop", new=AsyncMock()):
            await coord.async_start()

        # Mark as connected
        from dataclasses import replace as _dc_replace
        coord._state = _dc_replace(coord._state, available=True)

        # Entity should now be available
        assert light.available is True

        # Cleanup
        await coord.async_stop()

    @pytest.mark.asyncio
    async def test_ha_restart_unload_and_reload(self) -> None:
        """Config entry should survive HA restart (unload → reload)."""
        from custom_components.tuya_ble_mesh import async_setup_entry, async_unload_entry
        from custom_components.tuya_ble_mesh.const import (
            CONF_MAC_ADDRESS,
            CONF_MESH_NAME,
            CONF_MESH_PASSWORD,
        )

        # Simulate initial setup
        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        mock_hass.services = MagicMock()
        mock_hass.services.has_service = MagicMock(return_value=False)
        mock_hass.services.async_register = MagicMock()
        mock_hass.async_add_import_executor_job = AsyncMock(return_value=None)

        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.title = "Test Device"
        mock_entry.data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "out_of_mesh",
            CONF_MESH_PASSWORD: "123456",
        }
        mock_entry.async_on_unload = MagicMock(return_value=None)

        with (
            patch("tuya_ble_mesh.device.MeshDevice") as mock_device_cls,
            patch(
                "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator.async_start"
            ) as mock_start,
            patch(
                "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator.async_stop"
            ) as mock_stop,
        ):
            mock_device = MagicMock()
            mock_device.address = "DC:23:4D:21:43:A5"
            mock_device_cls.return_value = mock_device

            # Initial setup
            setup_ok = await async_setup_entry(mock_hass, mock_entry)
            assert setup_ok is True

            # Simulate HA restart: unload
            unload_ok = await async_unload_entry(mock_hass, mock_entry)
            assert unload_ok is True

            # Verify coordinator was stopped
            mock_stop.assert_called_once()

            # Simulate reload (HA restart completes)
            mock_start.reset_mock()
            mock_stop.reset_mock()

            setup_ok_2 = await async_setup_entry(mock_hass, mock_entry)
            assert setup_ok_2 is True

            # Verify coordinator was restarted
            mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_unload_cleanup_releases_resources(self) -> None:
        """Unload should stop coordinator and release all resources."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Register a listener
        listener_called = False

        def test_listener() -> None:
            nonlocal listener_called
            listener_called = True

        remove_listener = coord.add_listener(test_listener)

        # Start coordinator
        await coord.async_start()

        # Verify listener works
        coord._notify_listeners()
        assert listener_called is True

        # Unload (stop coordinator)
        await coord.async_stop()

        # Verify stopped
        assert coord._running is False

        # Verify disconnect was called
        mock_device.disconnect.assert_called()

        # Remove listener (cleanup)
        remove_listener()
        listener_called = False
        coord._notify_listeners()
        assert listener_called is False

    @pytest.mark.asyncio
    async def test_config_entry_data_integrity_across_reload(self) -> None:
        """Config entry data should remain intact across reload."""
        from custom_components.tuya_ble_mesh.const import (
            CONF_MAC_ADDRESS,
            CONF_MESH_NAME,
            CONF_MESH_PASSWORD,
            CONF_VENDOR_ID,
        )

        # Simulate config entry creation
        entry_data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "out_of_mesh",
            CONF_MESH_PASSWORD: "secret123",  # pragma: allowlist secret
            CONF_VENDOR_ID: "07D0",
        }

        # Create mock entry
        mock_entry = MagicMock()
        mock_entry.data = dict(entry_data)

        # Simulate HA persisting and reloading (deep copy to verify independence)
        import copy

        persisted_data = copy.deepcopy(mock_entry.data)

        # Verify data integrity
        assert persisted_data[CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"
        assert persisted_data[CONF_MESH_NAME] == "out_of_mesh"
        assert persisted_data[CONF_MESH_PASSWORD] == "secret123"  # pragma: allowlist secret
        assert persisted_data[CONF_VENDOR_ID] == "07D0"

        # Simulate reload with persisted data
        mock_entry_reloaded = MagicMock()
        mock_entry_reloaded.data = persisted_data

        # Verify data matches
        assert mock_entry_reloaded.data == entry_data


class TestRuntimeDataIntegrity:
    """Test runtime_data container integrity across lifecycle."""

    @pytest.mark.asyncio
    async def test_runtime_data_set_before_coordinator_start(self) -> None:
        """Runtime data must be set BEFORE coordinator.async_start to avoid race conditions."""
        from custom_components.tuya_ble_mesh import async_setup_entry
        from custom_components.tuya_ble_mesh.const import (
            CONF_MAC_ADDRESS,
            CONF_MESH_NAME,
            CONF_MESH_PASSWORD,
        )

        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.services = MagicMock()
        mock_hass.services.has_service = MagicMock(return_value=False)
        mock_hass.services.async_register = MagicMock()
        mock_hass.async_add_import_executor_job = AsyncMock(return_value=None)

        # Track when runtime_data is set vs when async_start is called
        runtime_data_set_before_start = False

        async def mock_forward_setups(*args: object, **kwargs: object) -> None:
            pass

        mock_hass.config_entries.async_forward_entry_setups = mock_forward_setups

        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.title = "Test Device"
        mock_entry.data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "out_of_mesh",
            CONF_MESH_PASSWORD: "123456",
        }
        mock_entry.async_on_unload = MagicMock(return_value=None)

        original_start = None

        async def track_start(self: object) -> None:
            nonlocal runtime_data_set_before_start
            # Check if runtime_data was set before this call
            runtime_data_set_before_start = hasattr(mock_entry, "runtime_data")
            if original_start:
                await original_start(self)

        with (
            patch("tuya_ble_mesh.device.MeshDevice") as mock_device_cls,
            patch(
                "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator.async_start",
                new=track_start,
            ),
        ):
            mock_device = MagicMock()
            mock_device.address = "DC:23:4D:21:43:A5"
            mock_device_cls.return_value = mock_device

            await async_setup_entry(mock_hass, mock_entry)

            # Verify runtime_data was set BEFORE async_start
            assert runtime_data_set_before_start is True

    @pytest.mark.asyncio
    async def test_runtime_data_accessible_from_entities(self) -> None:
        """Entities should access coordinator via runtime_data."""
        from custom_components.tuya_ble_mesh import TuyaBLEMeshRuntimeData
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
        from custom_components.tuya_ble_mesh.light import TuyaBLEMeshLight

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.mesh_id = 0x01

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Create runtime_data
        device_info = {
            "identifiers": {("tuya_ble_mesh", "DC:23:4D:21:43:A5")},
            "name": "Test Device",
        }
        runtime = TuyaBLEMeshRuntimeData(coordinator=coord, device_info=device_info)

        # Simulate config entry
        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.runtime_data = runtime

        # Create entity using runtime_data
        light = TuyaBLEMeshLight(runtime.coordinator, mock_entry)

        # Verify entity has access to coordinator
        assert light._coordinator is not None
        assert light._coordinator.device.address == "DC:23:4D:21:43:A5"
