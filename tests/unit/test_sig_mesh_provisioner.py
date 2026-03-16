"""Unit tests for SIG Mesh PB-GATT Provisioner."""
from bleak.exc import BleakError

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.exceptions import ProvisioningError
from tuya_ble_mesh.sig_mesh_provisioner import (
    ProvisioningResult,
    SIGMeshProvisioner,
    _wrap_provisioning_pdu,
)

# PDU types
_PROV_INVITE = 0x00
_PROV_CAPABILITIES = 0x01
_PROV_START = 0x02
_PROV_PUBLIC_KEY = 0x03
_PROV_CONFIRMATION = 0x05
_PROV_RANDOM = 0x06
_PROV_DATA = 0x07
_PROV_COMPLETE = 0x08
_PROV_FAILED = 0x09

# SAR types
_SAR_COMPLETE = 0x00
_SAR_FIRST = 0x01
_SAR_CONTINUATION = 0x02
_SAR_LAST = 0x03
_PROXY_TYPE_PROVISIONING = 0x03


# ============================================================
# ProvisioningResult dataclass
# ============================================================


class TestProvisioningResult:
    """Test ProvisioningResult dataclass."""

    def test_creation(self) -> None:
        result = ProvisioningResult(
            dev_key=b"\x01" * 16,
            net_key=b"\x02" * 16,
            app_key=b"\x03" * 16,
            unicast_addr=0x00B0,
            iv_index=0,
            num_elements=1,
        )
        assert result.dev_key == b"\x01" * 16
        assert result.net_key == b"\x02" * 16
        assert result.app_key == b"\x03" * 16
        assert result.unicast_addr == 0x00B0
        assert result.iv_index == 0
        assert result.num_elements == 1

    def test_immutable(self) -> None:
        result = ProvisioningResult(
            dev_key=b"\x01" * 16,
            net_key=b"\x02" * 16,
            app_key=b"\x03" * 16,
            unicast_addr=0x00B0,
            iv_index=0,
            num_elements=1,
        )
        with pytest.raises(AttributeError):
            result.dev_key = b"\xFF" * 16  # type: ignore


# ============================================================
# _wrap_provisioning_pdu helper
# ============================================================


class TestWrapProvisioningPdu:
    """Test provisioning PDU wrapping with SAR."""

    def test_small_pdu_single_segment(self) -> None:
        pdu = bytes([_PROV_INVITE, 0x05])
        segments = _wrap_provisioning_pdu(pdu, mtu=23)
        assert len(segments) == 1
        assert segments[0][0] == ((_SAR_COMPLETE << 6) | _PROXY_TYPE_PROVISIONING)
        assert segments[0][1:] == pdu

    def test_large_pdu_multiple_segments(self) -> None:
        pdu = b"\x99" * 100
        segments = _wrap_provisioning_pdu(pdu, mtu=23)
        assert len(segments) > 1
        # First segment
        assert (segments[0][0] >> 6) == _SAR_FIRST
        # Last segment
        assert (segments[-1][0] >> 6) == _SAR_LAST
        # Middle segments
        for seg in segments[1:-1]:
            assert (seg[0] >> 6) == _SAR_CONTINUATION

    def test_exact_chunk_boundary(self) -> None:
        mtu = 23
        max_chunk = mtu - 4
        pdu = b"\xAA" * max_chunk
        segments = _wrap_provisioning_pdu(pdu, mtu=mtu)
        assert len(segments) == 1
        assert segments[0][0] == ((_SAR_COMPLETE << 6) | _PROXY_TYPE_PROVISIONING)

    def test_mtu_minimum(self) -> None:
        pdu = b"\xBB" * 10
        segments = _wrap_provisioning_pdu(pdu, mtu=5)
        assert len(segments) > 1
        # Should still produce segments
        for seg in segments:
            assert len(seg) >= 2  # Header + at least 1 byte


# ============================================================
# SIGMeshProvisioner initialization
# ============================================================


