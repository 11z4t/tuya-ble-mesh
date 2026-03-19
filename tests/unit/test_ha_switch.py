"""Unit tests for the Tuya BLE Mesh switch entity platform."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from homeassistant.components.switch import SwitchDeviceClass  # noqa: E402

from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshDeviceState,
)
from custom_components.tuya_ble_mesh.switch import (  # noqa: E402
    TuyaBLEMeshSwitch,
    async_setup_entry,
)


def make_mock_coordinator(
    *,
    is_on: bool = True,
    available: bool = True,
) -> MagicMock:
    """Create a mock coordinator with configurable state."""
    coord = MagicMock()
    coord.state = TuyaBLEMeshDeviceState(
        is_on=is_on,
        available=available,
    )
    coord.device = MagicMock()
    coord.device.address = "DC:23:4D:21:43:A5"
    coord.device.send_power = AsyncMock()
    coord.add_listener = MagicMock(return_value=MagicMock())
    coord.async_add_listener = MagicMock(return_value=MagicMock())

    # send_command_with_retry: pass-through that executes the coro_func directly
    async def _pass_through(coro_func, **_kw):  # type: ignore[no-untyped-def]
        await coro_func()

    coord.send_command_with_retry = _pass_through
    return coord


@pytest.mark.requires_ha
class TestSwitchProperties:
    """Test TuyaBLEMeshSwitch properties."""

    def test_switch_unique_id(self) -> None:
        coord = make_mock_coordinator()
        switch = TuyaBLEMeshSwitch(coord, "test_entry")
        assert "DC:23:4D:21:43:A5" in switch.unique_id
        assert switch.unique_id.endswith("_switch")

    def test_switch_is_on_property(self) -> None:
        coord = make_mock_coordinator(is_on=True)
        switch = TuyaBLEMeshSwitch(coord, "test_entry")
        assert switch.is_on is True

    def test_switch_is_off_property(self) -> None:
        coord = make_mock_coordinator(is_on=False)
        switch = TuyaBLEMeshSwitch(coord, "test_entry")
        assert switch.is_on is False

    def test_switch_available(self) -> None:
        coord = make_mock_coordinator(available=True)
        switch = TuyaBLEMeshSwitch(coord, "test_entry")
        assert switch.available is True

    def test_switch_not_available(self) -> None:
        coord = make_mock_coordinator(available=False)
        switch = TuyaBLEMeshSwitch(coord, "test_entry")
        assert switch.available is False

    def test_switch_device_class_outlet(self) -> None:
        coord = make_mock_coordinator()
        switch = TuyaBLEMeshSwitch(coord, "test_entry")
        assert switch.device_class == SwitchDeviceClass.OUTLET

    def test_switch_should_poll_false(self) -> None:
        coord = make_mock_coordinator()
        switch = TuyaBLEMeshSwitch(coord, "test_entry")
        assert switch.should_poll is False

    def test_switch_with_device_info(self) -> None:
        """Test that device_info is properly set when provided."""
        from homeassistant.helpers.device_registry import DeviceInfo

        coord = make_mock_coordinator()
        device_info: DeviceInfo = {
            "identifiers": {("tuya_ble_mesh", "DC:23:4D:21:43:A5")},
            "name": "Test Plug",
            "manufacturer": "Tuya",
        }
        switch = TuyaBLEMeshSwitch(coord, "test_entry", device_info)
        assert switch._attr_device_info == device_info


@pytest.mark.requires_ha
class TestSwitchActions:
    """Test switch turn_on/turn_off actions."""

    @pytest.mark.asyncio
    async def test_switch_turn_on(self) -> None:
        coord = make_mock_coordinator()
        switch = TuyaBLEMeshSwitch(coord, "test_entry")

        await switch.async_turn_on()

        coord.device.send_power.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_switch_turn_off(self) -> None:
        coord = make_mock_coordinator()
        switch = TuyaBLEMeshSwitch(coord, "test_entry")

        await switch.async_turn_off()

        coord.device.send_power.assert_called_once_with(False)


@pytest.mark.requires_ha
class TestSwitchLifecycle:
    """Test HA lifecycle methods."""

    @pytest.mark.asyncio
    async def test_added_to_hass(self) -> None:
        coord = make_mock_coordinator()
        switch = TuyaBLEMeshSwitch(coord, "test_entry")
        switch.hass = MagicMock()

        await switch.async_added_to_hass()

        coord.async_add_listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_removed_from_hass(self) -> None:
        coord = make_mock_coordinator()
        remove_fn = MagicMock()
        coord.async_add_listener.return_value = remove_fn
        switch = TuyaBLEMeshSwitch(coord, "test_entry")
        switch.hass = MagicMock()

        await switch.async_added_to_hass()
        # CoordinatorEntity stores the unsubscribe fn via async_on_remove;
        # _call_on_remove_callbacks() triggers cleanup (as done by async_remove())
        switch._call_on_remove_callbacks()

        remove_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_triggers_ha_state_write(self) -> None:
        coord = make_mock_coordinator()
        switch = TuyaBLEMeshSwitch(coord, "test_entry")
        switch.async_write_ha_state = MagicMock()  # type: ignore[assignment]
        switch.hass = MagicMock()

        await switch.async_added_to_hass()
        callback = coord.async_add_listener.call_args[0][0]
        callback()

        switch.async_write_ha_state.assert_called_once()


@pytest.mark.requires_ha
class TestSwitchPlatformSetup:
    """Test async_setup_entry for the switch platform."""

    @pytest.mark.asyncio
    async def test_setup_creates_switch_for_plug(self) -> None:
        coord = make_mock_coordinator()
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = MagicMock()
        entry.data = {"device_type": "plug"}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], TuyaBLEMeshSwitch)

    @pytest.mark.asyncio
    async def test_setup_skips_light_device_type(self) -> None:
        coord = make_mock_coordinator()
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = MagicMock()
        entry.data = {"device_type": "light"}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_not_called()

    @pytest.mark.asyncio
    async def test_setup_creates_switch_for_sig_plug(self) -> None:
        """SIG plug device type should also create switch entity."""
        coord = make_mock_coordinator()
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = MagicMock()
        entry.data = {"device_type": "sig_plug"}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_called_once()
        entities = add_entities.call_args[0][0]
        assert len(entities) == 1
        assert isinstance(entities[0], TuyaBLEMeshSwitch)

    @pytest.mark.asyncio
    async def test_setup_skips_no_device_type(self) -> None:
        """Default (no device_type) should not create switch."""
        coord = make_mock_coordinator()
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "entry1"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = MagicMock()
        entry.data = {}
        add_entities = MagicMock()

        await async_setup_entry(hass, entry, add_entities)

        add_entities.assert_not_called()
