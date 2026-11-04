"""Provision a discovered SIG Mesh device into the shared network (Phase 4).

Flow: make sure the "SIG Mesh Network" entry exists and is loaded → PB-GATT
provisioning with the network's NetKey and an address picked from the
network's free space once the device reports its element count → configure
the node (AppKey Add, Composition, binds, publication) over the network's
shared link → add it as a node subentry (the entry reloads and starts a
coordinator for it).

PB-GATT still needs one direct connection to the new device: these bulbs
lack the Remote Provisioning Server model.

After PB-GATT succeeds the device is ours (we hold its device key), so a
configuration failure is not fatal: the subentry is stored with
``config_state=pending`` and configuration is retried when the node is next
reachable — no factory reset needed.

SECURITY: keys are never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_DEV_KEY,
    CONF_DEVICE_TYPE,
    CONF_ELEMENT_COUNT,
    CONF_MAC_ADDRESS,
    CONF_MODEL_ELEMENTS,
    CONF_NET_KEY,
    CONF_TEMP_MAX_K,
    CONF_TEMP_MIN_K,
    CONF_UNICAST_TARGET,
    DEVICE_TYPE_SIG_LIGHT,
    DOMAIN,
    SUBENTRY_TYPE_NODE,
)
from custom_components.tuya_ble_mesh.entry_nodes import network_runtime
from custom_components.tuya_ble_mesh.mesh_network import (
    allocate_node_address,
    network_entry,
    node_subentries,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

CONF_CONFIG_STATE = "config_state"
CONFIG_STATE_PENDING = "pending"
CONFIG_STATE_COMPLETE = "complete"

PROVISION_TIMEOUT = 180.0
# Seconds for the device to reboot from Provisioning (0x1827) to Proxy (0x1828)
POST_PROVISION_REBOOT_DELAY = 6.0
NETWORK_READY_TIMEOUT = 30.0
_MODEL_GENERIC_ONOFF_SERVER = 0x1000
_PROVISIONER_ADDR = 0x0001


class NetworkUnavailableError(Exception):
    """The shared network entry could not be created or loaded."""


async def async_ensure_network_entry(hass: HomeAssistant) -> ConfigEntry:
    """Return the loaded network entry, creating it on first use.

    Raises:
        NetworkUnavailableError: If it can't be created or doesn't load.
    """
    entry = network_entry(hass)
    if entry is None:
        _LOGGER.info("Creating the shared SIG Mesh network")
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "create_network"}, data={}
        )
        entry = network_entry(hass)
        if entry is None:
            msg = "SIG Mesh network entry could not be created"
            raise NetworkUnavailableError(msg)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + NETWORK_READY_TIMEOUT
    while network_runtime(entry) is None:
        if loop.time() > deadline:
            msg = "SIG Mesh network entry did not load"
            raise NetworkUnavailableError(msg)
        await asyncio.sleep(0.5)
    return entry


async def async_configure_network_node(
    device: Any, app_key: bytes, device_type: str
) -> dict[str, Any]:
    """Configure a provisioned node; return the subentry fields it yields.

    ``device`` is a node view on the network (it attaches and brings the
    shared link up on ``connect``).

    Raises:
        SIGMeshError / MeshConnectionError: If configuration fails.
    """
    from tuya_ble_mesh.sig_mesh_light import (
        configure_sig_node,
        model_elements_from_composition,
    )

    is_light = device_type == DEVICE_TYPE_SIG_LIGHT
    node = await configure_sig_node(
        device,
        app_key,
        fallback_models=(_MODEL_GENERIC_ONOFF_SERVER,),
        publish_to=_PROVISIONER_ADDR if is_light else None,
    )
    if node.failed_models:
        _LOGGER.warning(
            "Model App Bind failed for %s on %d model(s): %s",
            device.address,
            len(node.failed_models),
            ", ".join(f"elem{i}:0x{m:04X}" for i, m in node.failed_models),
        )
    extra: dict[str, Any] = {CONF_CONFIG_STATE: CONFIG_STATE_COMPLETE}
    if node.composition is not None:
        extra[CONF_MODEL_ELEMENTS] = {
            f"{m:04x}": i for m, i in model_elements_from_composition(node.composition).items()
        }
    if is_light:
        try:
            reported = await device.get_temperature_range()
            # Store only a range the device actually reported
            if not getattr(device, "_scale_temperature", True):
                extra[CONF_TEMP_MIN_K], extra[CONF_TEMP_MAX_K] = reported
        except Exception:
            _LOGGER.info("Temperature range query failed for %s; using default", device.address)
    return extra


async def _async_configure_with_fallback(
    manager: Any, device: Any, app_key: bytes, device_type: str
) -> dict[str, Any]:
    """Configure through the shared link; if that fails, via a direct link."""
    try:
        return await async_configure_network_node(device, app_key, device_type)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if manager.network.active_proxy == device.address:
            raise
        _LOGGER.info(
            "Configuring %s through %s failed (%s) — retrying with a direct link",
            device.address,
            manager.network.active_proxy,
            exc,
        )
    await manager.network.use_proxy(device.address)
    return await async_configure_network_node(device, app_key, device_type)


async def async_add_node_to_network(
    hass: HomeAssistant,
    mac: str,
    device_type: str,
    title: str,
    *,
    on_added: Callable[[ConfigEntry, str], None] | None = None,
) -> dict[str, Any]:
    """Provision + configure ``mac`` into the shared network and add its subentry.

    ``on_added(network_entry, subentry_id)`` runs synchronously right after
    the subentry is stored — before the network entry reloads — so callers
    can re-point existing registry entries to it (legacy migration).

    Returns:
        The node subentry data.

    Raises:
        NetworkUnavailableError: If the network entry can't be used.
        ProvisioningError / TimeoutError: If PB-GATT provisioning fails
            (the device is untouched and can simply be retried).
    """
    from tuya_ble_mesh.sig_mesh_provisioner import SIGMeshProvisioner

    from custom_components.tuya_ble_mesh.config_flow_sig import ha_ble_callbacks
    from custom_components.tuya_ble_mesh.device_factory import create_network_node

    mac = mac.upper()
    entry = await async_ensure_network_entry(hass)
    runtime = network_runtime(entry)
    if runtime is None:
        msg = "SIG Mesh network is not loaded"
        raise NetworkUnavailableError(msg)
    manager = runtime.manager
    net_key = bytes.fromhex(entry.data[CONF_NET_KEY])
    app_key = bytes.fromhex(entry.data[CONF_APP_KEY])
    ble_device_cb, ble_connect_cb = ha_ble_callbacks(hass)

    exclude, existing = _subentry_for_mac(entry, mac)
    # A node being re-added gets a fresh range (its old one counts as free).

    def _allocate(element_count: int) -> int:
        return allocate_node_address(hass, entry, element_count, exclude_subentry=exclude)

    provisioner = SIGMeshProvisioner(
        net_key=net_key,
        app_key=app_key,
        unicast_addr=0,
        iv_index=int(manager.network.iv_index),
        ble_device_callback=ble_device_cb,
        ble_connect_callback=ble_connect_cb,
        unicast_allocator=_allocate,
    )
    _LOGGER.info("Provisioning %s into the shared SIG Mesh network", mac)
    try:
        result = await asyncio.wait_for(provisioner.provision(mac), timeout=PROVISION_TIMEOUT)
    except TimeoutError:
        raise TimeoutError(f"Provisioning timed out after {PROVISION_TIMEOUT:.0f}s") from None
    _LOGGER.info(
        "PB-GATT provisioning of %s succeeded: unicast 0x%04X, %d element(s)",
        mac,
        result.unicast_addr,
        result.num_elements,
    )

    data: dict[str, Any] = {
        CONF_MAC_ADDRESS: mac,
        CONF_DEVICE_TYPE: device_type,
        CONF_UNICAST_TARGET: f"{result.unicast_addr:04X}",
        CONF_ELEMENT_COUNT: int(result.num_elements),
        CONF_DEV_KEY: result.dev_key.hex(),
        CONF_CONFIG_STATE: CONFIG_STATE_PENDING,
    }
    # From here on the node is ours: store it even if configuration fails.
    await asyncio.sleep(POST_PROVISION_REBOOT_DELAY)
    manager.network.forget_replay_state(result.unicast_addr, result.num_elements)
    manager.add_candidate(mac)
    device = create_network_node(manager.network, data, manager.rssi)
    stop_answering = answer_time_requests(device)
    try:
        data.update(await _async_configure_with_fallback(manager, device, app_key, device_type))
        _LOGGER.info("Node %s configured over the shared network", mac)
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.warning(
            "Configuring %s failed — it will be retried when the node is reachable",
            mac,
            exc_info=True,
        )
    finally:
        stop_answering()
        await device.disconnect()

    subentry_id = _store_node_subentry(hass, entry, existing, exclude, data, title)
    if on_added is not None:
        on_added(entry, subentry_id)
    return data


def answer_time_requests(device: Any) -> Any:
    """Answer Tuya timestamp requests while no coordinator owns ``device``.

    Tuya nodes drop the proxy link after a couple of unanswered requests,
    which would abort configuration midway. Returns an unregister function.
    """
    from tuya_ble_mesh.sig_mesh_protocol import (
        TUYA_CMD_TIMESTAMP_SYNC,
        TUYA_VENDOR_OPCODE,
        parse_tuya_vendor_frame,
        tuya_vendor_timestamp_response,
    )

    tasks: set[asyncio.Task[None]] = set()

    async def _reply() -> None:
        try:
            await device.send_vendor_command(tuya_vendor_timestamp_response())
        except Exception:
            _LOGGER.debug("Timestamp reply to %s failed", device.address, exc_info=True)

    def _on_vendor(opcode: int, params: bytes) -> None:
        if opcode != TUYA_VENDOR_OPCODE:
            return
        try:
            frame = parse_tuya_vendor_frame(params)
        except Exception:
            return
        if frame.command == TUYA_CMD_TIMESTAMP_SYNC:
            task = asyncio.get_running_loop().create_task(_reply())
            tasks.add(task)
            task.add_done_callback(tasks.discard)

    device.register_vendor_callback(_on_vendor)

    def _stop() -> None:
        with contextlib.suppress(ValueError):
            device.unregister_vendor_callback(_on_vendor)

    return _stop


def _subentry_for_mac(entry: ConfigEntry, mac: str) -> tuple[str | None, Any]:
    """Return ``(subentry_id, subentry)`` of the node with ``mac``, if any."""
    for sub_id, sub in node_subentries(entry).items():
        if str(sub.data.get(CONF_MAC_ADDRESS, "")).upper() == mac:
            return sub_id, sub
    return None, None


def _store_node_subentry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    existing: Any,
    existing_id: str | None,
    data: dict[str, Any],
    title: str,
) -> str:
    """Add (or replace the data of) the node's subentry; the entry reloads.

    Returns:
        The subentry id.
    """
    from homeassistant.config_entries import ConfigSubentry

    if existing is not None and existing_id is not None:
        hass.config_entries.async_update_subentry(entry, existing, data=data)
        return existing_id
    subentry = ConfigSubentry(
        data=MappingProxyType(data),
        subentry_type=SUBENTRY_TYPE_NODE,
        title=title,
        unique_id=data[CONF_MAC_ADDRESS],
    )
    hass.config_entries.async_add_subentry(entry, subentry)
    return str(subentry.subentry_id)


async def async_complete_pending_config(
    hass: HomeAssistant, entry: ConfigEntry, subentry_id: str, device: Any
) -> bool:
    """Finish configuring a node stored as ``pending`` (it is connected now).

    Returns:
        True if configuration completed and the subentry was updated.
    """
    sub = node_subentries(entry).get(subentry_id)
    if sub is None or sub.data.get(CONF_CONFIG_STATE) != CONFIG_STATE_PENDING:
        return False
    app_key = bytes.fromhex(entry.data[CONF_APP_KEY])
    try:
        extra = await async_configure_network_node(
            device, app_key, str(sub.data.get(CONF_DEVICE_TYPE, ""))
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.warning("Pending configuration of %s failed", device.address, exc_info=True)
        return False
    _LOGGER.info("Pending configuration of %s completed", device.address)
    hass.config_entries.async_update_subentry(entry, sub, data={**dict(sub.data), **extra})
    return True