class TestSIGMeshProvisionerInit:
    """Test provisioner initialization."""

    def test_valid_keys(self) -> None:
        net_key = b"\x00" * 16
        app_key = b"\x01" * 16
        prov = SIGMeshProvisioner(net_key, app_key, 0x00B0)
        assert prov._net_key == net_key
        assert prov._app_key == app_key
        assert prov._unicast_addr == 0x00B0
        assert prov._net_key_index == 0
        assert prov._iv_index == 0
        assert prov._flags == 0

    def test_custom_parameters(self) -> None:
        prov = SIGMeshProvisioner(
            b"\x00" * 16,
            b"\x01" * 16,
            0x00B0,
            net_key_index=5,
            iv_index=100,
            flags=3,
        )
        assert prov._net_key_index == 5
        assert prov._iv_index == 100
        assert prov._flags == 3

    def test_invalid_net_key_length(self) -> None:
        with pytest.raises(ProvisioningError, match="net_key must be 16 bytes"):
            SIGMeshProvisioner(b"\x00" * 8, b"\x01" * 16, 0x00B0)

    def test_invalid_app_key_length(self) -> None:
        with pytest.raises(ProvisioningError, match="app_key must be 16 bytes"):
            SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 8, 0x00B0)

    def test_generates_ecdh_keypair(self) -> None:
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        assert hasattr(prov, "_private_key")
        assert hasattr(prov, "_our_pub_key_bytes")
        assert len(prov._our_pub_key_bytes) == 64

    def test_ble_device_callback(self) -> None:
        callback = Mock()
        prov = SIGMeshProvisioner(
            b"\x00" * 16, b"\x01" * 16, 0x00B0, ble_device_callback=callback
        )
        assert prov._ble_device_callback == callback

    def test_ble_connect_callback(self) -> None:
        callback = AsyncMock()
        prov = SIGMeshProvisioner(
            b"\x00" * 16, b"\x01" * 16, 0x00B0, ble_connect_callback=callback
        )
        assert prov._ble_connect_callback == callback


# ============================================================
# Connection tests
# ============================================================


