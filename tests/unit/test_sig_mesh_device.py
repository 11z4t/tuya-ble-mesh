"""Unit tests for the SIGMeshDevice class."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from tuya_ble_mesh.exceptions import (  # noqa: E402
    SIGMeshError,
    SIGMeshKeyError,
)
from tuya_ble_mesh.sig_mesh_device import (  # noqa: E402
    _INITIAL_SEQ,
    SIGMeshDevice,
)


def make_mock_secrets() -> MagicMock:
    """Create a mock SecretsManager that returns valid hex keys."""
    secrets = MagicMock()
    # 16-byte keys as hex strings (32 chars)
    secrets.get = AsyncMock(
        side_effect=lambda item, field="password": {  # pragma: allowlist secret
            "s17-net-key": "f7a2a44f8e8a8029064f173ddc1e2b00",
            "s17-dev-key-00aa": "00112233445566778899aabbccddeeff",
            "s17-app-key": "3216d1509884b533248541792b877f98",
        }.get(item, "00" * 16)
    )
    return secrets


class TestSIGMeshDeviceProperties:
    """Test basic SIGMeshDevice properties."""

    def test_address_uppercased(self) -> None:
        dev = SIGMeshDevice("dc:23:4d:21:43:a5", 0x00AA, 0x0001, MagicMock())
        assert dev.address == "DC:23:4D:21:43:A5"

    def test_not_connected_initially(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        assert dev.is_connected is False

    def test_firmware_version_is_none(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        assert dev.firmware_version is None


class TestSIGMeshDeviceCallbacks:
    """Test callback registration."""

    def test_register_onoff_callback(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        cb = MagicMock()
        dev.register_onoff_callback(cb)
        assert cb in dev._onoff_callbacks

    def test_unregister_onoff_callback(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        cb = MagicMock()
        dev.register_onoff_callback(cb)
        dev.unregister_onoff_callback(cb)
        assert cb not in dev._onoff_callbacks

    def test_register_disconnect_callback(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        cb = MagicMock()
        dev.register_disconnect_callback(cb)
        assert cb in dev._disconnect_callbacks

    def test_unregister_disconnect_callback(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        cb = MagicMock()
        dev.register_disconnect_callback(cb)
        dev.unregister_disconnect_callback(cb)
        assert cb not in dev._disconnect_callbacks


class TestSIGMeshDeviceConnect:
    """Test connect and key loading."""

    @pytest.mark.asyncio
    async def test_connect_loads_keys(self) -> None:
        secrets = make_mock_secrets()
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, secrets)

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.start_notify = AsyncMock()
        mock_client.is_connected = True
        mock_client.set_disconnected_callback = MagicMock()

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakScanner") as mock_scanner,
            patch(
                "tuya_ble_mesh.sig_mesh_device.BleakClient",
                return_value=mock_client,
            ),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=MagicMock())
            await dev.connect(max_retries=1)

        assert dev._keys is not None
        assert dev.is_connected is True
        secrets.get.assert_any_call(
            "s17-net-key",
            "password",  # pragma: allowlist secret
        )
        secrets.get.assert_any_call(
            "s17-app-key",
            "password",  # pragma: allowlist secret
        )

    @pytest.mark.asyncio
    async def test_connect_key_failure_raises(self) -> None:
        secrets = MagicMock()
        secrets.get = AsyncMock(side_effect=RuntimeError("1Password unavailable"))
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, secrets)

        with pytest.raises(SIGMeshKeyError, match="Failed to load"):
            await dev.connect(max_retries=1)


class TestSIGMeshDeviceDisconnect:
    """Test disconnect."""

    @pytest.mark.asyncio
    async def test_disconnect_clears_keys(self) -> None:
        secrets = make_mock_secrets()
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, secrets)

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.start_notify = AsyncMock()
        mock_client.stop_notify = AsyncMock()
        mock_client.is_connected = True
        mock_client.set_disconnected_callback = MagicMock()

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakScanner") as mock_scanner,
            patch(
                "tuya_ble_mesh.sig_mesh_device.BleakClient",
                return_value=mock_client,
            ),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=MagicMock())
            await dev.connect(max_retries=1)

        await dev.disconnect()

        assert dev._keys is None
        assert dev._client is None


class TestSIGMeshDeviceSendPower:
    """Test send_power."""

    @pytest.mark.asyncio
    async def test_send_power_raises_when_not_connected(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())

        with pytest.raises(SIGMeshError, match="Not connected"):
            await dev.send_power(True)

    @pytest.mark.asyncio
    async def test_send_power_writes_to_gatt(self) -> None:
        secrets = make_mock_secrets()
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, secrets)

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.start_notify = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_client.is_connected = True
        mock_client.set_disconnected_callback = MagicMock()

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakScanner") as mock_scanner,
            patch(
                "tuya_ble_mesh.sig_mesh_device.BleakClient",
                return_value=mock_client,
            ),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=MagicMock())
            await dev.connect(max_retries=1)

        await dev.send_power(True)

        mock_client.write_gatt_char.assert_called_once()
        call_args = mock_client.write_gatt_char.call_args
        assert call_args[0][0] == "00002add-0000-1000-8000-00805f9b34fb"
        assert call_args[1]["response"] is False

    @pytest.mark.asyncio
    async def test_send_power_increments_tid(self) -> None:
        secrets = make_mock_secrets()
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, secrets)

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.start_notify = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_client.is_connected = True
        mock_client.set_disconnected_callback = MagicMock()

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakScanner") as mock_scanner,
            patch(
                "tuya_ble_mesh.sig_mesh_device.BleakClient",
                return_value=mock_client,
            ),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=MagicMock())
            await dev.connect(max_retries=1)

        assert dev._tid == 0
        await dev.send_power(True)
        assert dev._tid == 1
        await dev.send_power(False)
        assert dev._tid == 2


class TestSIGMeshDeviceSequence:
    """Test sequence number management."""

    def test_initial_seq(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        assert dev._seq == _INITIAL_SEQ

    def test_next_seq_increments(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        s1 = dev._next_seq()
        s2 = dev._next_seq()
        assert s2 == s1 + 1


class TestSIGMeshDeviceBLEDisconnect:
    """Test BLE disconnect callback."""

    def test_on_ble_disconnect_calls_callbacks(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        cb = MagicMock()
        dev.register_disconnect_callback(cb)

        dev._on_ble_disconnect(MagicMock())

        cb.assert_called_once()

    def test_on_ble_disconnect_clears_client(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        dev._client = MagicMock()

        dev._on_ble_disconnect(MagicMock())

        assert dev._client is None
