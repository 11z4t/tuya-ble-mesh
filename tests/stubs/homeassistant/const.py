"""Minimal stub for homeassistant.const."""
from enum import StrEnum


class Platform(StrEnum):
    LIGHT = "light"
    SENSOR = "sensor"
    SWITCH = "switch"
    UPDATE = "update"
    NUMBER = "number"
    BINARY_SENSOR = "binary_sensor"
    BUTTON = "button"
    SELECT = "select"


SIGNAL_STRENGTH_DECIBELS_MILLIWATT = "dBm"


class UnitOfEnergy(StrEnum):
    KILO_WATT_HOUR = "kWh"
    WATT_HOUR = "Wh"
    MEGA_WATT_HOUR = "MWh"


class UnitOfPower(StrEnum):
    WATT = "W"
    KILO_WATT = "kW"


class UnitOfElectricPotential(StrEnum):
    VOLT = "V"
    MILLIVOLT = "mV"


class UnitOfElectricCurrent(StrEnum):
    AMPERE = "A"
    MILLIAMPERE = "mA"


ATTR_FRIENDLY_NAME = "friendly_name"
STATE_ON = "on"
STATE_OFF = "off"
STATE_UNAVAILABLE = "unavailable"
STATE_UNKNOWN = "unknown"