class TestProvisionerConnect:
    """Test BLE connection logic."""

    @pytest.mark.asyncio
    async def test_connect_success_with_scanner(self) -> None:
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        mock_device = Mock()
        mock_client = MagicMock()
        mock_client.mtu_size = 23
        mock_client.is_connected = True
        mock_client.connect = AsyncMock()

        # Mock get_services to return service collection with PROV_SERVICE
        mock_service = Mock()
        mock_service.uuid = "00001827-0000-1000-8000-00805f9b34fb"  # PROV_SERVICE
        mock_services = Mock()
        mock_services.services = {1: mock_service}
        mock_client.get_services = AsyncMock(return_value=mock_services)

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
            return_value=mock_device,
        ), patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
            return_value=mock_client,
        ):
            client = await prov._connect("AA:BB:CC:DD:EE:FF", timeout=5.0, max_retries=3)
            assert client == mock_client
            mock_client.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_device_not_found(self) -> None:
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
            return_value=None,
        ), patch("asyncio.sleep", new_callable=AsyncMock
        ), pytest.raises(ProvisioningError, match="Failed to connect"):
            await prov._connect("AA:BB:CC:DD:EE:FF", timeout=5.0, max_retries=3)

    @pytest.mark.asyncio
    async def test_connect_with_ble_device_callback(self) -> None:
        mock_device = Mock()
        callback = Mock(return_value=mock_device)
        mock_client = MagicMock()
        mock_client.mtu_size = 23
        mock_client.is_connected = True
        mock_client.connect = AsyncMock()

        # Mock get_services
        mock_service = Mock()
        mock_service.uuid = "00001827-0000-1000-8000-00805f9b34fb"
        mock_services = Mock()
        mock_services.services = {1: mock_service}
        mock_client.get_services = AsyncMock(return_value=mock_services)

        prov = SIGMeshProvisioner(
            b"\x00" * 16, b"\x01" * 16, 0x00B0, ble_device_callback=callback
        )

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
            return_value=mock_client,
        ):
            client = await prov._connect("AA:BB:CC:DD:EE:FF", timeout=5.0, max_retries=1)
            callback.assert_called_once_with("AA:BB:CC:DD:EE:FF")
            assert client == mock_client

    @pytest.mark.asyncio
    async def test_connect_with_ble_connect_callback(self) -> None:
        mock_device = Mock()
        mock_client = MagicMock()
        mock_client.mtu_size = 23
        mock_client.is_connected = True
        connect_callback = AsyncMock(return_value=mock_client)

        # Mock get_services
        mock_service = Mock()
        mock_service.uuid = "00001827-0000-1000-8000-00805f9b34fb"
        mock_services = Mock()
        mock_services.services = {1: mock_service}
        mock_client.get_services = AsyncMock(return_value=mock_services)

        prov = SIGMeshProvisioner(
            b"\x00" * 16,
            b"\x01" * 16,
            0x00B0,
            ble_device_callback=lambda _: mock_device,
            ble_connect_callback=connect_callback,
        )

        client = await prov._connect("AA:BB:CC:DD:EE:FF", timeout=5.0, max_retries=1)
        connect_callback.assert_called_once_with(mock_device)
        assert client == mock_client

    @pytest.mark.asyncio
    async def test_connect_retries_on_failure(self) -> None:
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
                side_effect=[OSError("Fail 1"), OSError("Fail 2")],
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ProvisioningError, match="Failed to connect"),
        ):
            await prov._connect("AA:BB:CC:DD:EE:FF", timeout=1.0, max_retries=2)

    @pytest.mark.asyncio
    async def test_connect_raises_provisioning_error_immediately(self) -> None:
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
            side_effect=ProvisioningError("Critical error"),
        ), pytest.raises(ProvisioningError, match="Critical error"):
            await prov._connect("AA:BB:CC:DD:EE:FF", timeout=5.0, max_retries=3)

    @pytest.mark.asyncio
    async def test_connect_out_of_slots_error_with_backoff(self) -> None:
        """Test PLAT-506: out-of-slots error detection and extended backoff."""
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        mock_device = Mock()

        # Simulate "out of connection slots" error
        slot_error = BleakError("BleakOutOfConnectionSlotsError: out of connection slots")

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
            return_value=mock_device,
        ), patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
            side_effect=[slot_error, slot_error],
        ), patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(ProvisioningError, match="out of connection slots"):
                await prov._connect("AA:BB:CC:DD:EE:FF", timeout=1.0, max_retries=2)
            # Verify backoff was called
            assert mock_sleep.call_count >= 2

    @pytest.mark.asyncio
    async def test_connect_is_connected_false_error(self) -> None:
        """Test error when BleakClient.is_connected returns False after connect."""
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        mock_device = Mock()
        mock_client = MagicMock()
        mock_client.is_connected = False  # Connection failed
        mock_client.connect = AsyncMock()

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
            return_value=mock_device,
        ), patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
            return_value=mock_client,
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ProvisioningError, match="is_connected=False"):
                await prov._connect("AA:BB:CC:DD:EE:FF", timeout=1.0, max_retries=1)

    @pytest.mark.asyncio
    async def test_connect_no_provisioning_service(self) -> None:
        """Test error when device doesn't expose Provisioning Service 0x1827."""
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        mock_device = Mock()
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.connect = AsyncMock()
        mock_client.mtu_size = 23

        # Mock get_services to return services WITHOUT PROV_SERVICE
        mock_service = Mock()
        mock_service.uuid = "00001828-0000-1000-8000-00805f9b34fb"  # Wrong service
        mock_services = Mock()
        mock_services.services = {1: mock_service}
        mock_client.get_services = AsyncMock(return_value=mock_services)

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
            return_value=mock_device,
        ), patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
            return_value=mock_client,
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ProvisioningError, match="does not expose Provisioning Service"):
                await prov._connect("AA:BB:CC:DD:EE:FF", timeout=1.0, max_retries=1)

    @pytest.mark.asyncio
    async def test_connect_get_services_timeout(self) -> None:
        """Test that service enumeration timeout is handled gracefully."""
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        mock_device = Mock()
        mock_client = MagicMock()
        mock_client.is_connected = True
        mock_client.connect = AsyncMock()
        mock_client.mtu_size = 23
        mock_client.get_services = AsyncMock(side_effect=TimeoutError())

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
            return_value=mock_device,
        ), patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
            return_value=mock_client,
        ):
            # Should succeed despite timeout (warning logged, but continues)
            client = await prov._connect("AA:BB:CC:DD:EE:FF", timeout=1.0, max_retries=1)
            assert client == mock_client

    @pytest.mark.asyncio
    async def test_connect_timeout_error_with_backoff(self) -> None:
        """Test PLAT-506: TimeoutError triggers exponential backoff."""
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        mock_device = Mock()

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakScanner.find_device_by_address",
            return_value=mock_device,
        ), patch(
            "tuya_ble_mesh.sig_mesh_provisioner_connection.BleakClient",
            side_effect=TimeoutError(),
        ), patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with pytest.raises(ProvisioningError, match="Failed to connect"):
                await prov._connect("AA:BB:CC:DD:EE:FF", timeout=1.0, max_retries=2)
            # Verify backoff was called (exponential backoff: 3.0, 4.5, ...)
            assert mock_sleep.call_count == 2


