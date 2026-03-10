"""Sensor entities for Tuya BLE Mesh devices.

Provides RSSI (signal strength), firmware version, power, and energy sensors
using the EntityDescription pattern for HA Core Platinum quality compliance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
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

from custom_components.tuya_ble_mesh.entity import TuyaBLEMeshEntity

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
        if description.key in ("power", "energy") and not coordinator.capabilities.has_power_monitoring:
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


class TuyaBLEMeshSensor(TuyaBLEMeshEntity, SensorEntity):
    """Unified sensor entity for Tuya BLE Mesh devices using EntityDescription pattern.

    Inherits CoordinatorEntity (via TuyaBLEMeshEntity) for automatic state
    updates. All sensor-specific behavior is defined in SENSOR_DESCRIPTIONS
    via value_fn and available_fn callables.
    """

    _attr_should_poll = False
    _attr_unique_id: str

    entity_description: TuyaBLEMeshSensorEntityDescription

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        description: TuyaBLEMeshSensorEntityDescription,
        device_info: DeviceInfo | None = None,
    ) -> None:
        """Initialize the sensor entity."""
        super().__init__(coordinator, entry_id, device_info)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.device.address}_{description.key}"

    @property
    def available(self) -> bool:
        """Return True if the sensor is available."""
        if (
            self.entity_description.available_fn is not None
            and not self.entity_description.available_fn(self.coordinator.state)
        ):
            return False
        return self.coordinator.state.available

    @property
    def native_value(self) -> StateType:
        """Return the current sensor value."""
        return self.entity_description.value_fn(self.coordinator.state)
