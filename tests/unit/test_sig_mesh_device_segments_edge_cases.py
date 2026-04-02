"""Unit tests for sig_mesh_device_segments.py — uncovered edge cases.

Covers:
  _log_notify_exception: 108-109 (CancelledError from task.exception())
  _process_notify: 182 (dispatch when access_payload not None)
  _dispatch_access_payload_unlocked: 357 (OnOff CancelledError re-raise)
  _dispatch_access_payload_unlocked: 374 (Vendor CancelledError re-raise)
  _handle_composition_data: 414 (Composition CancelledError re-raise)
  _on_ble_disconnect: 430 (Disconnect CancelledError re-raise)
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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

from tuya_ble_mesh.sig_mesh_device_segments import SIGMeshDeviceSegmentsMixin
from tuya_ble_mesh.sig_mesh_protocol import AccessMessage, MeshKeys, NetworkPDU

# ── Minimal concrete device for testing ───────────────────────────────────────

_NET_KEY = "f7a2a44f8e8a8029064f173ddc1e2b00"  # pragma: allowlist secret
_DEV_KEY = "00112233445566778899aabbccddeeff"  # pragma: allowlist secret
_APP_KEY = "3216d1509884b533248541792b877f98"  # pragma: allowlist secret


@dataclass
class _FakeComposition:
    cid: int = 0x1234
    pid: int = 0x5678
    vid: int = 0x0001
    crpl: int = 10
    features: int = 0


class FakeSegmentDevice(SIGMeshDeviceSegmentsMixin):
    """Concrete subclass with all required mixin attributes."""

    def __init__(self) -> None:
        self._address = "DC:23:4D:21:43:A5"
        self._keys: MeshKeys | None = MeshKeys(
            net_key_hex=_NET_KEY,
            dev_key_hex=_DEV_KEY,
            app_key_hex=_APP_KEY,
        )
        self._client: Any = MagicMock()
        self._segment_lock = asyncio.Lock()
        self._segment_buffers: dict = {}
        self._pending_responses: dict = {}
        self._pending_notify_tasks: set = set()
        self._onoff_callbacks: list = []
        self._vendor_callbacks: list = []
        self._composition_callbacks: list = []
        self._disconnect_callbacks: list = []
        self._composition = None
        self._firmware_version: str | None = None


# ── _log_notify_exception lines 108-109 ───────────────────────────────────────


class TestLogNotifyException:
    """Lines 108-109: task.exception() raises CancelledError → pass."""

    def test_cancelled_error_from_exception_method_swallowed(self) -> None:
        dev = FakeSegmentDevice()
        mock_task: MagicMock = MagicMock(spec=asyncio.Task)
        mock_task.cancelled.return_value = False
        mock_task.exception.side_effect = asyncio.CancelledError()
        # Must not raise
        dev._log_notify_exception(mock_task)


# ── _process_notify line 182 ───────────────────────────────────────────────────


class TestProcessNotifyDispatch:
    """Line 182: _dispatch_access_payload called when access_payload is not None."""

    @pytest.mark.asyncio
    async def test_dispatches_when_access_payload_present(self) -> None:
        """Line 182: unsegmented PDU with valid payload → _dispatch_access_payload."""
        dev = FakeSegmentDevice()

        fake_net_pdu = NetworkPDU(
            ctl=0, ttl=5, seq=1, src=0x0100, dst=0x00B0, transport_pdu=b"\x00" * 11
        )
        fake_access_msg = AccessMessage(
            seg=False,
            akf=1,
            aid=0,
            access_payload=bytes([0x82, 0x04, 0x01]),  # OnOff Status ON
            raw=b"\x00",
        )

        dispatch_called = False

        async def mock_dispatch(src: int, payload: bytes) -> None:
            nonlocal dispatch_called
            dispatch_called = True

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_device_segments.parse_proxy_pdu",
                return_value=MagicMock(payload=b"\x00" * 14),
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_device_segments.decrypt_network_pdu",
                return_value=fake_net_pdu,
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_device_segments.decrypt_access_payload",
                return_value=fake_access_msg,
            ),
            patch.object(dev, "_dispatch_access_payload", side_effect=mock_dispatch),
        ):
            await dev._process_notify(b"\x00" * 14)

        assert dispatch_called


# ── _dispatch_access_payload_unlocked callbacks ───────────────────────────────


class TestOnOffCallbackCancelledError:
    """Line 357: CancelledError in OnOff callback re-raised."""

    @pytest.mark.asyncio
    async def test_cancelled_error_reraised(self) -> None:
        dev = FakeSegmentDevice()

        def raise_cancelled(on: bool) -> None:
            raise asyncio.CancelledError()

        dev._onoff_callbacks.append(raise_cancelled)

        # _OPCODE_ONOFF_STATUS = 0x8204, params must be non-empty
        with pytest.raises(asyncio.CancelledError):
            await dev._dispatch_access_payload_unlocked(
                src=0x0100,
                opcode=0x8204,  # _OPCODE_ONOFF_STATUS
                params=b"\x01",  # ON
            )


class TestVendorCallbackCancelledError:
    """Line 374: CancelledError in vendor callback re-raised."""

    @pytest.mark.asyncio
    async def test_cancelled_error_reraised(self) -> None:
        dev = FakeSegmentDevice()

        def raise_cancelled(opcode: int, params: bytes) -> None:
            raise asyncio.CancelledError()

        dev._vendor_callbacks.append(raise_cancelled)

        # Vendor opcode > 0xFFFF (3-byte opcode range)
        vendor_opcode = 0x010000C1  # typical Tuya vendor opcode
        with pytest.raises(asyncio.CancelledError):
            await dev._dispatch_access_payload_unlocked(
                src=0x0100,
                opcode=vendor_opcode,
                params=b"\x01\x00",
            )


class TestCompositionCallbackCancelledError:
    """Line 414: CancelledError in composition callback re-raised."""

    def test_cancelled_error_reraised(self) -> None:
        dev = FakeSegmentDevice()

        def raise_cancelled(comp: object) -> None:
            raise asyncio.CancelledError()

        dev._composition_callbacks.append(raise_cancelled)

        # Call _handle_composition_data with a minimal composition bytes
        # Composition Data: page 0, then CID(2) + PID(2) + VID(2) + CRPL(2) + Features(2)
        comp_bytes = bytes([0x00])  # page
        comp_bytes += b"\x34\x12"  # CID little-endian
        comp_bytes += b"\x78\x56"  # PID
        comp_bytes += b"\x01\x00"  # VID
        comp_bytes += b"\x0a\x00"  # CRPL
        comp_bytes += b"\x00\x00"  # Features

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_device_segments.parse_composition_data",
                return_value=_FakeComposition(),
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            dev._handle_composition_data(comp_bytes)


class TestDisconnectCallbackCancelledError:
    """Line 430: CancelledError in disconnect callback re-raised."""

    def test_cancelled_error_reraised(self) -> None:
        dev = FakeSegmentDevice()

        def raise_cancelled() -> None:
            raise asyncio.CancelledError()

        dev._disconnect_callbacks.append(raise_cancelled)

        with pytest.raises(asyncio.CancelledError):
            dev._on_ble_disconnect(MagicMock())
