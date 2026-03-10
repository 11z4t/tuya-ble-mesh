"""Unit tests for SIGMeshBridgeDevice and TelinkBridgeDevice."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from tuya_ble_mesh.exceptions import ConnectionError as MeshConnectionError  # noqa: E402
from tuya_ble_mesh.exceptions import SIGMeshError  # noqa: E402
from tuya_ble_mesh.sig_mesh_bridge import (  # noqa: E402
    SIGMeshBridgeDevice,
    TelinkBridgeDevice,
)


def _make_sig_device() -> SIGMeshBridgeDevice:
    """Create a SIG Mesh bridge device for testing."""
    return SIGMeshBridgeDevice("DC:23:4F:10:52:C4", 0x00B0, "192.168.5.10", 8099)


def _make_telink_device() -> TelinkBridgeDevice:
    """Create a Telink bridge device for testing."""
    return TelinkBridgeDevice("DC:23:4D:21:43:A5", "192.168.5.10", 8099)


def _patch_http_methods(
    dev: SIGMeshBridgeDevice | TelinkBridgeDevice,
    get_responses: list[dict[str, Any]] | None = None,
    post_responses: list[dict[str, Any]] | None = None,
    get_side_effect: Exception | None = None,
) -> None:
    """Patch _http_get and _http_post on a device to return canned responses.

    Args:
        dev: Device to patch.
        get_responses: List of dicts to return from successive _http_get calls.
        post_responses: List of dicts to return from successive _http_post calls.
        get_side_effect: If set, _http_get raises this on every call.
    """
    if get_side_effect is not None:
        dev._http_get = AsyncMock(side_effect=get_side_effect)  # type: ignore[method-assign]
    elif get_responses is not None:
        dev._http_get = AsyncMock(side_effect=get_responses)  # type: ignore[method-assign]
    else:
        dev._http_get = AsyncMock(return_value={})  # type: ignore[method-assign]

    if post_responses is not None:
        dev._http_post = AsyncMock(side_effect=post_responses)  # type: ignore[method-assign]
    else:
        dev._http_post = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]


# --- SIGMeshBridgeDevice ---


class TestSIGBridgeInit:
    """Test SIGMeshBridgeDevice initialization."""

    def test_address_uppercased(self) -> None:
        dev = SIGMeshBridgeDevice("dc:23:4f:10:52:c4", 0x00B0, "host", 8099)
        assert dev.address == "DC:23:4F:10:52:C4"

    def test_not_connected_initially(self) -> None:
        dev = _make_sig_device()
        assert dev.is_connected is False

    def test_firmware_version_none_initially(self) -> None:
        dev = _make_sig_device()
        assert dev.firmware_version is None


class TestSIGBridgeConnect:
    """Test SIG bridge connection."""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        dev = _make_sig_device()
        _patch_http_methods(dev, get_responses=[{"status": "ok"}])

        await dev.connect()

        assert dev.is_connected is True
        assert dev.firmware_version == "bridge"

    @pytest.mark.asyncio
    async def test_connect_failure_raises(self) -> None:
        dev = _make_sig_device()
        _patch_http_methods(dev, get_side_effect=MeshConnectionError("Connection refused"))

        with pytest.raises(MeshConnectionError, match="not reachable"):
            await dev.connect(timeout=0.1, max_retries=1)

    @pytest.mark.asyncio
    async def test_connect_bad_status_raises(self) -> None:
        dev = _make_sig_device()
        _patch_http_methods(dev, get_responses=[{"status": "error"}])

        with pytest.raises(MeshConnectionError, match="not reachable"):
            await dev.connect(timeout=0.1, max_retries=1)

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        dev = _make_sig_device()
        dev._connected = True
        await dev.disconnect()
        assert dev.is_connected is False


class TestSIGBridgeSendPower:
    """Test SIG bridge send_power."""

    @pytest.mark.asyncio
    async def test_send_power_on(self) -> None:
        dev = _make_sig_device()
        dev._connected = True
        _patch_http_methods(
            dev,
            get_responses=[{"action": "on", "success": True, "status": "ON", "timestamp": 123}],
            post_responses=[{"ok": True}],
        )

        await dev.send_power(True)

    @pytest.mark.asyncio
    async def test_send_power_not_connected_raises(self) -> None:
        dev = _make_sig_device()

        with pytest.raises(SIGMeshError, match="not connected"):
            await dev.send_power(True)

    @pytest.mark.asyncio
    async def test_send_power_fires_onoff_callback(self) -> None:
        dev = _make_sig_device()
        dev._connected = True
        callback = MagicMock()
        dev.register_onoff_callback(callback)
        _patch_http_methods(
            dev,
            get_responses=[{"action": "on", "success": True, "status": "ON", "timestamp": 1}],
            post_responses=[{"ok": True}],
        )

        await dev.send_power(True)

        callback.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_send_power_failure_raises(self) -> None:
        dev = _make_sig_device()
        dev._connected = True
        _patch_http_methods(
            dev,
            get_responses=[{"action": "on", "success": False, "error": "BLE timeout", "timestamp": 1}],
            post_responses=[{"ok": True}],
        )

        with (
            patch("tuya_ble_mesh.sig_mesh_bridge.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(SIGMeshError, match="Bridge command failed"),
        ):
            await dev.send_power(True, max_retries=1)


class TestSIGBridgeCallbacks:
    """Test callback registration and removal."""

    def test_register_unregister_onoff(self) -> None:
        dev = _make_sig_device()
        cb = MagicMock()
        dev.register_onoff_callback(cb)
        assert cb in dev._onoff_callbacks
        dev.unregister_onoff_callback(cb)
        assert cb not in dev._onoff_callbacks

    def test_register_unregister_vendor(self) -> None:
        dev = _make_sig_device()
        cb = MagicMock()
        dev.register_vendor_callback(cb)
        assert cb in dev._vendor_callbacks
        dev.unregister_vendor_callback(cb)
        assert cb not in dev._vendor_callbacks

    def test_register_unregister_disconnect(self) -> None:
        dev = _make_sig_device()
        cb = MagicMock()
        dev.register_disconnect_callback(cb)
        assert cb in dev._disconnect_callbacks
        dev.unregister_disconnect_callback(cb)
        assert cb not in dev._disconnect_callbacks


# --- TelinkBridgeDevice ---


class TestTelinkBridgeInit:
    """Test TelinkBridgeDevice initialization."""

    def test_address_uppercased(self) -> None:
        dev = TelinkBridgeDevice("dc:23:4d:21:43:a5", "host", 8099)
        assert dev.address == "DC:23:4D:21:43:A5"

    def test_not_connected_initially(self) -> None:
        dev = _make_telink_device()
        assert dev.is_connected is False

    def test_mesh_id_property(self) -> None:
        dev = _make_telink_device()
        assert dev.mesh_id == 0
        dev.mesh_id = 42
        assert dev.mesh_id == 42


class TestTelinkBridgeConnect:
    """Test Telink bridge connection."""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        dev = _make_telink_device()
        _patch_http_methods(dev, get_responses=[{"status": "ok"}])

        await dev.connect()

        assert dev.is_connected is True
        assert dev.firmware_version == "bridge-telink"

    @pytest.mark.asyncio
    async def test_connect_failure_raises(self) -> None:
        dev = _make_telink_device()
        _patch_http_methods(dev, get_side_effect=MeshConnectionError("refused"))

        with pytest.raises(MeshConnectionError, match="not reachable"):
            await dev.connect(timeout=0.1, max_retries=1)

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        dev = _make_telink_device()
        dev._connected = True
        await dev.disconnect()
        assert dev.is_connected is False


class TestTelinkBridgeCommands:
    """Test Telink bridge command methods."""

    async def _setup_connected_device(
        self, action: str
    ) -> TelinkBridgeDevice:
        dev = _make_telink_device()
        dev._connected = True
        _patch_http_methods(
            dev,
            get_responses=[
                {"action": action, "device_type": "telink", "success": True, "timestamp": 1},
            ],
            post_responses=[{"ok": True}],
        )
        return dev

    @pytest.mark.asyncio
    async def test_send_power_on(self) -> None:
        dev = await self._setup_connected_device("on")
        await dev.send_power(True)
        assert dev._is_on is True

    @pytest.mark.asyncio
    async def test_send_power_off(self) -> None:
        dev = await self._setup_connected_device("off")
        dev._is_on = True
        await dev.send_power(False)
        assert dev._is_on is False

    @pytest.mark.asyncio
    async def test_send_brightness(self) -> None:
        dev = await self._setup_connected_device("brightness")
        await dev.send_brightness(75)
        assert dev._brightness == 75
        assert dev._is_on is True

    @pytest.mark.asyncio
    async def test_send_color_temp(self) -> None:
        dev = await self._setup_connected_device("color_temp")
        await dev.send_color_temp(64)
        assert dev._color_temp == 64

    @pytest.mark.asyncio
    async def test_send_color(self) -> None:
        dev = await self._setup_connected_device("color")
        await dev.send_color(255, 128, 0)
        assert dev._red == 255
        assert dev._green == 128
        assert dev._blue == 0

    @pytest.mark.asyncio
    async def test_send_light_mode(self) -> None:
        dev = await self._setup_connected_device("light_mode")
        await dev.send_light_mode(1)
        assert dev._mode == 1

    @pytest.mark.asyncio
    async def test_send_color_brightness(self) -> None:
        dev = await self._setup_connected_device("color_brightness")
        await dev.send_color_brightness(50)
        assert dev._color_brightness == 50

    @pytest.mark.asyncio
    async def test_not_connected_raises(self) -> None:
        dev = _make_telink_device()
        with pytest.raises(SIGMeshError, match="not connected"):
            await dev.send_power(True)


class TestTelinkBridgeCallbacks:
    """Test Telink bridge callback management."""

    def test_register_unregister_status(self) -> None:
        dev = _make_telink_device()
        cb = MagicMock()
        dev.register_status_callback(cb)
        assert cb in dev._status_callbacks
        dev.unregister_status_callback(cb)
        assert cb not in dev._status_callbacks

    def test_register_unregister_disconnect(self) -> None:
        dev = _make_telink_device()
        cb = MagicMock()
        dev.register_disconnect_callback(cb)
        assert cb in dev._disconnect_callbacks
        dev.unregister_disconnect_callback(cb)
        assert cb not in dev._disconnect_callbacks

    @pytest.mark.asyncio
    async def test_fire_status_callback_on_command(self) -> None:
        dev = _make_telink_device()
        dev._connected = True
        cb = MagicMock()
        dev.register_status_callback(cb)
        _patch_http_methods(
            dev,
            get_responses=[
                {"action": "on", "device_type": "telink", "success": True, "timestamp": 1},
            ],
            post_responses=[{"ok": True}],
        )

        await dev.send_power(True)

        cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_fire_disconnect_on_timeout(self) -> None:
        dev = _make_telink_device()
        dev._connected = True
        cb = MagicMock()
        dev.register_disconnect_callback(cb)
        # _http_post succeeds, _http_get always fails (bridge unreachable during BLE op)
        dev._http_post = AsyncMock(return_value={"ok": True})  # type: ignore[method-assign]
        dev._http_get = AsyncMock(side_effect=MeshConnectionError("unreachable"))  # type: ignore[method-assign]

        with (
            patch("tuya_ble_mesh.sig_mesh_bridge._MAX_POLL_ATTEMPTS", 1),
            patch("tuya_ble_mesh.sig_mesh_bridge._POLL_INTERVAL", 0.01),
            patch("tuya_ble_mesh.sig_mesh_bridge.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(SIGMeshError, match="Timed out"),
        ):
            await dev.send_power(True)

        # Disconnect callback fires on each retry attempt
        assert cb.called
        assert dev.is_connected is False


class TestTelinkBridgeDefaultBrightness:
    """Test that turning on sets default brightness."""

    @pytest.mark.asyncio
    async def test_power_on_sets_default_brightness(self) -> None:
        dev = _make_telink_device()
        dev._connected = True
        dev._brightness = 0
        _patch_http_methods(
            dev,
            get_responses=[
                {"action": "on", "device_type": "telink", "success": True, "timestamp": 1},
            ],
            post_responses=[{"ok": True}],
        )

        await dev.send_power(True)

        assert dev._brightness == 100

    @pytest.mark.asyncio
    async def test_power_on_preserves_existing_brightness(self) -> None:
        dev = _make_telink_device()
        dev._connected = True
        dev._brightness = 50
        _patch_http_methods(
            dev,
            get_responses=[
                {"action": "on", "device_type": "telink", "success": True, "timestamp": 1},
            ],
            post_responses=[{"ok": True}],
        )

        await dev.send_power(True)

        assert dev._brightness == 50
