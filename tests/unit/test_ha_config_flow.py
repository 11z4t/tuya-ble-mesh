"""Unit tests for the Tuya BLE Mesh config flow."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

# Add project root for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import HANDLERS

from custom_components.tuya_ble_mesh.config_flow import (
    TuyaBLEMeshConfigFlow,
    TuyaBLEMeshOptionsFlow,
    _load_mesh_key_defaults,
    _test_bridge,
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
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    DOMAIN,
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
        flow = _make_flow()
        assert flow.VERSION == 1


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
        "custom_components.tuya_ble_mesh.config_flow._test_bridge",
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
        mock_bridge.assert_called_once_with("192.168.1.100", 8099)

    @pytest.mark.asyncio
    @patch(
        "custom_components.tuya_ble_mesh.config_flow._test_bridge",
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
            "custom_components.tuya_ble_mesh.config_flow._test_bridge",
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
        "custom_components.tuya_ble_mesh.config_flow._test_bridge",
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
        mock_bridge.assert_called_once_with("192.168.1.200", 9000)

    @pytest.mark.asyncio
    @patch(
        "custom_components.tuya_ble_mesh.config_flow._test_bridge",
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
            "custom_components.tuya_ble_mesh.config_flow._test_bridge",
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


class TestTestBridge:
    """Test _test_bridge() connection helper."""

    @pytest.mark.asyncio
    async def test_bridge_success(self) -> None:
        """Successful bridge connection returns True."""
        mock_reader = AsyncMock()
        response_body = json.dumps({"status": "ok"})
        http_response = f"HTTP/1.1 200 OK\r\n\r\n{response_body}"
        mock_reader.read = AsyncMock(return_value=http_response.encode())

        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = MagicMock()

        with patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)):
            result = await _test_bridge("192.168.1.100", 8099)

        assert result is True
        mock_writer.write.assert_called_once()
        mock_writer.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_bridge_bad_status(self) -> None:
        """Bridge returns non-ok status."""
        mock_reader = AsyncMock()
        response_body = json.dumps({"status": "error"})
        http_response = f"HTTP/1.1 200 OK\r\n\r\n{response_body}"
        mock_reader.read = AsyncMock(return_value=http_response.encode())

        mock_writer = MagicMock()
        mock_writer.write = MagicMock()
        mock_writer.drain = AsyncMock()
        mock_writer.close = MagicMock()

        with patch("asyncio.open_connection", return_value=(mock_reader, mock_writer)):
            result = await _test_bridge("192.168.1.100", 8099)

        assert result is False

    @pytest.mark.asyncio
    async def test_bridge_connection_refused(self) -> None:
        """Connection failure returns False."""
        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError):
            result = await _test_bridge("192.168.1.100", 8099)

        assert result is False

    @pytest.mark.asyncio
    async def test_bridge_timeout(self) -> None:
        """Timeout returns False."""
        import asyncio as _asyncio

        with patch("asyncio.open_connection", side_effect=_asyncio.TimeoutError):
            result = await _test_bridge("192.168.1.100", 8099)

        assert result is False


class TestLoadMeshKeyDefaults:
    """Test _load_mesh_key_defaults() helper."""

    def test_returns_empty_when_no_file(self) -> None:
        with patch("os.path.exists", return_value=False):
            result = _load_mesh_key_defaults()

        assert result == {"net_key": "", "dev_key": "", "app_key": ""}

    def test_loads_keys_from_file(self) -> None:
        keys_data = {
            "net_key": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1",  # pragma: allowlist secret
            "dev_key": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2",  # pragma: allowlist secret
            "app_key": "ccccccccccccccccccccccccccccccc3",  # pragma: allowlist secret
        }
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps(keys_data))),
        ):
            result = _load_mesh_key_defaults()

        assert result["net_key"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"
        assert result["dev_key"] == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2"
        assert result["app_key"] == "ccccccccccccccccccccccccccccccc3"

    def test_returns_empty_on_json_error(self) -> None:
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data="not valid json{")),
        ):
            result = _load_mesh_key_defaults()

        assert result == {"net_key": "", "dev_key": "", "app_key": ""}

    def test_returns_empty_for_missing_keys_in_file(self) -> None:
        with (
            patch("os.path.exists", return_value=True),
            patch("builtins.open", mock_open(read_data=json.dumps({}))),
        ):
            result = _load_mesh_key_defaults()

        assert result == {"net_key": "", "dev_key": "", "app_key": ""}


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
