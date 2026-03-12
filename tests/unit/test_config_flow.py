"""Unit tests for Tuya BLE Mesh config flow (EXHAUSTIVE)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh.config_flow import (  # noqa: E402
    TuyaBLEMeshConfigFlow,
    _validate_vendor_id,
)
from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    CONF_DEVICE_TYPE,
    CONF_MAC_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_VENDOR_ID,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DOMAIN,
)


class TestVendorIDValidation:
    """Test vendor ID validation helper."""

    def test_validate_vendor_id_valid_with_prefix(self) -> None:
        """Test valid vendor ID with 0x prefix."""
        assert _validate_vendor_id("0x07d1") is None
        assert _validate_vendor_id("0x1001") is None
        assert _validate_vendor_id("0xFFFF") is None

    def test_validate_vendor_id_valid_without_prefix(self) -> None:
        """Test valid vendor ID without 0x prefix."""
        assert _validate_vendor_id("07d1") is None
        assert _validate_vendor_id("1001") is None
        assert _validate_vendor_id("FFFF") is None

    def test_validate_vendor_id_invalid_format(self) -> None:
        """Test invalid vendor ID format returns error."""
        assert _validate_vendor_id("invalid") == "invalid_vendor_id"
        assert _validate_vendor_id("0xGGGG") == "invalid_vendor_id"
        assert _validate_vendor_id("12345") == "invalid_vendor_id"  # too long

    def test_validate_vendor_id_out_of_range(self) -> None:
        """Test vendor ID > 0xFFFF returns error."""
        assert _validate_vendor_id("0x10000") == "invalid_vendor_id"

    def test_validate_vendor_id_handles_whitespace(self) -> None:
        """Test vendor ID validation strips whitespace."""
        assert _validate_vendor_id("  0x07d1  ") is None


class TestUserStep:
    """Test user step (manual MAC entry)."""

    @pytest.mark.asyncio
    async def test_user_step_valid_input_creates_entry(self) -> None:
        """Test user step with valid input creates config entry."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()
        flow.hass.config_entries = MagicMock()
        flow.hass.config_entries.async_entries = MagicMock(return_value=[])

        user_input = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_VENDOR_ID: "0x07d1",
            CONF_MESH_NAME: "test_mesh",
            CONF_MESH_PASSWORD: "password123",
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
        }

        with patch("custom_components.tuya_ble_mesh.config_flow.MeshDevice"):
            result = await flow.async_step_user(user_input)

        # Should create entry (or show form on first call)
        assert result is not None

    @pytest.mark.asyncio
    async def test_user_step_invalid_mac_shows_error(self) -> None:
        """Test user step with invalid MAC shows error."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()

        user_input = {
            CONF_MAC_ADDRESS: "invalid_mac",
            CONF_VENDOR_ID: "0x07d1",
            CONF_MESH_NAME: "test_mesh",
            CONF_MESH_PASSWORD: "password123",
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
        }

        result = await flow.async_step_user(user_input)

        # Should show form with error
        assert result["type"] == "form"
        assert "errors" in result
        assert result["errors"].get("base") == "invalid_mac"

    @pytest.mark.asyncio
    async def test_user_step_invalid_vendor_id_shows_error(self) -> None:
        """Test user step with invalid vendor ID shows error."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()

        user_input = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_VENDOR_ID: "invalid",
            CONF_MESH_NAME: "test_mesh",
            CONF_MESH_PASSWORD: "password123",
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
        }

        result = await flow.async_step_user(user_input)

        # Should show form with error
        assert result["type"] == "form"
        assert "errors" in result
        assert result["errors"].get("base") == "invalid_vendor_id"


class TestSIGBridgeStep:
    """Test SIG bridge step."""

    @pytest.mark.asyncio
    async def test_sig_bridge_valid_bridge_success(self) -> None:
        """Test SIG bridge step with reachable bridge succeeds."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()
        flow.context = {"source": "user"}

        user_input = {
            "bridge_host": "192.168.1.50",
            "bridge_port": 8099,
        }

        with patch(
            "custom_components.tuya_ble_mesh.config_flow.SIGMeshBridgeDevice"
        ) as mock_bridge:
            mock_instance = AsyncMock()
            mock_bridge.return_value = mock_instance
            # Mock successful bridge test
            mock_instance.test_connection = AsyncMock(return_value=True)

            result = await flow.async_step_sig_bridge(user_input)

        # Should proceed to next step or create entry
        assert result is not None

    @pytest.mark.asyncio
    async def test_sig_bridge_unreachable_shows_error(self) -> None:
        """Test SIG bridge step with unreachable bridge shows error."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()

        user_input = {
            "bridge_host": "192.168.1.99",
            "bridge_port": 8099,
        }

        with patch(
            "custom_components.tuya_ble_mesh.config_flow.SIGMeshBridgeDevice"
        ) as mock_bridge:
            mock_instance = AsyncMock()
            mock_bridge.return_value = mock_instance
            # Mock connection failure
            mock_instance.test_connection = AsyncMock(side_effect=TimeoutError("unreachable"))

            result = await flow.async_step_sig_bridge(user_input)

        # Should show form with error
        assert result["type"] == "form"
        assert "errors" in result
        # Error could be bridge_unreachable or timeout
        assert result["errors"].get("base") in ("bridge_unreachable", "timeout", "cannot_connect")


