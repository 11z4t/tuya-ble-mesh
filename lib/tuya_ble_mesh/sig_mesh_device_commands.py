"""SIG Mesh device command methods mixin.

Provides command-sending methods (GenericOnOff, vendor, config) for ``SIGMeshDevice``.
Separated into mixin for maintainability (PLAT-600).

All command methods assume the parent class provides:
- ``_client: BleakClient | None``
- ``_keys: MeshKeys | None``
- ``_target_addr: int``
- ``_our_addr: int``
- ``_tid: int``
- ``_correlation_id: int``
- ``_seq_lock: asyncio.Lock``
- ``_segment_lock: asyncio.Lock``
- ``_pending_responses: dict[tuple[int, int], asyncio.Future[bytes]]``
- ``_next_seq() -> int``
- ``_next_seqs(n: int) -> int``
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from bleak.exc import BleakError

from tuya_ble_mesh.const import (
    DEFAULT_SIG_MESH_MAX_RETRIES,
    DEFAULT_SIG_MESH_RESPONSE_TIMEOUT,
    SIG_MESH_ONOFF_RESPONSE_TIMEOUT,
    STATUS_WAIT_POLL_INTERVAL,
)
from tuya_ble_mesh.exceptions import (
    ConnectionError as MeshConnectionError,
)
from tuya_ble_mesh.exceptions import (
    SIGMeshError,
    SIGMeshKeyError,
)
from tuya_ble_mesh.logging_context import MeshLogAdapter
from tuya_ble_mesh.sig_mesh_protocol import (
    SEG_DATA_SIZE,
    config_appkey_add,
    config_composition_get,
    config_model_app_bind,
    encrypt_network_pdu,
    generic_onoff_set,
    make_access_segmented,
    make_access_unsegmented,
    make_proxy_pdu,
)

if TYPE_CHECKING:
    from bleak import BleakClient

    from tuya_ble_mesh.sig_mesh_protocol import MeshKeys

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# SIG Mesh GATT Proxy UUIDs
SIG_MESH_PROXY_DATA_IN = "00002add-0000-1000-8000-00805f9b34fb"

# Opcodes for status responses
_OPCODE_APPKEY_STATUS = 0x8003
_OPCODE_MODEL_APP_STATUS = 0x803E

# Default TTL for mesh commands
_DEFAULT_TTL = 5

# BLE write retry backoff parameters
_BLE_WRITE_RETRY_INITIAL_BACKOFF = 1.0
_BLE_WRITE_RETRY_BACKOFF_MULTIPLIER = 2.0


class SIGMeshDeviceCommandsMixin:
    """Mixin providing command-sending methods for SIG Mesh devices."""

    # Type hints for attributes provided by parent class
    _client: BleakClient | None
    _keys: MeshKeys | None
    _target_addr: int
    _our_addr: int
    _tid: int
    _correlation_id: int
    _seq_lock: asyncio.Lock
    _segment_lock: asyncio.Lock
    _pending_responses: dict[tuple[int, int], asyncio.Future[bytes]]
    _address: str

    async def send_power(
        self, on: bool, *, max_retries: int = DEFAULT_SIG_MESH_MAX_RETRIES
    ) -> None:
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
        backoff = _BLE_WRITE_RETRY_INITIAL_BACKOFF

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
                backoff *= _BLE_WRITE_RETRY_BACKOFF_MULTIPLIER

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

        await self._client.write_gatt_char(SIG_MESH_PROXY_DATA_IN, proxy_pdu, response=False)

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

    async def _next_seq(self) -> int:
        """Return and increment the sequence number (24-bit wrap).

        Protected by asyncio.Lock to prevent nonce collision from
        concurrent callers. Delegates to seq_store.

        Raises:
            SIGMeshError: If sequence number exhausted (> 0xFFFFFF).
        """
        async with self._seq_lock:
            seq = self._seq_store.get_seq()  # type: ignore[attr-defined]
            if seq > 0xFFFFFF:
                msg = "Sequence number exhausted — reconnect required"
                raise SIGMeshError(msg)
            self._seq_store.set_seq(seq + 1)  # type: ignore[attr-defined]
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
            seq = self._seq_store.get_seq()  # type: ignore[attr-defined]
            if seq > 0xFFFFFF or (seq + n) > 0xFFFFFF:
                msg = "Sequence number exhausted — reconnect required"
                raise SIGMeshError(msg)
            self._seq_store.set_seq(seq + n)  # type: ignore[attr-defined]
            return seq

    async def send_config_appkey_add(
        self,
        app_key: bytes,
        *,
        net_idx: int = 0,
        app_idx: int = 0,
        response_timeout: float = DEFAULT_SIG_MESH_RESPONSE_TIMEOUT,
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

        # CF-1: Register response future BEFORE sending (protected by lock)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        async with self._segment_lock:
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
                await asyncio.sleep(STATUS_WAIT_POLL_INTERVAL)

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
            # CF-1: Cleanup response future (protected by lock)
            async with self._segment_lock:
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
        response_timeout: float = SIG_MESH_ONOFF_RESPONSE_TIMEOUT,
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

        # CF-1: Register response future BEFORE sending (protected by lock)
        loop = asyncio.get_running_loop()
        future_bind: asyncio.Future[bytes] = loop.create_future()
        async with self._segment_lock:
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
            # CF-1: Cleanup response future (protected by lock)
            async with self._segment_lock:
                self._pending_responses.pop(resp_key, None)

        status_bind = params_bind[0] if params_bind else 0xFF
        _LOGGER.info(
            "Model App Status from 0x%04X: 0x%02X (%s)",
            self._target_addr,
            status_bind,
            "Success" if status_bind == 0x00 else "Error",
        )
        return status_bind == 0x00
