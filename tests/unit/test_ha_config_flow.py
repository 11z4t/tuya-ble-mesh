"""Unit tests for the Tuya BLE Mesh config flow."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import HANDLERS

from custom_components.tuya_ble_mesh.config_flow import (
    TuyaBLEMeshConfigFlow,
    TuyaBLEMeshOptionsFlow,
    _parse_json_body,
    _test_bridge_with_session,
    _validate_bridge_host,
    _validate_hex_key,
    _validate_mac,
)
from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_DEV_KEY,
    CONF_DEVICE_TYPE,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
    CONF_UNICAST_OUR,
    CONF_UNICAST_TARGET,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    DOMAIN,
    SIG_MESH_PROV_UUID,
    SIG_MESH_PROXY_UUID,
)


def _make_flow() -> TuyaBLEMeshConfigFlow:
    """Create a config flow with a mock hass attached."""
    flow = TuyaBLEMeshConfigFlow()
    flow.context = {"source": "user"}
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.flow = MagicMock()
    hass.config_entries.flow.async_progress_by_handler = MagicMock(return_value=[])
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.config_entries.async_entry_for_domain_unique_id = MagicMock(return_value=None)
    flow.hass = hass
    return flow


# Test keys (not real secrets — random hex for unit tests)
_TEST_NET_KEY = "00112233445566778899aabbccddeeff"  # pragma: allowlist secret
_TEST_DEV_KEY = "ffeeddccbbaa99887766554433221100"  # pragma: allowlist secret
_TEST_APP_KEY = "aabbccddeeff00112233445566778899"  # pragma: allowlist secret


@pytest.mark.requires_ha
class TestParseJsonBody:
    """Test _parse_json_body() helper."""

    def test_valid_dict_returned(self) -> None:
        """Valid JSON dict is returned as-is."""
        result = _parse_json_body('{"status": "ok", "value": 123}')
        assert result == {"status": "ok", "value": 123}

    def test_valid_empty_dict(self) -> None:
        """Empty dict is valid."""
        result = _parse_json_body("{}")
        assert result == {}

    def test_invalid_json_returns_empty_dict(self) -> None:
        """Invalid JSON returns empty dict."""
        result = _parse_json_body("{invalid json")
        assert result == {}

    def test_json_array_returns_empty_dict(self) -> None:
        """JSON array (not dict) returns empty dict."""
        result = _parse_json_body("[1, 2, 3]")
        assert result == {}

    def test_json_string_returns_empty_dict(self) -> None:
        """JSON string (not dict) returns empty dict."""
        result = _parse_json_body('"hello"')
        assert result == {}

    def test_json_number_returns_empty_dict(self) -> None:
        """JSON number (not dict) returns empty dict."""
        result = _parse_json_body("123")
        assert result == {}

    def test_json_null_returns_empty_dict(self) -> None:
        """JSON null returns empty dict."""
        result = _parse_json_body("null")
        assert result == {}


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
class TestConfigFlowInit:
    """Test config flow initialization."""

    def test_domain_registered(self) -> None:
        assert DOMAIN in HANDLERS
        assert HANDLERS[DOMAIN] is TuyaBLEMeshConfigFlow

    def test_version(self) -> None:
        flow = _make_flow()
        assert flow.VERSION == 1

    def test_async_get_options_flow(self) -> None:
        """async_get_options_flow returns TuyaBLEMeshOptionsFlow instance."""
        config_entry = MagicMock()
        config_entry.data = {CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT}
        config_entry.entry_id = "test_entry"

        flow = TuyaBLEMeshConfigFlow.async_get_options_flow(config_entry)

        assert isinstance(flow, TuyaBLEMeshOptionsFlow)
        assert flow._config_entry is config_entry


@pytest.mark.requires_ha
class TestUserStep:
    """Test manual setup step."""

    @pytest.mark.asyncio
    async def test_user_step_shows_form(self) -> None:
        flow = _make_flow()
        result = await flow.async_step_user(None)

        assert result["type"] == "form"
        assert result["step_id"] == "user"

    @pytest.mark.asyncio
    async def test_user_step_valid_mac_creates_entry(self) -> None:
        flow = _make_flow()
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
        flow = _make_flow()
        result = await flow.async_step_user({CONF_MAC_ADDRESS: "invalid"})

        assert result["type"] == "form"
        assert result["errors"][CONF_MAC_ADDRESS] == "invalid_mac"

    @pytest.mark.asyncio
    async def test_user_step_mac_uppercased(self) -> None:
        flow = _make_flow()
        result = await flow.async_step_user({CONF_MAC_ADDRESS: "dc:23:4d:21:43:a5"})

        assert result["type"] == "create_entry"
        assert result["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"

    @pytest.mark.asyncio
    async def test_user_step_defaults(self) -> None:
        flow = _make_flow()
        result = await flow.async_step_user({CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5"})

        assert result["data"][CONF_MESH_NAME] == "out_of_mesh"
        assert result["data"][CONF_MESH_PASSWORD] == "123456"

    @pytest.mark.asyncio
    async def test_user_step_title_contains_mac_suffix(self) -> None:
        flow = _make_flow()
        result = await flow.async_step_user({CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5"})

        assert "21:43:A5" in result["title"]


@pytest.mark.requires_ha
class TestBluetoothStep:
    """Test bluetooth discovery step."""

    @pytest.mark.asyncio
    async def test_bluetooth_discovery(self) -> None:
        flow = _make_flow()
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


@pytest.mark.requires_ha
class TestConfirmStep:
    """Test confirm step after discovery."""

    @pytest.mark.asyncio
    async def test_confirm_creates_entry(self) -> None:
        flow = _make_flow()
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
        flow = _make_flow()
        flow._discovery_info = {
            "address": "DC:23:4D:21:43:A5",
            "name": "out_of_mesh_1234",
        }

        result = await flow.async_step_confirm(None)

        assert result["type"] == "form"
        assert result["step_id"] == "confirm"

    @pytest.mark.asyncio
    async def test_confirm_uses_defaults(self) -> None:
        flow = _make_flow()
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
        flow = _make_flow()
        flow._discovery_info = {
            "address": "DC:23:4D:21:43:A5",
            "name": "out_of_mesh_1234",
        }

        result = await flow.async_step_confirm({})

        assert result["title"] == "BLE Mesh Light 21:43:A5"


@pytest.mark.requires_ha
class TestDescriptionPlaceholders:
    """Test security warning description placeholders."""

    @pytest.mark.asyncio
    async def test_user_step_form_has_description_placeholders(self) -> None:
        flow = _make_flow()
        result = await flow.async_step_user(None)

        assert result["type"] == "form"
        assert "description_placeholders" in result

    @pytest.mark.asyncio
    async def test_confirm_step_form_has_description_placeholders(self) -> None:
        flow = _make_flow()
        flow._discovery_info = {
            "address": "DC:23:4D:21:43:A5",
            "name": "out_of_mesh_1234",
        }

        result = await flow.async_step_confirm(None)

        assert result["type"] == "form"
        assert "description_placeholders" in result
        assert result["description_placeholders"]["name"] == "out_of_mesh_1234"


@pytest.mark.requires_ha
class TestDeviceType:
    """Test device_type field in config flow."""

    @pytest.mark.asyncio
    async def test_user_flow_with_device_type_plug(self) -> None:
        flow = _make_flow()
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
        flow = _make_flow()
        result = await flow.async_step_user({CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5"})

        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == "light"

    @pytest.mark.asyncio
    async def test_confirm_default_device_type_is_light(self) -> None:
        flow = _make_flow()
        flow._discovery_info = {
            "address": "DC:23:4D:21:43:A5",
            "name": "out_of_mesh_1234",
        }

        result = await flow.async_step_confirm({})

        assert result["data"][CONF_DEVICE_TYPE] == "light"


@pytest.mark.requires_ha
class TestSIGPlugStep:
    """Test SIG Mesh plug configuration step."""

    @pytest.mark.asyncio
    async def test_user_step_branches_to_sig_plug(self) -> None:
        """User step with sig_plug device type redirects to sig_plug step."""
        flow = _make_flow()
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
        """Auto-provisioning: _run_provision is called and entry is created."""
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Mesh FF",
        }

        with patch.object(
            flow,
            "_run_provision",
            new=AsyncMock(return_value=(_TEST_NET_KEY, _TEST_DEV_KEY, _TEST_APP_KEY)),
        ):
            result = await flow.async_step_sig_plug({})

        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == "sig_plug"
        assert result["data"][CONF_UNICAST_TARGET] == "00B0"
        assert result["data"][CONF_UNICAST_OUR] == "0001"
        assert result["data"][CONF_IV_INDEX] == 0
        assert result["data"][CONF_NET_KEY] == _TEST_NET_KEY
        assert result["data"][CONF_DEV_KEY] == _TEST_DEV_KEY
        assert result["data"][CONF_APP_KEY] == _TEST_APP_KEY
        assert result["data"][CONF_MAC_ADDRESS] == "AA:BB:CC:DD:EE:FF"

    @pytest.mark.asyncio
    async def test_sig_plug_step_defaults(self) -> None:
        """Auto-provisioning sets fixed default unicast addresses."""
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Mesh FF",
        }

        with patch.object(
            flow,
            "_run_provision",
            new=AsyncMock(return_value=(_TEST_NET_KEY, _TEST_DEV_KEY, _TEST_APP_KEY)),
        ):
            result = await flow.async_step_sig_plug({})

        assert result["type"] == "create_entry"
        assert result["data"][CONF_UNICAST_TARGET] == "00B0"
        assert result["data"][CONF_UNICAST_OUR] == "0001"
        assert result["data"][CONF_IV_INDEX] == 0

    @pytest.mark.asyncio
    async def test_sig_plug_step_shows_form(self) -> None:
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Mesh FF",
        }

        result = await flow.async_step_sig_plug(None)

        assert result["type"] == "form"
        assert result["step_id"] == "sig_plug"


@pytest.mark.requires_ha
class TestAutoDiscovery:
    """Test auto-detection of SIG Mesh Proxy devices via bluetooth discovery."""

    @pytest.mark.asyncio
    async def test_discovery_with_proxy_uuid_routes_to_sig_plug(self) -> None:
        """Device with 0x1828 service UUID should route to sig_plug step."""
        flow = _make_flow()
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
        flow = _make_flow()
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
        flow = _make_flow()
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
        flow = _make_flow()
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
        flow = _make_flow()
        flow.async_set_unique_id = AsyncMock()
        flow._abort_if_unique_id_configured = lambda: None

        service_info = MagicMock(spec=BluetoothServiceInfoBleak)
        service_info.address = "AA:BB:CC:DD:EE:FF"
        service_info.name = "Mesh Proxy"
        service_info.service_uuids = [SIG_MESH_PROXY_UUID]

        # Step 1: bluetooth discovery → sig_plug form
        result = await flow.async_step_bluetooth(service_info)
        assert result["step_id"] == "sig_plug"

        # Step 2: submit sig_plug form (empty — auto-provisions) → entry created
        with patch.object(
            flow,
            "_run_provision",
            new=AsyncMock(return_value=(_TEST_NET_KEY, _TEST_DEV_KEY, _TEST_APP_KEY)),
        ):
            result = await flow.async_step_sig_plug({})
        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == "sig_plug"
        assert result["data"][CONF_MAC_ADDRESS] == "AA:BB:CC:DD:EE:FF"


@pytest.mark.requires_ha
class TestValidateHexKey:
    """Test _validate_hex_key() helper."""

    def test_valid_32_char_hex_lowercase(self) -> None:
        key = "00112233445566778899aabbccddeeff"  # pragma: allowlist secret
        assert _validate_hex_key(key) is True

    def test_valid_32_char_hex_uppercase(self) -> None:
        key = "00112233445566778899AABBCCDDEEFF"  # pragma: allowlist secret
        assert _validate_hex_key(key) is True

    def test_valid_32_char_hex_mixed(self) -> None:
        key = "00112233445566778899AaBbCcDdEeFf"  # pragma: allowlist secret
        assert _validate_hex_key(key) is True

    def test_invalid_too_short(self) -> None:
        assert _validate_hex_key("00112233") is False

    def test_invalid_too_long(self) -> None:
        key = "00112233445566778899aabbccddeeff00"  # pragma: allowlist secret
        assert _validate_hex_key(key) is False

    def test_invalid_non_hex_chars(self) -> None:
        assert _validate_hex_key("00112233445566778899aabbccddeegg") is False

    def test_invalid_empty(self) -> None:
        assert _validate_hex_key("") is False

    def test_invalid_spaces(self) -> None:
        assert _validate_hex_key("0011 2233 4455 6677 8899 aabb ccdd eeff") is False


@pytest.mark.requires_ha
class TestSigBridgeStep:
    """Test SIG Mesh Bridge plug configuration step."""

    @pytest.mark.asyncio
    async def test_sig_bridge_shows_form(self) -> None:
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Bridge Plug",
        }

        result = await flow.async_step_sig_bridge(None)

        assert result["type"] == "form"
        assert result["step_id"] == "sig_bridge"

    @pytest.mark.asyncio
    @patch(
        "custom_components.tuya_ble_mesh.config_flow._test_bridge_with_session",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_sig_bridge_creates_entry(self, mock_bridge: AsyncMock) -> None:
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Bridge Plug",
        }

        result = await flow.async_step_sig_bridge(
            {
                CONF_BRIDGE_HOST: "192.168.1.100",
                CONF_BRIDGE_PORT: 8099,
                CONF_UNICAST_TARGET: "00B0",
            }
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_SIG_BRIDGE_PLUG
        assert result["data"][CONF_BRIDGE_HOST] == "192.168.1.100"
        assert result["data"][CONF_BRIDGE_PORT] == 8099
        assert result["data"][CONF_MAC_ADDRESS] == "AA:BB:CC:DD:EE:FF"
        assert result["data"][CONF_UNICAST_TARGET] == "00B0"
        mock_bridge.assert_called_once()

    @pytest.mark.asyncio
    @patch(
        "custom_components.tuya_ble_mesh.config_flow._test_bridge_with_session",
        new_callable=AsyncMock,
        return_value=False,
    )
    async def test_sig_bridge_connection_failure(self, mock_bridge: AsyncMock) -> None:
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Bridge Plug",
        }

        result = await flow.async_step_sig_bridge(
            {
                CONF_BRIDGE_HOST: "192.168.1.100",
                CONF_BRIDGE_PORT: 8099,
            }
        )

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_user_step_branches_to_sig_bridge(self) -> None:
        flow = _make_flow()

        with patch(
            "custom_components.tuya_ble_mesh.config_flow._test_bridge_with_session",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await flow.async_step_user(
                {
                    CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF",
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG,
                }
            )

        assert result["type"] == "form"
        assert result["step_id"] == "sig_bridge"

    @pytest.mark.asyncio
    async def test_sig_bridge_invalid_host_shows_error(self) -> None:
        """Invalid bridge host shows error."""
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Bridge Plug",
        }

        result = await flow.async_step_sig_bridge(
            {
                CONF_BRIDGE_HOST: "http://malicious.com",
                CONF_BRIDGE_PORT: 8099,
            }
        )

        assert result["type"] == "form"
        assert result["errors"][CONF_BRIDGE_HOST] == "invalid_bridge_host"


@pytest.mark.requires_ha
class TestTelinkBridgeStep:
    """Test Telink Bridge light configuration step."""

    @pytest.mark.asyncio
    async def test_telink_bridge_shows_form(self) -> None:
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "Telink Bridge Light",
        }

        result = await flow.async_step_telink_bridge(None)

        assert result["type"] == "form"
        assert result["step_id"] == "telink_bridge"

    @pytest.mark.asyncio
    @patch(
        "custom_components.tuya_ble_mesh.config_flow._test_bridge_with_session",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_telink_bridge_creates_entry(self, mock_bridge: AsyncMock) -> None:
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "Telink Bridge Light",
        }

        result = await flow.async_step_telink_bridge(
            {
                CONF_BRIDGE_HOST: "192.168.1.200",
                CONF_BRIDGE_PORT: 9000,
            }
        )

        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_TELINK_BRIDGE_LIGHT
        assert result["data"][CONF_BRIDGE_HOST] == "192.168.1.200"
        assert result["data"][CONF_BRIDGE_PORT] == 9000
        assert result["data"][CONF_MAC_ADDRESS] == "AA:BB:CC:DD:EE:FF"
        assert result["title"] == "Telink Bridge Light DD:EE:FF"
        mock_bridge.assert_called_once()
        assert mock_bridge.call_args.args[-2:] == ("192.168.1.200", 9000)

    @pytest.mark.asyncio
    @patch(
        "custom_components.tuya_ble_mesh.config_flow._test_bridge_with_session",
        new_callable=AsyncMock,
        return_value=False,
    )
    async def test_telink_bridge_connection_failure(self, mock_bridge: AsyncMock) -> None:
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "Telink Bridge Light",
        }

        result = await flow.async_step_telink_bridge(
            {
                CONF_BRIDGE_HOST: "192.168.1.200",
                CONF_BRIDGE_PORT: 9000,
            }
        )

        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    @pytest.mark.asyncio
    async def test_user_step_branches_to_telink_bridge(self) -> None:
        flow = _make_flow()

        with patch(
            "custom_components.tuya_ble_mesh.config_flow._test_bridge_with_session",
            new_callable=AsyncMock,
            return_value=True,
        ):
            result = await flow.async_step_user(
                {
                    CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF",
                    CONF_DEVICE_TYPE: DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
                }
            )

        assert result["type"] == "form"
        assert result["step_id"] == "telink_bridge"

    @pytest.mark.asyncio
    async def test_telink_bridge_invalid_host_shows_error(self) -> None:
        """Invalid bridge host shows error."""
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "Telink Bridge Light",
        }

        result = await flow.async_step_telink_bridge(
            {
                CONF_BRIDGE_HOST: "127.0.0.1",  # SSRF risk
                CONF_BRIDGE_PORT: 9000,
            }
        )

        assert result["type"] == "form"
        assert result["errors"][CONF_BRIDGE_HOST] == "invalid_bridge_host"


@pytest.mark.requires_ha
class TestTestBridge:
    """Test _test_bridge_with_session() connection helper."""

    @pytest.mark.asyncio
    async def test_bridge_success(self) -> None:
        """Successful bridge connection returns True."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value='{"status": "ok"}')
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        _patch_target = "homeassistant.helpers.aiohttp_client.async_get_clientsession"
        with patch(_patch_target, return_value=mock_session):
            mock_hass = MagicMock()
            result = await _test_bridge_with_session(mock_hass, "192.168.1.100", 8099)

        assert result is True

    @pytest.mark.asyncio
    async def test_bridge_bad_status(self) -> None:
        """Bridge returns non-ok status."""
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        _patch_target = "homeassistant.helpers.aiohttp_client.async_get_clientsession"
        with patch(_patch_target, return_value=mock_session):
            mock_hass = MagicMock()
            result = await _test_bridge_with_session(mock_hass, "192.168.1.100", 8099)

        assert result is False

    @pytest.mark.asyncio
    async def test_bridge_connection_refused(self) -> None:
        """Connection failure returns False."""
        import aiohttp

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientError)

        _patch_target = "homeassistant.helpers.aiohttp_client.async_get_clientsession"
        with patch(_patch_target, return_value=mock_session):
            mock_hass = MagicMock()
            result = await _test_bridge_with_session(mock_hass, "192.168.1.100", 8099)

        assert result is False

    @pytest.mark.asyncio
    async def test_bridge_timeout(self) -> None:
        """Timeout returns False."""
        import asyncio as _asyncio

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=_asyncio.TimeoutError)

        _patch_target = "homeassistant.helpers.aiohttp_client.async_get_clientsession"
        with patch(_patch_target, return_value=mock_session):
            mock_hass = MagicMock()
            result = await _test_bridge_with_session(mock_hass, "192.168.1.100", 8099)

        assert result is False