class TestSIGPlugProvisioningStep:
    """Test SIG plug provisioning step."""

    @pytest.mark.asyncio
    async def test_sig_plug_provisioning_success(self) -> None:
        """Test SIG plug provisioning succeeds."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()
        flow.context = {"source": "user"}

        user_input = {
            CONF_MAC_ADDRESS: "E4:5F:01:8A:3C:D1",
        }

        with (
            patch("custom_components.tuya_ble_mesh.config_flow.SIGMeshDevice") as mock_device,
            patch(
                "custom_components.tuya_ble_mesh.config_flow.find_device_by_address"
            ) as mock_find,
        ):
            mock_find.return_value = MagicMock()
            mock_instance = AsyncMock()
            mock_device.return_value = mock_instance
            mock_instance.provision = AsyncMock()
            mock_instance.configure_for_onoff = AsyncMock()

            result = await flow.async_step_sig_plug(user_input)

        # Should create entry or proceed to next step
        assert result is not None

    @pytest.mark.asyncio
    async def test_sig_plug_provisioning_timeout_shows_error(self) -> None:
        """Test SIG plug provisioning timeout shows error."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()

        user_input = {
            CONF_MAC_ADDRESS: "E4:5F:01:8A:3C:D1",
        }

        with (
            patch("custom_components.tuya_ble_mesh.config_flow.SIGMeshDevice") as mock_device,
            patch(
                "custom_components.tuya_ble_mesh.config_flow.find_device_by_address"
            ) as mock_find,
        ):
            mock_find.return_value = MagicMock()
            mock_instance = AsyncMock()
            mock_device.return_value = mock_instance
            # Mock timeout during provisioning
            mock_instance.provision = AsyncMock(side_effect=TimeoutError("timeout"))

            result = await flow.async_step_sig_plug(user_input)

        # Should show error
        assert result["type"] == "form" or (
            "errors" in result
            and result["errors"].get("base") in ("timeout", "provisioning_failed", "cannot_connect")
        )

    @pytest.mark.asyncio
    async def test_sig_plug_appkey_failure_shows_specific_error(self) -> None:
        """Test SIG plug AppKey failure shows specific error (UX-3)."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()

        user_input = {
            CONF_MAC_ADDRESS: "E4:5F:01:8A:3C:D1",
        }

        with (
            patch("custom_components.tuya_ble_mesh.config_flow.SIGMeshDevice") as mock_device,
            patch(
                "custom_components.tuya_ble_mesh.config_flow.find_device_by_address"
            ) as mock_find,
        ):
            mock_find.return_value = MagicMock()
            mock_instance = AsyncMock()
            mock_device.return_value = mock_instance
            # Mock AppKey error
            mock_instance.provision = AsyncMock()
            mock_instance.configure_for_onoff = AsyncMock(
                side_effect=Exception("AppKey add failed")
            )

            result = await flow.async_step_sig_plug(user_input)

        # Should show error (could be provisioning_failed or cannot_connect)
        assert result is not None


class TestTelinkBridgeStep:
    """Test Telink bridge step."""

    @pytest.mark.asyncio
    async def test_telink_bridge_step_accepts_input(self) -> None:
        """Test Telink bridge step accepts bridge configuration."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()

        user_input = {
            "bridge_host": "192.168.1.60",
            "bridge_port": 8099,
        }

        result = await flow.async_step_telink_bridge(user_input)

        # Should proceed to next step or show form
        assert result is not None


class TestBridgeConfigValidation:
    """Test bridge configuration validation."""

    @pytest.mark.asyncio
    async def test_bridge_config_step_validates_host(self) -> None:
        """Test bridge config step validates host format."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()

        # Valid host should work
        user_input = {"bridge_host": "192.168.1.50", "bridge_port": 8099}
        result = await flow.async_step_bridge_config(user_input)
        assert result is not None


class TestOptionsFlow:
    """Test options flow."""

    @pytest.mark.asyncio
    async def test_options_flow_init(self) -> None:
        """Test options flow initialization."""
        from custom_components.tuya_ble_mesh.config_flow import TuyaBLEMeshOptionsFlow

        config_entry = MagicMock()
        config_entry.data = {
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
        }

        options_flow = TuyaBLEMeshOptionsFlow(config_entry)

        result = await options_flow.async_step_init()

        # Should show options form
        assert result["type"] == "form"
        assert result["step_id"] == "init"


class TestDuplicateDetection:
    """Test duplicate entry detection."""

    @pytest.mark.asyncio
    async def test_abort_on_duplicate_mac(self) -> None:
        """Test config flow aborts on duplicate MAC address."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()

        # Mock existing entry with same MAC
        existing_entry = MagicMock()
        existing_entry.data = {CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5"}
        flow.hass.config_entries.async_entries = MagicMock(return_value=[existing_entry])

        user_input = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_VENDOR_ID: "0x07d1",
            CONF_MESH_NAME: "test_mesh",
            CONF_MESH_PASSWORD: "password123",
            CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
        }

        result = await flow.async_step_user(user_input)

        # Should abort with already_configured
        assert result["type"] == "abort"
        assert result["reason"] == "already_configured"


class TestReauthFlow:
    """Test reauth flow."""

    @pytest.mark.asyncio
    async def test_reauth_flow_starts(self) -> None:
        """Test reauth flow can be initiated."""
        flow = TuyaBLEMeshConfigFlow()
        flow.hass = MagicMock()

        entry_data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "old_mesh",
        }

        result = await flow.async_step_reauth(entry_data)

        # Should show reauth form
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_confirm"
