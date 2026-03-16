"""Shared pytest fixtures for Tuya BLE Mesh integration tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    CONF_DEVICE_TYPE,
    CONF_MAC_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_VENDOR_ID,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
)
from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshDeviceState,
)

if TYPE_CHECKING:
    pass


@pytest.fixture
def mock_telink_light() -> MagicMock:
    """Create a mock Telink light device."""
    device = MagicMock()
    device.address = "DC:23:4D:21:43:A5"
    device.name = "Telink Light"
    device.device_type = DEVICE_TYPE_LIGHT
    device.supports_power_monitoring = False
    device.rssi = -65
    device.is_on = False
    device.brightness = 100
    device.color_temp = 64
    device.rgb_color = (255, 255, 255)
    device.firmware_version = "1.6"
    return device


@pytest.fixture
def mock_telink_plug() -> MagicMock:
    """Create a mock Telink plug device."""
    device = MagicMock()
    device.address = "DC:23:4D:21:43:B6"
    device.name = "Telink Plug"
    device.device_type = DEVICE_TYPE_PLUG
    device.supports_power_monitoring = True
    device.rssi = -70
    device.is_on = False
    device.power_w = 0.0
    device.energy_kwh = 0.0
    device.firmware_version = "1.5"
    return device


@pytest.fixture
def mock_sig_plug() -> MagicMock:
    """Create a mock SIG Mesh plug device."""
    device = MagicMock()
    device.address = "E4:5F:01:8A:3C:D1"
    device.name = "SIG Plug"
    device.device_type = DEVICE_TYPE_SIG_PLUG
    device.supports_power_monitoring = False
    device.rssi = -68
    device.is_on = False
    device.firmware_version = "2.1"
    return device


@pytest.fixture
def mock_sig_bridge_plug() -> MagicMock:
    """Create a mock SIG bridge-controlled plug device."""
    device = MagicMock()
    device.address = "E4:5F:01:8A:3C:D2"
    device.name = "SIG Bridge Plug"
    device.device_type = DEVICE_TYPE_SIG_BRIDGE_PLUG
    device.supports_power_monitoring = False
    device.rssi = -72
    device.is_on = False
    device.firmware_version = "2.2"
    return device


@pytest.fixture
def mock_telink_bridge_light() -> MagicMock:
    """Create a mock Telink bridge-controlled light device."""
    device = MagicMock()
    device.address = "DC:23:4D:21:43:C7"
    device.name = "Telink Bridge Light"
    device.device_type = DEVICE_TYPE_TELINK_BRIDGE_LIGHT
    device.supports_power_monitoring = False
    device.rssi = -63
    device.is_on = False
    device.brightness = 100
    device.color_temp = 64
    device.rgb_color = (255, 255, 255)
    device.firmware_version = "1.7"
    return device


@pytest.fixture
def mock_coordinator(mock_telink_light: MagicMock) -> MagicMock:
    """Create a mock TuyaBLEMeshCoordinator with default Telink light device."""
    coord = MagicMock()
    coord.device = mock_telink_light
    coord.state = TuyaBLEMeshDeviceState(
        rssi=-65,
        firmware_version="1.6",
        power_w=None,
        energy_kwh=None,
        available=True,
        last_seen=None,
    )
    coord.capabilities = MagicMock()
    coord.capabilities.has_power_monitoring = False
    coord.add_listener = MagicMock(return_value=MagicMock())
    coord.async_add_listener = MagicMock(return_value=MagicMock())
    coord.async_request_refresh = AsyncMock()

    # Add command methods
    coord.async_turn_on = AsyncMock()
    coord.async_turn_off = AsyncMock()
    coord.async_set_brightness = AsyncMock()
    coord.async_set_color_temp = AsyncMock()
    coord.async_set_rgb_color = AsyncMock()
    coord.async_disconnect = AsyncMock()
    coord.async_identify = AsyncMock()

    return coord


@pytest.fixture
def mock_config_entry(mock_telink_light: MagicMock) -> MagicMock:
    """Create a mock ConfigEntry for Telink light."""
    entry = MagicMock()
    entry.entry_id = "test_entry_id_12345"
    entry.title = "Telink Light"
    entry.data = {
        CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
        CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
        CONF_MESH_NAME: "test_mesh",
        CONF_MESH_PASSWORD: "test_password",
        CONF_VENDOR_ID: "0x1001",
    }
    entry.runtime_data = None
    entry.add_update_listener = MagicMock()
    return entry


@pytest.fixture
def mock_hass() -> MagicMock:
    """Create a mock HomeAssistant instance."""
    hass = MagicMock()
    hass.data = {}
    hass.states = MagicMock()
    hass.config_entries = MagicMock()
    hass.services = MagicMock()
    hass.bus = MagicMock()
    hass.loop = MagicMock()
    return hass


@pytest.fixture(
    params=[
        DEVICE_TYPE_LIGHT,
        DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    ]
)
def light_device_type(request: pytest.FixtureRequest) -> str:
    """Parametrize across light device types."""
    return request.param


@pytest.fixture(
    params=[
        DEVICE_TYPE_PLUG,
        DEVICE_TYPE_SIG_PLUG,
        DEVICE_TYPE_SIG_BRIDGE_PLUG,
    ]
)
def plug_device_type(request: pytest.FixtureRequest) -> str:
    """Parametrize across plug device types."""
    return request.param


@pytest.fixture(
    params=[
        DEVICE_TYPE_LIGHT,
        DEVICE_TYPE_PLUG,
        DEVICE_TYPE_SIG_PLUG,
        DEVICE_TYPE_SIG_BRIDGE_PLUG,
        DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    ]
)
def any_device_type(request: pytest.FixtureRequest) -> str:
    """Parametrize across all device types."""
    return request.param
