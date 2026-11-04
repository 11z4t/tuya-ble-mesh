"""Move a legacy one-network-per-device SIG Mesh entry into the shared network.

Legacy SIG entries each have their own NetKey and all use unicast 0x00B0, so
they can't simply join the shared network. Migration (plan §6, Phase 5):

1. Config Node Reset, sent over the bulb's current link with its old keys —
   the bulb wipes itself and advertises the Provisioning service (0x1827).
   No physical reset or power cycling.
2. Unload the legacy entry (frees its proxy slot, stops its reconnects).
3. Wait for the 0x1827 advertisement.
4. Provision + configure into the shared network (Phase 4 path).
5. Re-point the existing HA device and entities to the new node subentry
   (entity IDs, names, areas and automations survive), then remove the
   legacy entry.

If the bulb never acknowledges the reset and never shows up unprovisioned,
the legacy entry is reloaded and nothing is lost. If it was already
unprovisioned (e.g. a previous attempt reset it, or it was reset by hand),
step 1 is skipped.

SECURITY: keys are never logged.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from custom_components.tuya_ble_mesh.const import (
    CONF_DEVICE_TYPE,
    CONF_MAC_ADDRESS,
    CONF_NET_KEY,
    CONF_NETWORK_UUID,
    DEVICE_TYPE_SIG_LIGHT,
    DEVICE_TYPE_SIG_PLUG,
    DOMAIN,
)
from custom_components.tuya_ble_mesh.mesh_network import is_network_entry

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

ISSUE_LEGACY_SIG_NETWORK = "legacy_sig_network"
SIG_MESH_PROV_UUID = "00001827-0000-1000-8000-00805f9b34fb"
UNPROVISIONED_TIMEOUT = 60.0
NODE_RESET_TIMEOUT = 5.0
# An unprovisioned advert older than this is not trusted (HA keeps the last
# advertisement indefinitely while a device is connected and silent)
FRESH_ADVERT_S = 30.0


class MigrationError(Exception):
    """Migration failed; ``reason`` is a translation key for the repair form."""

    def __init__(self, reason: str, message: str = "") -> None:
        super().__init__(message or reason)
        self.reason = reason


def is_legacy_sig_entry(entry: Any) -> bool:
    """Return True for a direct SIG entry that has its own private network."""
    data = getattr(entry, "data", None) or {}
    return (
        not is_network_entry(entry)
        and data.get(CONF_DEVICE_TYPE) in (DEVICE_TYPE_SIG_LIGHT, DEVICE_TYPE_SIG_PLUG)
        and bool(data.get(CONF_NET_KEY))
    )


def issue_id_for(entry_id: str) -> str:
    """Return the repair issue id for a legacy entry."""
    return f"{ISSUE_LEGACY_SIG_NETWORK}_{entry_id}"


def async_create_migration_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Offer "Move to mesh network" for a legacy SIG entry."""
    from homeassistant.helpers import issue_registry as ir

    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id_for(entry.entry_id),
        is_fixable=True,
        is_persistent=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_LEGACY_SIG_NETWORK,
        translation_placeholders={"name": entry.title},
        data={"entry_id": entry.entry_id},
    )


