"""Minimal stub for homeassistant.helpers.update_coordinator."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any, Generic, TypeVar

from homeassistant.helpers.entity import Entity

_DataT = TypeVar("_DataT")


class DataUpdateCoordinator(Generic[_DataT]):
    def __init__(
        self,
        hass: Any = None,
        logger: Any = None,
        *,
        name: str = "",
        update_interval: timedelta | None = None,
        update_method: Callable[[], Any] | None = None,
    ) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self._listeners: list[Callable[[], None]] = []
        self.last_update_success: bool = True
        self.data: _DataT | None = None  # type: ignore[assignment]

    def async_add_listener(
        self, update_callback: Callable[[], None], context: Any = None
    ) -> Callable[[], None]:
        """Add a listener, return an unsubscribe callable."""
        self._listeners.append(update_callback)

        def remove() -> None:
            try:
                self._listeners.remove(update_callback)
            except ValueError:
                pass

        return remove

    def _notify_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def async_refresh(self) -> None:
        pass

    async def async_request_refresh(self) -> None:
        pass

    async def async_set_updated_data(self, data: _DataT) -> None:
        self.data = data
        self._notify_listeners()


class CoordinatorEntity(Entity, Generic[_DataT]):
    """Entity that subscribes to coordinator updates."""

    def __init__(self, coordinator: DataUpdateCoordinator[_DataT]) -> None:
        self.coordinator = coordinator
        self._on_remove_callbacks: list[Callable[[], None]] = []

    def async_on_remove(self, func: Callable[[], None]) -> None:
        """Register a callback to be called when entity is removed."""
        self._on_remove_callbacks.append(func)

    def _call_on_remove_callbacks(self) -> None:
        """Call all on-remove callbacks (simulates async_remove)."""
        for cb in self._on_remove_callbacks:
            cb()
        self._on_remove_callbacks.clear()

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates when added to HA."""
        remove = self.coordinator.async_add_listener(self.async_write_ha_state)
        self.async_on_remove(remove)

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success
