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
    _REASSEMBLY_TIMEOUT,
    SIGMeshDevice,
)

# _INITIAL_SEQ was removed in PLAT-402 (seq now starts at 0, not 2000)
_INITIAL_SEQ = 0


def make_mock_secrets() -> MagicMock:
    """Create a mock SecretsManager that returns valid hex keys."""
    secrets = MagicMock()
    # 16-byte keys as hex strings (32 chars)
    secrets.get = AsyncMock(
        side_effect=lambda item, field="password": {  # pragma: allowlist secret
            "s17-net-key": "f7a2a44f8e8a8029064f173ddc1e2b00",  # pragma: allowlist secret
            "s17-dev-key-00aa": "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
            "s17-app-key": "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
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

        # Reset mock after connect (which sends Composition Data Get)
        mock_client.write_gatt_char.reset_mock()

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
        assert dev.get_seq() == _INITIAL_SEQ

    @pytest.mark.asyncio
    async def test_next_seq_increments(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        s1 = await dev._next_seq()
        s2 = await dev._next_seq()
        assert s2 == s1 + 1

    def test_set_seq(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        dev.set_seq(5000)
        assert dev.get_seq() == 5000

    def test_get_seq(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        assert dev.get_seq() == _INITIAL_SEQ

    @pytest.mark.asyncio
    async def test_set_seq_persists_through_next_seq(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        dev.set_seq(9000)
        seq = await dev._next_seq()
        assert seq == 9000
        assert dev.get_seq() == 9001


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


class TestSegmentReassembly:
    """Test segmented message reassembly in SIGMeshDevice."""

    def _make_device_with_keys(self) -> SIGMeshDevice:
        """Create a SIGMeshDevice with mock keys loaded."""
        from tuya_ble_mesh.sig_mesh_protocol import MeshKeys

        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        dev._keys = MeshKeys(
            "f7a2a44f8e8a8029064f173ddc1e2b00",  # pragma: allowlist secret
            "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
            "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
        )
        return dev

    @pytest.mark.asyncio
    async def test_handle_segment_collects_segments(self) -> None:
        """_handle_segment should collect segments into buffer."""
        import struct

        dev = self._make_device_with_keys()
        # Create a fake segment: SEG=1, AKF=0, AID=0
        hdr = 0x80
        info = (0 << 23) | (100 << 10) | (0 << 5) | 1  # seg_o=0, seg_n=1
        pdu = bytes([hdr]) + struct.pack(">I", info)[1:] + b"\x42" * 12

        await dev._handle_segment(0x00AA, 0x0001, pdu)

        assert (0x00AA, 0x0001, 100, 0) in dev._segment_buffers
        buf = dev._segment_buffers[(0x00AA, 0x0001, 100, 0)]
        assert 0 in buf.segments
        assert buf.seg_n == 1

    def test_handle_segment_completes_on_last(self) -> None:
        """When all segments arrive, buffer should be consumed."""
        from tuya_ble_mesh.sig_mesh_protocol import make_access_segmented

        dev = self._make_device_with_keys()
        assert dev._keys is not None

        # Create a real segmented message
        access_payload = b"\x82\x04\x01"  # OnOff Status ON
        segments = make_access_segmented(
            dev._keys.dev_key,
            0x00AA,
            0x0001,
            100,
            0,
            access_payload + b"\x00" * 10,  # pad to force 2 segments
        )

        # Feed each segment through _handle_segment
        for _seq, transport_pdu in segments:
            dev._handle_segment(0x00AA, 0x0001, transport_pdu)

        # Buffer should be consumed after complete reassembly
        assert (0x00AA, 100 & 0x1FFF) not in dev._segment_buffers

    @pytest.mark.asyncio
    async def test_stale_buffer_cleanup(self) -> None:
        """Stale buffers should be removed by _clean_stale_buffers."""
        import struct
        import time

        dev = self._make_device_with_keys()

        # Insert a fake buffer with old timestamp
        hdr = 0x80
        info = (0 << 23) | (200 << 10) | (0 << 5) | 1
        pdu = bytes([hdr]) + struct.pack(">I", info)[1:] + b"\x42" * 12
        await dev._handle_segment(0x00AA, 0x0001, pdu)

        # Make the buffer look stale
        buf = dev._segment_buffers[(0x00AA, 0x0001, 200, 0)]
        buf.created_at = time.monotonic() - _REASSEMBLY_TIMEOUT - 1.0

        # Trigger cleanup with another segment
        info2 = (0 << 23) | (300 << 10) | (0 << 5) | 0
        pdu2 = bytes([hdr]) + struct.pack(">I", info2)[1:] + b"\x42" * 8
        await dev._handle_segment(0x00BB, 0x0001, pdu2)

        # Stale buffer should be gone
        assert (0x00AA, 0x0001, 200, 0) not in dev._segment_buffers

    @pytest.mark.asyncio
    async def test_dispatch_access_payload_onoff(self) -> None:
        """_dispatch_access_payload should invoke onoff callbacks."""
        dev = self._make_device_with_keys()
        cb = MagicMock()
        dev.register_onoff_callback(cb)

        # OnOff Status: ON
        await dev._dispatch_access_payload(0x00AA, b"\x82\x04\x01")

        cb.assert_called_once_with(True)

    def test_dispatch_access_payload_unknown_opcode(self) -> None:
        """Unknown opcode should not crash."""
        dev = self._make_device_with_keys()
        cb = MagicMock()
        dev.register_onoff_callback(cb)

        # Unknown 2-byte opcode
        dev._dispatch_access_payload(0x00AA, b"\x80\xff\x42")

        cb.assert_not_called()

    def test_segment_buffers_initialized_empty(self) -> None:
        """New device should have empty segment buffers."""
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        assert dev._segment_buffers == {}


class TestVendorCallbacks:
    """Test vendor callback registration and dispatch."""

    def test_register_vendor_callback(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        cb = MagicMock()
        dev.register_vendor_callback(cb)
        assert cb in dev._vendor_callbacks

    def test_unregister_vendor_callback(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        cb = MagicMock()
        dev.register_vendor_callback(cb)
        dev.unregister_vendor_callback(cb)
        assert cb not in dev._vendor_callbacks

    @pytest.mark.asyncio
    async def test_dispatch_vendor_opcode(self) -> None:
        """3-byte vendor opcodes should invoke vendor callbacks."""
        from tuya_ble_mesh.sig_mesh_protocol import MeshKeys

        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        dev._keys = MeshKeys(
            "f7a2a44f8e8a8029064f173ddc1e2b00",  # pragma: allowlist secret
            "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
            "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
        )
        cb = MagicMock()
        dev.register_vendor_callback(cb)

        # 3-byte vendor opcode 0xCDD007
        await dev._dispatch_access_payload(0x00AA, b"\xcd\xd0\x07\x01\x02\x03")

        cb.assert_called_once()
        call_args = cb.call_args[0]
        assert call_args[0] == 0xCDD007
        assert call_args[1] == b"\x01\x02\x03"

    def test_dispatch_2byte_opcode_does_not_invoke_vendor(self) -> None:
        """2-byte opcodes should NOT invoke vendor callbacks."""
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        cb = MagicMock()
        dev.register_vendor_callback(cb)

        # 2-byte opcode (OnOff Status)
        dev._dispatch_access_payload(0x00AA, b"\x82\x04\x01")

        cb.assert_not_called()


class TestCompositionData:
    """Test Composition Data handling and firmware version."""

    def _make_device_with_keys(self) -> SIGMeshDevice:
        """Create a SIGMeshDevice with mock keys loaded."""
        from tuya_ble_mesh.sig_mesh_protocol import MeshKeys

        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        dev._keys = MeshKeys(
            "f7a2a44f8e8a8029064f173ddc1e2b00",  # pragma: allowlist secret
            "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
            "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
        )
        return dev

    def test_firmware_version_initially_none(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        assert dev.firmware_version is None

    def test_register_composition_callback(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        cb = MagicMock()
        dev.register_composition_callback(cb)
        assert cb in dev._composition_callbacks

    def test_unregister_composition_callback(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        cb = MagicMock()
        dev.register_composition_callback(cb)
        dev.unregister_composition_callback(cb)
        assert cb not in dev._composition_callbacks

    def test_handle_composition_data_sets_firmware_version(self) -> None:
        """Composition Data Status should set firmware_version."""
        import struct

        dev = self._make_device_with_keys()

        # Build Composition Data Status params:
        # page(1) + CID(2) + PID(2) + VID(2) + CRPL(2) + Features(2) + elements
        page = b"\x00"
        cid = struct.pack("<H", 0x07D0)  # Tuya
        pid = struct.pack("<H", 0x0001)
        vid = struct.pack("<H", 0x0002)
        crpl = struct.pack("<H", 10)
        features = struct.pack("<H", 0x0003)
        params = page + cid + pid + vid + crpl + features + b"\x00" * 4

        dev._handle_composition_data(params)

        assert dev.firmware_version == "CID:07D0 PID:0001 VID:0002"
        assert dev._composition is not None
        assert dev._composition.cid == 0x07D0

    def test_handle_composition_data_invokes_callbacks(self) -> None:
        """Composition callbacks should be invoked."""
        import struct

        dev = self._make_device_with_keys()
        cb = MagicMock()
        dev.register_composition_callback(cb)

        page = b"\x00"
        cid = struct.pack("<H", 0x07D0)
        pid = struct.pack("<H", 0x0001)
        vid = struct.pack("<H", 0x0002)
        crpl = struct.pack("<H", 10)
        features = struct.pack("<H", 0x0003)
        params = page + cid + pid + vid + crpl + features

        dev._handle_composition_data(params)

        cb.assert_called_once()
        comp = cb.call_args[0][0]
        assert comp.cid == 0x07D0

    @pytest.mark.asyncio
    async def test_dispatch_composition_status_opcode(self) -> None:
        """Opcode 0x02 should route to _handle_composition_data."""
        import struct

        dev = self._make_device_with_keys()
        cb = MagicMock()
        dev.register_composition_callback(cb)

        # Opcode 0x02 (1-byte) + page + composition data
        page = b"\x00"
        cid = struct.pack("<H", 0x07D0)
        pid = struct.pack("<H", 0x0001)
        vid = struct.pack("<H", 0x0002)
        crpl = struct.pack("<H", 10)
        features = struct.pack("<H", 0x0003)
        access_payload = b"\x02" + page + cid + pid + vid + crpl + features

        await dev._dispatch_access_payload(0x00AA, access_payload)

        cb.assert_called_once()
        assert dev.firmware_version is not None

    def test_handle_composition_data_short_ignored(self) -> None:
        """Short composition data should be silently ignored."""
        dev = self._make_device_with_keys()
        dev._handle_composition_data(b"\x00\x01\x02")  # Too short
        assert dev.firmware_version is None

    @pytest.mark.asyncio
    async def test_request_composition_data_raises_when_not_connected(self) -> None:
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        with pytest.raises(SIGMeshError, match="Not connected"):
            await dev.request_composition_data()

    @pytest.mark.asyncio
    async def test_request_composition_data_writes_gatt(self) -> None:
        """request_composition_data should write Config Composition Get."""
        secrets = make_mock_secrets()
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, secrets)

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.start_notify = AsyncMock()
        mock_client.write_gatt_char = AsyncMock()
        mock_client.is_connected = True

        with (
            patch("tuya_ble_mesh.sig_mesh_device.BleakScanner") as mock_scanner,
            patch(
                "tuya_ble_mesh.sig_mesh_device.BleakClient",
                return_value=mock_client,
            ),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=MagicMock())
            await dev.connect(max_retries=1)

        # connect() calls request_composition_data internally,
        # plus we can verify it wrote to GATT
        assert mock_client.write_gatt_char.call_count >= 1
