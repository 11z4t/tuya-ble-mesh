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
from collections.abc import Callable
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic

from tuya_ble_mesh.exceptions import (
    ConnectionError as MeshConnectionError,
)
from tuya_ble_mesh.exceptions import (
    SIGMeshError,
    SIGMeshKeyError,
)
from tuya_ble_mesh.sig_mesh_protocol import (
    MeshKeys,
    decrypt_access_payload,
    decrypt_network_pdu,
    encrypt_network_pdu,
    generic_onoff_set,
    make_access_unsegmented,
    make_proxy_pdu,
    parse_access_opcode,
    parse_proxy_pdu,
)

_LOGGER = logging.getLogger(__name__)

# SIG Mesh GATT Proxy UUIDs
SIG_MESH_PROXY_SERVICE = "00001828-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROXY_DATA_IN = "00002add-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROXY_DATA_OUT = "00002ade-0000-1000-8000-00805f9b34fb"

# GenericOnOff Status opcode
_OPCODE_ONOFF_STATUS = 0x8204

# Callback types
OnOffCallback = Callable[[bool], Any]
DisconnectCallback = Callable[[], Any]

# Initial sequence number (in-memory, not persisted)
_INITIAL_SEQ = 2000

# Default TTL for mesh commands
_DEFAULT_TTL = 5


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
    ) -> None:
        """Initialize a SIG Mesh device interface.

        Args:
            address: BLE MAC address (e.g. ``DC:23:4D:21:43:A5``).
            target_addr: Target unicast address (e.g. 0x00AA).
            our_addr: Our unicast address (e.g. 0x0001).
            secrets: SecretsManager instance for key loading.
            op_item_prefix: 1Password item name prefix for keys.
            iv_index: Mesh IV Index.
        """
        self._address = address.upper()
        self._target_addr = target_addr
        self._our_addr = our_addr
        self._secrets = secrets
        self._op_item_prefix = op_item_prefix
        self._iv_index = iv_index

        self._client: BleakClient | None = None
        self._keys: MeshKeys | None = None
        self._seq = _INITIAL_SEQ
        self._tid = 0

        self._onoff_callbacks: list[OnOffCallback] = []
        self._disconnect_callbacks: list[DisconnectCallback] = []

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
        """Return firmware version (not available for SIG Mesh v1)."""
        return None

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
                device = await BleakScanner.find_device_by_address(self._address, timeout=timeout)
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

        # Zero key material
        self._keys = None
        _LOGGER.info("Disconnected from %s", self._address)

    async def send_power(self, on: bool) -> None:
        """Send GenericOnOff Set command.

        Args:
            on: True to turn on, False to turn off.

        Raises:
            SIGMeshError: If not connected or keys not loaded.
        """
        if self._client is None or self._keys is None:
            msg = "Not connected"
            raise SIGMeshError(msg)

        access_payload = generic_onoff_set(on, self._tid)
        self._tid = (self._tid + 1) & 0xFF

        seq = self._next_seq()
        app_key = self._keys.app_key
        if app_key is None:
            msg = "No application key loaded"
            raise SIGMeshKeyError(msg)

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

        await self._client.write_gatt_char(SIG_MESH_PROXY_DATA_IN, proxy_pdu, response=False)
        _LOGGER.info(
            "GenericOnOff %s sent to 0x%04X (seq=%d)",
            "ON" if on else "OFF",
            self._target_addr,
            seq,
        )

    # --- Private helpers ---

    def _next_seq(self) -> int:
        """Return and increment the sequence number."""
        seq = self._seq
        self._seq += 1
        return seq

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

        Decrypts the network PDU and dispatches GenericOnOff Status
        to registered callbacks.

        Args:
            _sender: The characteristic that sent the notification.
            data: Raw proxy PDU bytes.
        """
        if self._keys is None:
            return

        try:
            proxy = parse_proxy_pdu(bytes(data))
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
        if access_msg is None or access_msg.access_payload is None:
            _LOGGER.debug(
                "Access payload decryption failed (seg=%s)",
                access_msg.seg if access_msg else "N/A",
            )
            return

        try:
            opcode, params = parse_access_opcode(access_msg.access_payload)
        except Exception:
            _LOGGER.debug("Failed to parse access opcode", exc_info=True)
            return

        if opcode == _OPCODE_ONOFF_STATUS and params:
            on_state = bool(params[0])
            _LOGGER.info(
                "GenericOnOff Status from 0x%04X: %s",
                net_pdu.src,
                "ON" if on_state else "OFF",
            )
            for callback in self._onoff_callbacks:
                try:
                    callback(on_state)
                except Exception:
                    _LOGGER.warning("OnOff callback error", exc_info=True)
        else:
            _LOGGER.debug(
                "Received opcode 0x%04X (%d param bytes) from 0x%04X",
                opcode,
                len(params),
                net_pdu.src,
            )

    def _on_ble_disconnect(self, _client: BleakClient) -> None:
        """Handle BLE disconnection event.

        Args:
            _client: The disconnected BleakClient.
        """
        _LOGGER.warning("SIG Mesh device disconnected: %s", self._address)
        self._client = None
        for callback in self._disconnect_callbacks:
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
