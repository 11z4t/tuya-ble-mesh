"""SIG Mesh device segment reassembly and notification dispatch.

Provides ``SIGMeshDeviceSegmentsMixin`` which handles:

- GATT Proxy notification processing
- Segmented message reassembly per BT Mesh spec
- Access payload dispatch to callbacks and pending response futures
- BLE disconnection handling

This mixin is not intended for standalone use — it requires attributes
defined in ``SIGMeshDevice.__init__``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tuya_ble_mesh.exceptions import MalformedPacketError
from tuya_ble_mesh.logging_context import MeshLogAdapter
from tuya_ble_mesh.sig_mesh_protocol import (
    _OPCODE_COMPOSITION_STATUS,
    CompositionData,
    decrypt_access_payload,
    decrypt_network_pdu,
    parse_access_opcode,
    parse_composition_data,
    parse_proxy_pdu,
    parse_segment_header,
    reassemble_and_decrypt_segments,
)

if TYPE_CHECKING:
    from bleak.backends.characteristic import BleakGATTCharacteristic

    from tuya_ble_mesh.sig_mesh_protocol import MeshKeys

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# Reassembly timeout for segmented messages (seconds)
_REASSEMBLY_TIMEOUT = 10.0

# Opcodes for status responses
_OPCODE_ONOFF_STATUS = 0x8204
_OPCODE_APPKEY_STATUS = 0x8003
_OPCODE_MODEL_APP_STATUS = 0x803E


@dataclass
class _ReassemblyBuffer:
    """Buffer for collecting segmented transport PDU chunks."""

    src: int
    dst: int
    akf: int
    aid: int
    szmic: int
    seq_zero: int
    seg_n: int
    segments: dict[int, bytes] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)


class SIGMeshDeviceSegmentsMixin:
    """Mixin providing segment reassembly and notification dispatch.

    Requires attributes defined in ``SIGMeshDevice.__init__``:
    ``_keys``, ``_client``, ``_address``, ``_segment_lock``,
    ``_segment_buffers``, ``_pending_responses``, ``_pending_notify_tasks``,
    ``_onoff_callbacks``, ``_vendor_callbacks``, ``_composition_callbacks``,
    ``_disconnect_callbacks``, ``_composition``, ``_firmware_version``.
    """

    # Type stubs for attributes defined in SIGMeshDevice.__init__
    _keys: MeshKeys | None
    _address: str
    _client: Any
    _segment_lock: asyncio.Lock
    _segment_buffers: dict[tuple[int, int, int, int], _ReassemblyBuffer]
    _pending_responses: dict[tuple[int, int], asyncio.Future[bytes]]
    _pending_notify_tasks: set[asyncio.Task[None]]
    _onoff_callbacks: list[Any]
    _vendor_callbacks: list[Any]
    _composition_callbacks: list[Any]
    _disconnect_callbacks: list[Any]
    _composition: CompositionData | None
    _firmware_version: str | None

    def _log_notify_exception(self, task: asyncio.Task[None]) -> None:
        """Log exceptions from notify processing tasks.

        Args:
            task: The completed task to check for exceptions.
        """
        if task.cancelled():
            return
        try:
            exc = task.exception()
            if exc is not None:
                _LOGGER.error(
                    "Notify processing task failed for %s",
                    self._address,
                    exc_info=exc,
                )
        except asyncio.CancelledError:
            pass

    def _on_notify(self, _sender: BleakGATTCharacteristic, data: bytearray) -> None:
        """Handle a GATT Proxy notification.

        Schedules crypto processing as an asyncio task to avoid blocking
        the event loop (or BLE callback thread on some platforms).

        Args:
            _sender: The characteristic that sent the notification.
            data: Raw proxy PDU bytes.
        """
        if self._keys is None:
            return
        data_copy = bytes(data)
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._process_notify(data_copy))
            self._pending_notify_tasks.add(task)
            task.add_done_callback(self._pending_notify_tasks.discard)
            task.add_done_callback(self._log_notify_exception)
        except RuntimeError:
            # asyncio.get_running_loop() raises RuntimeError if no loop is running
            # (documented stdlib behavior). This can happen during shutdown.
            _LOGGER.debug("No running event loop for notify callback")

    async def _process_notify(self, data: bytes) -> None:
        """Decrypt and dispatch a GATT Proxy notification.

        Supports both unsegmented and segmented messages.

        Args:
            data: Raw proxy PDU bytes.
        """
        if self._keys is None:
            return

        try:
            proxy = parse_proxy_pdu(data)
        except MalformedPacketError:
            _LOGGER.debug("Failed to parse proxy PDU (%d bytes)", len(data), exc_info=True)
            return

        net_pdu = decrypt_network_pdu(
            self._keys.enc_key,
            self._keys.priv_key,
            self._keys.nid,
            proxy.payload,
            iv_index=self._keys.iv_index,
        )
        if net_pdu is None:
            _LOGGER.debug("Network PDU decryption failed or NID mismatch")
            return

        access_msg = decrypt_access_payload(
            self._keys,
            net_pdu.src,
            net_pdu.dst,
            net_pdu.seq,
            net_pdu.transport_pdu,
        )
        if access_msg is None:
            _LOGGER.debug("Access payload decryption failed")
            return

        if access_msg.seg:
            await self._handle_segment(net_pdu.src, net_pdu.dst, net_pdu.transport_pdu)
            return

        if access_msg.access_payload is None:
            _LOGGER.debug("Unsegmented access payload decryption failed")
            return

        await self._dispatch_access_payload(net_pdu.src, access_msg.access_payload)

    async def _handle_segment(self, src: int, dst: int, transport_pdu: bytes) -> None:
        """Collect a segment and attempt reassembly when complete.

        CF-1: Protected with _segment_lock to prevent race conditions in concurrent
        notify callbacks corrupting segment reassembly state.

        Args:
            src: Source unicast address.
            dst: Destination address.
            transport_pdu: Lower transport PDU (segmented).
        """
        try:
            seg_hdr = parse_segment_header(transport_pdu)
        except MalformedPacketError:
            _LOGGER.debug("Failed to parse segment header", exc_info=True)
            return

        # Per BT Mesh spec: buffer key must include src, dst, seq_zero, and aid
        buf_key = (src, dst, seg_hdr.seq_zero, seg_hdr.aid)

        # CF-1: Lock ALL access to _segment_buffers to prevent race conditions
        async with self._segment_lock:
            # Get or create reassembly buffer
            buf = self._segment_buffers.get(buf_key)
            if buf is None:
                buf = _ReassemblyBuffer(
                    src=src,
                    dst=dst,
                    akf=seg_hdr.akf,
                    aid=seg_hdr.aid,
                    szmic=seg_hdr.szmic,
                    seq_zero=seg_hdr.seq_zero,
                    seg_n=seg_hdr.seg_n,
                )
                self._segment_buffers[buf_key] = buf

            buf.segments[seg_hdr.seg_o] = seg_hdr.segment_data

            _LOGGER.debug(
                "Segment %d/%d received from 0x%04X (seq_zero=%d)",
                seg_hdr.seg_o,
                seg_hdr.seg_n,
                src,
                seg_hdr.seq_zero,
            )

            # Check if all segments received
            if len(buf.segments) == buf.seg_n + 1:
                await self._complete_reassembly(buf_key)

            # Clean stale buffers
            await self._clean_stale_buffers()

    async def _complete_reassembly(self, buf_key: tuple[int, int, int, int]) -> None:
        """Decrypt a fully reassembled segmented message and dispatch.

        CF-1: Called while holding _segment_lock to ensure atomic buffer removal.

        Args:
            buf_key: (src, dst, seq_zero, aid) key into _segment_buffers.
        """
        # CF-1: Buffer removal happens while lock is held (caller holds _segment_lock)
        buf = self._segment_buffers.pop(buf_key, None)
        if buf is None or self._keys is None:
            return

        access_payload = reassemble_and_decrypt_segments(
            self._keys,
            buf.src,
            buf.dst,
            buf.segments,
            buf.seg_n,
            buf.szmic,
            buf.seq_zero,
            buf.akf,
        )

        if access_payload is None:
            _LOGGER.debug(
                "Segmented reassembly decryption failed from 0x%04X",
                buf.src,
            )
            return

        _LOGGER.debug(
            "Reassembled %d segments from 0x%04X (%d bytes)",
            buf.seg_n + 1,
            buf.src,
            len(access_payload),
        )

        # CF-1: Parse opcode and call unlocked version since we already hold _segment_lock
        try:
            opcode, params = parse_access_opcode(access_payload)
        except MalformedPacketError:
            _LOGGER.debug("Failed to parse access opcode", exc_info=True)
            return

        await self._dispatch_access_payload_unlocked(buf.src, opcode, params)

    async def _clean_stale_buffers(self) -> None:
        """Remove reassembly buffers older than _REASSEMBLY_TIMEOUT.

        CF-1: Called while holding _segment_lock to ensure thread-safe iteration.
        """
        # CF-1: Iteration happens while lock is held (caller holds _segment_lock)
        now = time.monotonic()
        stale = [
            key
            for key, buf in self._segment_buffers.items()
            if now - buf.created_at > _REASSEMBLY_TIMEOUT
        ]
        for key in stale:
            _LOGGER.debug("Discarding stale reassembly buffer: %s", key)
            del self._segment_buffers[key]

    async def _dispatch_access_payload(self, src: int, access_payload: bytes) -> None:
        """Parse opcode and route to appropriate handler.

        CF-1: Protected with _segment_lock to prevent race conditions when accessing
        _pending_responses from concurrent notify callbacks.

        Shared by both unsegmented and reassembled segmented paths.
        Pending response futures (from send_config_*) are resolved first.

        Args:
            src: Source unicast address.
            access_payload: Decrypted access layer payload.
        """
        try:
            opcode, params = parse_access_opcode(access_payload)
        except MalformedPacketError:
            _LOGGER.debug("Failed to parse access opcode", exc_info=True)
            return

        # CF-1: Lock access to _pending_responses to prevent race conditions
        async with self._segment_lock:
            await self._dispatch_access_payload_unlocked(src, opcode, params)

    async def _dispatch_access_payload_unlocked(
        self, src: int, opcode: int, params: bytes
    ) -> None:
        """Dispatch access payload without acquiring lock (lock must be held by caller).

        CF-1: Called while holding _segment_lock. Do not call directly unless lock is held.

        Args:
            src: Source unicast address.
            opcode: Parsed opcode.
            params: Opcode parameters.
        """
        # Resolve pending config response futures (AppKey Status, Model App Status)
        # Match first pending response with matching opcode (FIFO order by correlation_id)
        matched_key = None
        for key in self._pending_responses:
            if key[0] == opcode:
                matched_key = key
                break
        if matched_key is not None:
            future = self._pending_responses.pop(matched_key)
            if not future.done():
                future.set_result(params)
            return

        if opcode == _OPCODE_ONOFF_STATUS and params:
            on_state = bool(params[0])
            _LOGGER.info(
                "GenericOnOff Status from 0x%04X: %s",
                src,
                "ON" if on_state else "OFF",
            )
            for callback in list(self._onoff_callbacks):
                try:
                    callback(on_state)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.warning("OnOff callback error", exc_info=True)
        elif opcode == _OPCODE_COMPOSITION_STATUS:
            self._handle_composition_data(params)
        elif opcode > 0xFFFF:
            # 3-byte vendor opcode
            _LOGGER.debug(
                "Vendor opcode 0x%06X (%d param bytes) from 0x%04X",
                opcode,
                len(params),
                src,
            )
            for vcb in list(self._vendor_callbacks):
                try:
                    vcb(opcode, params)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.warning("Vendor callback error", exc_info=True)
        else:
            _LOGGER.debug(
                "Received opcode 0x%04X (%d param bytes) from 0x%04X",
                opcode,
                len(params),
                src,
            )

    def _handle_composition_data(self, params: bytes) -> None:
        """Handle a Composition Data Status response.

        Parses the composition data, sets firmware_version, and
        notifies composition callbacks.

        Args:
            params: Parameters after opcode 0x02.
        """
        try:
            comp = parse_composition_data(params)
        except MalformedPacketError:
            _LOGGER.debug("Failed to parse Composition Data", exc_info=True)
            return

        self._composition = comp
        self._firmware_version = f"CID:{comp.cid:04X} PID:{comp.pid:04X} VID:{comp.vid:04X}"

        _LOGGER.info(
            "Composition Data from device: %s (CRPL=%d, features=0x%04X)",
            self._firmware_version,
            comp.crpl,
            comp.features,
        )

        for callback in list(self._composition_callbacks):
            try:
                callback(comp)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning("Composition callback error", exc_info=True)

    def _on_ble_disconnect(self, _client: Any) -> None:
        """Handle BLE disconnection event.

        Args:
            _client: The disconnected BleakClient.
        """
        _LOGGER.warning("SIG Mesh device disconnected: %s", self._address)
        self._client = None
        for callback in list(self._disconnect_callbacks):
            try:
                callback()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning("Disconnect callback error", exc_info=True)
