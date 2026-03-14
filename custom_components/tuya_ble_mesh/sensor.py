"""Sensor entities for Tuya BLE Mesh devices.

Provides RSSI (signal strength), firmware version, power, and energy sensors
using the EntityDescription pattern for HA Core Platinum quality compliance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory  # type: ignore[attr-defined]
from homeassistant.helpers.typing import StateType

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from custom_components.tuya_ble_mesh import TuyaBLEMeshConfigEntry
    from custom_components.tuya_ble_mesh.coordinator import (
        TuyaBLEMeshCoordinator,
        TuyaBLEMeshDeviceState,
    )

    AddEntitiesCallback = Callable[..., None]

_LOGGER = logging.getLogger(__name__)

# BLE mesh serializes commands — limit to one concurrent update
PARALLEL_UPDATES = 1


def _connection_quality(state: TuyaBLEMeshDeviceState) -> str | None:
    """Map RSSI to a connection quality label.

    Returns 'good' for RSSI ≥ -60, 'marginal' for -80 to -61, 'poor' for < -80.
    Returns None when RSSI is not available.
    """
    if state.rssi is None:
        return None
    if state.rssi >= -60:
        return "good"
    if state.rssi >= -80:
        return "marginal"
    return "poor"


def _last_seen_datetime(state: TuyaBLEMeshDeviceState) -> datetime | None:
    """Convert last_seen Unix timestamp to a UTC-aware datetime, or None."""
    if state.last_seen is None:
        return None
    return datetime.fromtimestamp(state.last_seen, tz=UTC)


@dataclass(frozen=True, kw_only=True)
class TuyaBLEMeshSensorEntityDescription(SensorEntityDescription):
    """Extended sensor entity description for Tuya BLE Mesh sensors.

    Attributes:
        key: Unique identifier for this sensor type.
        device_class: Classification of the sensor (signal strength, power, etc).
        native_unit_of_measurement: Unit displayed in the UI.
        state_class: Type of state (measurement, total, etc) for statistics.
        entity_category: Category (diagnostic, config, etc) or None for primary.
        entity_registry_enabled_default: Whether entity is enabled by default.
        suggested_display_precision: Number of decimal places to display.
        value_fn: Callable that extracts the sensor value from coordinator state.
        available_fn: Optional callable to determine availability based on state.
    """

    value_fn: Callable[[TuyaBLEMeshDeviceState], StateType]
    available_fn: Callable[[TuyaBLEMeshDeviceState], bool] | None = None


SENSOR_DESCRIPTIONS: tuple[TuyaBLEMeshSensorEntityDescription, ...] = (
    TuyaBLEMeshSensorEntityDescription(
        key="rssi",
        translation_key="rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda state: state.rssi,
    ),
    TuyaBLEMeshSensorEntityDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,  # G3: disabled by default
        value_fn=lambda state: state.firmware_version,
    ),
    TuyaBLEMeshSensorEntityDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda state: state.power_w,
        available_fn=lambda state: state.power_w is not None,
    ),
    TuyaBLEMeshSensorEntityDescription(
        key="energy",
        translation_key="energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda state: state.energy_kwh,
        available_fn=lambda state: state.energy_kwh is not None,
    ),
    TuyaBLEMeshSensorEntityDescription(
        key="connection_quality",
        translation_key="connection_quality",
        device_class=SensorDeviceClass.ENUM,
        options=["good", "marginal", "poor"],
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:signal",
        value_fn=_connection_quality,
    ),
    TuyaBLEMeshSensorEntityDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_seen_datetime,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuyaBLEMeshConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE Mesh sensor entities from a config entry.

    Creates sensor entities based on SENSOR_DESCRIPTIONS. Power and energy
    sensors are only created if the device supports power monitoring (most
    BLE Mesh plugs like Malmbergs S17 do NOT have power metering).

    Args:
        hass: Home Assistant instance.
        entry: Config entry being set up.
        async_add_entities: Callback to register new entities.
    """
    runtime_data = entry.runtime_data
    coordinator: TuyaBLEMeshCoordinator = runtime_data.coordinator
    device_info: DeviceInfo = runtime_data.device_info

    # Create sensors based on descriptions
    entities: list[SensorEntity] = []
    for description in SENSOR_DESCRIPTIONS:
        # Power/energy sensors require device support
        if description.key in ("power", "energy") and not getattr(
            coordinator.device, "supports_power_monitoring", False
        ):
            continue

        entities.append(
            TuyaBLEMeshSensor(
                coordinator=coordinator,
                entry_id=entry.entry_id,
                description=description,
                device_info=device_info,
            )
        )

    async_add_entities(entities)


class TuyaBLEMeshSensor(SensorEntity):
    """Unified sensor entity for Tuya BLE Mesh devices using EntityDescription pattern.

    This class replaces individual sensor classes (RSSI, firmware, power, energy)
    with a single, data-driven implementation following HA Core Platinum patterns.
    All sensor-specific behavior is defined in SENSOR_DESCRIPTIONS via value_fn
    and available_fn callables.
    """

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_unique_id: str

    entity_description: TuyaBLEMeshSensorEntityDescription

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        description: TuyaBLEMeshSensorEntityDescription,
        device_info: DeviceInfo | None = None,
    ) -> None:
        """Initialize the sensor entity.

        Args:
            coordinator: Coordinator managing device state.
            entry_id: Config entry ID for this integration instance.
            description: Sensor entity description defining sensor behavior.
            device_info: Device registry information.
        """
        self.entity_description = description
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device.address}_{description.key}"
        if device_info is not None:
            self._attr_device_info = device_info
        self._remove_listener: Any = None

    @property
    def unique_id(self) -> str:
        """Return unique ID for this sensor."""
        return self._attr_unique_id

    @property
    def available(self) -> bool:
        """Return True if the sensor is available.

        Uses description.available_fn if provided, otherwise falls back
        to coordinator.state.available. This allows sensors like power/energy
        to report unavailable if the device doesn't support those features.
        """
        if (
            self.entity_description.available_fn is not None
            and not self.entity_description.available_fn(self._coordinator.state)
        ):
            return False
        # Base availability from coordinator
        return self._coordinator.state.available

    @property
    def native_value(self) -> StateType:
        """Return the current sensor value.

        Extracts the value from coordinator state using the value_fn
        defined in the entity description. This eliminates duplicate
        property definitions across multiple sensor classes.
        """
        return self.entity_description.value_fn(self._coordinator.state)

    async def async_added_to_hass(self) -> None:
        """Register state listener when added to Home Assistant."""
        self._remove_listener = self._coordinator.add_listener(self._handle_coordinator_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove state listener when removed from Home Assistant."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        Called by coordinator when device state changes. Triggers
        entity state write to Home Assistant.
        """
        self.async_write_ha_state()
