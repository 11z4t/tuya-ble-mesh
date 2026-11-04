"""Iterate the devices a config entry provides, for platform setup.

A legacy entry provides one device (``entry.runtime_data``). A SIG Mesh
Network entry provides one device per node subentry; its entities must be
added with ``config_subentry_id`` so HA files them under that subentry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from custom_components.tuya_ble_mesh.const import CONF_DEVICE_TYPE
from custom_components.tuya_ble_mesh.mesh_network import (
    MeshNetworkRuntimeData,
    node_subentries,
)

if TYPE_CHECKING:
    from homeassistant.helpers.entity import Entity
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.tuya_ble_mesh import TuyaBLEMeshRuntimeData
    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator


@dataclass(frozen=True)
class EntryNode:
    """One device provided by a config entry."""

    subentry_id: str | None
    # Stable key for entity unique IDs that historically used entry_id
    entity_key: str
    device_type: str | None
    runtime: TuyaBLEMeshRuntimeData


def iter_entry_nodes(entry: Any) -> list[EntryNode]:
    """Return the devices ``entry`` provides (empty if not loaded)."""
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        return []
    if isinstance(runtime, MeshNetworkRuntimeData):
        subs = node_subentries(entry)
        nodes: list[EntryNode] = []
        for sub_id, node_runtime in runtime.nodes.items():
            sub = subs.get(sub_id)
            device_type = sub.data.get(CONF_DEVICE_TYPE) if sub is not None else None
            nodes.append(EntryNode(sub_id, sub_id, device_type, node_runtime))
        return nodes
    return [EntryNode(None, entry.entry_id, entry.data.get(CONF_DEVICE_TYPE), runtime)]


def add_node_entities(
    async_add_entities: AddEntitiesCallback, node: EntryNode, entities: list[Entity] | list[Any]
) -> None:
    """Add ``entities`` for ``node`` (under its subentry when it has one)."""
    if not entities:
        return
    if node.subentry_id is None:
        async_add_entities(entities)
    else:
        async_add_entities(entities, config_subentry_id=node.subentry_id)


def network_runtime(entry: Any) -> MeshNetworkRuntimeData | None:
    """Return the network runtime data if ``entry`` is a loaded network entry."""
    runtime = getattr(entry, "runtime_data", None)
    return runtime if isinstance(runtime, MeshNetworkRuntimeData) else None


def all_coordinators(entry: Any) -> list[TuyaBLEMeshCoordinator]:
    """Return every coordinator ``entry`` runs."""
    return [node.runtime.coordinator for node in iter_entry_nodes(entry)]
