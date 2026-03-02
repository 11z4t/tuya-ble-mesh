"""Unit tests for the MeshDevice command interface."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.const import TELINK_CHAR_COMMAND
from tuya_ble_mesh.device import MESH_ADDRESS_ALL, MESH_ADDRESS_DEFAULT, MeshDevice
from tuya_ble_mesh.exceptions import (
    ConnectionError,
    ProtocolError,
    TimeoutError,
)
from tuya_ble_mesh.protocol import StatusResponse

MAC = "DC:23:4D:21:43:A5"
MESH_NAME = b"out_of_mesh"
MESH_PASS = b"123456"
SESSION_KEY = b"\x00" * 16


def _make_device(**kwargs: object) -> MeshDevice:
    """Create a MeshDevice with default test parameters."""
    return MeshDevice(MAC, MESH_NAME, MESH_PASS, **kwargs)  # type: ignore[arg-type]


def _make_connected_device() -> tuple[MeshDevice, AsyncMock]:
    """Create a MeshDevice in connected state with mock client."""
    device = _make_device()
    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.start_notify = AsyncMock()
    device._client = client
    device._session_key = SESSION_KEY
    device._connected = True
    return device, client


# --- Construction ---


class TestConstruction:
    """Test MeshDevice initialization."""

    def test_default_mesh_id(self) -> None:
        device = _make_device()
        assert device.mesh_id == MESH_ADDRESS_DEFAULT

    def test_broadcast_mesh_id(self) -> None:
        device = _make_device(mesh_id=MESH_ADDRESS_ALL)
        assert device.mesh_id == 0xFFFF

    def test_custom_mesh_id(self) -> None:
        device = _make_device(mesh_id=42)
        assert device.mesh_id == 42

    def test_address_uppercased(self) -> None:
        device = MeshDevice("dc:23:4d:21:43:a5", MESH_NAME, MESH_PASS)
        assert device.address == MAC

    def test_invalid_mac_raises(self) -> None:
        with pytest.raises(ProtocolError):
            MeshDevice("INVALID", MESH_NAME, MESH_PASS)

    def test_not_connected_initially(self) -> None:
        device = _make_device()
        assert device.is_connected is False


# --- mesh_id property ---


class TestMeshId:
    """Test mesh_id getter/setter."""

    def test_set_valid(self) -> None:
        device = _make_device()
        device.mesh_id = 100
        assert device.mesh_id == 100

    def test_set_zero(self) -> None:
        device = _make_device()
        device.mesh_id = 0
        assert device.mesh_id == 0

    def test_set_max(self) -> None:
        device = _make_device()
        device.mesh_id = 0xFFFF
        assert device.mesh_id == 0xFFFF

    def test_set_negative_raises(self) -> None:
        device = _make_device()
        with pytest.raises(ProtocolError, match="mesh_id"):
            device.mesh_id = -1

    def test_set_too_large_raises(self) -> None:
        device = _make_device()
        with pytest.raises(ProtocolError, match="mesh_id"):
            device.mesh_id = 0x10000


# --- connect / disconnect ---


class TestConnect:
    """Test connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        device = _make_device()

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.start_notify = AsyncMock()

        with (
            patch("tuya_ble_mesh.device.BleakClient", return_value=mock_client),
            patch(
                "tuya_ble_mesh.device.provision",
                return_value=SESSION_KEY,
            ),
        ):
            await device.connect()

        assert device.is_connected is True
        mock_client.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_already_connected(self) -> None:
        device, _ = _make_connected_device()
        await device.connect()  # should be a no-op

    @pytest.mark.asyncio
    async def test_connect_ble_failure(self) -> None:
        device = _make_device()

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(side_effect=OSError("BLE fail"))

        with (
            patch("tuya_ble_mesh.device.BleakClient", return_value=mock_client),
            pytest.raises(ConnectionError, match="Failed to connect"),
        ):
            await device.connect()

        assert device.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_provision_failure(self) -> None:
        device = _make_device()

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with (
            patch("tuya_ble_mesh.device.BleakClient", return_value=mock_client),
            patch(
                "tuya_ble_mesh.device.provision",
                side_effect=Exception("pair failed"),
            ),
            pytest.raises(ConnectionError, match="Provisioning failed"),
        ):
            await device.connect()

        assert device.is_connected is False
        mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        device, client = _make_connected_device()
        await device.disconnect()

        assert device.is_connected is False
        client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self) -> None:
        device = _make_device()
        await device.disconnect()  # should not raise


