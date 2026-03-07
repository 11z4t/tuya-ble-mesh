"""Unit tests for the Tuya BLE Mesh config flow."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import HANDLERS

from custom_components.tuya_ble_mesh.config_flow import (
    TuyaBLEMeshConfigFlow,
    _validate_mac,
)
from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_DEV_KEY,
    CONF_DEVICE_TYPE,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
    CONF_UNICAST_OUR,
    CONF_UNICAST_TARGET,
    DOMAIN,
    SIG_MESH_PROXY_UUID,
)

# Test keys (not real secrets — random hex for unit tests)
_TEST_NET_KEY = "00112233445566778899aabbccddeeff"  # pragma: allowlist secret
_TEST_DEV_KEY = "ffeeddccbbaa99887766554433221100"  # pragma: allowlist secret
_TEST_APP_KEY = "aabbccddeeff00112233445566778899"  # pragma: allowlist secret


class TestValidateMac:
    """Test MAC address validation."""

    def test_valid_mac_uppercase(self) -> None:
        assert _validate_mac("DC:23:4D:21:43:A5") is None

    def test_valid_mac_lowercase(self) -> None:
        assert _validate_mac("dc:23:4d:21:43:a5") is None

    def test_valid_mac_mixed_case(self) -> None:
        assert _validate_mac("Dc:23:4d:21:43:A5") is None

    def test_invalid_mac_no_colons(self) -> None:
        assert _validate_mac("DC234D2143A5") == "invalid_mac"

    def test_invalid_mac_too_short(self) -> None:
        assert _validate_mac("DC:23:4D") == "invalid_mac"

    def test_invalid_mac_empty(self) -> None:
        assert _validate_mac("") == "invalid_mac"

    def test_invalid_mac_wrong_separator(self) -> None:
        assert _validate_mac("DC-23-4D-21-43-A5") == "invalid_mac"

    def test_invalid_mac_non_hex(self) -> None:
        assert _validate_mac("GG:23:4D:21:43:A5") == "invalid_mac"


class TestConfigFlowInit:
    """Test config flow initialization."""

    def test_domain_registered(self) -> None:
        assert DOMAIN in HANDLERS
        assert HANDLERS[DOMAIN] is TuyaBLEMeshConfigFlow

    def test_version(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        assert flow.VERSION == 1


class TestUserStep:
    """Test manual setup step."""

    @pytest.mark.asyncio
    async def test_user_step_shows_form(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        result = await flow.async_step_user(None)

        assert result["type"] == "form"
        assert result["step_id"] == "user"

    @pytest.mark.asyncio
    async def test_user_step_valid_mac_creates_entry(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        result = await flow.async_step_user(
            {
                CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
                CONF_MESH_NAME: "my_mesh",
                CONF_MESH_PASSWORD: "my_pass",  # pragma: allowlist secret
            }
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"
        assert result["data"][CONF_MESH_NAME] == "my_mesh"
        assert result["data"][CONF_MESH_PASSWORD] == "my_pass"  # pragma: allowlist secret

    @pytest.mark.asyncio
    async def test_user_step_invalid_mac_shows_error(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        result = await flow.async_step_user({CONF_MAC_ADDRESS: "invalid"})

        assert result["type"] == "form"
        assert result["errors"][CONF_MAC_ADDRESS] == "invalid_mac"

    @pytest.mark.asyncio
    async def test_user_step_mac_uppercased(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        result = await flow.async_step_user({CONF_MAC_ADDRESS: "dc:23:4d:21:43:a5"})

        assert result["type"] == "create_entry"
        assert result["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"

    @pytest.mark.asyncio
    async def test_user_step_defaults(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        result = await flow.async_step_user({CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5"})

        assert result["data"][CONF_MESH_NAME] == "out_of_mesh"
        assert result["data"][CONF_MESH_PASSWORD] == "123456"

    @pytest.mark.asyncio
    async def test_user_step_title_contains_mac_suffix(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        result = await flow.async_step_user({CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5"})

        assert "21:43:A5" in result["title"]


class TestBluetoothStep:
    """Test bluetooth discovery step."""

    @pytest.mark.asyncio
    async def test_bluetooth_discovery(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = lambda: None

        service_info = MagicMock(spec=BluetoothServiceInfoBleak)
        service_info.address = "DC:23:4D:21:43:A5"
        service_info.name = "out_of_mesh_1234"

        result = await flow.async_step_bluetooth(service_info)

        # Should show confirm form
        assert result["type"] == "form"
        assert result["step_id"] == "confirm"
        flow.async_set_unique_id.assert_called_once_with("DC:23:4D:21:43:A5")


class TestConfirmStep:
    """Test confirm step after discovery."""

    @pytest.mark.asyncio
    async def test_confirm_creates_entry(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        # Simulate discovery
        flow._discovery_info = {
            "address": "DC:23:4D:21:43:A5",
            "name": "out_of_mesh_1234",
        }

        result = await flow.async_step_confirm(
            {CONF_MESH_NAME: "my_mesh", CONF_MESH_PASSWORD: "pass123"}  # pragma: allowlist secret
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"
        assert result["data"][CONF_MESH_NAME] == "my_mesh"

    @pytest.mark.asyncio
    async def test_confirm_shows_form_without_input(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        flow._discovery_info = {
            "address": "DC:23:4D:21:43:A5",
            "name": "out_of_mesh_1234",
        }

        result = await flow.async_step_confirm(None)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"

    @pytest.mark.asyncio
    async def test_confirm_uses_defaults(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        flow._discovery_info = {
            "address": "DC:23:4D:21:43:A5",
            "name": "out_of_mesh_1234",
        }

        result = await flow.async_step_confirm({})

        assert result["type"] == "create_entry"
        assert result["data"][CONF_MESH_NAME] == "out_of_mesh"
        assert result["data"][CONF_MESH_PASSWORD] == "123456"

    @pytest.mark.asyncio
    async def test_confirm_title_from_discovery_name(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        flow._discovery_info = {
            "address": "DC:23:4D:21:43:A5",
            "name": "out_of_mesh_1234",
        }

        result = await flow.async_step_confirm({})

        assert result["title"] == "BLE Mesh Light 21:43:A5"


class TestDescriptionPlaceholders:
    """Test security warning description placeholders."""

    @pytest.mark.asyncio
    async def test_user_step_form_has_description_placeholders(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        result = await flow.async_step_user(None)

        assert result["type"] == "form"
        assert "description_placeholders" in result

    @pytest.mark.asyncio
    async def test_confirm_step_form_has_description_placeholders(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        flow._discovery_info = {
            "address": "DC:23:4D:21:43:A5",
            "name": "out_of_mesh_1234",
        }

        result = await flow.async_step_confirm(None)

        assert result["type"] == "form"
        assert "description_placeholders" in result
        assert result["description_placeholders"]["name"] == "out_of_mesh_1234"


class TestDeviceType:
    """Test device_type field in config flow."""

    @pytest.mark.asyncio
    async def test_user_flow_with_device_type_plug(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        result = await flow.async_step_user(
            {
                CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
                CONF_DEVICE_TYPE: "plug",
            }
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == "plug"

    @pytest.mark.asyncio
    async def test_default_device_type_is_light(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        result = await flow.async_step_user({CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5"})

        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == "light"

    @pytest.mark.asyncio
    async def test_confirm_default_device_type_is_light(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        flow._discovery_info = {
            "address": "DC:23:4D:21:43:A5",
            "name": "out_of_mesh_1234",
        }

        result = await flow.async_step_confirm({})

        assert result["data"][CONF_DEVICE_TYPE] == "light"


class TestSIGPlugStep:
    """Test SIG Mesh plug configuration step."""

    @pytest.mark.asyncio
    async def test_user_step_branches_to_sig_plug(self) -> None:
        """User step with sig_plug device type redirects to sig_plug step."""
        flow = TuyaBLEMeshConfigFlow()
        result = await flow.async_step_user(
            {
                CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF",
                CONF_DEVICE_TYPE: "sig_plug",
            }
        )

        # Should show the sig_plug form
        assert result["type"] == "form"
        assert result["step_id"] == "sig_plug"

    @pytest.mark.asyncio
    async def test_sig_plug_step_creates_entry(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Mesh FF",
        }

        result = await flow.async_step_sig_plug(
            {
                CONF_UNICAST_TARGET: "00aa",
                CONF_UNICAST_OUR: "0001",
                CONF_IV_INDEX: 0,
                CONF_NET_KEY: _TEST_NET_KEY,
                CONF_DEV_KEY: _TEST_DEV_KEY,
                CONF_APP_KEY: _TEST_APP_KEY,
            }
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == "sig_plug"
        assert result["data"][CONF_UNICAST_TARGET] == "00aa"
        assert result["data"][CONF_UNICAST_OUR] == "0001"
        assert result["data"][CONF_IV_INDEX] == 0
        assert result["data"][CONF_NET_KEY] == _TEST_NET_KEY
        assert result["data"][CONF_MAC_ADDRESS] == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_sig_plug_step_defaults(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Mesh FF",
        }

        # With keys provided, entry should be created with defaults
        result = await flow.async_step_sig_plug(
            {
                CONF_NET_KEY: _TEST_NET_KEY,
                CONF_DEV_KEY: _TEST_DEV_KEY,
                CONF_APP_KEY: _TEST_APP_KEY,
            }
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_UNICAST_TARGET] == "00B0"
        assert result["data"][CONF_UNICAST_OUR] == "0001"
        assert result["data"][CONF_IV_INDEX] == 0

    @pytest.mark.asyncio
    async def test_sig_plug_step_shows_form(self) -> None:
        flow = TuyaBLEMeshConfigFlow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Mesh FF",
        }

        result = await flow.async_step_sig_plug(None)

        assert result["type"] == "form"
        assert result["step_id"] == "sig_plug"


class TestAutoDiscovery:
    """Test auto-detection of SIG Mesh Proxy devices via bluetooth discovery."""

    @pytest.mark.asyncio
    async def test_discovery_with_proxy_uuid_routes_to_sig_plug(self) -> None:
        """Device with 0x1828 service UUID should route to sig_plug step."""
        flow = TuyaBLEMeshConfigFlow()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = lambda: None

        service_info = MagicMock(spec=BluetoothServiceInfoBleak)
        service_info.address = "AA:BB:CC:DD:EE:FF"
        service_info.name = "Mesh Proxy"
        service_info.service_uuids = [SIG_MESH_PROXY_UUID]

        result = await flow.async_step_bluetooth(service_info)

        assert result["type"] == "form"
        assert result["step_id"] == "sig_plug"

    @pytest.mark.asyncio
    async def test_discovery_without_proxy_uuid_routes_to_confirm(self) -> None:
        """Device without 0x1828 UUID should route to confirm step."""
        flow = TuyaBLEMeshConfigFlow()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = lambda: None

        service_info = MagicMock(spec=BluetoothServiceInfoBleak)
        service_info.address = "DC:23:4D:21:43:A5"
        service_info.name = "out_of_mesh_1234"
        service_info.service_uuids = []

        result = await flow.async_step_bluetooth(service_info)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"

    @pytest.mark.asyncio
    async def test_discovery_proxy_sets_discovery_info(self) -> None:
        """Discovery info should be populated for sig_plug step."""
        flow = TuyaBLEMeshConfigFlow()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = lambda: None

        service_info = MagicMock(spec=BluetoothServiceInfoBleak)
        service_info.address = "AA:BB:CC:DD:EE:FF"
        service_info.name = "Mesh Proxy"
        service_info.service_uuids = [SIG_MESH_PROXY_UUID]

        await flow.async_step_bluetooth(service_info)

        assert flow._discovery_info is not None
        assert flow._discovery_info["address"] == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_discovery_proxy_no_service_uuids_attr(self) -> None:
        """Device without service_uuids attribute should route to confirm."""
        flow = TuyaBLEMeshConfigFlow()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = lambda: None

        service_info = MagicMock(spec=BluetoothServiceInfoBleak)
        service_info.address = "DC:23:4D:21:43:A5"
        service_info.name = "out_of_mesh_1234"
        # Remove service_uuids attribute
        del service_info.service_uuids

        result = await flow.async_step_bluetooth(service_info)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"

    @pytest.mark.asyncio
    async def test_discovery_proxy_completes_full_flow(self) -> None:
        """Proxy discovery → sig_plug form → entry creation."""
        flow = TuyaBLEMeshConfigFlow()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = lambda: None

        service_info = MagicMock(spec=BluetoothServiceInfoBleak)
        service_info.address = "AA:BB:CC:DD:EE:FF"
        service_info.name = "Mesh Proxy"
        service_info.service_uuids = [SIG_MESH_PROXY_UUID]

        # Step 1: bluetooth discovery → sig_plug form
        result = await flow.async_step_bluetooth(service_info)
        assert result["step_id"] == "sig_plug"

        # Step 2: submit sig_plug form with keys → entry created
        result = await flow.async_step_sig_plug(
            {
                CONF_NET_KEY: _TEST_NET_KEY,
                CONF_DEV_KEY: _TEST_DEV_KEY,
                CONF_APP_KEY: _TEST_APP_KEY,
            }
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == "sig_plug"
        assert result["data"][CONF_MAC_ADDRESS] == "AA:BB:CC:DD:EE:FF"
