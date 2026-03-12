"""Unit tests for button.py — Identify and Reconnect buttons."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity  # noqa: E402
from homeassistant.helpers.entity import EntityCategory  # noqa: E402

from custom_components.tuya_ble_mesh.button import (  # noqa: E402
    TuyaBLEMeshIdentifyButton,
    TuyaBLEMeshReconnectButton,
    async_setup_entry,
)
from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshCoordinator,
    TuyaBLEMeshDeviceState,
)


def make_mock_coordinator(*, available: bool = True) -> MagicMock:
    """Return a minimal coordinator mock with a device."""
    coord = MagicMock(spec=TuyaBLEMeshCoordinator)
    coord.state = TuyaBLEMeshDeviceState(available=available)
    coord.device = MagicMock()
    coord.device.address = "DC:23:4D:21:43:A5"
    coord.device.send_power = AsyncMock()
    coord.device.disconnect = AsyncMock()
    coord.schedule_reconnect = MagicMock()
    coord.add_listener = MagicMock(return_value=MagicMock())
    coord.async_add_listener = MagicMock(return_value=MagicMock())
    return coord


# --- TuyaBLEMeshIdentifyButton ---


class TestIdentifyButtonConstruction:
    """Test Identify button attributes."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshIdentifyButton(coord, "entry1")
        assert btn.unique_id == "DC:23:4D:21:43:A5_identify"

    def test_entity_category(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshIdentifyButton(coord, "entry1")
        assert btn._attr_entity_category == EntityCategory.CONFIG

    def test_translation_key(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshIdentifyButton(coord, "entry1")
        assert btn._attr_translation_key == "identify"

    def test_icon(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshIdentifyButton(coord, "entry1")
        assert btn._attr_icon == "mdi:flash-alert"

    def test_inherits_button_entity(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshIdentifyButton(coord, "entry1")
        assert isinstance(btn, ButtonEntity)


class TestIdentifyButtonPress:
    """Test Identify button press behavior."""

    @pytest.mark.asyncio
    async def test_press_flashes_device(self) -> None:
        """Pressing identify flashes LED 3 times (off/on pairs)."""
        from custom_components.tuya_ble_mesh.button import (
            _IDENTIFY_FLASH_COUNT,
            _IDENTIFY_FLASH_INTERVAL,
        )

        coord = make_mock_coordinator()
        btn = TuyaBLEMeshIdentifyButton(coord, "entry1")

        with patch("custom_components.tuya_ble_mesh.button.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await btn.async_press()

        assert coord.device.send_power.call_count == _IDENTIFY_FLASH_COUNT * 2
        assert mock_sleep.call_count == _IDENTIFY_FLASH_COUNT * 2
        mock_sleep.assert_called_with(_IDENTIFY_FLASH_INTERVAL)

    @pytest.mark.asyncio
    async def test_press_alternates_off_on(self) -> None:
        """Verify press sends False then True on each flash cycle."""
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshIdentifyButton(coord, "entry1")

        with patch("custom_components.tuya_ble_mesh.button.asyncio.sleep", new_callable=AsyncMock):
            await btn.async_press()

        calls = [call.args[0] for call in coord.device.send_power.call_args_list]
        # Each cycle: False then True
        for i in range(0, len(calls), 2):
            assert calls[i] is False, f"Expected False at index {i}"
            assert calls[i + 1] is True, f"Expected True at index {i+1}"

    @pytest.mark.asyncio
    async def test_press_skips_if_no_send_power(self) -> None:
        """Devices without send_power are gracefully skipped."""
        coord = make_mock_coordinator()
        del coord.device.send_power  # remove the attribute
        btn = TuyaBLEMeshIdentifyButton(coord, "entry1")

        with patch("custom_components.tuya_ble_mesh.button.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await btn.async_press()  # should not raise

        mock_sleep.assert_not_called()


# --- TuyaBLEMeshReconnectButton ---


class TestReconnectButtonConstruction:
    """Test Reconnect button attributes."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshReconnectButton(coord, "entry1")
        assert btn.unique_id == "DC:23:4D:21:43:A5_reconnect"

    def test_device_class(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshReconnectButton(coord, "entry1")
        assert btn._attr_device_class == ButtonDeviceClass.RESTART

    def test_entity_category(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshReconnectButton(coord, "entry1")
        assert btn._attr_entity_category == EntityCategory.CONFIG

    def test_translation_key(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshReconnectButton(coord, "entry1")
        assert btn._attr_translation_key == "reconnect"

    def test_inherits_button_entity(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshReconnectButton(coord, "entry1")
        assert isinstance(btn, ButtonEntity)


class TestReconnectButtonAvailable:
    """Test Reconnect button is always available."""

    def test_available_when_connected(self) -> None:
        coord = make_mock_coordinator(available=True)
        btn = TuyaBLEMeshReconnectButton(coord, "entry1")
        assert btn.available is True

    def test_available_when_disconnected(self) -> None:
        """Reconnect button must work even when device is offline."""
        coord = make_mock_coordinator(available=False)
        btn = TuyaBLEMeshReconnectButton(coord, "entry1")
        assert btn.available is True


class TestReconnectButtonPress:
    """Test Reconnect button press behavior."""

    @pytest.mark.asyncio
    async def test_press_disconnects_and_schedules_reconnect(self) -> None:
        coord = make_mock_coordinator()
        btn = TuyaBLEMeshReconnectButton(coord, "entry1")

        await btn.async_press()

        coord.device.disconnect.assert_called_once()
        coord.schedule_reconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_press_swallows_os_error(self) -> None:
        """OSError during disconnect is suppressed — reconnect still runs."""
        coord = make_mock_coordinator()
        coord.device.disconnect = AsyncMock(side_effect=OSError("BLE gone"))
        btn = TuyaBLEMeshReconnectButton(coord, "entry1")

        await btn.async_press()  # must not raise

        coord.schedule_reconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_press_swallows_connection_error(self) -> None:
        """ConnectionError during disconnect is suppressed."""
        coord = make_mock_coordinator()
        coord.device.disconnect = AsyncMock(side_effect=ConnectionError("reset"))
        btn = TuyaBLEMeshReconnectButton(coord, "entry1")

        await btn.async_press()  # must not raise

        coord.schedule_reconnect.assert_called_once()


# --- async_setup_entry ---


class TestButtonSetupEntry:
    """Test async_setup_entry creates expected entities."""

    @pytest.mark.asyncio
    async def test_setup_creates_two_buttons(self) -> None:
        coord = make_mock_coordinator()
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = MagicMock()

        added: list[object] = []
        await async_setup_entry(MagicMock(), entry, added.extend)

        assert len(added) == 2
        types = {type(e) for e in added}
        assert TuyaBLEMeshIdentifyButton in types
        assert TuyaBLEMeshReconnectButton in types

    @pytest.mark.asyncio
    async def test_setup_entity_unique_ids(self) -> None:
        coord = make_mock_coordinator()
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = None

        added: list[object] = []
        await async_setup_entry(MagicMock(), entry, added.extend)

        unique_ids = {e.unique_id for e in added}  # type: ignore[attr-defined]
        assert "DC:23:4D:21:43:A5_identify" in unique_ids
        assert "DC:23:4D:21:43:A5_reconnect" in unique_ids
