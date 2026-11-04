"""Home Assistant runtime for a shared SIG Mesh network entry.

``MeshNetworkManager`` owns the lib ``MeshNetwork`` for one "SIG Mesh
Network" config entry: SEQ persistence (one counter for the provisioner,
reserved ahead in a ``Store``), proxy candidates from HA bluetooth, address
allocation over the node subentries, and fan-out of link state to the
per-node coordinators.

SECURITY: keys come from ``entry.data`` and are never logged.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_DEVICE_TYPE,
    CONF_ELEMENT_COUNT,
    CONF_ENTRY_KIND,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_NET_KEY,
    CONF_NETWORK_UUID,
    CONF_PROVISIONER_ADDR,
    CONF_UNICAST_TARGET,
    DEVICE_TYPE_SIG_LIGHT,
    DEVICE_TYPE_SIG_PLUG,
    DOMAIN,
    ENTRY_KIND_SIG_NETWORK,
    NETWORK_SEQ_RESERVE_BLOCK,
    SIGNAL_NETWORK_UPDATE,
    SUBENTRY_TYPE_NODE,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import DeviceInfo

    from custom_components.tuya_ble_mesh import TuyaBLEMeshRuntimeData
    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1
# Legacy SIG entries all used 0x00B0 + up to 6 elements on their own networks;
# keep that range free until they are migrated (plan D3).
LEGACY_RESERVED_RANGE = (0x00B0, 6)


def is_network_entry(entry: Any) -> bool:
    """Return True for a "SIG Mesh Network" config entry."""
    data = getattr(entry, "data", None) or {}
    return bool(data.get(CONF_ENTRY_KIND) == ENTRY_KIND_SIG_NETWORK)


def network_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the (single) SIG Mesh Network entry, if one exists."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if is_network_entry(entry):
            return entry
    return None


def node_subentries(entry: ConfigEntry) -> dict[str, Any]:
    """Return ``{subentry_id: subentry}`` for node subentries of ``entry``."""
    subentries = getattr(entry, "subentries", None) or {}
    return {
        sub_id: sub
        for sub_id, sub in subentries.items()
        if getattr(sub, "subentry_type", None) == SUBENTRY_TYPE_NODE
    }


def used_unicast_ranges(
    hass: HomeAssistant, entry: ConfigEntry, *, exclude_subentry: str | None = None
) -> list[tuple[int, int]]:
    """Return address ranges taken on this network (plus reserved ones)."""
    provisioner = int(entry.data.get(CONF_PROVISIONER_ADDR, 1))
    ranges: list[tuple[int, int]] = [(provisioner, 1)]
    for sub_id, sub in node_subentries(entry).items():
        if sub_id == exclude_subentry:
            continue
        try:
            start = int(str(sub.data[CONF_UNICAST_TARGET]), 16)
        except (KeyError, ValueError):
            continue
        ranges.append((start, int(sub.data.get(CONF_ELEMENT_COUNT, 1) or 1)))
    legacy_left = any(
        not is_network_entry(other)
        and other.data.get(CONF_DEVICE_TYPE) in (DEVICE_TYPE_SIG_LIGHT, DEVICE_TYPE_SIG_PLUG)
        for other in hass.config_entries.async_entries(DOMAIN)
    )
    if legacy_left:
        ranges.append(LEGACY_RESERVED_RANGE)
    ranges.extend((int(s), int(c)) for s, c in entry.options.get("reserved_ranges", []))
    return ranges


def allocate_node_address(
    hass: HomeAssistant,
    entry: ConfigEntry,
    element_count: int,
    *,
    exclude_subentry: str | None = None,
) -> int:
    """Pick the primary unicast address for a new node on ``entry``'s network.

    Args:
        exclude_subentry: A node being re-provisioned; its old range is
            treated as free.

    Raises:
        AddressSpaceExhaustedError: If no range fits.
    """
    from tuya_ble_mesh.sig_mesh_address_allocator import allocate_unicast

    used = used_unicast_ranges(hass, entry, exclude_subentry=exclude_subentry)
    return int(allocate_unicast(element_count, used))


@dataclass
class MeshNetworkRuntimeData:
    """``entry.runtime_data`` of a network entry."""

    manager: MeshNetworkManager
    nodes: dict[str, TuyaBLEMeshRuntimeData]
    hub_device_info: DeviceInfo
    cancel_listeners: list[Callable[[], None]] = field(default_factory=list)
    # subentry_id -> subentry data snapshot at setup (to detect removals)
    known_nodes: dict[str, dict[str, Any]] = field(default_factory=dict)


class MeshNetworkManager:
    """Owns the shared ``MeshNetwork`` of one network config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Create the manager (call ``async_setup`` before use)."""
        self._hass = hass
        self._entry = entry
        self.network_uuid: str = str(entry.data[CONF_NETWORK_UUID])
        self._store: Any = None
        self.network: Any = None
        self._coordinators: list[TuyaBLEMeshCoordinator] = []
        self._node_macs: list[str] = []
        self._unsub_link: Callable[[], None] | None = None
        self._seq_store: Any = None

    @property
    def signal(self) -> str:
        """Dispatcher signal fired on link changes."""
        return SIGNAL_NETWORK_UPDATE.format(self._entry.entry_id)

    @property
    def node_macs(self) -> list[str]:
        """Return MACs of the nodes on this network."""
        return list(self._node_macs)

    async def async_setup(self) -> None:
        """Load persisted SEQ/IV state and build the lib network."""
        from homeassistant.helpers.storage import Store
        from tuya_ble_mesh.sig_mesh_network import MeshNetwork

        from custom_components.tuya_ble_mesh.coordinator import _ReservingSeqStore

        self._store = Store(self._hass, _STORE_VERSION, f"{DOMAIN}.network.{self.network_uuid}")
        stored = await self._store.async_load() or {}
        iv_index = int(stored.get("iv_index", self._entry.data.get(CONF_IV_INDEX, 0)))
        # The stored value is a reservation that was persisted *before* any
        # SEQ below it was used, so it is always safe to resume from it.
        seq_start = int(stored.get("seq", 0))
        # Persist the first reservation before anything is sent.
        reserved = seq_start + NETWORK_SEQ_RESERVE_BLOCK
        await self._store.async_save({"seq": reserved, "iv_index": iv_index})
        self._seq_store = _ReservingSeqStore(
            seq_start, self._persist_seq, block=NETWORK_SEQ_RESERVE_BLOCK, reserved=reserved
        )
        self.network = MeshNetwork(
            self._entry.data[CONF_NET_KEY],
            self._entry.data[CONF_APP_KEY],
            iv_index=iv_index,
            our_addr=int(self._entry.data.get(CONF_PROVISIONER_ADDR, 1)),
            seq_store=self._seq_store,
            candidate_provider=self._candidates,
            ble_device_callback=self._ble_device,
        )
        self._unsub_link = self.network.add_link_listener(self._on_link_state)
        self._node_macs = [
            str(sub.data.get(CONF_MAC_ADDRESS, "")).upper()
            for sub in node_subentries(self._entry).values()
            if sub.data.get(CONF_MAC_ADDRESS)
        ]
        _LOGGER.info(
            "SIG Mesh network %s: NID=0x%02X, IV=%d, SEQ from %d, %d node(s)",
            self._entry.title,
            self.network.nid,
            iv_index,
            seq_start,
            len(self._node_macs),
        )

    def add_candidate(self, mac: str) -> None:
        """Consider ``mac`` as a proxy too (a node being added)."""
        mac = mac.upper()
        if mac not in self._node_macs:
            self._node_macs.append(mac)

    def register_coordinator(self, coordinator: TuyaBLEMeshCoordinator) -> None:
        """Track a node coordinator for link-up fan-out."""
        self._coordinators.append(coordinator)

    @property
    def coordinators(self) -> list[TuyaBLEMeshCoordinator]:
        """Return the node coordinators."""
        return list(self._coordinators)

    # --- HA bluetooth glue ---

    def rssi(self, mac: str) -> int | None:
        """Return the last advertisement RSSI for ``mac``."""
        try:
            from homeassistant.components.bluetooth import async_last_service_info

            info = async_last_service_info(self._hass, mac, connectable=False)
        except Exception:  # bluetooth not set up
            return None
        return None if info is None else int(info.rssi)

    def _ble_device(self, mac: str) -> Any:
        from homeassistant.components.bluetooth import async_ble_device_from_address

        return async_ble_device_from_address(self._hass, mac, connectable=True)

    def _candidates(self) -> list[Any]:
        from tuya_ble_mesh.sig_mesh_proxy_selector import ProxyCandidate

        try:
            from homeassistant.components.bluetooth import async_last_service_info
        except ImportError:
            return []
        candidates = []
        for mac in self._node_macs:
            try:
                info = async_last_service_info(self._hass, mac, connectable=True)
            except Exception:  # bluetooth not set up
                info = None
            if info is None:
                continue
            candidates.append(ProxyCandidate(mac, int(info.rssi), float(info.time)))
        return candidates

    # --- Link state ---

    def _on_link_state(self, up: bool) -> None:
        from homeassistant.helpers.dispatcher import async_dispatcher_send

        async_dispatcher_send(self._hass, self.signal)
        if not up:
            return
        _LOGGER.info("SIG Mesh network %s up via %s", self._entry.title, self.network.active_proxy)
        for coordinator in self._coordinators:
            if not coordinator.state.available:
                coordinator.notify_device_seen()

    def notify_node_seen(self) -> None:
        """A node advertised while the link is down: shorten everyone's backoff."""
        if self.network is not None and self.network.is_connected:
            return
        for coordinator in self._coordinators:
            if not coordinator.state.available:
                coordinator.notify_device_seen()

    # --- SEQ persistence ---

    def _persist_seq(self, reserved: int) -> None:
        store = self._store
        if store is None:
            return
        data = {"seq": reserved, "iv_index": self.network.iv_index if self.network else 0}
        self._entry.async_create_background_task(
            self._hass, store.async_save(data), f"{DOMAIN}_network_seq"
        )

    async def async_shutdown(self) -> None:
        """Close the link and persist SEQ."""
        if self._unsub_link is not None:
            self._unsub_link()
            self._unsub_link = None
        if self.network is not None:
            await self.network.close()
        if self._store is not None and self._seq_store is not None:
            seq = max(int(self._seq_store.get_seq()), int(self._seq_store.reserved))
            await self._store.async_save(
                {"seq": seq, "iv_index": self.network.iv_index if self.network else 0}
            )