@pytest.mark.requires_ha
class TestValidateBridgeHost:
    """Test _validate_bridge_host() helper."""

    def test_valid_ipv4(self) -> None:
        assert _validate_bridge_host("192.168.1.100") is None

    def test_valid_hostname(self) -> None:
        assert _validate_bridge_host("myhost.local") is None

    def test_empty_string(self) -> None:
        assert _validate_bridge_host("") == "invalid_bridge_host"

    def test_url_rejected(self) -> None:
        assert _validate_bridge_host("http://192.168.1.100") == "invalid_bridge_host"

    def test_path_rejected(self) -> None:
        assert _validate_bridge_host("192.168.1.100/path") == "invalid_bridge_host"

    def test_backslash_rejected(self) -> None:
        """Reject backslash in host (Windows path injection)."""
        assert _validate_bridge_host("192.168.1.100\\path") == "invalid_bridge_host"

    def test_pattern_mismatch_rejected(self) -> None:
        """Reject malformed host string that doesn't match pattern."""
        assert _validate_bridge_host("host@domain") == "invalid_bridge_host"

    def test_ssrf_loopback_ipv4_rejected(self) -> None:
        """Reject loopback address (127.0.0.1 SSRF risk)."""
        assert _validate_bridge_host("127.0.0.1") == "invalid_bridge_host"

    def test_ssrf_link_local_rejected(self) -> None:
        """Reject link-local address (169.254.x.x SSRF risk)."""
        assert _validate_bridge_host("169.254.169.254") == "invalid_bridge_host"

    def test_ssrf_ipv6_loopback_rejected(self) -> None:
        """Reject IPv6 loopback (::1 SSRF risk)."""
        assert _validate_bridge_host("::1") == "invalid_bridge_host"

    def test_ssrf_ipv6_link_local_rejected(self) -> None:
        """Reject IPv6 link-local (fe80:: SSRF risk)."""
        assert _validate_bridge_host("fe80::1") == "invalid_bridge_host"

    def test_ssrf_hex_encoded_ip_rejected(self) -> None:
        """Reject hex-encoded IP (0x7f000001 = 127.0.0.1)."""
        assert _validate_bridge_host("0x7f000001") == "invalid_bridge_host"

    def test_ssrf_hex_uppercase_rejected(self) -> None:
        """Reject uppercase hex-encoded IP."""
        assert _validate_bridge_host("0X7F000001") == "invalid_bridge_host"

    def test_valid_hostname_not_ssrf(self) -> None:
        """Hostnames are allowed (not resolved, so no SSRF check)."""
        assert _validate_bridge_host("localhost") is None