# --- Context manager ---


class TestContextManager:
    """Test async context manager."""

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.start_notify = AsyncMock()

        with (
            patch("tuya_ble_mesh.device.BleakClient", return_value=mock_client),
            patch(
                "tuya_ble_mesh.device.provision",
                return_value=SESSION_KEY,
            ),
        ):
            device = _make_device()
            async with device:
                assert device.is_connected is True
            assert device.is_connected is False


# --- send_command ---


class TestSendCommand:
    """Test raw command sending."""

    @pytest.mark.asyncio
    async def test_send_command(self) -> None:
        device, client = _make_connected_device()
        await device.send_command(0xD0, b"\x01")

        client.write_gatt_char.assert_called_once()
        args = client.write_gatt_char.call_args
        assert args[0][0] == TELINK_CHAR_COMMAND
        assert len(args[0][1]) == 20
        assert args[1]["response"] is False

    @pytest.mark.asyncio
    async def test_send_command_not_connected_raises(self) -> None:
        device = _make_device()
        with pytest.raises(ConnectionError, match="Not connected"):
            await device.send_command(0xD0, b"\x01")

    @pytest.mark.asyncio
    async def test_send_command_custom_dest(self) -> None:
        device, client = _make_connected_device()
        await device.send_command(0xD0, b"\x01", dest_id=42)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_sequence_increments(self) -> None:
        device, _ = _make_connected_device()
        seq1 = device._next_sequence()
        seq2 = device._next_sequence()
        assert seq2 == seq1 + 1

    @pytest.mark.asyncio
    async def test_sequence_wraps(self) -> None:
        device, _ = _make_connected_device()
        device._sequence = 0xFFFFFF
        seq = device._next_sequence()
        assert seq == 0xFFFFFF
        next_seq = device._next_sequence()
        assert next_seq == 0


# --- send_power ---


class TestSendPower:
    """Test power on/off commands."""

    @pytest.mark.asyncio
    async def test_power_on(self) -> None:
        device, client = _make_connected_device()
        await device.send_power(True)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_power_off(self) -> None:
        device, client = _make_connected_device()
        await device.send_power(False)
        client.write_gatt_char.assert_called_once()


# --- send_brightness ---


