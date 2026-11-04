"""Shared SIG Mesh network: one proxy link, many nodes.

``MeshNetwork`` holds the network-wide state — NetKey/AppKey, IV index, the
provisioner's single SEQ counter, the receive replay cache — and one
``GattProxyLink`` to whichever node is best heard. Nodes (``SIGMeshNode``
views, see ``sig_mesh_node``) attach to it; incoming network PDUs are
decrypted once here and routed to the node that owns the source address,
which then does upper-transport decryption with its own device key.

SECURITY: Key material is never logged; only addresses, NID and lengths.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from tuya_ble_mesh.exceptions import MalformedPacketError, MeshConnectionError, SIGMeshError
from tuya_ble_mesh.sig_mesh_address_allocator import range_contains
from tuya_ble_mesh.sig_mesh_protocol import (
    MeshKeys,
    NetworkPDU,
    decrypt_network_pdu,
    parse_proxy_pdu,
)
from tuya_ble_mesh.sig_mesh_protocol_codec import PROXY_TYPE_NETWORK
from tuya_ble_mesh.sig_mesh_proxy_link import BleDeviceCallback, GattProxyLink
from tuya_ble_mesh.sig_mesh_proxy_selector import ProxyCandidate, ProxySelector
from tuya_ble_mesh.sig_mesh_replay import ReplayCache

_LOGGER = logging.getLogger(__name__)

PROVISIONER_ADDR = 0x0001
DEFAULT_LINK_TIMEOUT = 20.0
# Try at most this many candidates per connect round
MAX_CANDIDATES_PER_ROUND = 3
SEQ_MAX = 0xFFFFFF

# Proxy-layer SAR (Mesh Profile 6.3.1)
_SAR_COMPLETE = 0
_SAR_FIRST = 1
_SAR_CONTINUATION = 2
_SAR_LAST = 3

# Placeholder device key for the network-level MeshKeys: only the network
# layer (NID/EncryptionKey/PrivacyKey) of that object is ever used.
_NO_DEV_KEY = "00" * 16

CandidateProvider = Callable[[], list[ProxyCandidate]]
LinkListener = Callable[[bool], None]


class SeqStoreLike(Protocol):
    """Sequence number persistence (same shape as ``sig_mesh_device.SeqStore``)."""

    def get_seq(self) -> int:
        """Return the next sequence number to use."""
        ...

    def set_seq(self, seq: int) -> None:
        """Store the next sequence number to use."""
        ...


class _MemorySeq:
    def __init__(self) -> None:
        self._seq = 0

    def get_seq(self) -> int:
        return self._seq

    def set_seq(self, seq: int) -> None:
        self._seq = seq


class NetworkNode(Protocol):
    """What ``MeshNetwork`` needs from an attached node."""

    @property
    def address(self) -> str:
        """Node MAC address."""
        ...

    @property
    def unicast(self) -> int:
        """Primary unicast address."""
        ...

    @property
    def element_count(self) -> int:
        """Number of elements (consecutive unicast addresses)."""
        ...

    def on_network_pdu(self, pdu: NetworkPDU) -> None:
        """Handle a network PDU whose source is this node."""
        ...

    def on_link_state(self, up: bool) -> None:
        """Handle the shared proxy link coming up or going down."""
        ...


class MeshNetwork:
    """One SIG Mesh network reached through a single GATT proxy link."""

    def __init__(
        self,
        net_key_hex: str,
        app_key_hex: str,
        *,
        iv_index: int = 0,
        our_addr: int = PROVISIONER_ADDR,
        seq_store: SeqStoreLike | None = None,
        candidate_provider: CandidateProvider | None = None,
        ble_device_callback: BleDeviceCallback | None = None,
        selector: ProxySelector | None = None,
        link_timeout: float = DEFAULT_LINK_TIMEOUT,
    ) -> None:
        """Create a network (not connected).

        Args:
            net_key_hex: NetKey (index 0), hex.
            app_key_hex: AppKey (index 0), hex.
            iv_index: Current IV index.
            our_addr: Provisioner unicast address (source of everything we send).
            seq_store: SEQ persistence; in-memory from 0 if None.
            candidate_provider: Returns nodes currently heard (for proxy choice).
                Defaults to "every attached node, unknown RSSI".
            ble_device_callback: ``address -> BLEDevice`` resolver (HA bluetooth).
            selector: Proxy ranking state.
            link_timeout: Seconds allowed per proxy connect attempt.
        """
        self._net_key_hex = net_key_hex
        self._app_key_hex = app_key_hex
        self._keys = MeshKeys(net_key_hex, _NO_DEV_KEY, app_key_hex, iv_index=iv_index)
        self._our_addr = our_addr
        self._seq_store: SeqStoreLike = seq_store if seq_store is not None else _MemorySeq()
        self._seq_lock = asyncio.Lock()
        self._candidate_provider = candidate_provider
        self._ble_device_callback = ble_device_callback
        self.selector = selector if selector is not None else ProxySelector()
        self._link_timeout = link_timeout
        self._link: GattProxyLink | None = None
        self._connect_task: asyncio.Task[None] | None = None
        self._nodes: dict[str, NetworkNode] = {}
        self._replay = ReplayCache()
        self._listeners: list[LinkListener] = []
        self._sar_buffer = b""
        self._closed = False

    # --- Properties ---

    @property
    def our_addr(self) -> int:
        """Return the provisioner's unicast address."""
        return self._our_addr

    @property
    def iv_index(self) -> int:
        """Return the current IV index."""
        return int(self._keys.iv_index)

    @property
    def nid(self) -> int:
        """Return the network's NID (safe to log)."""
        return int(self._keys.nid)

    @property
    def is_connected(self) -> bool:
        """Return True while the shared proxy link is up."""
        return self._link is not None and self._link.is_connected

    @property
    def active_proxy(self) -> str | None:
        """Return the MAC of the node currently carrying the link."""
        return self._link.address if self.is_connected and self._link is not None else None

    @property
    def link_rssi(self) -> int | None:
        """Return the proxy connection RSSI, if known."""
        return self._link.rssi if self._link is not None else None

    @property
    def nodes(self) -> list[NetworkNode]:
        """Return attached nodes."""
        return list(self._nodes.values())

    def get_seq(self) -> int:
        """Return the next SEQ that will be used."""
        return self._seq_store.get_seq()

    # --- Keys ---

    def node_keys(self, dev_key_hex: str) -> MeshKeys:
        """Build the key set a node view uses (network keys + its device key)."""
        return MeshKeys(
            self._net_key_hex, dev_key_hex, self._app_key_hex, iv_index=self._keys.iv_index
        )

    # --- Nodes ---

    def attach(self, node: NetworkNode) -> None:
        """Route PDUs from ``node``'s address range to it.

        Raises:
            SIGMeshError: If the range overlaps another attached node.
        """
        for other in self._nodes.values():
            if other.address == node.address:
                continue
            lo = max(other.unicast, node.unicast)
            hi = min(other.unicast + other.element_count, node.unicast + node.element_count)
            if lo < hi:
                msg = (
                    f"Unicast range 0x{node.unicast:04X}+{node.element_count} of "
                    f"{node.address} overlaps {other.address}"
                )
                raise SIGMeshError(msg)
        self._nodes[node.address] = node

    def detach(self, node: NetworkNode) -> None:
        """Stop routing to ``node`` (the link stays up for the others)."""
        if self._nodes.get(node.address) is node:
            del self._nodes[node.address]

    def forget_replay_state(self, unicast: int, element_count: int) -> None:
        """Drop replay state for a range (after a node reset/reprovision)."""
        self._replay.forget(unicast, element_count)

    def node_for(self, addr: int) -> NetworkNode | None:
        """Return the node whose element range contains ``addr``."""
        for node in self._nodes.values():
            if range_contains(node.unicast, node.element_count, addr):
                return node
        return None

    def add_link_listener(self, listener: LinkListener) -> Callable[[], None]:
        """Call ``listener(up)`` on link changes; returns an unsubscribe function."""
        self._listeners.append(listener)

        def _remove() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(listener)

        return _remove

    # --- Link management ---

    async def ensure_connected(self, timeout: float | None = None) -> None:
        """Bring the shared link up (single-flight across all callers).

        Raises:
            MeshConnectionError: If no proxy node could be connected.
        """
        if self.is_connected:
            return
        if self._closed:
            msg = "Mesh network is closed"
            raise MeshConnectionError(msg)
        task = self._connect_task
        if task is None or task.done():
            task = asyncio.get_running_loop().create_task(self._connect_round())
            task.add_done_callback(_consume_exception)
            self._connect_task = task
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError as exc:
            msg = "Timed out waiting for the mesh proxy link"
            raise MeshConnectionError(msg) from exc

    def _ranked_candidates(self) -> list[str]:
        if self._candidate_provider is not None:
            candidates = self._candidate_provider()
        else:
            candidates = [ProxyCandidate(n.address, None, _now()) for n in self._nodes.values()]
        ranked: list[str] = self.selector.rank(candidates)
        return ranked

    async def _connect_round(self, only: str | None = None) -> None:
        ranked = [only.upper()] if only is not None else self._ranked_candidates()
        if not ranked:
            msg = "No mesh proxy node in range"
            raise MeshConnectionError(msg)
        last_error: Exception | None = None
        for address in ranked[:MAX_CANDIDATES_PER_ROUND]:
            link = GattProxyLink(
                address,
                self._on_notify,
                self._on_link_dropped,
                ble_device_callback=self._ble_device_callback,
            )
            try:
                await link.connect(self._link_timeout)
            except MeshConnectionError as exc:
                last_error = exc
                self.selector.record_failure(address)
                _LOGGER.info("Mesh proxy candidate %s failed: %s", address, exc)
                continue
            if self._closed:
                await link.disconnect()
                msg = "Mesh network closed while connecting"
                raise MeshConnectionError(msg)
            self._link = link
            self._sar_buffer = b""
            self.selector.record_success(address)
            self._notify_link_state(True)
            return
        msg = f"No mesh proxy node reachable (tried {min(len(ranked), MAX_CANDIDATES_PER_ROUND)})"
        raise MeshConnectionError(msg) from last_error

    def _on_link_dropped(self, link: GattProxyLink) -> None:
        address: str = link.address
        if link is not self._link:
            return
        self._link = None
        self.selector.record_failure(address)
        self._notify_link_state(False)

    def _notify_link_state(self, up: bool) -> None:
        for node in list(self._nodes.values()):
            try:
                node.on_link_state(up)
            except Exception:
                _LOGGER.warning("Node link-state handler failed (%s)", node.address, exc_info=True)
        for listener in list(self._listeners):
            try:
                listener(up)
            except Exception:
                _LOGGER.warning("Mesh link listener failed", exc_info=True)

    async def switch_proxy(self) -> None:
        """Drop the current link and reconnect, avoiding the current proxy."""
        link = self._link
        if link is not None:
            self.selector.record_failure(link.address)
            self._link = None
            await link.disconnect()
            self._notify_link_state(False)
        await self.ensure_connected(self._link_timeout * MAX_CANDIDATES_PER_ROUND)

    async def use_proxy(self, address: str) -> None:
        """Carry the link through ``address`` (e.g. a node being configured).

        Raises:
            MeshConnectionError: If that node can't be connected.
        """
        if self.active_proxy == address.upper():
            return
        task = self._connect_task
        if task is not None and not task.done():
            with contextlib.suppress(MeshConnectionError):
                await asyncio.shield(task)
        link = self._link
        if link is not None:
            self._link = None
            await link.disconnect()
            self._notify_link_state(False)
        task = asyncio.get_running_loop().create_task(self._connect_round(only=address))
        task.add_done_callback(_consume_exception)
        self._connect_task = task
        await asyncio.shield(task)

    async def close(self) -> None:
        """Close the link for good (entry unload)."""
        self._closed = True
        task = self._connect_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, MeshConnectionError):
                await task
        link, self._link = self._link, None
        if link is not None:
            await link.disconnect()
            self._notify_link_state(False)

    # --- Send path ---

    async def write(self, proxy_pdu: bytes) -> None:
        """Write a proxy PDU to the active link.

        Raises:
            MeshConnectionError: If the link is down.
        """
        link = self._link
        if link is None:
            msg = "Mesh proxy link is down"
            raise MeshConnectionError(msg)
        await link.write(proxy_pdu)

    async def next_seqs(self, count: int = 1) -> int:
        """Reserve ``count`` consecutive SEQ values and return the first.

        Raises:
            SIGMeshError: If the 24-bit SEQ space is exhausted (IV Update needed).
        """
        async with self._seq_lock:
            seq = self._seq_store.get_seq()
            if seq + count - 1 > SEQ_MAX:
                msg = "Sequence number exhausted — IV Update required"
                raise SIGMeshError(msg)
            self._seq_store.set_seq(seq + count)
            return seq

    # --- Receive path ---

    def _on_notify(self, data: bytes) -> None:
        try:
            proxy = parse_proxy_pdu(data)
        except MalformedPacketError:
            return
        if proxy.sar == _SAR_FIRST:
            self._sar_buffer = proxy.payload
            return
        if proxy.sar == _SAR_CONTINUATION:
            self._sar_buffer += proxy.payload
            return
        payload = proxy.payload
        if proxy.sar == _SAR_LAST:
            payload, self._sar_buffer = self._sar_buffer + proxy.payload, b""
        if proxy.pdu_type != PROXY_TYPE_NETWORK:
            # Secure Network Beacons / proxy configuration: not used yet
            _LOGGER.debug("Proxy PDU type 0x%02X ignored", proxy.pdu_type)
            return
        self.handle_network_pdu_bytes(payload)

    def handle_network_pdu_bytes(self, payload: bytes) -> NetworkPDU | None:
        """Decrypt, replay-check and route one network PDU.

        Returns:
            The routed PDU, or None if it was dropped.
        """
        pdu = decrypt_network_pdu(
            self._keys.enc_key,
            self._keys.priv_key,
            self._keys.nid,
            payload,
            iv_index=self._keys.iv_index,
        )
        if pdu is None:
            return None
        if pdu.src == self._our_addr:
            return None
        if not self._replay.check_and_record(pdu.src, pdu.seq, self._keys.iv_index):
            _LOGGER.debug("Duplicate/replayed PDU from 0x%04X seq=%d dropped", pdu.src, pdu.seq)
            return None
        node = self.node_for(pdu.src)
        if node is None:
            _LOGGER.debug("PDU from unknown source 0x%04X dropped", pdu.src)
            return None
        node.on_network_pdu(pdu)
        return pdu


def _now() -> float:
    return time.monotonic()


def _consume_exception(task: asyncio.Task[Any]) -> None:
    if not task.cancelled():
        task.exception()