@pytest.mark.requires_ha
class TestSigPlugKeyValidationErrors:
    """Test error handling in sig_plug auto-provisioning step."""

    @pytest.mark.asyncio
    async def test_invalid_net_key_shows_error(self) -> None:
        """Provisioning failure shows provisioning_failed error."""
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Mesh FF",
        }

        with patch.object(
            flow,
            "_run_provision",
            new=AsyncMock(side_effect=Exception("BLE connection failed")),
        ):
            result = await flow.async_step_sig_plug({})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "provisioning_failed"

    @pytest.mark.asyncio
    async def test_invalid_dev_key_shows_error(self) -> None:
        """Provisioning failure with different error also shows provisioning_failed."""
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Mesh FF",
        }

        with patch.object(
            flow,
            "_run_provision",
            new=AsyncMock(side_effect=RuntimeError("timeout")),
        ):
            result = await flow.async_step_sig_plug({})

        assert result["type"] == "form"
        assert result["errors"]["base"] == "provisioning_failed"

    @pytest.mark.asyncio
    async def test_invalid_app_key_shows_error(self) -> None:
        """Provisioning failure returns form, not entry."""
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Mesh FF",
        }

        with patch.object(
            flow,
            "_run_provision",
            new=AsyncMock(side_effect=Exception("device not found")),
        ):
            result = await flow.async_step_sig_plug({})

        assert result["type"] == "form"
        assert result["step_id"] == "sig_plug"
        assert result["errors"]["base"] == "provisioning_failed"

    @pytest.mark.asyncio
    async def test_all_keys_invalid_shows_all_errors(self) -> None:
        """After provisioning failure, form is shown again with base error."""
        flow = _make_flow()
        flow._discovery_info = {
            "address": "AA:BB:CC:DD:EE:FF",
            "name": "SIG Mesh FF",
        }

        with patch.object(
            flow,
            "_run_provision",
            new=AsyncMock(side_effect=Exception("confirmation mismatch")),
        ):
            result = await flow.async_step_sig_plug({})

        assert result["type"] == "form"
        assert "base" in result["errors"]


