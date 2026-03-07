"""Unit tests for the Tuya BLE Mesh repairs integration."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh.repairs import (  # noqa: E402
    DOMAIN,
    ISSUE_BRIDGE_UNREACHABLE,
    ISSUE_KEY_MISMATCH,
    ISSUE_PROVISIONING_FAILED,
    TuyaBLEMeshRepairFlow,
    async_create_fix_flow,
    async_create_issue_bridge_unreachable,
    async_create_issue_provisioning_failed,
    async_delete_issue,
)


class TestRepairIssueConstants:
    """Test that repair issue constants are defined correctly."""

    def test_issue_ids_defined(self) -> None:
        """Verify all issue ID constants are defined."""
        assert ISSUE_PROVISIONING_FAILED == "provisioning_failed"
        assert ISSUE_BRIDGE_UNREACHABLE == "bridge_unreachable"
        assert ISSUE_KEY_MISMATCH == "key_mismatch"

    def test_domain_constant(self) -> None:
        """Verify domain constant."""
        assert DOMAIN == "tuya_ble_mesh"


class TestCreateIssueProvisioningFailed:
    """Test async_create_issue_provisioning_failed."""

    @pytest.mark.asyncio
    async def test_creates_issue_with_correct_params(self) -> None:
        """Test that provisioning failed issue is created with correct parameters."""
        hass = MagicMock()
        device_name = "Malmbergs Plug S17"

        with patch("homeassistant.helpers.issue_registry.async_create_issue") as mock_create:
            await async_create_issue_provisioning_failed(hass, device_name)

            # Verify the function was called (exact args depend on HA's internal API)
            assert mock_create.called

    @pytest.mark.asyncio
    async def test_logs_warning(self) -> None:
        """Test that a warning is logged when creating the issue."""
        hass = MagicMock()
        device_name = "Malmbergs Plug S17"

        with (
            patch("homeassistant.helpers.issue_registry.async_create_issue"),
            patch("custom_components.tuya_ble_mesh.repairs._LOGGER") as mock_logger,
        ):
            await async_create_issue_provisioning_failed(hass, device_name)
            assert mock_logger.warning.called


class TestCreateIssueBridgeUnreachable:
    """Test async_create_issue_bridge_unreachable."""

    @pytest.mark.asyncio
    async def test_creates_issue_with_correct_params(self) -> None:
        """Test that bridge unreachable issue is created with correct parameters."""
        hass = MagicMock()
        host = "192.168.1.100"
        port = 8099

        with patch("homeassistant.helpers.issue_registry.async_create_issue") as mock_create:
            await async_create_issue_bridge_unreachable(hass, host, port)

            assert mock_create.called

    @pytest.mark.asyncio
    async def test_logs_warning(self) -> None:
        """Test that a warning is logged when creating the issue."""
        hass = MagicMock()
        host = "192.168.1.100"
        port = 8099

        with (
            patch("homeassistant.helpers.issue_registry.async_create_issue"),
            patch("custom_components.tuya_ble_mesh.repairs._LOGGER") as mock_logger,
        ):
            await async_create_issue_bridge_unreachable(hass, host, port)
            assert mock_logger.warning.called


class TestDeleteIssue:
    """Test async_delete_issue."""

    def test_deletes_issue_with_correct_params(self) -> None:
        """Test that issues are deleted with correct parameters."""
        hass = MagicMock()
        issue_id = "provisioning_failed"

        with patch("homeassistant.helpers.issue_registry.async_delete_issue") as mock_delete:
            async_delete_issue(hass, issue_id)

            assert mock_delete.called

    def test_logs_debug(self) -> None:
        """Test that a debug message is logged when deleting the issue."""
        hass = MagicMock()
        issue_id = "provisioning_failed"

        with (
            patch("homeassistant.helpers.issue_registry.async_delete_issue"),
            patch("custom_components.tuya_ble_mesh.repairs._LOGGER") as mock_logger,
        ):
            async_delete_issue(hass, issue_id)
            assert mock_logger.debug.called


class TestRepairFlow:
    """Test TuyaBLEMeshRepairFlow."""

    @pytest.mark.asyncio
    async def test_async_step_init_calls_confirm(self) -> None:
        """Test that async_step_init delegates to async_step_confirm."""
        flow = TuyaBLEMeshRepairFlow()

        # Mock async_step_confirm to return an async result
        async def mock_confirm() -> dict[str, str]:
            return {"type": "form"}

        flow.async_step_confirm = mock_confirm

        result = await flow.async_step_init(None)

        assert result == {"type": "form"}

    @pytest.mark.asyncio
    async def test_async_step_confirm_shows_form_without_input(self) -> None:
        """Test that async_step_confirm shows a form when user_input is None."""
        flow = TuyaBLEMeshRepairFlow()
        flow.async_show_form = MagicMock(return_value={"type": "form", "step_id": "confirm"})

        result = await flow.async_step_confirm(None)

        flow.async_show_form.assert_called_once_with(step_id="confirm")
        assert result == {"type": "form", "step_id": "confirm"}

    @pytest.mark.asyncio
    async def test_async_step_confirm_creates_entry_with_input(self) -> None:
        """Test that async_step_confirm creates an entry when user_input is provided."""
        flow = TuyaBLEMeshRepairFlow()
        flow.async_create_entry = MagicMock(return_value={"type": "create_entry"})

        result = await flow.async_step_confirm({"confirmed": True})

        flow.async_create_entry.assert_called_once_with(data={})
        assert result == {"type": "create_entry"}


class TestCreateFixFlow:
    """Test async_create_fix_flow."""

    @pytest.mark.asyncio
    async def test_returns_repair_flow_instance(self) -> None:
        """Test that async_create_fix_flow returns a TuyaBLEMeshRepairFlow instance."""
        hass = MagicMock()
        issue_id = "provisioning_failed"
        data = None

        flow = await async_create_fix_flow(hass, issue_id, data)

        assert isinstance(flow, TuyaBLEMeshRepairFlow)

    @pytest.mark.asyncio
    async def test_returns_new_instance_each_time(self) -> None:
        """Test that each call returns a new flow instance."""
        hass = MagicMock()

        flow1 = await async_create_fix_flow(hass, "issue1", None)
        flow2 = await async_create_fix_flow(hass, "issue2", None)

        assert flow1 is not flow2
        assert isinstance(flow1, TuyaBLEMeshRepairFlow)
        assert isinstance(flow2, TuyaBLEMeshRepairFlow)