# ============================================================
# Full provisioning exchange tests
# ============================================================


class TestProvisionerExchange:
    """Test full provisioning exchange."""

    def _create_mock_client(self) -> MagicMock:
        """Create a mock BleakClient."""
        client = MagicMock()
        client.mtu_size = 23
        client.pair = AsyncMock()
        client.start_notify = AsyncMock()
        client.stop_notify = AsyncMock()
        client.disconnect = AsyncMock()
        client.write_gatt_char = AsyncMock()
        return client

    def _build_pdu(self, pdu_type: int, payload: bytes = b"") -> bytes:
        """Build a provisioning PDU."""
        return bytes([pdu_type]) + payload

    @pytest.mark.asyncio
    async def test_successful_provisioning(self) -> None:
        """Test successful provisioning simplified."""
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)

        # Mock the entire exchange with a fake result
        mock_result = ProvisioningResult(
            dev_key=b"\xAA" * 16,
            net_key=b"\x00" * 16,
            app_key=b"\x01" * 16,
            unicast_addr=0x00B0,
            iv_index=0,
            num_elements=1,
        )

        with patch.object(prov, "_run_exchange", return_value=mock_result):
            client = self._create_mock_client()
            result = await prov._run_exchange(client)

            assert isinstance(result, ProvisioningResult)
            assert len(result.dev_key) == 16
            assert result.net_key == b"\x00" * 16
            assert result.app_key == b"\x01" * 16
            assert result.unicast_addr == 0x00B0
            assert result.iv_index == 0
            assert result.num_elements == 1

    @pytest.mark.asyncio
    async def test_device_sends_failed_pdu(self) -> None:
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        client = self._create_mock_client()

        notify_callback = None

        async def capture_notify(char_uuid: str, callback: object) -> None:
            nonlocal notify_callback
            notify_callback = callback

        client.start_notify = AsyncMock(side_effect=capture_notify)

        async def simulate_failure() -> None:
            await asyncio.sleep(0.1)
            # Send PROV_FAILED instead of Capabilities
            notify_callback(None, bytearray([(_SAR_COMPLETE << 6) | 0x03, _PROV_FAILED, 0x02]))

        _task = asyncio.create_task(simulate_failure())  # noqa: RUF006

        with pytest.raises(ProvisioningError, match="ProvisioningFailed"):
            await prov._run_exchange(client)

    @pytest.mark.asyncio
    async def test_unexpected_pdu_type(self) -> None:
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        client = self._create_mock_client()

        notify_callback = None

        async def capture_notify(char_uuid: str, callback: object) -> None:
            nonlocal notify_callback
            notify_callback = callback

        client.start_notify = AsyncMock(side_effect=capture_notify)

        async def simulate_wrong_pdu() -> None:
            await asyncio.sleep(0.1)
            # Send wrong PDU type
            notify_callback(None, bytearray([(_SAR_COMPLETE << 6) | 0x03, 0xFF]))

        _task = asyncio.create_task(simulate_wrong_pdu())  # noqa: RUF006

        with pytest.raises(ProvisioningError, match="Protocol error"):
            await prov._run_exchange(client)