@pytest.mark.requires_ha
class TestBluetoothSigMeshProxyDiscovery:
    """Test SIG Mesh proxy discovery via bluetooth step with UUID 1828."""

    @pytest.mark.asyncio
    async def test_bluetooth_with_proxy_uuid_sets_unique_id(self) -> None:
        flow = _make_flow()
        flow.async_set_unique_id = AsyncMock()

        service_info = MagicMock(spec=BluetoothServiceInfoBleak)
        service_info.address = "AA:BB:CC:DD:EE:FF"
        service_info.name = "SigMesh"
        service_info.service_uuids = [SIG_MESH_PROXY_UUID]

        await flow.async_step_bluetooth(service_info)

        flow.async_set_unique_id.assert_called_once_with("AA:BB:CC:DD:EE:FF")

    @pytest.mark.asyncio
    async def test_bluetooth_proxy_preserves_name_in_discovery(self) -> None:
        flow = _make_flow()
        flow.async_set_unique_id = AsyncMock()

        service_info = MagicMock(spec=BluetoothServiceInfoBleak)
        service_info.address = "AA:BB:CC:DD:EE:FF"
        service_info.name = "SigMesh"
        service_info.service_uuids = [SIG_MESH_PROXY_UUID]

        await flow.async_step_bluetooth(service_info)

        assert flow._discovery_info["name"] == "SigMesh"

    @pytest.mark.asyncio
    async def test_bluetooth_none_name_becomes_empty(self) -> None:
        flow = _make_flow()
        flow.async_set_unique_id = AsyncMock()

        service_info = MagicMock(spec=BluetoothServiceInfoBleak)
        service_info.address = "AA:BB:CC:DD:EE:FF"
        service_info.name = None
        service_info.service_uuids = []

        await flow.async_step_bluetooth(service_info)

        assert flow._discovery_info["name"] == ""