class TestSendBrightness:
    """Test brightness commands."""

    @pytest.mark.asyncio
    async def test_valid_brightness(self) -> None:
        device, client = _make_connected_device()
        await device.send_brightness(100)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_brightness_zero(self) -> None:
        device, _ = _make_connected_device()
        await device.send_brightness(0)

    @pytest.mark.asyncio
    async def test_brightness_max(self) -> None:
        device, _ = _make_connected_device()
        await device.send_brightness(255)

    @pytest.mark.asyncio
    async def test_brightness_too_high(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Brightness"):
            await device.send_brightness(256)

    @pytest.mark.asyncio
    async def test_brightness_negative(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Brightness"):
            await device.send_brightness(-1)


# --- send_color_temp ---


class TestSendColorTemp:
    """Test color temperature commands."""

    @pytest.mark.asyncio
    async def test_valid_temp(self) -> None:
        device, client = _make_connected_device()
        await device.send_color_temp(64)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_temp_too_high(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Color temp"):
            await device.send_color_temp(256)

    @pytest.mark.asyncio
    async def test_temp_negative(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Color temp"):
            await device.send_color_temp(-1)


# --- send_color ---


class TestSendColor:
    """Test RGB color commands."""

    @pytest.mark.asyncio
    async def test_valid_color(self) -> None:
        device, client = _make_connected_device()
        await device.send_color(255, 128, 0)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_color_out_of_range(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="red"):
            await device.send_color(256, 0, 0)

    @pytest.mark.asyncio
    async def test_color_green_out_of_range(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="green"):
            await device.send_color(0, -1, 0)

    @pytest.mark.asyncio
    async def test_color_blue_out_of_range(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="blue"):
            await device.send_color(0, 0, 300)


# --- send_color_brightness ---


class TestSendColorBrightness:
    """Test color brightness commands."""

    @pytest.mark.asyncio
    async def test_valid(self) -> None:
        device, client = _make_connected_device()
        await device.send_color_brightness(50)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_out_of_range(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Color brightness"):
            await device.send_color_brightness(256)


# --- send_light_mode ---


class TestSendLightMode:
    """Test light mode commands."""

    @pytest.mark.asyncio
    async def test_valid(self) -> None:
        device, client = _make_connected_device()
        await device.send_light_mode(0)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_out_of_range(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Light mode"):
            await device.send_light_mode(256)


# --- send_mesh_address ---


class TestSendMeshAddress:
    """Test mesh address assignment."""

    @pytest.mark.asyncio
    async def test_valid(self) -> None:
        device, client = _make_connected_device()
        await device.send_mesh_address(100)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_zero_raises(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Mesh address"):
            await device.send_mesh_address(0)

    @pytest.mark.asyncio
    async def test_too_large_raises(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Mesh address"):
            await device.send_mesh_address(0x8000)


# --- send_mesh_reset ---


class TestSendMeshReset:
    """Test mesh reset command."""

    @pytest.mark.asyncio
    async def test_sends_reset(self) -> None:
        device, client = _make_connected_device()
        await device.send_mesh_reset()
        client.write_gatt_char.assert_called_once()


# --- Status callbacks ---


class TestStatusCallbacks:
    """Test status notification handling."""

    def test_register_and_unregister(self) -> None:
        device = _make_device()
        cb = MagicMock()
        device.register_status_callback(cb)
        assert cb in device._status_callbacks
        device.unregister_status_callback(cb)
        assert cb not in device._status_callbacks

    def test_notification_dispatches(self) -> None:
        device, _ = _make_connected_device()
        received: list[StatusResponse] = []
        device.register_status_callback(lambda s: received.append(s))

        # Build a fake 20-byte notification that decrypt_notification can handle
        with patch("tuya_ble_mesh.device.decrypt_notification") as mock_decrypt:
            # Return 20 bytes with valid status fields
            fake = bytearray(20)
            fake[3] = 1  # mesh_id
            fake[12] = 0  # mode
            fake[13] = 100  # white_brightness
            fake[14] = 50  # white_temp
            fake[15] = 0  # color_brightness
            fake[16] = 0  # red
            fake[17] = 0  # green
            fake[18] = 0  # blue
            mock_decrypt.return_value = bytes(fake)

            device._handle_notification(0, bytearray(20))

        assert len(received) == 1
        assert received[0].white_brightness == 100
        assert received[0].white_temp == 50

    def test_notification_without_session_key(self) -> None:
        device = _make_device()
        # Should not raise
        device._handle_notification(0, bytearray(20))

    def test_callback_exception_isolated(self) -> None:
        device, _ = _make_connected_device()

        def bad_callback(_status: StatusResponse) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        device.register_status_callback(bad_callback)
        received: list[StatusResponse] = []
        device.register_status_callback(lambda s: received.append(s))

        with patch("tuya_ble_mesh.device.decrypt_notification") as mock_decrypt:
            fake = bytearray(20)
            mock_decrypt.return_value = bytes(fake)
            device._handle_notification(0, bytearray(20))

        # Second callback still received the status despite first raising
        assert len(received) == 1


# --- wait_for_status ---


class TestWaitForStatus:
    """Test wait_for_status method."""

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(TimeoutError, match="No status"):
            await device.wait_for_status(timeout=0.01)
