"""Per-node views onto a shared ``MeshNetwork``.

``SIGMeshNode`` / ``SIGMeshLightNode`` expose exactly the duck type of
``SIGMeshDevice`` / ``SIGMeshLight`` (so coordinators and entities don't
change), but instead of owning a GATT connection they borrow the network's
single proxy link:

- sends: SEQ from the network's shared counter, writes through the
  network's link, encryption with the network keys + this node's device key;
- receives: the network decrypts the network layer once and routes PDUs by
  source address; this node does upper-transport decryption, segment
  reassembly, pending-response matching and callbacks with the inherited
  code — so a Config Status from node B can never resolve node A's request;
- ``connect()`` = attach + make sure the shared link is up;
  ``disconnect()`` = detach (the link stays up for the other nodes).

SECURITY: key material is never logged.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from tuya_ble_mesh.const import DEFAULT_CONNECTION_TIMEOUT, DEFAULT_MAX_RETRIES
from tuya_ble_mesh.exceptions import SIGMeshError
from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice
from tuya_ble_mesh.sig_mesh_light import SIGMeshLight
from tuya_ble_mesh.sig_mesh_network import MeshNetwork
from tuya_ble_mesh.sig_mesh_protocol import NetworkPDU

_LOGGER = logging.getLogger(__name__)

RssiProvider = Callable[[str], "int | None"]


class _NetworkNodeMixin:
    """Transport overrides that route a SIG Mesh device through ``MeshNetwork``."""

    # Provided by SIGMeshDevice
    _address: str
    _target_addr: int
    _keys: Any
    _pending_notify_tasks: set[asyncio.Task[None]]
    _process_network_pdu: Callable[[NetworkPDU], Any]
    _on_ble_disconnect: Callable[[Any], None]
    _log_notify_exception: Callable[[asyncio.Task[None]], None]
    request_composition_data: Callable[[], Any]

    def _init_network(
        self,
        network: MeshNetwork,
        dev_key_hex: str,
        element_count: int,
        rssi_provider: RssiProvider | None,
    ) -> None:
        self._network = network
        self._dev_key_hex = dev_key_hex
        self._element_count = max(1, element_count)
        self._rssi_provider = rssi_provider
        self._attached = False

    # --- Identity (NetworkNode protocol) ---

    @property
    def network(self) -> MeshNetwork:
        """Return the network this node belongs to."""
        return self._network

    @property
    def unicast(self) -> int:
        """Return the node's primary unicast address."""
        return self._target_addr

    @property
    def element_count(self) -> int:
        """Return the number of elements."""
        return self._element_count

    # --- Link state ---

    @property
    def _client(self) -> Any:
        """Non-None while the shared link is up and this node is attached.

        Inherited send code only checks ``_client is None``; it never writes
        through it (``_write_proxy`` is overridden).
        """
        if self._attached and self._network.is_connected:
            return self._network
        return None

    @_client.setter
    def _client(self, _value: Any) -> None:
        """Ignore assignments from inherited code — the network owns the link."""

    @property
    def is_connected(self) -> bool:
        """Return True while the shared link is up and this node is attached."""
        return self._client is not None

    @property
    def rssi(self) -> int | None:
        """Return the node's advertisement RSSI (or the link RSSI if it is the proxy)."""
        if self._rssi_provider is not None:
            value = self._rssi_provider(self._address)
            if value is not None:
                return value
        if self._network.active_proxy == self._address:
            rssi: int | None = self._network.link_rssi
            return rssi
        return None

    async def connect(
        self,
        timeout: float = DEFAULT_CONNECTION_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        *,
        fresh_services: bool = False,
    ) -> None:
        """Attach to the network and make sure the shared proxy link is up.

        Raises:
            MeshConnectionError: If no proxy node could be connected.
            SIGMeshError: If this node's address range collides with another.
        """
        del timeout, max_retries, fresh_services  # the network owns link policy
        self._keys = self._network.node_keys(self._dev_key_hex)
        self._network.attach(self)
        self._attached = True
        await self._network.ensure_connected()
        try:
            await self.request_composition_data()
        except (TimeoutError, SIGMeshError):
            _LOGGER.debug("Composition Data request to %s failed", self._address, exc_info=True)

    async def disconnect(self) -> None:
        """Detach from the network; the shared link stays up for other nodes."""
        self._attached = False
        self._network.detach(self)
        for task in list(self._pending_notify_tasks):
            task.cancel()
        if self._pending_notify_tasks:
            await asyncio.gather(*self._pending_notify_tasks, return_exceptions=True)
        self._pending_notify_tasks.clear()
        self._keys = None

    # --- Network callbacks ---

    def on_network_pdu(self, pdu: NetworkPDU) -> None:
        """Process a PDU from this node (called by ``MeshNetwork``)."""
        if self._keys is None:
            return
        try:
            task = asyncio.get_running_loop().create_task(self._process_network_pdu(pdu))
        except RuntimeError:
            return
        self._pending_notify_tasks.add(task)
        task.add_done_callback(self._pending_notify_tasks.discard)
        task.add_done_callback(self._log_notify_exception)

    def on_link_state(self, up: bool) -> None:
        """Fail in-flight requests and notify listeners when the link drops."""
        if not up and self._attached:
            self._on_ble_disconnect(None)

    # --- Transport overrides ---

    async def _write_proxy(self, data: bytes) -> None:
        await self._network.write(data)

    async def _next_seq(self) -> int:
        return int(await self._network.next_seqs(1))

    async def _next_seqs(self, n: int) -> int:
        return int(await self._network.next_seqs(n))

    # SEQ belongs to the network; per-device persistence hooks are no-ops.
    def set_seq_store(self, _seq_store: Any) -> None:
        """Ignore: SEQ is persisted per network."""

    def set_seq(self, _seq: int) -> None:
        """Ignore: SEQ is persisted per network."""

    def get_seq(self) -> int:
        """Return the network's next SEQ."""
        return int(self._network.get_seq())


class SIGMeshNode(_NetworkNodeMixin, SIGMeshDevice):  # type: ignore[misc]
    """Generic SIG Mesh node (e.g. a plug) on a shared network."""

    def __init__(
        self,
        address: str,
        network: MeshNetwork,
        unicast: int,
        dev_key_hex: str,
        *,
        element_count: int = 1,
        rssi_provider: RssiProvider | None = None,
    ) -> None:
        """Create a node view.

        Args:
            address: Node MAC.
            network: Shared network.
            unicast: Primary unicast address.
            dev_key_hex: Node device key (hex).
            element_count: Number of elements.
            rssi_provider: ``address -> advertisement RSSI``.
        """
        SIGMeshDevice.__init__(self, address, unicast, network.our_addr, None)
        self._init_network(network, dev_key_hex, element_count, rssi_provider)


class SIGMeshLightNode(_NetworkNodeMixin, SIGMeshLight):  # type: ignore[misc]
    """SIG Mesh light on a shared network."""

    def __init__(
        self,
        address: str,
        network: MeshNetwork,
        unicast: int,
        dev_key_hex: str,
        *,
        element_count: int = 1,
        model_elements: dict[int, int] | None = None,
        temp_range_k: tuple[int, int] | None = None,
        rssi_provider: RssiProvider | None = None,
    ) -> None:
        """Create a light node view (see ``SIGMeshNode`` for the common args)."""
        SIGMeshLight.__init__(
            self,
            address,
            unicast,
            network.our_addr,
            None,
            model_elements=model_elements,
            temp_range_k=temp_range_k,
        )
        self._init_network(network, dev_key_hex, element_count, rssi_provider)
