"""SIG Mesh device command methods.

Provides ``SIGMeshDeviceCommandsMixin`` which handles:

- GenericOnOff Set commands with retry and exponential backoff
- Tuya vendor model commands
- Config Composition Data Get
- Config AppKey Add (segmented transport)
- Config Model App Bind
- Read-only node feature probes (relay, proxy, friend, beacon, TTL, transmit)

This mixin is not intended for standalone use — it requires attributes
defined in ``SIGMeshDevice.__init__``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tuya_ble_mesh.const import (
    DEFAULT_SIG_MESH_MAX_RETRIES,
    DEFAULT_SIG_MESH_RESPONSE_TIMEOUT,
    SIG_MESH_ONOFF_RESPONSE_TIMEOUT,
    STATUS_WAIT_POLL_INTERVAL,
)
from tuya_ble_mesh.exceptions import (
    MalformedPacketError,
    MeshConnectionError,
    SIGMeshError,
    SIGMeshKeyError,
)
from tuya_ble_mesh.logging_context import MeshLogAdapter
from tuya_ble_mesh.sig_mesh_device_segments import (
    _OPCODE_APPKEY_STATUS,
    _OPCODE_MODEL_APP_STATUS,
)
from tuya_ble_mesh.sig_mesh_protocol import (
    MAX_UNSEG_ACCESS_PAYLOAD,
    OP_CONFIG_COMPOSITION_STATUS,
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
from tuya_ble_mesh.sig_mesh_protocol_codec import (
    FEATURE_STATE_NAMES,
    MODEL_HEALTH_SERVER,
    OP_CONFIG_BEACON_GET,
    OP_CONFIG_BEACON_STATUS,
    OP_CONFIG_DEFAULT_TTL_GET,
    OP_CONFIG_DEFAULT_TTL_STATUS,
    OP_CONFIG_FRIEND_GET,
    OP_CONFIG_FRIEND_STATUS,
    OP_CONFIG_GATT_PROXY_GET,
    OP_CONFIG_GATT_PROXY_STATUS,
    OP_CONFIG_MODEL_PUB_STATUS,
    OP_CONFIG_NETWORK_TRANSMIT_GET,
    OP_CONFIG_NETWORK_TRANSMIT_STATUS,
    OP_CONFIG_NODE_RESET,
    OP_CONFIG_NODE_RESET_STATUS,
    OP_CONFIG_RELAY_GET,
    OP_CONFIG_RELAY_STATUS,
    OP_HEALTH_ATTENTION_STATUS,
    config_model_app_bind_vendor,
    config_model_pub_set,
    config_state_get,
    health_attention_set,
    parse_relay_status,
    parse_single_byte_status,
    parse_transmit_byte,
    segment_ack,
)

if TYPE_CHECKING:
    from tuya_ble_mesh.sig_mesh_protocol import MeshKeys

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# SIG Mesh GATT Proxy Data In UUID (same as in sig_mesh_device.py)
SIG_MESH_PROXY_DATA_IN = "00002add-0000-1000-8000-00805f9b34fb"

# Default TTL for mesh commands
_DEFAULT_TTL = 5

# BLE write retry backoff parameters
_BLE_WRITE_RETRY_INITIAL_BACKOFF = 1.0
_BLE_WRITE_RETRY_BACKOFF_MULTIPLIER = 2.0


class SIGMeshDeviceCommandsMixin:
    """Mixin providing command methods for SIGMeshDevice.

    Requires attributes defined in ``SIGMeshDevice.__init__``:
    ``_client``, ``_keys``, ``_address``, ``_target_addr``,
    ``_our_addr``, ``_tid``, ``_correlation_id``, ``_segment_lock``,
    ``_pending_responses``.

    Also requires ``_next_seq()`` and ``_next_seqs()`` methods.
    """

    # Type stubs for attributes defined in SIGMeshDevice.__init__
    _client: Any
    _keys: MeshKeys | None
    _address: str
    _target_addr: int
    _our_addr: int
    _tid: int
    _correlation_id: int
    _segment_lock: asyncio.Lock
    _pending_responses: dict[tuple[int, int], asyncio.Future[bytes]]

    async def _next_seq(self) -> int:
        raise NotImplementedError

    async def _next_seqs(self, n: int) -> int:
        raise NotImplementedError

    async def _write_proxy(self, data: bytes) -> None:
        """Write a proxy PDU to Mesh Proxy Data In.

        Uses the characteristic object resolved at connect time when available
        (handles devices/caches exposing the UUID more than once).
        """
        char = getattr(self, "_data_in_char", None)
        await self._client.write_gatt_char(
            char if char is not None else SIG_MESH_PROXY_DATA_IN, data, response=False
        )

    async def _send_access(
        self,
        access_payload: bytes,
        *,
        use_dev_key: bool,
        dst: int | None = None,
        expect_opcode: int | None = None,
        response_timeout: float = DEFAULT_SIG_MESH_RESPONSE_TIMEOUT,
    ) -> bytes | None:
        """Encrypt and send an access message, optionally awaiting a reply.

        Chooses unsegmented or segmented transport based on payload length.

        Args:
            access_payload: Opcode + parameters.
            use_dev_key: True for Config messages (device key), False for app key.
            dst: Destination address (defaults to the device's primary unicast).
            expect_opcode: If set, wait for a message with this opcode and return
                its parameters.
            response_timeout: Seconds to wait for the reply.

        Returns:
            Reply parameters if ``expect_opcode`` was given, else None.

        Raises:
            SIGMeshError: If not connected, keys missing, or reply timed out.
        """
        if self._client is None or self._keys is None:
            msg = "Not connected"
            raise SIGMeshError(msg)
        if use_dev_key:
            key, akf, aid = self._keys.dev_key, 0, 0
        else:
            if self._keys.app_key is None:
                msg = "No application key loaded"
                raise SIGMeshKeyError(msg)
            key, akf, aid = self._keys.app_key, 1, self._keys.aid
        target = self._target_addr if dst is None else dst

        if len(access_payload) <= MAX_UNSEG_ACCESS_PAYLOAD:
            seq = await self._next_seq()
            pdus = [
                (
                    seq,
                    make_access_unsegmented(
                        key,
                        self._our_addr,
                        target,
                        seq,
                        self._keys.iv_index,
                        access_payload,
                        akf=akf,
                        aid=aid,
                    ),
                )
            ]
        else:
            n_segs = (len(access_payload) + 4 + SEG_DATA_SIZE - 1) // SEG_DATA_SIZE
            seq_start = await self._next_seqs(n_segs)
            pdus = make_access_segmented(
                key,
                self._our_addr,
                target,
                seq_start,
                self._keys.iv_index,
                access_payload,
                akf=akf,
                aid=aid,
            )

        future: asyncio.Future[bytes] | None = None
        resp_key: tuple[int, int] | None = None
        if expect_opcode is not None:
            future = asyncio.get_running_loop().create_future()
            async with self._segment_lock:
                resp_key = (expect_opcode, self._correlation_id)
                self._correlation_id += 1
                self._pending_responses[resp_key] = future

        try:
            for i, (seg_seq, transport_pdu) in enumerate(pdus):
                network_pdu = encrypt_network_pdu(
                    self._keys.enc_key,
                    self._keys.priv_key,
                    self._keys.nid,
                    ctl=0,
                    ttl=_DEFAULT_TTL,
                    seq=seg_seq,
                    src=self._our_addr,
                    dst=target,
                    transport_pdu=transport_pdu,
                    iv_index=self._keys.iv_index,
                )
                await self._write_proxy(make_proxy_pdu(network_pdu))
                if len(pdus) > 1 and i < len(pdus) - 1:
                    await asyncio.sleep(STATUS_WAIT_POLL_INTERVAL)
            _LOGGER.debug(
                "Access message sent to 0x%04X (opcode=%s, %d PDU(s), %s key)",
                target,
                access_payload[:2].hex(),
                len(pdus),
                "dev" if use_dev_key else "app",
            )
            if future is None:
                return None
            return await asyncio.wait_for(asyncio.shield(future), timeout=response_timeout)
        except TimeoutError:
            msg = f"Timeout waiting for opcode 0x{expect_opcode or 0:04X} from 0x{target:04X}"
            raise SIGMeshError(msg) from None
        finally:
            if resp_key is not None:
                async with self._segment_lock:
                    self._pending_responses.pop(resp_key, None)

    async def _send_segment_ack(self, dst: int, seq_zero: int, block_ack: int) -> None:
        """Send a Segment Acknowledgment control message to ``dst``."""
        if self._client is None or self._keys is None:
            return
        seq = await self._next_seq()
        network_pdu = encrypt_network_pdu(
            self._keys.enc_key,
            self._keys.priv_key,
            self._keys.nid,
            ctl=1,
            ttl=_DEFAULT_TTL,
            seq=seq,
            src=self._our_addr,
            dst=dst,
            transport_pdu=segment_ack(seq_zero, block_ack),
            iv_index=self._keys.iv_index,
        )
        await self._write_proxy(make_proxy_pdu(network_pdu))
        _LOGGER.debug("Segment Ack sent to 0x%04X (seq_zero=%d)", dst, seq_zero)

    async def get_composition_data(
        self, *, response_timeout: float = DEFAULT_SIG_MESH_RESPONSE_TIMEOUT
    ) -> bytes:
        """Send Config Composition Data Get (page 0) and await the Status.

        The parsed result is also stored on the device and delivered to
        composition callbacks.

        Returns:
            Raw Composition Data Status parameters.
        """
        params = await self._send_access(
            config_composition_get(page=0),
            use_dev_key=True,
            expect_opcode=OP_CONFIG_COMPOSITION_STATUS,
            response_timeout=response_timeout,
        )
        return params or b""

    async def probe_node_features(
        self, *, response_timeout: float = SIG_MESH_ONOFF_RESPONSE_TIMEOUT
    ) -> dict[str, str]:
        """Read the node's relay/proxy/friend/beacon/TTL/transmit state.

        Read-only (Config ``* Get`` only). Each probe is independent: a probe
        that times out or fails to parse is reported as ``"no_reply"`` /
        ``"malformed"`` rather than aborting the rest.

        Returns:
            Human-readable state per feature, e.g. ``{"relay": "enabled
            (2x @ 20ms)", "gatt_proxy": "enabled", ...}``.
        """

        def _feature(value: int) -> str:
            return str(FEATURE_STATE_NAMES.get(value, f"0x{value:02X}"))

        def _relay(params: bytes) -> str:
            st = parse_relay_status(params)
            rt = st.retransmit
            return f"{_feature(st.relay)} ({rt.transmissions}x @ {rt.interval_ms}ms)"

        def _transmit(params: bytes) -> str:
            tx = parse_transmit_byte(parse_single_byte_status(params, "Network Transmit"))
            return f"{tx.transmissions}x @ {tx.interval_ms}ms"

        probes: list[tuple[str, int, int, Callable[[bytes], str]]] = [
            ("relay", OP_CONFIG_RELAY_GET, OP_CONFIG_RELAY_STATUS, _relay),
            (
                "gatt_proxy",
                OP_CONFIG_GATT_PROXY_GET,
                OP_CONFIG_GATT_PROXY_STATUS,
                lambda p: _feature(parse_single_byte_status(p, "GATT Proxy")),
            ),
            (
                "friend",
                OP_CONFIG_FRIEND_GET,
                OP_CONFIG_FRIEND_STATUS,
                lambda p: _feature(parse_single_byte_status(p, "Friend")),
            ),
            (
                "secure_beacon",
                OP_CONFIG_BEACON_GET,
                OP_CONFIG_BEACON_STATUS,
                lambda p: _feature(parse_single_byte_status(p, "Beacon")),
            ),
            (
                "default_ttl",
                OP_CONFIG_DEFAULT_TTL_GET,
                OP_CONFIG_DEFAULT_TTL_STATUS,
                lambda p: str(parse_single_byte_status(p, "Default TTL")),
            ),
            (
                "network_transmit",
                OP_CONFIG_NETWORK_TRANSMIT_GET,
                OP_CONFIG_NETWORK_TRANSMIT_STATUS,
                _transmit,
            ),
        ]
        result: dict[str, str] = {}
        for name, get_op, status_op, decode in probes:
            try:
                params = await self._send_access(
                    config_state_get(get_op),
                    use_dev_key=True,
                    expect_opcode=status_op,
                    response_timeout=response_timeout,
                )
            except SIGMeshError:
                if self._client is None:
                    raise
                result[name] = "no_reply"
                continue
            try:
                result[name] = decode(params or b"")
            except MalformedPacketError:
                result[name] = "malformed"
        return result

    async def send_config_node_reset(
        self, *, response_timeout: float = SIG_MESH_ONOFF_RESPONSE_TIMEOUT
    ) -> bool:
        """Tell the node to forget its keys and become unprovisioned.

        Sent with the device key. The node answers Config Node Reset Status
        and then wipes itself, after which it advertises the Provisioning
        service (0x1827) again.

        Returns:
            True if the node acknowledged the reset, False on no reply (the
            reset may still have happened — check for its 0x1827 advert).
        """
        try:
            await self._send_access(
                config_state_get(OP_CONFIG_NODE_RESET),
                use_dev_key=True,
                expect_opcode=OP_CONFIG_NODE_RESET_STATUS,
                response_timeout=response_timeout,
            )
        except SIGMeshError:
            if self._client is None:
                raise
            _LOGGER.warning("No Node Reset Status from 0x%04X", self._target_addr)
            return False
        _LOGGER.info("Node 0x%04X acknowledged Config Node Reset", self._target_addr)
        return True

    async def send_config_model_app_bind_vendor(
        self,
        element_addr: int,
        app_idx: int,
        cid: int,
        model_id: int,
        *,
        response_timeout: float = SIG_MESH_ONOFF_RESPONSE_TIMEOUT,
    ) -> bool:
        """Bind an application key to a vendor model and await the Status."""
        params = await self._send_access(
            config_model_app_bind_vendor(element_addr, app_idx, cid, model_id),
            use_dev_key=True,
            expect_opcode=_OPCODE_MODEL_APP_STATUS,
            response_timeout=response_timeout,
        )
        status = params[0] if params else 0xFF
        _LOGGER.info(
            "Model App Status (vendor 0x%04X:0x%04X @0x%04X): 0x%02X",
            cid,
            model_id,
            element_addr,
            status,
        )
        return status == 0x00

    async def send_config_model_pub_set(
        self,
        element_addr: int,
        publish_addr: int,
        app_idx: int,
        model_id: int,
        *,
        response_timeout: float = DEFAULT_SIG_MESH_RESPONSE_TIMEOUT,
    ) -> bool:
        """Configure a SIG model to publish its status to ``publish_addr``."""
        params = await self._send_access(
            config_model_pub_set(element_addr, publish_addr, app_idx, model_id),
            use_dev_key=True,
            expect_opcode=OP_CONFIG_MODEL_PUB_STATUS,
            response_timeout=response_timeout,
        )
        status = params[0] if params else 0xFF
        _LOGGER.info(
            "Model Publication Status (model 0x%04X @0x%04X -> 0x%04X): 0x%02X",
            model_id,
            element_addr,
            publish_addr,
            status,
        )
        return status == 0x00

    async def send_attention(self, seconds: int = 5) -> bool:
        """Make the node blink by itself for ``seconds`` (Health Attention Set).

        Nodes provisioned before the Health Server was app-bound don't answer;
        in that case the Health Server is bound (device key) and the request
        retried once.

        Returns:
            True if the node acknowledged the attention timer.
        """
        payload = health_attention_set(max(0, min(int(seconds), 0xFF)))
        for attempt in (1, 2):
            try:
                await self._send_access(
                    payload,
                    use_dev_key=False,
                    expect_opcode=OP_HEALTH_ATTENTION_STATUS,
                    response_timeout=SIG_MESH_ONOFF_RESPONSE_TIMEOUT,
                )
                _LOGGER.info("Attention %ds started on 0x%04X", seconds, self._target_addr)
                return True
            except SIGMeshError:
                if attempt == 2:
                    break
                _LOGGER.info("No Attention Status; binding Health Server and retrying")
                try:
                    await self.send_config_model_app_bind(self._target_addr, 0, MODEL_HEALTH_SERVER)
                except SIGMeshError:
                    break
        _LOGGER.warning("Node 0x%04X did not acknowledge Attention", self._target_addr)
        return False

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

                await self._write_proxy(proxy_pdu)
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
        # _send_access picks segmented transport for payloads > 11 bytes
        # (e.g. the 13-byte Tuya timestamp sync response).
        await self._send_access(access_payload, use_dev_key=False)

        _LOGGER.info(
            "Vendor command sent to 0x%04X (opcode=%s, %d bytes)",
            self._target_addr,
            access_payload[:3].hex(),
            len(access_payload),
        )

    async def request_composition_data(self) -> None:
        """Send Config Composition Data Get to retrieve device info.

        Uses the device key (akf=0) since this is a config message.

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
        await self._write_proxy(proxy_pdu)
        _LOGGER.info(
            "Composition Data Get sent to 0x%04X (seq=%d)",
            self._target_addr,
            seq,
        )

    async def send_config_appkey_add(
        self,
        app_key: bytes,
        *,
        net_idx: int = 0,
        app_idx: int = 0,
        response_timeout: float = DEFAULT_SIG_MESH_RESPONSE_TIMEOUT,
    ) -> bool:
        """Send Config AppKey Add and wait for Status response.

        Uses segmented transport (2 segments) and device key (akf=0).

        Args:
            app_key: 16-byte application key to add.
            net_idx: Network key index (0-4095).
            app_idx: Application key index (0-4095).
            response_timeout: Seconds to wait for AppKey Status response.

        Returns:
            True if device responded with Success (0x00), False otherwise.

        Raises:
            SIGMeshError: If not connected or keys not loaded.
        """
        if self._client is None or self._keys is None:
            msg = "Not connected"
            raise SIGMeshError(msg)

        access_payload = config_appkey_add(net_idx, app_idx, app_key)
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
                await self._write_proxy(proxy_pdu)
                await asyncio.sleep(STATUS_WAIT_POLL_INTERVAL)

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
        """Send Config Model App Bind and wait for Status.

        Uses unsegmented transport and device key (akf=0).

        Args:
            element_addr: Element unicast address.
            app_idx: Application key index to bind.
            model_id: SIG Model ID (e.g. 0x1000 for GenericOnOff Server).
            response_timeout: Seconds to wait for Model App Status response.

        Returns:
            True if device responded with Success (0x00), False otherwise.

        Raises:
            SIGMeshError: If not connected or keys not loaded.
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
            await self._write_proxy(proxy_pdu)
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


# Import BleakError at module level for send_power exception handling
try:
    from bleak.exc import BleakError
except ImportError:  # pragma: no cover
    BleakError = OSError