def async_delete_migration_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the migration issue of a legacy entry."""
    from homeassistant.helpers import issue_registry as ir

    ir.async_delete_issue(hass, DOMAIN, issue_id_for(entry_id))


def _unprovisioned_now(hass: HomeAssistant, mac: str) -> bool:
    """True if a fresh advertisement shows ``mac`` advertising 0x1827."""
    try:
        from homeassistant.components.bluetooth import async_last_service_info

        info = async_last_service_info(hass, mac, connectable=True)
    except Exception:
        return False
    if info is None or SIG_MESH_PROV_UUID not in (getattr(info, "service_uuids", None) or []):
        return False
    return bool(time.monotonic() - float(getattr(info, "time", 0.0)) < FRESH_ADVERT_S)


async def _async_wait_unprovisioned(hass: HomeAssistant, mac: str, timeout: float) -> bool:
    """Wait for ``mac`` to advertise the Provisioning service."""
    if _unprovisioned_now(hass, mac):
        return True
    from homeassistant.components.bluetooth import (
        BluetoothCallbackMatcher,
        BluetoothScanningMode,
        async_process_advertisements,
    )

    def _is_unprovisioned(info: Any) -> bool:
        return SIG_MESH_PROV_UUID in (getattr(info, "service_uuids", None) or [])

    try:
        await async_process_advertisements(
            hass,
            _is_unprovisioned,
            BluetoothCallbackMatcher(address=mac, connectable=True),
            BluetoothScanningMode.ACTIVE,
            timeout,
        )
    except TimeoutError:
        return False
    return True


async def _async_reset_node(entry: ConfigEntry) -> bool:
    """Send Config Node Reset over the legacy link; True if acknowledged.

    Raises:
        MigrationError("not_connected"): If the legacy link is down.
    """
    runtime = getattr(entry, "runtime_data", None)
    device: Any = getattr(getattr(runtime, "coordinator", None), "device", None)
    if device is None or not getattr(device, "is_connected", False):
        msg = "Legacy link is down"
        raise MigrationError("not_connected", msg)
    try:
        acked = bool(await device.send_config_node_reset(response_timeout=NODE_RESET_TIMEOUT))
    except Exception as exc:
        _LOGGER.warning("Node Reset to %s failed: %s", device.address, exc)
        return False
    _LOGGER.info("Node Reset sent to %s (acknowledged=%s)", device.address, acked)
    return acked


def _repoint_registries(
    hass: HomeAssistant, legacy: ConfigEntry, network: ConfigEntry, subentry_id: str, mac: str
) -> None:
    """Move the legacy device + entities onto the node subentry (keeps entity IDs)."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_device(identifiers={(DOMAIN, mac)})
    if device is not None:
        hub = dev_reg.async_get_device(identifiers={(DOMAIN, network.data[CONF_NETWORK_UUID])})
        update: dict[str, Any] = {
            "add_config_entry_id": network.entry_id,
            "add_config_subentry_id": subentry_id,
        }
        if hub is not None:
            update["via_device_id"] = hub.id
        dev_reg.async_update_device(device.id, **update)
        dev_reg.async_update_device(device.id, remove_config_entry_id=legacy.entry_id)
    legacy_firmware_uid = f"{legacy.entry_id}_firmware"
    for ent in er.async_entries_for_config_entry(ent_reg, legacy.entry_id):
        changes: dict[str, Any] = {
            "config_entry_id": network.entry_id,
            "config_subentry_id": subentry_id,
        }
        if ent.unique_id == legacy_firmware_uid:
            changes["new_unique_id"] = f"{subentry_id}_firmware"
        ent_reg.async_update_entity(ent.entity_id, **changes)
    _LOGGER.info("Moved HA device/entities of %s to the shared network", mac)


async def async_migrate_legacy_entry(hass: HomeAssistant, legacy: ConfigEntry) -> str:
    """Move ``legacy`` into the shared network.

    Returns:
        The new node's subentry id.

    Raises:
        MigrationError: With a translation-key ``reason``.
    """
    from custom_components.tuya_ble_mesh.config_flow_network import (
        NetworkUnavailableError,
        async_add_node_to_network,
        async_ensure_network_entry,
    )

    if not is_legacy_sig_entry(legacy):
        raise MigrationError("not_legacy")
    mac = str(legacy.data[CONF_MAC_ADDRESS]).upper()
    device_type = str(legacy.data[CONF_DEVICE_TYPE])

    try:
        await async_ensure_network_entry(hass)
    except NetworkUnavailableError as exc:
        raise MigrationError("network_unavailable", str(exc)) from exc

    already_reset = _unprovisioned_now(hass, mac)
    acked = False
    if not already_reset:
        acked = await _async_reset_node(legacy)

    # Free the proxy slot and stop the legacy coordinator reconnecting
    await hass.config_entries.async_unload(legacy.entry_id)

    if not await _async_wait_unprovisioned(hass, mac, UNPROVISIONED_TIMEOUT):
        if not already_reset and not acked:
            # The bulb most likely never reset: put things back as they were
            _LOGGER.warning("%s did not reset — restoring its legacy entry", mac)
            await hass.config_entries.async_reload(legacy.entry_id)
            raise MigrationError("reset_failed")
        raise MigrationError("not_unprovisioned")

    subentry: list[str] = []

    def _on_added(network: ConfigEntry, subentry_id: str) -> None:
        subentry.append(subentry_id)
        _repoint_registries(hass, legacy, network, subentry_id, mac)

    try:
        await async_add_node_to_network(hass, mac, device_type, legacy.title, on_added=_on_added)
    except TimeoutError as exc:
        raise MigrationError("timeout", str(exc)) from exc
    except Exception as exc:
        _LOGGER.warning("Provisioning %s into the network failed", mac, exc_info=True)
        raise MigrationError("provisioning_failed", str(exc)) from exc

    async_delete_migration_issue(hass, legacy.entry_id)
    await hass.config_entries.async_remove(legacy.entry_id)
    _LOGGER.info("%s moved to the shared SIG Mesh network", mac)
    return subentry[0]
