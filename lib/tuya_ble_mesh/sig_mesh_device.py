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
from typing import Any, Protocol

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError

from tuya_ble_mesh.exceptions import (
    ConnectionError as MeshConnectionError,
    MalformedPacketError,
    SecretAccessError,
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


class SeqStore(Protocol):
    """Protocol for sequence number persistence.

    Implementations can persist seq to disk (HA coordinator) or keep in-memory (default).
    """

    def get_seq(self) -> int:
        """Return the current sequence number.

        Returns:
            Current sequence number.
        """
        ...

    def set_seq(self, seq: int) -> None:
        """Set the sequence number.

        Args:
            seq: Sequence number to set.
        """
        ...


class InMemorySeqStore:
    """Default in-memory sequence number store.

    Starts from 0 (not the legacy _INITIAL_SEQ=2000).
    HA coordinator should provide a persistent store via the Store mechanism.
    """

    def __init__(self, initial_seq: int = 0) -> None:
        """Initialize the in-memory seq store.

        Args:
            initial_seq: Initial sequence number (default 0).
        """
        self._seq = initial_seq

    def get_seq(self) -> int:
        """Return the current sequence number.

        Returns:
            Current sequence number.
        """
        return self._seq

    def set_seq(self, seq: int) -> None:
        """Set the sequence number.

        Args:
            seq: Sequence number to set.
        """
        self._seq = seq


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
        seq_store: SeqStore | None = None,
        ble_device_callback: Any = None,
        adapter: str | None = None,
    ) -> None:
        """Initialize a SIG Mesh device interface.

        Args:
            address: BLE MAC address (e.g. ``DC:23:4D:21:43:A5``).
            target_addr: Target unicast address (e.g. 0x00AA).
            our_addr: Our unicast address (e.g. 0x0001).
            secrets: SecretsManager instance for key loading.
            op_item_prefix: 1Password item name prefix for keys.
            iv_index: Mesh IV Index.
            seq_store: Optional SeqStore for sequence number persistence.
                If None, uses InMemorySeqStore starting from 0.
            ble_device_callback: Optional callback(address) → BLEDevice for
                HA Bluetooth Proxy support. If None, uses BleakScanner.
            adapter: BLE adapter name (e.g. "hci0"). Forces scan and connect
                via this specific adapter, bypassing HA's habluetooth routing.
        """
        self._address = address.upper()
        self._target_addr = target_addr
        self._our_addr = our_addr
        self._secrets = secrets
        self._op_item_prefix = op_item_prefix
        self._iv_index = iv_index
        self._ble_device_callback = ble_device_callback
        self._adapter = adapter

        self._client: BleakClient | None = None
        self._keys: MeshKeys | None = None
        self._seq_store: SeqStore = seq_store if seq_store is not None else InMemorySeqStore()
        self._seq_lock = asyncio.Lock()
        self._tid = 0
        self._correlation_id = 0

        self._onoff_callbacks: list[OnOffCallback] = []
        self._vendor_callbacks: list[VendorCallback] = []
        self._composition_callbacks: list[CompositionCallback] = []
        self._disconnect_callbacks: list[DisconnectCallback] = []

        # Composition Data and firmware version
        self._composition: CompositionData | None = None
        self._firmware_version: str | None = None

        # Segmented message reassembly buffers: (src, dst, seq_zero, aid) -> buffer
        # Per BT Mesh spec, must include dst and aid to avoid collision
        self._segment_buffers: dict[tuple[int, int, int, int], _ReassemblyBuffer] = {}

        # Pending response futures: (opcode, correlation_id) -> Future(params)
        # Correlation ID prevents concurrent requests with same opcode from colliding
        self._pending_responses: dict[tuple[int, int], asyncio.Future[bytes]] = {}

        # Pending notify processing tasks (for lifecycle management)
        self._pending_notify_tasks: set[asyncio.Task] = set()

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

        Delegates to the configured seq_store.

        Args:
            seq: Sequence number to set.
        """
        self._seq_store.set_seq(seq)

    def get_seq(self) -> int:
        """Return the current sequence number (for persistence).

        Delegates to the configured seq_store.

        Returns:
            Current sequence number.
        """
        return self._seq_store.get_seq()

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
            ConnectionError: If BLE connection fails after all retries.
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
            ConnectionError: If BLE connection fails after all retries.
        """
        await self._load_keys()

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
                    scan_kwargs: dict[str, Any] = {"timeout": timeout}
                    if self._adapter is not None:
                        scan_kwargs["adapter"] = self._adapter
                    _LOGGER.debug(
                        "Scanning for %s (adapter=%s)",
                        self._address,
                        self._adapter or "default",
                    )
                    device = await BleakScanner.find_device_by_address(
                        self._address, **scan_kwargs
                    )
                if device is None:
                    msg = f"Device {self._address} not found"
                    raise MeshConnectionError(msg)

                client_kwargs: dict[str, Any] = {
                    "timeout": timeout,
                    "disconnected_callback": self._on_ble_disconnect,
                }
                if self._adapter is not None:
                    client_kwargs["adapter"] = self._adapter
                client = BleakClient(device, **client_kwargs)
                await client.connect()

                # Subscribe to Proxy Data Out notifications
                # Wrap in try/except: on some BlueZ versions, start_notify
                # triggers EOFError on the D-Bus connection for mesh devices.
                try:
                    await client.start_notify(SIG_MESH_PROXY_DATA_OUT, self._on_notify)
                except (EOFError, Exception) as notify_exc:
                    _LOGGER.warning(
                        "Notification subscription failed for %s: %s (%s) — "
                        "device will work but won't receive push status updates",
                        self._address,
                        notify_exc,
                        type(notify_exc).__name__,
                    )

                self._client = client
                _LOGGER.info("Connected to %s", self._address)

                # Request Composition Data (non-critical)
                try:
                    await self.request_composition_data()
                except (TimeoutError, SIGMeshError, BleakError):
                    _LOGGER.debug(
                        "Composition Data request failed (non-critical)",
                        exc_info=True,
                    )
                return

            except (BleakError, MeshConnectionError, OSError) as exc:
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
            except (AttributeError, TypeError):
                pass  # Frozen dataclass, best effort only
            self._keys = None

        # Cancel all pending notify tasks
        for task in self._pending_notify_tasks:
            task.cancel()
        if self._pending_notify_tasks:
            await asyncio.gather(*self._pending_notify_tasks, return_exceptions=True)
        self._pending_notify_tasks.clear()

        _LOGGER.info("Disconnected from %s", self._address)

    def _log_notify_exception(self, task: asyncio.Task) -> None:
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
            except (BleakError, OSError) as exc:
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

    async def send_vendor_command(self, access_payload: bytes) -> None:
        """Send a Tuya vendor model command (uses AppKey encryption).

        Args:
            access_payload: Complete access layer payload including opcode bytes.

        Raises:
            SIGMeshError: If not connected or keys not loaded.
            MeshConnectionError: If BLE write fails.
        """
        if self._client is None or self._keys is None:
            msg = "Not connected"
            raise SIGMeshError(msg)

        app_key = self._keys.app_key
        if app_key is None:
            msg = "No application key loaded"
            raise SIGMeshKeyError(msg)

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
            "Vendor command sent to 0x%04X (opcode=%s, seq=%d, %d bytes)",
            self._target_addr,
            access_payload[:3].hex(),
            seq,
            len(access_payload),
        )

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
        concurrent callers. Delegates to seq_store.

        Raises:
            SIGMeshError: If sequence number exhausted (> 0xFFFFFF).
        """
        async with self._seq_lock:
            seq = self._seq_store.get_seq()
            if seq > 0xFFFFFF:
                msg = "Sequence number exhausted — reconnect required"
                raise SIGMeshError(msg)
            self._seq_store.set_seq(seq + 1)
            return seq

    async def _next_seqs(self, n: int) -> int:
        """Reserve n consecutive sequence numbers and return the first.

        Used for segmented messages that need a contiguous seq range.
        Wraps at 24-bit boundary per SIG Mesh spec. Delegates to seq_store.

        Args:
            n: Number of sequence numbers to reserve.

        Returns:
            First sequence number of the reserved range.

        Raises:
            SIGMeshError: If sequence number exhausted (> 0xFFFFFF).
        """
        async with self._seq_lock:
            seq = self._seq_store.get_seq()
            if seq > 0xFFFFFF or (seq + n) > 0xFFFFFF:
                msg = "Sequence number exhausted — reconnect required"
                raise SIGMeshError(msg)
            self._seq_store.set_seq(seq + n)
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
        corr_id = self._correlation_id
        self._correlation_id += 1
        resp_key = (_OPCODE_APPKEY_STATUS, corr_id)
        self._pending_responses[resp_key] = future

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
                "AppKey Add sent to 0x%04X (%d segments, seq_start=%d, corr_id=%d)",
                self._target_addr,
                len(segments),
                seq_start,
                corr_id,
            )

            params = await asyncio.wait_for(asyncio.shield(future), timeout=response_timeout)
        except TimeoutError:
            msg = "Timeout waiting for AppKey Status response"
            raise SIGMeshError(msg) from None
        finally:
            self._pending_responses.pop(resp_key, None)

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
        corr_id = self._correlation_id
        self._correlation_id += 1
        resp_key = (_OPCODE_MODEL_APP_STATUS, corr_id)
        self._pending_responses[resp_key] = future_bind

        try:
            await self._client.write_gatt_char(SIG_MESH_PROXY_DATA_IN, proxy_pdu, response=False)
            _LOGGER.info(
                "Model App Bind sent: element=0x%04X app_idx=%d model=0x%04X (seq=%d, corr_id=%d)",
                element_addr,
                app_idx,
                model_id,
                seq,
                corr_id,
            )

            params_bind = await asyncio.wait_for(
                asyncio.shield(future_bind), timeout=response_timeout
            )
        except TimeoutError:
            msg = "Timeout waiting for Model App Status response"
            raise SIGMeshError(msg) from None
        finally:
            self._pending_responses.pop(resp_key, None)

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
        except (SecretAccessError, OSError, ValueError) as exc:
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
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._process_notify(data_copy))
            self._pending_notify_tasks.add(task)
            task.add_done_callback(self._pending_notify_tasks.discard)
            task.add_done_callback(self._log_notify_exception)
        except RuntimeError:
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
        except (MalformedPacketError, ValueError):
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
        except (MalformedPacketError, ValueError):
            _LOGGER.debug("Failed to parse segment header", exc_info=True)
            return

        # Per BT Mesh spec: buffer key must include src, dst, seq_zero, and aid
        buf_key = (src, dst, seg_hdr.seq_zero, seg_hdr.aid)

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

    def _complete_reassembly(self, buf_key: tuple[int, int, int, int]) -> None:
        """Decrypt a fully reassembled segmented message and dispatch.

        Args:
            buf_key: (src, dst, seq_zero, aid) key into _segment_buffers.
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
        except (MalformedPacketError, ValueError):
            _LOGGER.debug("Failed to parse access opcode", exc_info=True)
            return

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
                except BaseException:  # noqa: S110
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
                except BaseException:  # noqa: S110
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
        except (MalformedPacketError, ValueError):
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
            except BaseException:  # noqa: S110
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
            except BaseException:  # noqa: S110
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
        except (OSError, asyncio.TimeoutError):
            _LOGGER.debug("bluetoothctl remove failed (ignored)", exc_info=True)
