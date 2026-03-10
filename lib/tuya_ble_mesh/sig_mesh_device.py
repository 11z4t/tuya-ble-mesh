"""High-level SIG Mesh device interface for GATT Proxy control.

Provides ``SIGMeshDevice`` for connecting to a standard Bluetooth SIG Mesh
device via GATT Proxy (UUID 0x1828), sending GenericOnOff commands, and
receiving status notifications.

Key material is loaded from 1Password via ``SecretsManager`` (Rule S10).
All byte parsing is delegated to ``sig_mesh_protocol`` (Rule S3).
All crypto operations are in ``sig_mesh_crypto`` (Rule S4).

SECURITY: Key material is NEVER logged, printed, or included in exceptions.
Only addresses, lengths, and opcodes are safe to log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

from tuya_ble_mesh.exceptions import (
    MeshConnectionError,
    SIGMeshError,
    SIGMeshKeyError,
)
from tuya_ble_mesh.logging_context import MeshLogAdapter, mesh_operation
from tuya_ble_mesh.sig_mesh_protocol import (
    _OPCODE_COMPOSITION_STATUS,
    SEG_DATA_SIZE,
    CompositionData,
    MeshKeys,
    config_appkey_add,
    config_composition_get,
    config_model_app_bind,
    decrypt_access_payload,
    decrypt_network_pdu,
    encrypt_network_pdu,
    generic_onoff_set,
    make_access_segmented,
    make_access_unsegmented,
    make_proxy_pdu,
    parse_access_opcode,
    parse_composition_data,
    parse_proxy_pdu,
    parse_segment_header,
    reassemble_and_decrypt_segments,
)

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# SIG Mesh GATT Proxy UUIDs
SIG_MESH_PROXY_SERVICE = "00001828-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROXY_DATA_IN = "00002add-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROXY_DATA_OUT = "00002ade-0000-1000-8000-00805f9b34fb"

# Opcodes for status responses
_OPCODE_ONOFF_STATUS = 0x8204
_OPCODE_APPKEY_STATUS = 0x8003
_OPCODE_MODEL_APP_STATUS = 0x803E

# Callback types
OnOffCallback = Callable[[bool], Any]
VendorCallback = Callable[[int, bytes], Any]
CompositionCallback = Callable[[CompositionData], Any]
DisconnectCallback = Callable[[], Any]

# Initial sequence number (in-memory, not persisted)
_INITIAL_SEQ = 2000

# Default TTL for mesh commands
_DEFAULT_TTL = 5

# Reassembly timeout for segmented messages (seconds)
_REASSEMBLY_TIMEOUT = 10.0


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


class SIGMeshDevice:
    """High-level interface to a SIG Mesh device via GATT Proxy.

    Provides the same duck-type interface as ``MeshDevice`` for use
    with ``TuyaBLEMeshCoordinator``.
    """

    def __init__(
        self,
        address: str,
        target_addr: int,
        our_addr: int,
        secrets: Any,
        *,
        op_item_prefix: str = "s17",
        iv_index: int = 0,
        ble_device_callback: Any = None,
    ) -> None:
        """Initialize a SIG Mesh device interface.

        Args:
            address: BLE MAC address (e.g. ``DC:23:4D:21:43:A5``).
            target_addr: Target unicast address (e.g. 0x00AA).
            our_addr: Our unicast address (e.g. 0x0001).
            secrets: SecretsManager instance for key loading.
            op_item_prefix: 1Password item name prefix for keys.
            iv_index: Mesh IV Index.
            ble_device_callback: Optional callback(address) → BLEDevice for
                HA Bluetooth Proxy support. If None, uses BleakScanner.
        """
        self._address = address.upper()
        self._target_addr = target_addr
        self._our_addr = our_addr
        self._secrets = secrets
        self._op_item_prefix = op_item_prefix
        self._iv_index = iv_index
        self._ble_device_callback = ble_device_callback

        self._client: BleakClient | None = None
        self._keys: MeshKeys | None = None
        self._seq = _INITIAL_SEQ
        self._seq_lock = asyncio.Lock()
        self._tid = 0
        self._event_loop: asyncio.AbstractEventLoop | None = None

        self._onoff_callbacks: list[OnOffCallback] = []
        self._vendor_callbacks: list[VendorCallback] = []
        self._composition_callbacks: list[CompositionCallback] = []
        self._disconnect_callbacks: list[DisconnectCallback] = []

        # Composition Data and firmware version
        self._composition: CompositionData | None = None
        self._firmware_version: str | None = None

        # Segmented message reassembly buffers: (src, seq_zero) -> buffer
        self._segment_buffers: dict[tuple[int, int], _ReassemblyBuffer] = {}

        # Pending response futures: opcode -> Future(params)
        self._pending_responses: dict[int, asyncio.Future[bytes]] = {}

    @property
    def address(self) -> str:
        """Return the device BLE MAC address."""
        return self._address

    @property
    def is_connected(self) -> bool:
        """Return True if the BLE client is connected."""
        return self._client is not None and self._client.is_connected

    @property
    def firmware_version(self) -> str | None:
        """Return firmware version derived from Composition Data (CID/PID/VID)."""
        return self._firmware_version

    def set_seq(self, seq: int) -> None:
        """Override the current sequence number (for restore on startup).

        Args:
            seq: Sequence number to set.
        """
        self._seq = seq

    def get_seq(self) -> int:
        """Return the current sequence number (for persistence).

        Returns:
            Current sequence number.
        """
        return self._seq

    def register_onoff_callback(self, callback: OnOffCallback) -> None:
        """Register a callback for GenericOnOff Status notifications.

        Args:
            callback: Called with ``on: bool`` when status received.
        """
        self._onoff_callbacks.append(callback)

    def unregister_onoff_callback(self, callback: OnOffCallback) -> None:
        """Remove a previously registered onoff callback.

        Args:
            callback: The callback to remove.
        """
        self._onoff_callbacks.remove(callback)

    def register_vendor_callback(self, callback: VendorCallback) -> None:
        """Register a callback for Tuya vendor messages.

        Args:
            callback: Called with ``(opcode: int, params: bytes)``.
        """
        self._vendor_callbacks.append(callback)

    def unregister_vendor_callback(self, callback: VendorCallback) -> None:
        """Remove a previously registered vendor callback.

        Args:
            callback: The callback to remove.
        """
        self._vendor_callbacks.remove(callback)

    def register_composition_callback(self, callback: CompositionCallback) -> None:
        """Register a callback for Composition Data responses.

        Args:
            callback: Called with ``CompositionData`` when received.
        """
        self._composition_callbacks.append(callback)

    def unregister_composition_callback(self, callback: CompositionCallback) -> None:
        """Remove a previously registered composition callback.

        Args:
            callback: The callback to remove.
        """
        self._composition_callbacks.remove(callback)

    def register_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Register a callback for disconnect events.

        Args:
            callback: Called when BLE connection is lost.
        """
        self._disconnect_callbacks.append(callback)

    def unregister_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Remove a previously registered disconnect callback.

        Args:
            callback: The callback to remove.
        """
        self._disconnect_callbacks.remove(callback)

    async def connect(
        self,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> None:
        """Connect to the device, load keys, and subscribe to notifications.

        Args:
            timeout: Connection timeout per attempt in seconds.
            max_retries: Maximum number of connection attempts.

        Raises:
            SIGMeshKeyError: If keys cannot be loaded from 1Password.
            MeshConnectionError: If BLE connection fails after all retries.
        """
        async with mesh_operation(self._address, "connect"):
            await self._connect_impl(timeout=timeout, max_retries=max_retries)

    async def _connect_impl(
        self,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> None:
        """Internal connect implementation (called within mesh_operation context).

        Args:
            timeout: Connection timeout per attempt in seconds.
            max_retries: Maximum number of connection attempts.

        Raises:
            SIGMeshKeyError: If keys cannot be loaded.
            MeshConnectionError: If BLE connection fails after all retries.
        """
        await self._load_keys()
        self._event_loop = asyncio.get_running_loop()

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                _LOGGER.info(
                    "Connecting to %s (attempt %d/%d)",
                    self._address,
                    attempt,
                    max_retries,
                )
                if self._ble_device_callback is not None:
                    device = self._ble_device_callback(self._address)
                else:
                    device = await BleakScanner.find_device_by_address(
                        self._address, timeout=timeout
                    )
                if device is None:
                    msg = f"Device {self._address} not found"
                    raise MeshConnectionError(msg)

                client = BleakClient(
                    device,
                    timeout=timeout,
                    disconnected_callback=self._on_ble_disconnect,
                )
                await client.connect()

                # Subscribe to Proxy Data Out notifications
                await client.start_notify(SIG_MESH_PROXY_DATA_OUT, self._on_notify)

                self._client = client
                _LOGGER.info("Connected to %s", self._address)

                # Request Composition Data (non-critical)
                try:
                    await self.request_composition_data()
                except Exception:
                    _LOGGER.debug(
                        "Composition Data request failed (non-critical)",
                        exc_info=True,
                    )
                return

            except Exception as exc:
                last_error = exc
                _LOGGER.warning(
                    "Connection attempt %d failed for %s",
                    attempt,
                    self._address,
                    exc_info=True,
                )
                # Remove cached BLE device between retries
                await self._bluetoothctl_remove()
                await asyncio.sleep(2.0)

        msg = f"Failed to connect to {self._address} after {max_retries} attempts"
        raise MeshConnectionError(msg) from last_error

    async def disconnect(self) -> None:
        """Disconnect from the device and zero key material."""
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.stop_notify(SIG_MESH_PROXY_DATA_OUT)
            with contextlib.suppress(Exception):
                await self._client.disconnect()
            self._client = None

        # Zero-fill key material before clearing (defense in depth)
        if self._keys is not None:
            # Attempt to overwrite key bytes in memory with zeros
            # Note: Python's memory management may not guarantee immediate zeroing,
            # but this is best-effort defense-in-depth against memory forensics.
            try:
                for attr in ("net_key", "dev_key", "app_key", "enc_key", "priv_key", "network_id"):
                    val = getattr(self._keys, attr, None)
                    # Overwrite mutable bytearray if possible
                    if isinstance(val, bytearray) and len(val) > 0:
                        val[:] = b"\x00" * len(val)
            except Exception:
                pass  # Frozen dataclass, best effort only
            self._keys = None
        _LOGGER.info("Disconnected from %s", self._address)

    async def send_power(self, on: bool, *, max_retries: int = 3) -> None:
        """Send GenericOnOff Set command with retry.

        Retries on transient BLE write failures with exponential backoff.

        Args:
            on: True to turn on, False to turn off.
            max_retries: Maximum retry attempts (default 3).

        Raises:
            SIGMeshError: If not connected or keys not loaded.
            MeshConnectionError: If BLE write fails after all retries.
        """
        if self._client is None or self._keys is None:
            msg = "Not connected"
            raise SIGMeshError(msg)

        app_key = self._keys.app_key
        if app_key is None:
            msg = "No application key loaded"
            raise SIGMeshKeyError(msg)

        last_error: Exception | None = None
        backoff = 1.0

        for attempt in range(1, max_retries + 1):
            try:
                access_payload = generic_onoff_set(on, self._tid)
                self._tid = (self._tid + 1) & 0xFF

                seq = await self._next_seq()

                transport_pdu = make_access_unsegmented(
                    app_key,
                    self._our_addr,
                    self._target_addr,
                    seq,
                    self._keys.iv_index,
                    access_payload,
                    akf=1,
                    aid=self._keys.aid,
                )

                network_pdu = encrypt_network_pdu(
                    self._keys.enc_key,
                    self._keys.priv_key,
                    self._keys.nid,
                    ctl=0,
                    ttl=_DEFAULT_TTL,
                    seq=seq,
                    src=self._our_addr,
                    dst=self._target_addr,
                    transport_pdu=transport_pdu,
                    iv_index=self._keys.iv_index,
                )

                proxy_pdu = make_proxy_pdu(network_pdu)

                await self._client.write_gatt_char(
                    SIG_MESH_PROXY_DATA_IN, proxy_pdu, response=False
                )
                _LOGGER.info(
                    "GenericOnOff %s sent to 0x%04X (seq=%d, attempt=%d)",
                    "ON" if on else "OFF",
                    self._target_addr,
                    seq,
                    attempt,
                )
                return
            except (SIGMeshError, SIGMeshKeyError):
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= max_retries:
                    break
                _LOGGER.warning(
                    "BLE write attempt %d/%d failed for %s: %s, retrying in %.1fs",
                    attempt,
                    max_retries,
                    self._address,
                    type(exc).__name__,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= 2.0

        msg = f"BLE write failed for {self._address} after {max_retries} attempts"
        raise MeshConnectionError(msg) from last_error

    async def request_composition_data(self) -> None:
        """Send Config Composition Data Get to retrieve device info.

        Uses the device key (akf=0) since this is a config message.
        Response arrives asynchronously via notifications.

        Raises:
            SIGMeshError: If not connected or keys not loaded.
        """
        if self._client is None or self._keys is None:
            msg = "Not connected"
            raise SIGMeshError(msg)

        access_payload = config_composition_get(page=0)
        seq = await self._next_seq()

        transport_pdu = make_access_unsegmented(
            self._keys.dev_key,
            self._our_addr,
            self._target_addr,
            seq,
            self._keys.iv_index,
            access_payload,
            akf=0,
            aid=0,
        )

        network_pdu = encrypt_network_pdu(
            self._keys.enc_key,
            self._keys.priv_key,
            self._keys.nid,
            ctl=0,
            ttl=_DEFAULT_TTL,
            seq=seq,
            src=self._our_addr,
            dst=self._target_addr,
            transport_pdu=transport_pdu,
            iv_index=self._keys.iv_index,
        )

        proxy_pdu = make_proxy_pdu(network_pdu)
        await self._client.write_gatt_char(SIG_MESH_PROXY_DATA_IN, proxy_pdu, response=False)
        _LOGGER.info(
            "Composition Data Get sent to 0x%04X (seq=%d)",
            self._target_addr,
            seq,
        )

    # --- Private helpers ---

    async def _next_seq(self) -> int:
        """Return and increment the sequence number (24-bit wrap).

        Protected by asyncio.Lock to prevent nonce collision from
        concurrent callers.

        Raises:
            SIGMeshError: If sequence number exhausted (> 0xFFFFFF).
        """
        async with self._seq_lock:
            if self._seq > 0xFFFFFF:
                msg = "Sequence number exhausted — reconnect required"
                raise SIGMeshError(msg)
            seq = self._seq
            self._seq += 1
            return seq

    async def _next_seqs(self, n: int) -> int:
        """Reserve n consecutive sequence numbers and return the first.

        Used for segmented messages that need a contiguous seq range.
        Wraps at 24-bit boundary per SIG Mesh spec.

        Args:
            n: Number of sequence numbers to reserve.

        Returns:
            First sequence number of the reserved range.

        Raises:
            SIGMeshError: If sequence number exhausted (> 0xFFFFFF).
        """
        async with self._seq_lock:
            if self._seq > 0xFFFFFF or (self._seq + n) > 0xFFFFFF:
                msg = "Sequence number exhausted — reconnect required"
                raise SIGMeshError(msg)
            seq = self._seq
            self._seq += n
            return seq

    async def send_config_appkey_add(
        self,
        app_key: bytes,
        *,
        net_idx: int = 0,
        app_idx: int = 0,
        response_timeout: float = 15.0,
    ) -> bool:
        """Send Config AppKey Add (opcode 0x00) and wait for Status response.

        The 20-byte access payload requires segmented transport (2 segments).
        Uses device key (akf=0) as required for config messages.

        Args:
            app_key: 16-byte application key to add.
            net_idx: Network key index (0-4095).
            app_idx: Application key index (0-4095).
            response_timeout: Seconds to wait for AppKey Status response.

        Returns:
            True if device responded with Success (0x00), False otherwise.

        Raises:
            SIGMeshError: If not connected or keys not loaded.
            TimeoutError: If no response within response_timeout.
        """
        if self._client is None or self._keys is None:
            msg = "Not connected"
            raise SIGMeshError(msg)

        access_payload = config_appkey_add(net_idx, app_idx, app_key)

        # Pre-compute n_segs to reserve contiguous sequence numbers
        upper_len = len(access_payload) + 4  # + 4-byte MIC (szmic=0)
        n_segs = (upper_len + SEG_DATA_SIZE - 1) // SEG_DATA_SIZE
        seq_start = await self._next_seqs(n_segs)

        segments = make_access_segmented(
            self._keys.dev_key,
            self._our_addr,
            self._target_addr,
            seq_start,
            self._keys.iv_index,
            access_payload,
            akf=0,
            aid=0,
        )

        # Register response future BEFORE sending
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        self._pending_responses[_OPCODE_APPKEY_STATUS] = future

        try:
            for seg_seq, transport_pdu in segments:
                network_pdu = encrypt_network_pdu(
                    self._keys.enc_key,
                    self._keys.priv_key,
                    self._keys.nid,
                    ctl=0,
                    ttl=_DEFAULT_TTL,
                    seq=seg_seq,
                    src=self._our_addr,
                    dst=self._target_addr,
                    transport_pdu=transport_pdu,
                    iv_index=self._keys.iv_index,
                )
                proxy_pdu = make_proxy_pdu(network_pdu)
                await self._client.write_gatt_char(
                    SIG_MESH_PROXY_DATA_IN, proxy_pdu, response=False
                )
                await asyncio.sleep(0.1)

            _LOGGER.info(
                "AppKey Add sent to 0x%04X (%d segments, seq_start=%d)",
                self._target_addr,
                len(segments),
                seq_start,
            )

            params = await asyncio.wait_for(asyncio.shield(future), timeout=response_timeout)
        except TimeoutError:
            msg = "Timeout waiting for AppKey Status response"
            raise SIGMeshError(msg) from None
        finally:
            self._pending_responses.pop(_OPCODE_APPKEY_STATUS, None)

        status = params[0] if params else 0xFF
        _LOGGER.info(
            "AppKey Status from 0x%04X: 0x%02X (%s)",
            self._target_addr,
            status,
            "Success" if status == 0x00 else "Error",
        )
        return status == 0x00

    async def send_config_model_app_bind(
        self,
        element_addr: int,
        app_idx: int,
        model_id: int,
        *,
        response_timeout: float = 10.0,
    ) -> bool:
        """Send Config Model App Bind (opcode 0x803D) and wait for Status.

        The 8-byte access payload fits in an unsegmented message.
        Uses device key (akf=0) as required for config messages.

        Args:
            element_addr: Element unicast address.
            app_idx: Application key index to bind.
            model_id: SIG Model ID (e.g. 0x1000 for GenericOnOff Server).
            response_timeout: Seconds to wait for Model App Status response.

        Returns:
            True if device responded with Success (0x00), False otherwise.

        Raises:
            SIGMeshError: If not connected or keys not loaded.
            TimeoutError: If no response within response_timeout.
        """
        if self._client is None or self._keys is None:
            msg = "Not connected"
            raise SIGMeshError(msg)

        access_payload = config_model_app_bind(element_addr, app_idx, model_id)
        seq = await self._next_seq()

        transport_pdu = make_access_unsegmented(
            self._keys.dev_key,
            self._our_addr,
            self._target_addr,
            seq,
            self._keys.iv_index,
            access_payload,
            akf=0,
            aid=0,
        )
        network_pdu = encrypt_network_pdu(
            self._keys.enc_key,
            self._keys.priv_key,
            self._keys.nid,
            ctl=0,
            ttl=_DEFAULT_TTL,
            seq=seq,
            src=self._our_addr,
            dst=self._target_addr,
            transport_pdu=transport_pdu,
            iv_index=self._keys.iv_index,
        )
        proxy_pdu = make_proxy_pdu(network_pdu)

        # Register response future BEFORE sending
        loop = asyncio.get_running_loop()
        future_bind: asyncio.Future[bytes] = loop.create_future()
        self._pending_responses[_OPCODE_MODEL_APP_STATUS] = future_bind

        try:
            await self._client.write_gatt_char(SIG_MESH_PROXY_DATA_IN, proxy_pdu, response=False)
            _LOGGER.info(
                "Model App Bind sent: element=0x%04X app_idx=%d model=0x%04X (seq=%d)",
                element_addr,
                app_idx,
                model_id,
                seq,
            )

            params_bind = await asyncio.wait_for(
                asyncio.shield(future_bind), timeout=response_timeout
            )
        except TimeoutError:
            msg = "Timeout waiting for Model App Status response"
            raise SIGMeshError(msg) from None
        finally:
            self._pending_responses.pop(_OPCODE_MODEL_APP_STATUS, None)

        status_bind = params_bind[0] if params_bind else 0xFF
        _LOGGER.info(
            "Model App Status from 0x%04X: 0x%02X (%s)",
            self._target_addr,
            status_bind,
            "Success" if status_bind == 0x00 else "Error",
        )
        return status_bind == 0x00

    async def _load_keys(self) -> None:
        """Load mesh keys from 1Password via SecretsManager.

        Raises:
            SIGMeshKeyError: If any required key is missing.
        """
        prefix = self._op_item_prefix
        try:
            net_key_hex = await self._secrets.get(
                f"{prefix}-net-key",
                "password",  # pragma: allowlist secret
            )
            dev_key_hex = await self._secrets.get(
                f"{prefix}-dev-key-{self._target_addr:04x}",
                "password",  # pragma: allowlist secret
            )
            app_key_hex = await self._secrets.get(
                f"{prefix}-app-key",
                "password",  # pragma: allowlist secret
            )
        except Exception as exc:
            msg = f"Failed to load SIG Mesh keys for prefix '{prefix}'"
            raise SIGMeshKeyError(msg) from exc

        self._keys = MeshKeys(
            net_key_hex,
            dev_key_hex,
            app_key_hex,
            iv_index=self._iv_index,
        )
        _LOGGER.info(
            "SIG Mesh keys loaded (prefix=%s, NID=0x%02X, AID=0x%02X)",
            prefix,
            self._keys.nid,
            self._keys.aid,
        )

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
        if self._event_loop is not None and self._event_loop.is_running():
            self._event_loop.create_task(self._process_notify(data_copy))

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
        except Exception:
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
            self._handle_segment(net_pdu.src, net_pdu.dst, net_pdu.transport_pdu)
            return

        if access_msg.access_payload is None:
            _LOGGER.debug("Unsegmented access payload decryption failed")
            return

        self._dispatch_access_payload(net_pdu.src, access_msg.access_payload)

    def _handle_segment(self, src: int, dst: int, transport_pdu: bytes) -> None:
        """Collect a segment and attempt reassembly when complete.

        Args:
            src: Source unicast address.
            dst: Destination address.
            transport_pdu: Lower transport PDU (segmented).
        """
        try:
            seg_hdr = parse_segment_header(transport_pdu)
        except Exception:
            _LOGGER.debug("Failed to parse segment header", exc_info=True)
            return

        buf_key = (src, seg_hdr.seq_zero)

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
            self._complete_reassembly(buf_key)

        # Clean stale buffers
        self._clean_stale_buffers()

    def _complete_reassembly(self, buf_key: tuple[int, int]) -> None:
        """Decrypt a fully reassembled segmented message and dispatch.

        Args:
            buf_key: (src, seq_zero) key into _segment_buffers.
        """
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

        self._dispatch_access_payload(buf.src, access_payload)

    def _clean_stale_buffers(self) -> None:
        """Remove reassembly buffers older than _REASSEMBLY_TIMEOUT."""
        now = time.monotonic()
        stale = [
            key
            for key, buf in self._segment_buffers.items()
            if now - buf.created_at > _REASSEMBLY_TIMEOUT
        ]
        for key in stale:
            _LOGGER.debug("Discarding stale reassembly buffer: %s", key)
            del self._segment_buffers[key]

    def _dispatch_access_payload(self, src: int, access_payload: bytes) -> None:
        """Parse opcode and route to appropriate handler.

        Shared by both unsegmented and reassembled segmented paths.
        Pending response futures (from send_config_*) are resolved first.

        Args:
            src: Source unicast address.
            access_payload: Decrypted access layer payload.
        """
        try:
            opcode, params = parse_access_opcode(access_payload)
        except Exception:
            _LOGGER.debug("Failed to parse access opcode", exc_info=True)
            return

        # Resolve pending config response futures (AppKey Status, Model App Status)
        if opcode in self._pending_responses:
            future = self._pending_responses.pop(opcode)
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
        except Exception:
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
            except Exception:
                _LOGGER.warning("Composition callback error", exc_info=True)

    def _on_ble_disconnect(self, _client: BleakClient) -> None:
        """Handle BLE disconnection event.

        Args:
            _client: The disconnected BleakClient.
        """
        _LOGGER.warning("SIG Mesh device disconnected: %s", self._address)
        self._client = None
        for callback in list(self._disconnect_callbacks):
            try:
                callback()
            except Exception:
                _LOGGER.warning("Disconnect callback error", exc_info=True)

    async def _bluetoothctl_remove(self) -> None:
        """Remove device from BlueZ cache via bluetoothctl."""
        try:
            process = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                "remove",
                self._address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=5)
        except Exception:
            _LOGGER.debug("bluetoothctl remove failed (ignored)", exc_info=True)
