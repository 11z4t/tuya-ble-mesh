"""Setup / unload of a "SIG Mesh Network" config entry and its node subentries."""

from __future__ import annotations

import asyncio
import importlib
import logging
from typing import TYPE_CHECKING, Any

from custom_components.tuya_ble_mesh.config_flow_network import (
    CONF_CONFIG_STATE,
    CONFIG_STATE_PENDING,
)
from custom_components.tuya_ble_mesh.const import (
    CONF_DEVICE_TYPE,
    CONF_MAC_ADDRESS,
    DEFAULT_NETWORK_NAME,
    DEVICE_MODEL_NAMES,
    DOMAIN,
    PLATFORMS,
)
from custom_components.tuya_ble_mesh.mesh_network import (
    MeshNetworkManager,
    MeshNetworkRuntimeData,
    node_subentries,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

_LOGGER = logging.getLogger(__name__)

NODE_RESET_TIMEOUT = 5.0
PENDING_CONFIG_ATTEMPTS = 30
PENDING_CONFIG_RETRY_S = 60.0


async def async_setup_network_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the shared network, one coordinator per node, and platforms."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

    from custom_components.tuya_ble_mesh import TuyaBLEMeshRuntimeData, _async_register_services
    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
    from custom_components.tuya_ble_mesh.device_factory import create_network_node

    manager = MeshNetworkManager(hass, entry)
    await manager.async_setup()

    hub_info = DeviceInfo(
        identifiers={(DOMAIN, manager.network_uuid)},
        name=entry.title or DEFAULT_NETWORK_NAME,
        manufacturer="Bluetooth SIG Mesh",
        model="Mesh network",
        entry_type=DeviceEntryType.SERVICE,
    )
    # Create the hub first so node devices can point at it (via_device)
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **hub_info)

    nodes: dict[str, TuyaBLEMeshRuntimeData] = {}
    known: dict[str, dict[str, Any]] = {}
    for sub_id, sub in node_subentries(entry).items():
        try:
            device = create_network_node(manager.network, sub.data, manager.rssi)
        except ValueError as exc:
            _LOGGER.error("Skipping node %s: %s", sub.title, exc)
            continue
        coordinator = TuyaBLEMeshCoordinator(device, hass=hass, entry_id=None, entry=entry)
        mac = str(sub.data[CONF_MAC_ADDRESS]).upper()
        device_type = sub.data.get(CONF_DEVICE_TYPE, "")
        info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            name=sub.title,
            manufacturer="Tuya",
            model=DEVICE_MODEL_NAMES.get(device_type, "SIG Mesh node"),
            connections={("mac", mac)},
            via_device=(DOMAIN, manager.network_uuid),
        )
        nodes[sub_id] = TuyaBLEMeshRuntimeData(coordinator=coordinator, device_info=info)
        known[sub_id] = dict(sub.data)
        manager.register_coordinator(coordinator)

    runtime = MeshNetworkRuntimeData(
        manager=manager, nodes=nodes, hub_device_info=hub_info, known_nodes=known
    )
    entry.runtime_data = runtime

    for platform in PLATFORMS:
        await hass.async_add_import_executor_job(
            importlib.import_module, f"custom_components.tuya_ble_mesh.{platform}"
        )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_register_services(hass)

    for sub_id, node_runtime in nodes.items():
        coord = node_runtime.coordinator
        entry.async_create_background_task(
            hass, _async_start_node(coord), f"{DOMAIN}_node_start_{coord.device.address}"
        )
        if known[sub_id].get(CONF_CONFIG_STATE) == CONFIG_STATE_PENDING:
            entry.async_create_background_task(
                hass,
                _async_finish_pending(hass, entry, sub_id, coord),
                f"{DOMAIN}_node_config_{coord.device.address}",
            )

    _register_advert_callbacks(hass, entry, manager, runtime)
    entry.async_on_unload(entry.add_update_listener(_async_network_update_listener))
    return True


async def _async_start_node(coordinator: TuyaBLEMeshCoordinator) -> None:
    """Initial connect; on failure fall back to the coordinator's reconnect loop."""
    try:
        await coordinator.async_initial_connect()
    except Exception as exc:
        _LOGGER.info("Node %s not reachable yet (%s) — will retry", coordinator.device.address, exc)
        coordinator.schedule_reconnect()


async def _async_finish_pending(
    hass: HomeAssistant, entry: ConfigEntry, subentry_id: str, coordinator: TuyaBLEMeshCoordinator
) -> None:
    """Retry configuration of a node stored as pending until it succeeds."""
    from custom_components.tuya_ble_mesh.config_flow_network import (
        async_complete_pending_config,
    )

    for _ in range(PENDING_CONFIG_ATTEMPTS):
        if coordinator.state.available and await async_complete_pending_config(
            hass, entry, subentry_id, coordinator.device
        ):
            return
        await asyncio.sleep(PENDING_CONFIG_RETRY_S)
    _LOGGER.warning("Giving up configuring %s for now", coordinator.device.address)


def _register_advert_callbacks(
    hass: HomeAssistant,
    entry: ConfigEntry,
    manager: MeshNetworkManager,
    runtime: MeshNetworkRuntimeData,
) -> None:
    try:
        from homeassistant.components.bluetooth import (
            BluetoothCallbackMatcher,
            BluetoothChange,
            async_register_callback,
        )
    except ImportError:
        return

    def _seen(_info: Any, _change: Any) -> None:
        manager.notify_node_seen()

    for mac in manager.node_macs:
        cancel = async_register_callback(
            hass, _seen, BluetoothCallbackMatcher(address=mac), BluetoothChange.ADVERTISEMENT
        )
        runtime.cancel_listeners.append(cancel)


async def _async_network_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reset removed nodes (best effort), then reload to pick up changes."""
    runtime = getattr(entry, "runtime_data", None)
    if isinstance(runtime, MeshNetworkRuntimeData):
        current = set(node_subentries(entry))
        for sub_id in set(runtime.known_nodes) - current:
            node_runtime = runtime.nodes.get(sub_id)
            if node_runtime is not None:
                await _async_reset_removed_node(node_runtime.coordinator)
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_reset_removed_node(coordinator: TuyaBLEMeshCoordinator) -> None:
    """Send Config Node Reset to a node whose subentry was deleted."""
    device: Any = coordinator.device
    if not getattr(device, "is_connected", False):
        _LOGGER.warning(
            "Node %s removed while unreachable — it keeps its keys until reset by hand",
            device.address,
        )
        return
    try:
        acked = await device.send_config_node_reset(response_timeout=NODE_RESET_TIMEOUT)
    except Exception:
        _LOGGER.warning("Node Reset to %s failed", device.address, exc_info=True)
        return
    _LOGGER.info("Removed node %s reset (acknowledged=%s)", device.address, acked)


async def async_unload_network_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop node coordinators, close the shared link, unload platforms."""
    runtime = getattr(entry, "runtime_data", None)
    if isinstance(runtime, MeshNetworkRuntimeData):
        for cancel in runtime.cancel_listeners:
            cancel()
        for node_runtime in runtime.nodes.values():
            await node_runtime.coordinator.async_stop()
        await runtime.manager.async_shutdown()
    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok
