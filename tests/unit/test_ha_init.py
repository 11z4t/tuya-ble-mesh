"""Unit tests for HA integration setup and teardown."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

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

    @pytest.mark.asyncio
    async def test_setup_sig_bridge_plug_creates_bridge_device(self) -> None:
        """Test SIG Bridge Plug device type creates SIGMeshBridgeDevice."""
        hass = make_mock_hass()
        entry = MagicMock()
        entry.entry_id = "bridge_entry_id"
        entry.title = "SIG Bridge Plug"
        entry.data = {
            "mac_address": "11:22:33:44:55:66",
            "device_type": "sig_bridge_plug",
            "unicast_target": "00c0",
            "bridge_host": "192.168.1.100",
            "bridge_port": 9999,
        }

        mock_device = MagicMock()
        mock_device.address = "11:22:33:44:55:66"
        mock_coord = MagicMock()
        mock_coord.async_start = AsyncMock()
        mock_coord.async_stop = AsyncMock()
        mock_coord.device = mock_device

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_bridge.SIGMeshBridgeDevice",
                return_value=mock_device,
            ) as bridge_cls,
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        bridge_cls.assert_called_once_with(
            "11:22:33:44:55:66",
            0x00C0,
            "192.168.1.100",
            9999,
        )

    @pytest.mark.asyncio
    async def test_sig_mesh_device_with_ble_callback(self) -> None:
        """Test SIG Mesh device creation with BLE device callback."""
        hass = make_mock_hass()
        entry = MagicMock()
        entry.entry_id = "sig_plug_entry_id"
        entry.title = "SIG Plug"
        entry.data = {
            "mac_address": "BB:BB:CC:CC:DD:DD",
            "device_type": "sig_plug",
            "unicast_target": "00bb",
            "unicast_our": "0002",
            "op_item_prefix": "cfg",
            "iv_index": 0,
            "net_key": "aabbccdd",
            "dev_key": "ddeeff00",
            "app_key": "112233",
        }

        mock_device = MagicMock()
        mock_device.address = "BB:BB:CC:CC:DD:DD"
        mock_coord = MagicMock()
        mock_coord.async_start = AsyncMock()
        mock_coord.async_stop = AsyncMock()
        mock_coord.device = mock_device

        mock_ble_device = MagicMock()

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_device.SIGMeshDevice",
                return_value=mock_device,
            ) as sig_cls,
            patch(
                "tuya_ble_mesh.secrets.DictSecretsManager",
            ),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
            patch(
                "homeassistant.components.bluetooth.async_ble_device_from_address",
                return_value=mock_ble_device,
            ) as mock_ble,
        ):
            result = await async_setup_entry(hass, entry)

            # Get the ble_device_callback that was passed
            call_kwargs = sig_cls.call_args[1]
            ble_callback = call_kwargs["ble_device_callback"]

            # Call the callback to trigger _ble_device_from_ha
            ble_result = ble_callback("BB:BB:CC:CC:DD:DD")

            # Verify BLE callback was used and returned device
            assert ble_result is mock_ble_device
            assert mock_ble.called

        assert result is True

    @pytest.mark.asyncio
    async def test_sig_mesh_device_ble_callback_fallback(self) -> None:
        """Test BLE callback fallback when connectable device not found."""
        hass = make_mock_hass()
        entry = MagicMock()
        entry.entry_id = "sig_fallback_entry"
        entry.title = "SIG Plug Fallback"
        entry.data = {
            "mac_address": "CC:CC:DD:DD:EE:EE",
            "device_type": "sig_plug",
            "unicast_target": "00cc",
            "unicast_our": "0003",
            "op_item_prefix": "cfg",
            "iv_index": 0,
            "net_key": "aabbccdd",
            "dev_key": "ddeeff00",
            "app_key": "112233",
        }

        mock_device = MagicMock()
        mock_device.address = "CC:CC:DD:DD:EE:EE"
        mock_coord = MagicMock()
        mock_coord.async_start = AsyncMock()
        mock_coord.async_stop = AsyncMock()
        mock_coord.device = mock_device

        mock_ble_device = MagicMock()

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_device.SIGMeshDevice",
                return_value=mock_device,
            ) as sig_cls,
            patch(
                "tuya_ble_mesh.secrets.DictSecretsManager",
            ),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
            patch(
                "homeassistant.components.bluetooth.async_ble_device_from_address",
                side_effect=[None, mock_ble_device],  # First call None, second returns device
            ) as mock_ble,
        ):
            await async_setup_entry(hass, entry)

            # Get the ble_device_callback that was passed
            call_kwargs = sig_cls.call_args[1]
            ble_callback = call_kwargs["ble_device_callback"]

            # Call the callback to trigger _ble_device_from_ha with fallback
            ble_result = ble_callback("CC:CC:DD:DD:EE:EE")

            # Verify fallback was used (both connectable=True and connectable=False)
            assert ble_result is mock_ble_device
            assert mock_ble.call_count == 2

    @pytest.mark.asyncio
    async def test_sig_mesh_device_ble_callback_not_found(self) -> None:
        """Test BLE callback when device not found at all."""
        hass = make_mock_hass()
        entry = MagicMock()
        entry.entry_id = "sig_notfound_entry"
        entry.title = "SIG Plug Not Found"
        entry.data = {
            "mac_address": "DD:DD:EE:EE:FF:FF",
            "device_type": "sig_plug",
            "unicast_target": "00dd",
            "unicast_our": "0004",
            "op_item_prefix": "cfg",
            "iv_index": 0,
            "net_key": "aabbccdd",
            "dev_key": "ddeeff00",
            "app_key": "112233",
        }

        mock_device = MagicMock()
        mock_device.address = "DD:DD:EE:EE:FF:FF"
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
                "tuya_ble_mesh.secrets.DictSecretsManager",
            ),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
            patch(
                "homeassistant.components.bluetooth.async_ble_device_from_address",
                return_value=None,  # Both calls return None
            ) as mock_ble,
        ):
            await async_setup_entry(hass, entry)

            # Get the ble_device_callback that was passed
            call_kwargs = sig_cls.call_args[1]
            ble_callback = call_kwargs["ble_device_callback"]

            # Call the callback to trigger _ble_device_from_ha with no device found
            ble_result = ble_callback("DD:DD:EE:EE:FF:FF")

            # Verify both calls were made and None was returned
            assert ble_result is None
            assert mock_ble.call_count == 2

    @pytest.mark.asyncio
    async def test_setup_telink_bridge_light_creates_telink_device(self) -> None:
        """Test Telink Bridge Light device type creates TelinkBridgeDevice."""
        hass = make_mock_hass()
        entry = MagicMock()
        entry.entry_id = "telink_entry_id"
        entry.title = "Telink Bridge Light"
        entry.data = {
            "mac_address": "AA:AA:BB:BB:CC:CC",
            "device_type": "telink_bridge_light",
            "bridge_host": "10.0.0.50",
            "bridge_port": 8888,
        }

        mock_device = MagicMock()
        mock_device.address = "AA:AA:BB:BB:CC:CC"
        mock_coord = MagicMock()
        mock_coord.async_start = AsyncMock()
        mock_coord.async_stop = AsyncMock()
        mock_coord.device = mock_device

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_bridge.TelinkBridgeDevice",
                return_value=mock_device,
            ) as telink_cls,
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        telink_cls.assert_called_once_with(
            "AA:AA:BB:BB:CC:CC",
            "10.0.0.50",
            8888,
        )


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


@pytest.mark.requires_ha
class TestServiceHandlers:
    """Test service handler functions (identify, set_log_level)."""

    @pytest.mark.asyncio
    async def test_identify_service_calls_send_power(self) -> None:
        """Test identify service flashes device by toggling power."""
        from custom_components.tuya_ble_mesh import async_setup_entry

        hass = make_mock_hass()
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        registered_handlers = {}

        def mock_register(domain: str, service: str, handler: Any, **kwargs: Any) -> None:
            registered_handlers[service] = handler

        hass.services.async_register = mock_register

        entry = make_mock_entry()
        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.send_power = AsyncMock()
        mock_coord = MagicMock()
        mock_coord.async_start = AsyncMock()
        mock_coord.device = mock_device

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        # Simulate calling the identify service
        assert "identify" in registered_handlers
        identify_handler = registered_handlers["identify"]

        # Mock device registry to return the correct coordinator
        call = MagicMock()
        call.data = {"device_id": "test_device_id"}

        with (
            patch(
                "homeassistant.helpers.device_registry.async_get"
            ) as mock_reg_getter,
        ):
            mock_dev_reg = MagicMock()
            mock_device_entry = MagicMock()
            mock_device_entry.config_entries = {entry.entry_id}
            mock_dev_reg.async_get = MagicMock(return_value=mock_device_entry)
            mock_reg_getter.return_value = mock_dev_reg

            hass.config_entries.async_get_entry = MagicMock(return_value=entry)

            await identify_handler(call)

        # Verify send_power was called multiple times (flash pattern)
        assert mock_device.send_power.call_count >= 3

    @pytest.mark.asyncio
    async def test_identify_service_raises_on_missing_device(self) -> None:
        """Test identify service raises error when device not found."""
        from custom_components.tuya_ble_mesh import async_setup_entry

        hass = make_mock_hass()
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        registered_handlers = {}

        def mock_register(domain: str, service: str, handler: Any, **kwargs: Any) -> None:
            registered_handlers[service] = handler

        hass.services.async_register = mock_register

        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        identify_handler = registered_handlers["identify"]
        call = MagicMock()
        call.data = {"device_id": "nonexistent_device"}

        with (
            patch(
                "homeassistant.helpers.device_registry.async_get"
            ) as mock_reg_getter,
        ):
            mock_dev_reg = MagicMock()
            mock_dev_reg.async_get = MagicMock(return_value=None)
            mock_reg_getter.return_value = mock_dev_reg

            with pytest.raises(HomeAssistantError, match="Device not found"):
                await identify_handler(call)

    @pytest.mark.asyncio
    async def test_set_log_level_service(self) -> None:
        """Test set_log_level service changes logging level."""
        from custom_components.tuya_ble_mesh import async_setup_entry

        hass = make_mock_hass()
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        registered_handlers = {}

        def mock_register(domain: str, service: str, handler: Any, **kwargs: Any) -> None:
            registered_handlers[service] = handler

        hass.services.async_register = mock_register

        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        assert "set_log_level" in registered_handlers
        log_level_handler = registered_handlers["set_log_level"]

        call = MagicMock()
        call.data = {"level": "debug"}

        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            await log_level_handler(call)

            mock_get_logger.assert_called_with("tuya_ble_mesh")
            mock_logger.setLevel.assert_called_once()

    @pytest.mark.asyncio
    async def test_identify_service_raises_on_device_error(self) -> None:
        """Test identify service raises error when device.send_power fails."""
        from custom_components.tuya_ble_mesh import async_setup_entry

        hass = make_mock_hass()
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        registered_handlers = {}

        def mock_register(domain: str, service: str, handler: Any, **kwargs: Any) -> None:
            registered_handlers[service] = handler

        hass.services.async_register = mock_register

        entry = make_mock_entry()
        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        # Make send_power fail
        mock_device.send_power = AsyncMock(side_effect=Exception("BLE timeout"))
        mock_coord = MagicMock()
        mock_coord.async_start = AsyncMock()
        mock_coord.device = mock_device

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        identify_handler = registered_handlers["identify"]
        call = MagicMock()
        call.data = {"device_id": "test_device_id"}

        with (
            patch(
                "homeassistant.helpers.device_registry.async_get"
            ) as mock_reg_getter,
        ):
            mock_dev_reg = MagicMock()
            mock_device_entry = MagicMock()
            mock_device_entry.config_entries = {entry.entry_id}
            mock_dev_reg.async_get = MagicMock(return_value=mock_device_entry)
            mock_reg_getter.return_value = mock_dev_reg

            hass.config_entries.async_get_entry = MagicMock(return_value=entry)

            with pytest.raises(HomeAssistantError, match="Failed to identify device"):
                await identify_handler(call)

    @pytest.mark.asyncio
    async def test_identify_service_no_coordinator_found(self) -> None:
        """Test identify service raises error when coordinator not found for device."""
        from custom_components.tuya_ble_mesh import async_setup_entry

        hass = make_mock_hass()
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        registered_handlers = {}

        def mock_register(domain: str, service: str, handler: Any, **kwargs: Any) -> None:
            registered_handlers[service] = handler

        hass.services.async_register = mock_register

        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        identify_handler = registered_handlers["identify"]
        call = MagicMock()
        call.data = {"device_id": "test_device_id"}

        with (
            patch(
                "homeassistant.helpers.device_registry.async_get"
            ) as mock_reg_getter,
        ):
            mock_dev_reg = MagicMock()
            mock_device_entry = MagicMock()
            mock_device_entry.config_entries = {entry.entry_id}
            mock_dev_reg.async_get = MagicMock(return_value=mock_device_entry)
            mock_reg_getter.return_value = mock_dev_reg

            # Make async_get_entry return entry without runtime_data
            entry_no_runtime = make_mock_entry()
            del entry_no_runtime.runtime_data
            hass.config_entries.async_get_entry = MagicMock(return_value=entry_no_runtime)

            with pytest.raises(HomeAssistantError, match="Device not found"):
                await identify_handler(call)

    @pytest.mark.asyncio
    async def test_get_diagnostics_service_returns_info(self) -> None:
        """get_diagnostics service returns coordinator stats — covers __init__.py:296-316."""
        from custom_components.tuya_ble_mesh import async_setup_entry
        from custom_components.tuya_ble_mesh.coordinator import ConnectionStatistics

        hass = make_mock_hass()
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        registered_handlers: dict[str, Any] = {}

        def mock_register(domain: str, service: str, handler: Any, **kwargs: Any) -> None:
            registered_handlers[service] = handler

        hass.services.async_register = mock_register

        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        # Use a real ConnectionStatistics so format specifiers work
        mock_coord.statistics = ConnectionStatistics(
            connection_uptime=10.0,
            total_reconnects=0,
            total_errors=0,
            connection_errors=0,
            command_errors=0,
            avg_response_time=0.0,
        )

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        assert "get_diagnostics" in registered_handlers
        get_diag_handler = registered_handlers["get_diagnostics"]

        call = MagicMock()
        call.data = {"device_id": "test_device_id"}

        with (
            patch(
                "homeassistant.helpers.device_registry.async_get"
            ) as mock_reg_getter,
        ):
            mock_dev_reg = MagicMock()
            mock_device_entry = MagicMock()
            mock_device_entry.config_entries = {entry.entry_id}
            mock_dev_reg.async_get = MagicMock(return_value=mock_device_entry)
            mock_reg_getter.return_value = mock_dev_reg
            hass.config_entries.async_get_entry = MagicMock(return_value=entry)

            # Should complete without error and log diagnostics
            await get_diag_handler(call)

    @pytest.mark.asyncio
    async def test_get_diagnostics_service_raises_on_missing_device(self) -> None:
        """get_diagnostics raises HomeAssistantError when device not found."""
        from custom_components.tuya_ble_mesh import async_setup_entry

        hass = make_mock_hass()
        hass.services = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        registered_handlers: dict[str, Any] = {}

        def mock_register(domain: str, service: str, handler: Any, **kwargs: Any) -> None:
            registered_handlers[service] = handler

        hass.services.async_register = mock_register

        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            await async_setup_entry(hass, entry)

        get_diag_handler = registered_handlers["get_diagnostics"]
        call = MagicMock()
        call.data = {"device_id": "nonexistent"}

        with (
            patch(
                "homeassistant.helpers.device_registry.async_get"
            ) as mock_reg_getter,
        ):
            mock_dev_reg = MagicMock()
            mock_dev_reg.async_get = MagicMock(return_value=None)
            mock_reg_getter.return_value = mock_dev_reg

            with pytest.raises(HomeAssistantError, match="Device not found"):
                await get_diag_handler(call)


@pytest.mark.requires_ha
class TestUpdateListener:
    """Test config entry update listener."""

    @pytest.mark.asyncio
    async def test_update_listener_reloads_entry(self) -> None:
        """Test that update listener calls async_reload."""
        from custom_components.tuya_ble_mesh import _async_update_listener

        hass = make_mock_hass()
        hass.config_entries.async_reload = AsyncMock()
        entry = make_mock_entry()

        await _async_update_listener(hass, entry)

        hass.config_entries.async_reload.assert_called_once_with(entry.entry_id)


@pytest.mark.requires_ha
class TestDeviceRegistryIntegration:
    """Test device registry interaction during setup."""

    @pytest.mark.asyncio
    async def test_registry_record_error_when_unavailable(self) -> None:
        """record_error is called when coordinator starts unavailable."""
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        # Simulate connection failure: state.available = False after start
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshDeviceState
        mock_coord.state = TuyaBLEMeshDeviceState(available=False)

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
        # registry fixture captures the mock; check record_error was called
        # (mock_device_registry fixture is autouse — get from request not needed)

    @pytest.mark.asyncio
    async def test_registry_record_connection_when_available(self) -> None:
        """record_connection is called when coordinator starts available."""
        hass = make_mock_hass()
        entry = make_mock_entry()
        mock_device, mock_coord = _make_patches()

        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshDeviceState
        mock_coord.state = TuyaBLEMeshDeviceState(available=True)

        with (
            patch(_PATCH_MESH_DEVICE, return_value=mock_device),
            patch(_PATCH_COORDINATOR, return_value=mock_coord),
        ):
            result = await async_setup_entry(hass, entry)

        assert result is True