@pytest.mark.requires_ha
class TestRunProvision:
    """Test _run_provision provisioning flow."""

    @pytest.mark.asyncio
    async def test_run_provision_success_full_flow(self) -> None:
        """Successful provisioning returns all three keys."""
        flow = _make_flow()

        # Mock provisioner result
        mock_prov_result = MagicMock()
        mock_prov_result.dev_key = bytes.fromhex(_TEST_DEV_KEY)
        mock_prov_result.num_elements = 1

        # Mock device connection and config
        mock_device = MagicMock()
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.send_config_appkey_add = AsyncMock(return_value=True)
        mock_device.send_config_model_app_bind = AsyncMock(return_value=True)

        with (
            patch("tuya_ble_mesh.sig_mesh_provisioner.SIGMeshProvisioner") as mock_prov_cls,
            patch("tuya_ble_mesh.sig_mesh_device.SIGMeshDevice", return_value=mock_device),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_provisioner = MagicMock()
            mock_provisioner.provision = AsyncMock(return_value=mock_prov_result)
            mock_prov_cls.return_value = mock_provisioner

            net_key, dev_key, app_key = await flow._run_provision("AA:BB:CC:DD:EE:FF")

        # Verify keys are 32-char hex strings
        assert len(net_key) == 32
        assert len(dev_key) == 32
        assert len(app_key) == 32
        assert all(c in "0123456789abcdef" for c in net_key)
        assert dev_key == _TEST_DEV_KEY

    @pytest.mark.asyncio
    async def test_run_provision_appkey_add_failed(self) -> None:
        """AppKey add failure is logged but provisioning succeeds."""
        flow = _make_flow()

        mock_prov_result = MagicMock()
        mock_prov_result.dev_key = bytes.fromhex(_TEST_DEV_KEY)
        mock_prov_result.num_elements = 1

        mock_device = MagicMock()
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.send_config_appkey_add = AsyncMock(return_value=False)  # FAIL
        mock_device.send_config_model_app_bind = AsyncMock(return_value=True)

        with (
            patch("tuya_ble_mesh.sig_mesh_provisioner.SIGMeshProvisioner") as mock_prov_cls,
            patch("tuya_ble_mesh.sig_mesh_device.SIGMeshDevice", return_value=mock_device),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_provisioner = MagicMock()
            mock_provisioner.provision = AsyncMock(return_value=mock_prov_result)
            mock_prov_cls.return_value = mock_provisioner

            net_key, dev_key, _app_key = await flow._run_provision("AA:BB:CC:DD:EE:FF")

        # Should still return keys (warning logged)
        assert len(net_key) == 32
        assert dev_key == _TEST_DEV_KEY

    @pytest.mark.asyncio
    async def test_run_provision_model_bind_failed(self) -> None:
        """Model bind failure is logged but provisioning succeeds."""
        flow = _make_flow()

        mock_prov_result = MagicMock()
        mock_prov_result.dev_key = bytes.fromhex(_TEST_DEV_KEY)
        mock_prov_result.num_elements = 1

        mock_device = MagicMock()
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.send_config_appkey_add = AsyncMock(return_value=True)
        mock_device.send_config_model_app_bind = AsyncMock(return_value=False)  # FAIL

        with (
            patch("tuya_ble_mesh.sig_mesh_provisioner.SIGMeshProvisioner") as mock_prov_cls,
            patch("tuya_ble_mesh.sig_mesh_device.SIGMeshDevice", return_value=mock_device),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_provisioner = MagicMock()
            mock_provisioner.provision = AsyncMock(return_value=mock_prov_result)
            mock_prov_cls.return_value = mock_provisioner

            net_key, dev_key, _app_key = await flow._run_provision("AA:BB:CC:DD:EE:FF")

        # Should still return keys
        assert len(net_key) == 32
        assert dev_key == _TEST_DEV_KEY

    @pytest.mark.asyncio
    async def test_run_provision_post_config_exception(self) -> None:
        """Exception in post-provisioning config is caught and logged."""
        flow = _make_flow()

        mock_prov_result = MagicMock()
        mock_prov_result.dev_key = bytes.fromhex(_TEST_DEV_KEY)
        mock_prov_result.num_elements = 1

        mock_device = MagicMock()
        mock_device.connect = AsyncMock(side_effect=Exception("connection timeout"))
        mock_device.disconnect = AsyncMock()

        with (
            patch("tuya_ble_mesh.sig_mesh_provisioner.SIGMeshProvisioner") as mock_prov_cls,
            patch("tuya_ble_mesh.sig_mesh_device.SIGMeshDevice", return_value=mock_device),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            mock_provisioner = MagicMock()
            mock_provisioner.provision = AsyncMock(return_value=mock_prov_result)
            mock_prov_cls.return_value = mock_provisioner

            # Should still return keys despite post-config failure
            net_key, dev_key, _app_key = await flow._run_provision("AA:BB:CC:DD:EE:FF")

        assert len(net_key) == 32
        assert dev_key == _TEST_DEV_KEY
        mock_device.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_provision_ble_callbacks_called(self) -> None:
        """BLE device and connect callbacks are properly invoked."""
        flow = _make_flow()

        mock_prov_result = MagicMock()
        mock_prov_result.dev_key = bytes.fromhex(_TEST_DEV_KEY)
        mock_prov_result.num_elements = 1

        mock_device = MagicMock()
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.send_config_appkey_add = AsyncMock(return_value=True)
        mock_device.send_config_model_app_bind = AsyncMock(return_value=True)

        # Capture the callbacks passed to SIGMeshProvisioner
        captured_provisioner_kwargs = {}

        def capture_provisioner_init(**kwargs: Any) -> MagicMock:
            captured_provisioner_kwargs.update(kwargs)
            mock_provisioner = MagicMock()
            mock_provisioner.provision = AsyncMock(return_value=mock_prov_result)
            return mock_provisioner

        # Mock establish_connection to avoid real BLE calls
        mock_client = MagicMock()
        with (
            patch(
                "bleak_retry_connector.establish_connection",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner.SIGMeshProvisioner",
                side_effect=capture_provisioner_init,
            ),
            patch("tuya_ble_mesh.sig_mesh_device.SIGMeshDevice", return_value=mock_device),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await flow._run_provision("AA:BB:CC:DD:EE:FF")

        # Verify callbacks were passed
        assert "ble_device_callback" in captured_provisioner_kwargs
        assert "ble_connect_callback" in captured_provisioner_kwargs

        # Test the callbacks
        ble_device_cb = captured_provisioner_kwargs["ble_device_callback"]
        ble_connect_cb = captured_provisioner_kwargs["ble_connect_callback"]

        # Test ble_device_cb with connectable=True device
        mock_ble_device_connectable = MagicMock()
        with patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            return_value=mock_ble_device_connectable,
        ):
            result = ble_device_cb("AA:BB:CC:DD:EE:FF")
            assert result is mock_ble_device_connectable

        # Test ble_device_cb fallback to connectable=False when connectable=True returns None
        with patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address"
        ) as mock_bt:
            mock_ble_device_non_connectable = MagicMock()
            mock_bt.side_effect = [None, mock_ble_device_non_connectable]
            result = ble_device_cb("AA:BB:CC:DD:EE:FF")
            assert result is mock_ble_device_non_connectable

        # Test ble_connect_cb - verify it's callable and calls establish_connection
        assert callable(ble_connect_cb)
        # Call the callback to cover line 602 - uses mock_client from the outer patch
        mock_ble_device = MagicMock()
        mock_ble_device.address = "AA:BB:CC:DD:EE:FF"
        result = await ble_connect_cb(mock_ble_device)
        assert result is not None

    @pytest.mark.asyncio
    async def test_run_provision_ble_device_not_found(self) -> None:
        """BLE device callback logs warning when device not found."""
        flow = _make_flow()

        mock_prov_result = MagicMock()
        mock_prov_result.dev_key = bytes.fromhex(_TEST_DEV_KEY)
        mock_prov_result.num_elements = 1

        mock_device = MagicMock()
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.send_config_appkey_add = AsyncMock(return_value=True)
        mock_device.send_config_model_app_bind = AsyncMock(return_value=True)

        # Capture the callbacks passed to SIGMeshProvisioner
        captured_provisioner_kwargs = {}

        def capture_provisioner_init(**kwargs: Any) -> MagicMock:
            captured_provisioner_kwargs.update(kwargs)
            mock_provisioner = MagicMock()
            mock_provisioner.provision = AsyncMock(return_value=mock_prov_result)
            return mock_provisioner

        # Mock establish_connection to avoid real BLE calls
        mock_client = MagicMock()
        with (
            patch(
                "bleak_retry_connector.establish_connection",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner.SIGMeshProvisioner",
                side_effect=capture_provisioner_init,
            ),
            patch("tuya_ble_mesh.sig_mesh_device.SIGMeshDevice", return_value=mock_device),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await flow._run_provision("AA:BB:CC:DD:EE:FF")

        # Verify callbacks were passed
        assert "ble_device_callback" in captured_provisioner_kwargs
        assert "ble_connect_callback" in captured_provisioner_kwargs

        ble_device_cb = captured_provisioner_kwargs["ble_device_callback"]
        _ble_connect_cb = captured_provisioner_kwargs["ble_connect_callback"]

        # Test ble_device_cb when device not found (both connectable=True and False return None)
        with patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address"
        ) as mock_bt:
            mock_bt.return_value = None  # Both calls return None
            result = ble_device_cb("AA:BB:CC:DD:EE:FF")
            assert result is None


def _make_options_flow(
    device_type: str, entry_data: dict[str, Any] | None = None
) -> TuyaBLEMeshOptionsFlow:
    """Create an options flow with a mock config entry and hass."""
    data: dict[str, Any] = {CONF_DEVICE_TYPE: device_type}
    if entry_data is not None:
        data.update(entry_data)

    config_entry = MagicMock()
    config_entry.data = data
    config_entry.entry_id = "test_entry_id"

    flow = TuyaBLEMeshOptionsFlow(config_entry)
    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_update_entry = MagicMock()
    flow.hass = hass
    return flow


@pytest.mark.requires_ha
class TestOptionsFlowInit:
    """Test options flow shows correct form for each device type."""

    @pytest.mark.asyncio
    async def test_bridge_device_shows_bridge_fields(self) -> None:
        """sig_bridge_plug shows bridge_host and bridge_port fields."""
        flow = _make_options_flow(DEVICE_TYPE_SIG_BRIDGE_PLUG)
        result = await flow.async_step_init(None)

        assert result["type"] == "form"
        assert result["step_id"] == "init"
        schema_keys = [str(k) for k in result["data_schema"].schema]
        assert CONF_BRIDGE_HOST in schema_keys
        assert CONF_BRIDGE_PORT in schema_keys
        assert CONF_MESH_NAME not in schema_keys
        assert CONF_UNICAST_TARGET not in schema_keys

    @pytest.mark.asyncio
    async def test_sig_plug_shows_unicast_fields(self) -> None:
        """sig_plug shows unicast_target and iv_index fields."""
        flow = _make_options_flow(DEVICE_TYPE_SIG_PLUG)
        result = await flow.async_step_init(None)

        assert result["type"] == "form"
        assert result["step_id"] == "init"
        schema_keys = [str(k) for k in result["data_schema"].schema]
        assert CONF_UNICAST_TARGET in schema_keys
        assert CONF_IV_INDEX in schema_keys
        assert CONF_BRIDGE_HOST not in schema_keys
        assert CONF_MESH_NAME not in schema_keys

    @pytest.mark.asyncio
    async def test_light_shows_mesh_fields(self) -> None:
        """Default light type shows mesh_name, mesh_password, mesh_address."""
        flow = _make_options_flow(DEVICE_TYPE_LIGHT)
        result = await flow.async_step_init(None)

        assert result["type"] == "form"
        assert result["step_id"] == "init"
        schema_keys = [str(k) for k in result["data_schema"].schema]
        assert CONF_MESH_NAME in schema_keys
        assert CONF_MESH_PASSWORD in schema_keys
        assert CONF_MESH_ADDRESS in schema_keys
        assert CONF_BRIDGE_HOST not in schema_keys
        assert CONF_UNICAST_TARGET not in schema_keys


@pytest.mark.requires_ha
class TestOptionsFlowSubmit:
    """Test options flow submits data and updates config entry."""

    @pytest.mark.asyncio
    async def test_submit_bridge_options(self) -> None:
        """Submitting bridge options updates entry and creates result."""
        flow = _make_options_flow(
            DEVICE_TYPE_SIG_BRIDGE_PLUG,
            {CONF_BRIDGE_HOST: "10.0.0.1", CONF_BRIDGE_PORT: 8099},
        )
        result = await flow.async_step_init({CONF_BRIDGE_HOST: "10.0.0.2", CONF_BRIDGE_PORT: 9000})

        assert result["type"] == "create_entry"
        assert result["title"] == ""
        assert result["data"] == {}
        flow.hass.config_entries.async_update_entry.assert_called_once()
        call_kwargs = flow.hass.config_entries.async_update_entry.call_args
        new_data = call_kwargs[1]["data"]
        assert new_data[CONF_BRIDGE_HOST] == "10.0.0.2"
        assert new_data[CONF_BRIDGE_PORT] == 9000

    @pytest.mark.asyncio
    async def test_submit_sig_plug_options(self) -> None:
        """Submitting sig_plug options updates entry."""
        flow = _make_options_flow(
            DEVICE_TYPE_SIG_PLUG,
            {CONF_UNICAST_TARGET: "00B0", CONF_IV_INDEX: 0},
        )
        result = await flow.async_step_init({CONF_UNICAST_TARGET: "00C0", CONF_IV_INDEX: 1})

        assert result["type"] == "create_entry"
        flow.hass.config_entries.async_update_entry.assert_called_once()
        call_kwargs = flow.hass.config_entries.async_update_entry.call_args
        new_data = call_kwargs[1]["data"]
        assert new_data[CONF_UNICAST_TARGET] == "00C0"
        assert new_data[CONF_IV_INDEX] == 1

    @pytest.mark.asyncio
    async def test_submit_light_options(self) -> None:
        """Submitting light/default options updates entry."""
        flow = _make_options_flow(DEVICE_TYPE_LIGHT)
        result = await flow.async_step_init(
            {
                CONF_MESH_NAME: "new_mesh",
                CONF_MESH_PASSWORD: "newpass",  # pragma: allowlist secret
                CONF_MESH_ADDRESS: 5,
            }
        )

        assert result["type"] == "create_entry"
        flow.hass.config_entries.async_update_entry.assert_called_once()
        call_kwargs = flow.hass.config_entries.async_update_entry.call_args
        new_data = call_kwargs[1]["data"]
        assert new_data[CONF_MESH_NAME] == "new_mesh"
        assert new_data[CONF_MESH_PASSWORD] == "newpass"  # pragma: allowlist secret
        assert new_data[CONF_MESH_ADDRESS] == 5


@pytest.mark.requires_ha
class TestOptionsFlowMerge:
    """Test that existing config entry data is preserved on update."""

    @pytest.mark.asyncio
    async def test_existing_data_preserved(self) -> None:
        """Updating one field preserves all other existing fields."""
        existing_data = {
            CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_PLUG,
            CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF",
            CONF_NET_KEY: _TEST_NET_KEY,
            CONF_DEV_KEY: _TEST_DEV_KEY,
            CONF_APP_KEY: _TEST_APP_KEY,
            CONF_UNICAST_TARGET: "00B0",
            CONF_IV_INDEX: 0,
        }
        flow = _make_options_flow(DEVICE_TYPE_SIG_PLUG, existing_data)

        # Only change unicast_target
        result = await flow.async_step_init({CONF_UNICAST_TARGET: "00C0", CONF_IV_INDEX: 0})

        assert result["type"] == "create_entry"
        call_kwargs = flow.hass.config_entries.async_update_entry.call_args
        new_data = call_kwargs[1]["data"]
        # Changed field
        assert new_data[CONF_UNICAST_TARGET] == "00C0"
        # Preserved fields
        assert new_data[CONF_MAC_ADDRESS] == "AA:BB:CC:DD:EE:FF"
        assert new_data[CONF_NET_KEY] == _TEST_NET_KEY
        assert new_data[CONF_DEV_KEY] == _TEST_DEV_KEY
        assert new_data[CONF_APP_KEY] == _TEST_APP_KEY
        assert new_data[CONF_DEVICE_TYPE] == DEVICE_TYPE_SIG_PLUG


@pytest.mark.requires_ha
class TestReauthFlow:
    """Test reauth flow when mesh credentials fail."""

    @pytest.mark.asyncio
    async def test_reauth_shows_form(self) -> None:
        """Reauth step redirects to reauth_confirm."""
        flow = _make_flow()
        flow.context = {"entry_id": "test_entry"}

        # Create a mock entry
        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
            CONF_MESH_NAME: "old_mesh",
            CONF_MESH_PASSWORD: "old_pass",  # pragma: allowlist secret
        }
        mock_entry.entry_id = "test_entry"
        flow.hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)

        result = await flow.async_step_reauth({})

        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"

    @pytest.mark.asyncio
    async def test_reauth_confirm_shows_mesh_fields_for_light(self) -> None:
        """Reauth confirm shows mesh fields for light devices."""
        flow = _make_flow()
        flow.context = {"entry_id": "test_entry"}

        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
        }
        mock_entry.entry_id = "test_entry"
        flow.hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)

        result = await flow.async_step_reauth_confirm(None)

        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
        schema_keys = [str(k) for k in result["data_schema"].schema]
        assert CONF_MESH_NAME in schema_keys
        assert CONF_MESH_PASSWORD in schema_keys

    @pytest.mark.asyncio
    async def test_reauth_confirm_shows_bridge_fields_for_bridge(self) -> None:
        """Reauth confirm shows bridge fields for bridge devices."""
        flow = _make_flow()
        flow.context = {"entry_id": "test_entry"}

        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF",
            CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG,
        }
        mock_entry.entry_id = "test_entry"
        flow.hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)

        result = await flow.async_step_reauth_confirm(None)

        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
        schema_keys = [str(k) for k in result["data_schema"].schema]
        assert CONF_BRIDGE_HOST in schema_keys
        assert CONF_BRIDGE_PORT in schema_keys

    @pytest.mark.asyncio
    async def test_reauth_confirm_updates_entry(self) -> None:
        """Submitting reauth updates entry and reloads."""
        flow = _make_flow()
        flow.context = {"entry_id": "test_entry"}

        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
            CONF_MESH_NAME: "old_mesh",
            CONF_MESH_PASSWORD: "old_pass",  # pragma: allowlist secret
        }
        mock_entry.entry_id = "test_entry"
        flow.hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)
        flow.hass.config_entries.async_update_entry = MagicMock()
        flow.hass.config_entries.async_reload = AsyncMock()

        result = await flow.async_step_reauth_confirm(
            {
                CONF_MESH_NAME: "new_mesh",
                CONF_MESH_PASSWORD: "new_pass",  # pragma: allowlist secret
            }
        )

        assert result["type"] == "abort"
        assert result["reason"] == "reauth_successful"
        flow.hass.config_entries.async_update_entry.assert_called_once()
        flow.hass.config_entries.async_reload.assert_called_once_with("test_entry")

    @pytest.mark.asyncio
    async def test_reauth_confirm_no_entry_shows_form(self) -> None:
        """Reauth confirm with no entry still shows form."""
        flow = _make_flow()
        flow.context = {}

        flow.hass.config_entries.async_get_entry = MagicMock(return_value=None)

        result = await flow.async_step_reauth_confirm(None)

        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"

    @pytest.mark.asyncio
    async def test_reauth_confirm_telink_bridge_shows_bridge_fields(self) -> None:
        """Telink bridge device shows bridge fields in reauth."""
        flow = _make_flow()
        flow.context = {"entry_id": "test_entry"}

        mock_entry = MagicMock()
        mock_entry.data = {
            CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF",
            CONF_DEVICE_TYPE: DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
        }
        mock_entry.entry_id = "test_entry"
        flow.hass.config_entries.async_get_entry = MagicMock(return_value=mock_entry)

        result = await flow.async_step_reauth_confirm(None)

        assert result["type"] == "form"
        schema_keys = [str(k) for k in result["data_schema"].schema]
        assert CONF_BRIDGE_HOST in schema_keys
        assert CONF_BRIDGE_PORT in schema_keys


