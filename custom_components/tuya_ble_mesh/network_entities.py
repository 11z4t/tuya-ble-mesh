"""Entities on the SIG Mesh Network hub device (link state, active proxy)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from custom_components.tuya_ble_mesh.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceInfo

    from custom_components.tuya_ble_mesh.mesh_network import MeshNetworkManager

_LOGGER = logging.getLogger(__name__)


class _MeshNetworkEntity:
    """Shared plumbing: hub device, unique ID, refresh on link changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _key: str = ""

    def __init__(self, manager: MeshNetworkManager, device_info: DeviceInfo) -> None:
        self._manager = manager
        self._attr_device_info = device_info
        self._attr_unique_id = f"{manager.network_uuid}_{self._key}"

    async def async_added_to_hass(self) -> None:
        from homeassistant.helpers.dispatcher import async_dispatcher_connect

        entity: Any = self
        entity.async_on_remove(
            async_dispatcher_connect(entity.hass, self._manager.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        entity: Any = self
        entity.async_write_ha_state()


class MeshLinkSensor(_MeshNetworkEntity, BinarySensorEntity):  # type: ignore[misc]
    """On while the shared proxy link is up."""

    _key = "mesh_link"
    _attr_translation_key = "mesh_link"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool:
        """Return True while the mesh link is up."""
        network = self._manager.network
        return bool(network is not None and network.is_connected)


class MeshActiveProxySensor(_MeshNetworkEntity, SensorEntity):  # type: ignore[misc]
    """MAC of the node currently carrying the link."""

    _key = "active_proxy"
    _attr_translation_key = "active_proxy"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        """Return the active proxy MAC (None while down)."""
        network = self._manager.network
        return None if network is None else network.active_proxy

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose link RSSI and node count."""
        network = self._manager.network
        return {
            "link_rssi": None if network is None else network.link_rssi,
            "nodes": len(self._manager.node_macs),
        }


class MeshSwitchProxyButton(_MeshNetworkEntity, ButtonEntity):  # type: ignore[misc]
    """Drop the current proxy and reconnect through another node."""

    _key = "switch_proxy"
    _attr_translation_key = "switch_proxy"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Switch to the next-best proxy node."""
        network = self._manager.network
        if network is None:
            return
        try:
            await network.switch_proxy()
        except Exception as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mesh_switch_proxy_failed",
                translation_placeholders={"error": str(exc)},
            ) from exc