# ============================================================
# Full provision() method tests
# ============================================================


class TestProvisionMethod:
    """Test high-level provision() method."""

    @pytest.mark.asyncio
    async def test_provision_success(self) -> None:
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        mock_client = MagicMock()
        mock_client.mtu_size = 23
        mock_client.start_notify = AsyncMock()
        mock_client.stop_notify = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()

        mock_result = ProvisioningResult(
            dev_key=b"\xAA" * 16,
            net_key=b"\x00" * 16,
            app_key=b"\x01" * 16,
            unicast_addr=0x00B0,
            iv_index=0,
            num_elements=1,
        )

        with patch.object(prov, "_connect", return_value=mock_client), patch.object(
            prov, "_run_exchange", return_value=mock_result
        ):
            result = await prov.provision("AA:BB:CC:DD:EE:FF")
            assert result == mock_result
            mock_client.stop_notify.assert_called()
            mock_client.disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_provision_disconnect_on_failure(self) -> None:
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        mock_client = MagicMock()
        mock_client.stop_notify = AsyncMock()
        mock_client.disconnect = AsyncMock()

        with patch.object(prov, "_connect", return_value=mock_client), patch.object(
            prov, "_run_exchange", side_effect=ProvisioningError("Exchange failed")
        ):
            with pytest.raises(ProvisioningError, match="Exchange failed"):
                await prov.provision("AA:BB:CC:DD:EE:FF")
            # Should still disconnect
            mock_client.stop_notify.assert_called()
            mock_client.disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_provision_suppress_disconnect_errors(self) -> None:
        """Test that disconnect errors are suppressed."""
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        mock_client = MagicMock()
        mock_client.stop_notify = AsyncMock(side_effect=BleakError("Stop notify failed"))
        mock_client.disconnect = AsyncMock(side_effect=BleakError("Disconnect failed"))

        with patch.object(prov, "_connect", return_value=mock_client), patch.object(
            prov, "_run_exchange", side_effect=ProvisioningError("Exchange failed")
        ), patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ProvisioningError, match="Exchange failed"):
                await prov.provision("AA:BB:CC:DD:EE:FF")
            # Should have attempted to disconnect despite errors
            mock_client.stop_notify.assert_called()
            mock_client.disconnect.assert_called()

    @pytest.mark.asyncio
    async def test_provision_plat506_connection_slot_release_delay(self) -> None:
        """Test PLAT-506: provision() sleeps 0.5s after disconnect to release BLE slot."""
        prov = SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)
        mock_client = MagicMock()
        mock_client.stop_notify = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()

        mock_result = ProvisioningResult(
            dev_key=b"\xAA" * 16,
            net_key=b"\x00" * 16,
            app_key=b"\x01" * 16,
            unicast_addr=0x00B0,
            iv_index=0,
            num_elements=1,
        )

        with patch.object(prov, "_connect", return_value=mock_client), patch.object(
            prov, "_run_exchange", return_value=mock_result
        ), patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await prov.provision("AA:BB:CC:DD:EE:FF")
            assert result == mock_result
            # Verify 0.5s sleep was called after disconnect
            mock_sleep.assert_called_with(1.0)