@pytest.mark.requires_ha
class TestDeviceDiscoveryStaleProtection:
    """Test PLAT-509: Stale discovery flow protection (lines 353-357)."""

    @pytest.mark.asyncio
    async def test_discovery_aborts_when_device_not_advertising(self) -> None:
        """Discovery flow aborts when device is no longer advertising."""
        flow = _make_flow()
        flow.context = {"source": "bluetooth"}

        # Mock discovery info with SIG mesh proxy UUID
        discovery_info = MagicMock(spec=BluetoothServiceInfoBleak)
        discovery_info.name = "tymesh_12345"
        discovery_info.address = "AA:BB:CC:DD:EE:FF"
        discovery_info.rssi = -60
        discovery_info.service_uuids = [SIG_MESH_PROXY_UUID]

        # Mock async_ble_device_from_address to return None (device not advertising)
        with patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            return_value=None,
        ):
            result = await flow.async_step_bluetooth(discovery_info)

        assert result["type"] == "abort"
        assert result["reason"] == "device_not_available"

    @pytest.mark.asyncio
    async def test_discovery_continues_when_bluetooth_manager_unavailable(self) -> None:
        """Discovery flow skips stale check if BluetoothManager is unavailable."""
        flow = _make_flow()
        flow.context = {"source": "bluetooth"}

        discovery_info = MagicMock(spec=BluetoothServiceInfoBleak)
        discovery_info.name = "tymesh_67890"
        discovery_info.address = "BB:CC:DD:EE:FF:00"
        discovery_info.rssi = -55
        discovery_info.service_uuids = [SIG_MESH_PROXY_UUID]

        # Mock async_ble_device_from_address to raise RuntimeError
        with patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            side_effect=RuntimeError("BluetoothManager not initialized"),
        ):
            result = await flow.async_step_bluetooth(discovery_info)

        # Should NOT abort — continues to user form
        assert result["type"] == "form"


