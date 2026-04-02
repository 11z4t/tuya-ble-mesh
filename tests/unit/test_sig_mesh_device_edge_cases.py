"""Unit tests for sig_mesh_device.py — edge cases not covered by test_sig_mesh_device.py.

Covers:
  rssi: 230-232
  connect (ble_device_callback, adapter, device not found, notify fail,
           composition fail, BleakError retry → final raise): 310, 314,
           324-325, 332, 339-340, 354-355, 361-374
  disconnect (keys zeroing exception, pending tasks): 392-394, 399, 401
  _next_seq / _next_seqs exhausted: 420-421, 443-444
  _bluetoothctl_remove: 487-497
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.exc import BleakError

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "custom_components"
        / "tuya_ble_mesh"
        / "lib"
    ),
)

from tuya_ble_mesh.exceptions import MeshConnectionError, SIGMeshError
from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

_MAC = "DC:23:4D:21:43:A5"


def _secrets(**kw: str) -> MagicMock:
    """Mock SecretsManager returning 16-byte hex keys."""
    s = MagicMock()
    defaults = {
        "s17-net-key": "f7a2a44f8e8a8029064f173ddc1e2b00",  # pragma: allowlist secret
        "s17-dev-key-00aa": "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
        "s17-app-key": "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
    }
    defaults.update(kw)

    def _get(item: str, field: str = "password") -> str:  # pragma: allowlist secret
        return defaults.get(item, "00" * 16)

    s.get = AsyncMock(side_effect=_get)
    return s


def _dev(**kwargs: object) -> SIGMeshDevice:
    return SIGMeshDevice(_MAC, 0x00AA, 0x0001, _secrets(), **kwargs)


def _mock_client(*, start_notify_side_effect: object = None) -> MagicMock:
    """Build a mock BleakClient for use in connect tests."""
    c = MagicMock()
    c.connect = AsyncMock()
    c.disconnect = AsyncMock()
    c.stop_notify = AsyncMock()
    c.write_gatt_char = AsyncMock()
    c.is_connected = True
    c.mtu_size = 23
    if start_notify_side_effect is None:
        c.start_notify = AsyncMock()
    else:
        c.start_notify = AsyncMock(side_effect=start_notify_side_effect)
    return c


# ── rssi property ─────────────────────────────────────────────────────────────


class TestRssi:
    """Lines 230-232: rssi returns None when not connected, int when connected."""

    def test_rssi_none_when_not_connected(self) -> None:
        dev = _dev()
        assert dev.rssi is None

    def test_rssi_from_client_when_connected(self) -> None:
        dev = _dev()
        client = MagicMock()
        client.rssi = -55
        dev._client = client
        assert dev.rssi == -55

    def test_rssi_none_when_client_lacks_attribute(self) -> None:
        dev = _dev()
        dev._client = MagicMock(spec=[])  # No rssi attr
        assert dev.rssi is None


# ── connect edge cases ────────────────────────────────────────────────────────


class TestConnectEdgeCases:
    """Lines 310, 314, 324-325, 332, 339-340, 354-355, 361-374."""

    @pytest.mark.asyncio
    async def test_connect_uses_ble_device_callback(self) -> None:
        """Line 310: ble_device_callback is called instead of BleakScanner."""
        mock_device = MagicMock()
        cb = MagicMock(return_value=mock_device)
        dev = SIGMeshDevice(_MAC, 0x00AA, 0x0001, _secrets(), ble_device_callback=cb)
        client = _mock_client()

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakClient", return_value=client),
            patch.object(dev, "request_composition_data", new_callable=AsyncMock),
        ):
            await dev.connect(max_retries=1)

        cb.assert_called_once_with(_MAC)
        assert dev.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_with_adapter_forwards_to_scanner_and_client(self) -> None:
        """Lines 314, 332: adapter kwarg passed to BleakScanner and BleakClient."""
        dev = _dev(adapter="hci1")
        client = _mock_client()

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakScanner") as mock_scanner,
            patch("tuya_ble_mesh.sig_mesh_device.BleakClient", return_value=client) as mock_cls,
            patch.object(dev, "request_composition_data", new_callable=AsyncMock),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=MagicMock())
            await dev.connect(max_retries=1)

        assert mock_scanner.find_device_by_address.call_args.kwargs.get("adapter") == "hci1"
        assert mock_cls.call_args.kwargs.get("adapter") == "hci1"

    @pytest.mark.asyncio
    async def test_connect_device_not_found_raises(self) -> None:
        """Lines 324-325: BleakScanner returns None → MeshConnectionError."""
        dev = _dev()

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakScanner") as mock_scanner,
            patch.object(dev, "_bluetoothctl_remove", new_callable=AsyncMock),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(MeshConnectionError, match="Failed to connect"),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=None)
            await dev.connect(max_retries=1)

    @pytest.mark.asyncio
    async def test_connect_notify_subscription_failure_continues(self) -> None:
        """Lines 339-340: start_notify raises BleakError → warning, connect succeeds."""
        dev = _dev()
        client = _mock_client(start_notify_side_effect=BleakError("DBUS error"))

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakScanner") as mock_scanner,
            patch("tuya_ble_mesh.sig_mesh_device.BleakClient", return_value=client),
            patch.object(dev, "request_composition_data", new_callable=AsyncMock),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=MagicMock())
            await dev.connect(max_retries=1)

        assert dev.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_composition_data_failure_continues(self) -> None:
        """Lines 354-355: request_composition_data raises → debug logged, connect succeeds."""
        dev = _dev()
        client = _mock_client()

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakScanner") as mock_scanner,
            patch("tuya_ble_mesh.sig_mesh_device.BleakClient", return_value=client),
            patch.object(
                dev,
                "request_composition_data",
                new_callable=AsyncMock,
                side_effect=TimeoutError(),
            ),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=MagicMock())
            await dev.connect(max_retries=1)

        assert dev.is_connected is True

    @pytest.mark.asyncio
    async def test_connect_bleak_error_retried_then_raises(self) -> None:
        """Lines 361-374: BleakError caught, retried, final MeshConnectionError raised."""
        dev = _dev()
        client = _mock_client()
        client.connect = AsyncMock(side_effect=BleakError("connection failed"))

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakScanner") as mock_scanner,
            patch("tuya_ble_mesh.sig_mesh_device.BleakClient", return_value=client),
            patch.object(dev, "_bluetoothctl_remove", new_callable=AsyncMock),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(MeshConnectionError, match="Failed to connect"),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=MagicMock())
            await dev.connect(max_retries=2)

        assert client.connect.call_count == 2


# ── disconnect edge cases ─────────────────────────────────────────────────────


class TestDisconnectEdgeCases:
    """Lines 392-394, 399, 401: disconnect error handling."""

    @pytest.mark.asyncio
    async def test_disconnect_keys_zero_fill_exception_swallowed(self) -> None:
        """Lines 392-394: AttributeError during key zeroing is swallowed."""
        dev = _dev()
        # Set up a fake _keys object whose attributes raise AttributeError
        fake_keys = MagicMock()
        fake_keys.net_key = MagicMock()  # Not a bytearray → isinstance check fails
        fake_keys.dev_key = bytearray(b"\x01" * 16)
        dev._keys = fake_keys
        dev._client = None  # Already disconnected

        await dev.disconnect()

        assert dev._keys is None

    @pytest.mark.asyncio
    async def test_disconnect_cancels_pending_notify_tasks(self) -> None:
        """Lines 399, 401: pending notify tasks are cancelled and awaited."""
        dev = _dev()
        dev._client = None

        # Add a pending task that yields once then returns
        async def noop() -> None:
            await asyncio.sleep(999)

        task = asyncio.create_task(noop())
        dev._pending_notify_tasks.add(task)

        await dev.disconnect()

        assert task.cancelled() or task.done()
        assert len(dev._pending_notify_tasks) == 0


# ── _next_seq / _next_seqs exhausted ─────────────────────────────────────────


class TestSequenceExhaustion:
    """Lines 420-421, 443-444: SIGMeshError on seq > 0xFFFFFF."""

    @pytest.mark.asyncio
    async def test_next_seq_exhausted(self) -> None:
        """Lines 420-421: seq > 0xFFFFFF → SIGMeshError."""
        dev = _dev()
        dev._seq_store.set_seq(0x1000000)  # One past max
        with pytest.raises(SIGMeshError, match="exhausted"):
            await dev._next_seq()

    @pytest.mark.asyncio
    async def test_next_seqs_exhausted_when_sum_overflows(self) -> None:
        """Lines 443-444: seq + n > 0xFFFFFF → SIGMeshError."""
        dev = _dev()
        dev._seq_store.set_seq(0xFFFFFF)  # Exactly at max
        with pytest.raises(SIGMeshError, match="exhausted"):
            await dev._next_seqs(2)  # 0xFFFFFF + 2 > 0xFFFFFF


# ── _bluetoothctl_remove ──────────────────────────────────────────────────────


class TestBluetooth:
    """Lines 487-497: _bluetoothctl_remove swallows TimeoutError and OSError."""

    @pytest.mark.asyncio
    async def test_bluetoothctl_remove_success(self) -> None:
        """Lines 487-495: subprocess launched, wait() called."""
        dev = _dev()
        mock_proc = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            await dev._bluetoothctl_remove()

        mock_proc.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_bluetoothctl_remove_oserror_swallowed(self) -> None:
        """Lines 496-497: OSError from create_subprocess_exec is swallowed."""
        dev = _dev()
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("not found"),
        ):
            await dev._bluetoothctl_remove()

    @pytest.mark.asyncio
    async def test_bluetoothctl_remove_timeout_swallowed(self) -> None:
        """Lines 496-497: TimeoutError from wait_for is swallowed."""
        dev = _dev()
        mock_proc = MagicMock()
        mock_proc.wait = AsyncMock(side_effect=TimeoutError())

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            await dev._bluetoothctl_remove()
