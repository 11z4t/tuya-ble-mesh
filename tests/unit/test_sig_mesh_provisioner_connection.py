"""Unit tests for sig_mesh_provisioner_connection.py — uncovered paths.

Targets lines missed by test_sig_mesh_provisioner.py:
  _cleanup_stale_connections: 96-114, 117-118
  _connect: 166, 206, 215, 232, 261-263, 285-287
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "custom_components"
        / "tuya_ble_mesh"
        / "lib"
    ),
)

from tuya_ble_mesh.exceptions import ProvisioningError
from tuya_ble_mesh.sig_mesh_provisioner import SIGMeshProvisioner

_NET_KEY = b"\x00" * 16
_APP_KEY = b"\x01" * 16
_ADDR = 0x00B0
_MAC = "AA:BB:CC:DD:EE:FF"
_PROV_SERVICE = "00001827-0000-1000-8000-00805f9b34fb"


def _prov(**kwargs: object) -> SIGMeshProvisioner:
    return SIGMeshProvisioner(_NET_KEY, _APP_KEY, _ADDR, **kwargs)


def _mock_proc(*, returncode: int = 0, stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


def _connected_client(*, has_get_services: bool = True) -> MagicMock:
    """Build a mock BleakClient that appears connected with PROV_SERVICE."""
    svc = MagicMock()
    svc.uuid = _PROV_SERVICE
    if has_get_services:
        client = MagicMock()
        client.is_connected = True
        client.mtu_size = 23
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.get_services = AsyncMock(return_value=[svc])
    else:
        # Restrict spec so hasattr(client, "get_services") returns False
        client = MagicMock(spec=["is_connected", "mtu_size", "connect", "disconnect", "services"])
        client.is_connected = True
        client.mtu_size = 23
        client.connect = AsyncMock()
        client.disconnect = AsyncMock()
        client.services = [svc]
    return client


# ── _cleanup_stale_connections ────────────────────────────────────────────────


class TestCleanupStaleConnections:
    """Cover lines 96-114 and 117-118 in _cleanup_stale_connections."""

    @pytest.mark.asyncio
    async def test_subprocess_returncode_zero(self) -> None:
        """Lines 96-101, 114: subprocess exits 0 → success log + sleep."""
        prov = _prov()
        proc = _mock_proc(returncode=0)
        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await prov._cleanup_stale_connections("aa:bb:cc:dd:ee:ff")
        proc.communicate.assert_called_once()

    @pytest.mark.asyncio
    async def test_subprocess_returncode_nonzero(self) -> None:
        """Lines 102-108, 114: subprocess exits 1 → debug log + sleep."""
        prov = _prov()
        proc = _mock_proc(returncode=1, stderr=b"No such device in device list")
        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await prov._cleanup_stale_connections("aa:bb:cc:dd:ee:ff")
        proc.communicate.assert_called_once()

    @pytest.mark.asyncio
    async def test_subprocess_communicate_timeout(self) -> None:
        """Lines 109-112, 114: communicate() raises TimeoutError → kill + wait."""
        prov = _prov()
        proc = _mock_proc()
        proc.communicate = AsyncMock(side_effect=TimeoutError())
        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await prov._cleanup_stale_connections("aa:bb:cc:dd:ee:ff")
        proc.kill.assert_called_once()
        proc.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_outer_oserror_swallowed(self) -> None:
        """Lines 117-118: create_subprocess_exec raises OSError → swallowed."""
        prov = _prov()
        with (
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=OSError("Permission denied"),
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            # Must not raise
            await prov._cleanup_stale_connections("aa:bb:cc:dd:ee:ff")


# ── _connect edge cases ───────────────────────────────────────────────────────


class TestConnectAdapterPaths:
    """Cover lines 166 and 206: adapter kwarg forwarded to scanner and client."""

    @pytest.mark.asyncio
    async def test_scan_kwargs_include_adapter(self) -> None:
        """Line 166: _adapter set → BleakScanner receives adapter kwarg."""
        prov = _prov(adapter="hci1")
        mock_device = MagicMock()
        mock_client = _connected_client()

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
                return_value=mock_device,
            ) as mock_scan,
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
                return_value=mock_client,
            ),
        ):
            client = await prov._connect(_MAC, timeout=5.0, max_retries=1)

        assert client is mock_client
        assert mock_scan.call_args.kwargs.get("adapter") == "hci1"

    @pytest.mark.asyncio
    async def test_bleakclient_kwargs_include_adapter(self) -> None:
        """Line 206: _adapter set, no ble_connect_callback → BleakClient(adapter=...)."""
        prov = _prov(adapter="hci1")
        mock_device = MagicMock()
        mock_client = _connected_client()

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
                return_value=mock_device,
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
                return_value=mock_client,
            ) as mock_cls,
        ):
            client = await prov._connect(_MAC, timeout=5.0, max_retries=1)

        assert client is mock_client
        assert mock_cls.call_args.kwargs.get("adapter") == "hci1"


class TestConnectCallbackNone:
    """Line 215: ble_connect_callback returning None → ProvisioningError."""

    @pytest.mark.asyncio
    async def test_connect_callback_returns_none(self) -> None:
        mock_device = MagicMock()
        prov = _prov(
            ble_device_callback=lambda _: mock_device,
            ble_connect_callback=AsyncMock(return_value=None),
        )
        with pytest.raises(ProvisioningError, match="returned None client"):
            await prov._connect(_MAC, timeout=5.0, max_retries=1)


class TestConnectClientServicesFallback:
    """Line 232: client without get_services → falls back to client.services."""

    @pytest.mark.asyncio
    async def test_client_services_property_used(self) -> None:
        prov = _prov()
        mock_device = MagicMock()
        mock_client = _connected_client(has_get_services=False)

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
                return_value=mock_device,
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
                return_value=mock_client,
            ),
        ):
            client = await prov._connect(_MAC, timeout=5.0, max_retries=1)

        assert client is mock_client


class TestConnectClientDisconnectOnError:
    """Lines 261-263 and 285-287: client disconnected when connect error occurs."""

    @pytest.mark.asyncio
    async def test_timeout_disconnects_existing_client(self) -> None:
        """Lines 261-263: TimeoutError during connect.connect() → client.disconnect()."""
        prov = _prov()
        mock_device = MagicMock()
        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=TimeoutError())
        mock_client.disconnect = AsyncMock()

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
                return_value=mock_device,
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
                return_value=mock_client,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ProvisioningError, match="Failed to connect"),
        ):
            await prov._connect(_MAC, timeout=5.0, max_retries=1)

        mock_client.disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_oserror_disconnects_existing_client(self) -> None:
        """Lines 285-287: OSError during connect.connect() → client.disconnect()."""
        prov = _prov()
        mock_device = MagicMock()
        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=OSError("Connection reset by peer"))
        mock_client.disconnect = AsyncMock()

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
                return_value=mock_device,
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
                return_value=mock_client,
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ProvisioningError, match="Failed to connect"),
        ):
            await prov._connect(_MAC, timeout=5.0, max_retries=1)

        mock_client.disconnect.assert_called()