@pytest.mark.requires_ha
class TestDeviceTypeAutoDetection:
    """Test device type auto-detection from service UUIDs (line 380)."""

    @pytest.mark.asyncio
    async def test_telink_uuid_auto_detects_light(self) -> None:
        """Telink mesh UUID prefix auto-detects LIGHT type (line 380)."""
        flow = _make_flow()
        flow.context = {"source": "bluetooth"}

        # Discovery with Telink mesh UUID (00010203-0405-0607-0809-0a0b0c0d...)
        discovery_info = MagicMock(spec=BluetoothServiceInfoBleak)
        discovery_info.name = "out_of_mesh"
        discovery_info.address = "CC:DD:EE:FF:00:11"
        discovery_info.rssi = -50
        discovery_info.service_uuids = ["00010203-0405-0607-0809-0a0b0c0d1234"]

        with patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            side_effect=RuntimeError,  # Skip stale check
        ):
            result = await flow.async_step_bluetooth(discovery_info)

        # With Telink UUID, auto-detects light and creates entry via zero-knowledge flow
        assert result["type"] == "create_entry"
        assert "BLE Mesh Light" in result["title"]
        assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_LIGHT


@pytest.mark.requires_ha
class TestZeroKnowledgeConfigFlow:
    """Test PLAT-511: Zero-knowledge config flow for auto-detected devices."""

    @pytest.mark.asyncio
    async def test_auto_detected_plug_creates_entry_with_defaults(self) -> None:
        """Auto-detected SIG plug creates entry with zero user input (lines 439-455)."""
        flow = _make_flow()
        flow.context = {"source": "bluetooth"}

        # Set discovery info with auto-detected plug (NOT SIG_PLUG, use DEVICE_TYPE_PLUG)
        # This triggers the zero-knowledge flow in async_step_confirm
        flow._discovery_info = {
            "address": "DD:EE:FF:00:11:22",
            "name": "tymesh_plug",
            "auto_device_type": DEVICE_TYPE_PLUG,  # PLUG, not SIG_PLUG
        }

        # async_step_confirm with no user_input triggers auto-creation
        result = await flow.async_step_confirm(None)

        assert result["type"] == "create_entry"
        assert "BLE Mesh Plug" in result["title"]
        assert result["data"][CONF_MAC_ADDRESS] == "DD:EE:FF:00:11:22"
        assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_PLUG
        # Defaults should be set
        assert result["data"][CONF_MESH_NAME] == "out_of_mesh"
        assert result["data"][CONF_MESH_PASSWORD] == "123456"

    @pytest.mark.asyncio
    async def test_auto_detected_light_creates_entry_with_defaults(self) -> None:
        """Auto-detected light creates entry with zero user input (lines 439-455)."""
        flow = _make_flow()
        flow.context = {"source": "bluetooth"}

        flow._discovery_info = {
            "address": "EE:FF:00:11:22:33",
            "name": "out_of_mesh_light",
            "auto_device_type": DEVICE_TYPE_LIGHT,
        }

        result = await flow.async_step_confirm(None)

        assert result["type"] == "create_entry"
        assert "BLE Mesh Light" in result["title"]
        assert result["data"][CONF_MAC_ADDRESS] == "EE:FF:00:11:22:33"
        assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_LIGHT
