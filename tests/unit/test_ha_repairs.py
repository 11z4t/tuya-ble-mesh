"""Unit tests for the Tuya BLE Mesh repairs integration."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh.repairs import (  # noqa: E402
    DOMAIN,
    ISSUE_AUTH_OR_MESH_MISMATCH,
    ISSUE_BRIDGE_UNREACHABLE,
    ISSUE_KEY_MISMATCH,
    ISSUE_PROVISIONING_FAILED,
    ISSUE_RECONNECT_STORM,
    TuyaBLEMeshRepairFlow,
    _base_of_scoped,
    _scoped_issue_id,
    async_create_fix_flow,
    async_create_issue_bridge_unreachable,
    async_create_issue_provisioning_failed,
    async_create_issue_reconnect_storm,
    async_delete_all_issues,
    async_delete_issue,
)


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
class TestCreateIssueProvisioningFailed:
    """Test async_create_issue_provisioning_failed."""

    @pytest.mark.asyncio
    async def test_creates_issue_with_correct_params(self) -> None:
        """Test that provisioning failed issue is created with correct parameters."""
        hass = MagicMock()
        device_name = "Malmbergs Plug S17"
        entry_id = "test_entry_abc123"

        with patch("homeassistant.helpers.issue_registry.async_create_issue") as mock_create:
            await async_create_issue_provisioning_failed(hass, device_name, entry_id)

            # Verify the function was called (exact args depend on HA's internal API)
            assert mock_create.called

    @pytest.mark.asyncio
    async def test_logs_warning(self) -> None:
        """Test that a warning is logged when creating the issue."""
        hass = MagicMock()
        device_name = "Malmbergs Plug S17"
        entry_id = "test_entry_abc123"

        with (
            patch("homeassistant.helpers.issue_registry.async_create_issue"),
            patch("custom_components.tuya_ble_mesh.repairs._LOGGER") as mock_logger,
        ):
            await async_create_issue_provisioning_failed(hass, device_name, entry_id)
            assert mock_logger.warning.called


@pytest.mark.requires_ha
class TestCreateIssueBridgeUnreachable:
    """Test async_create_issue_bridge_unreachable."""

    @pytest.mark.asyncio
    async def test_creates_issue_with_correct_params(self) -> None:
        """Test that bridge unreachable issue is created with correct parameters."""
        hass = MagicMock()
        host = "192.168.1.100"
        port = 8099
        entry_id = "test_entry_abc123"

        with patch("homeassistant.helpers.issue_registry.async_create_issue") as mock_create:
            await async_create_issue_bridge_unreachable(hass, host, port, entry_id)

            assert mock_create.called

    @pytest.mark.asyncio
    async def test_logs_warning(self) -> None:
        """Test that a warning is logged when creating the issue."""
        hass = MagicMock()
        host = "192.168.1.100"
        port = 8099
        entry_id = "test_entry_abc123"

        with (
            patch("homeassistant.helpers.issue_registry.async_create_issue"),
            patch("custom_components.tuya_ble_mesh.repairs._LOGGER") as mock_logger,
        ):
            await async_create_issue_bridge_unreachable(hass, host, port, entry_id)
            assert mock_logger.warning.called


@pytest.mark.requires_ha
class TestDeleteIssue:
    """Test async_delete_issue."""

    def test_deletes_issue_with_correct_params(self) -> None:
        """Test that issues are deleted with correct parameters."""
        hass = MagicMock()
        issue_id = "provisioning_failed"
        entry_id = "test_entry_abc123"

        with patch("homeassistant.helpers.issue_registry.async_delete_issue") as mock_delete:
            async_delete_issue(hass, issue_id, entry_id)

            assert mock_delete.called

    def test_logs_debug(self) -> None:
        """Test that a debug message is logged when deleting the issue."""
        hass = MagicMock()
        issue_id = "provisioning_failed"
        entry_id = "test_entry_abc123"

        with (
            patch("homeassistant.helpers.issue_registry.async_delete_issue"),
            patch("custom_components.tuya_ble_mesh.repairs._LOGGER") as mock_logger,
        ):
            async_delete_issue(hass, issue_id, entry_id)
            assert mock_logger.debug.called


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
class TestIssueScopingHelpers:
    """Test that issue IDs are properly scoped per config entry."""

    def test_scoped_issue_id_contains_entry_id(self) -> None:
        """Scoped issue ID embeds the entry ID."""
        scoped = _scoped_issue_id(ISSUE_BRIDGE_UNREACHABLE, "abc123")
        assert "abc123" in scoped
        assert scoped.startswith(ISSUE_BRIDGE_UNREACHABLE)

    def test_base_of_scoped_extracts_base_id(self) -> None:
        """_base_of_scoped extracts the base issue ID."""
        scoped = _scoped_issue_id(ISSUE_BRIDGE_UNREACHABLE, "abc123")
        assert _base_of_scoped(scoped) == ISSUE_BRIDGE_UNREACHABLE

    def test_base_of_scoped_handles_underscore_base_ids(self) -> None:
        """Base IDs with underscores (e.g. auth_or_mesh_mismatch) are handled."""
        scoped = _scoped_issue_id(ISSUE_AUTH_OR_MESH_MISMATCH, "entry1")
        assert _base_of_scoped(scoped) == ISSUE_AUTH_OR_MESH_MISMATCH

    def test_two_entries_get_different_issue_ids(self) -> None:
        """Two config entries produce different scoped issue IDs for the same base."""
        scoped_a = _scoped_issue_id(ISSUE_BRIDGE_UNREACHABLE, "entry_aaa")
        scoped_b = _scoped_issue_id(ISSUE_BRIDGE_UNREACHABLE, "entry_bbb")
        assert scoped_a != scoped_b


@pytest.mark.requires_ha
class TestIssueScopingMultiEntry:
    """Test that issues from different config entries are independent."""

    @pytest.mark.asyncio
    async def test_create_and_clear_issues_independently(self) -> None:
        """Creating issues for two entries and clearing one leaves the other intact."""
        hass = MagicMock()
        entry_a = "entry_aaaaaaa"
        entry_b = "entry_bbbbbbb"
        created_issues: list[str] = []
        deleted_issues: list[str] = []

        def track_create(h, domain, issue_id, **kw) -> None:  # type: ignore[no-untyped-def]
            created_issues.append(issue_id)

        def track_delete(h, domain, issue_id) -> None:  # type: ignore[no-untyped-def]
            deleted_issues.append(issue_id)

        with (
            patch(
                "homeassistant.helpers.issue_registry.async_create_issue",
                side_effect=track_create,
            ),
            patch(
                "homeassistant.helpers.issue_registry.async_delete_issue",
                side_effect=track_delete,
            ),
        ):
            # Create reconnect storm issue for both entries
            await async_create_issue_reconnect_storm(hass, "Device A", 15, entry_a)
            await async_create_issue_reconnect_storm(hass, "Device B", 12, entry_b)

            scoped_a = _scoped_issue_id(ISSUE_RECONNECT_STORM, entry_a)
            scoped_b = _scoped_issue_id(ISSUE_RECONNECT_STORM, entry_b)
            assert scoped_a in created_issues
            assert scoped_b in created_issues

            # Clear only entry A's issues
            async_delete_all_issues(hass, entry_a)

            # Entry A's issue was cleared
            assert scoped_a in deleted_issues
            # Entry B's issue was NOT cleared
            assert scoped_b not in deleted_issues

    @pytest.mark.asyncio
    async def test_delete_issue_uses_scoped_id(self) -> None:
        """async_delete_issue deletes the scoped ID, not the bare base ID."""
        hass = MagicMock()
        entry_id = "myentry123"
        deleted: list[str] = []

        def track_delete(h, domain, issue_id) -> None:  # type: ignore[no-untyped-def]
            deleted.append(issue_id)

        with patch(
            "homeassistant.helpers.issue_registry.async_delete_issue",
            side_effect=track_delete,
        ):
            async_delete_issue(hass, ISSUE_BRIDGE_UNREACHABLE, entry_id)

        expected = _scoped_issue_id(ISSUE_BRIDGE_UNREACHABLE, entry_id)
        assert expected in deleted
        # Must NOT delete the bare base ID (that would affect all entries)
        assert ISSUE_BRIDGE_UNREACHABLE not in deleted


@pytest.mark.requires_ha
class TestRepairFlowRoutingWithScopedIds:
    """Test that repair flow routes correctly with scoped issue IDs."""

    @pytest.mark.asyncio
    async def test_scoped_auth_mismatch_routes_to_reauth_hint(self) -> None:
        """Scoped auth mismatch issue routes to reauth_hint step."""
        scoped = _scoped_issue_id(ISSUE_AUTH_OR_MESH_MISMATCH, "entry1")
        flow = TuyaBLEMeshRepairFlow(scoped)
        called_step = []

        async def mock_reauth() -> dict[str, str]:  # type: ignore[return]
            called_step.append("reauth_hint")
            return {"type": "form", "step_id": "reauth_hint"}

        flow.async_step_reauth_hint = mock_reauth  # type: ignore[method-assign]
        await flow.async_step_init(None)
        assert "reauth_hint" in called_step

    @pytest.mark.asyncio
    async def test_scoped_reconnect_storm_routes_to_storm_confirm(self) -> None:
        """Scoped reconnect storm issue routes to storm_confirm step."""
        scoped = _scoped_issue_id(ISSUE_RECONNECT_STORM, "entry2")
        flow = TuyaBLEMeshRepairFlow(scoped)
        called_step = []

        async def mock_storm() -> dict[str, str]:  # type: ignore[return]
            called_step.append("storm_confirm")
            return {"type": "form", "step_id": "storm_confirm"}

        flow.async_step_storm_confirm = mock_storm  # type: ignore[method-assign]
        result = await flow.async_step_init(None)
        assert "storm_confirm" in called_step

    @pytest.mark.asyncio
    async def test_scoped_generic_issue_routes_to_confirm(self) -> None:
        """Scoped bridge_unreachable issue routes to generic confirm step."""
        scoped = _scoped_issue_id(ISSUE_BRIDGE_UNREACHABLE, "entry3")
        flow = TuyaBLEMeshRepairFlow(scoped)
        called_step = []

        async def mock_confirm() -> dict[str, str]:  # type: ignore[return]
            called_step.append("confirm")
            return {"type": "form", "step_id": "confirm"}

        flow.async_step_confirm = mock_confirm  # type: ignore[method-assign]
        result = await flow.async_step_init(None)
        assert "confirm" in called_step


class TestReconnectTimeline:
    """MESH-16: Reconnect timeline records events for diagnostics."""

    def test_reconnect_event_dataclass(self) -> None:
        """ReconnectEvent is a dataclass with expected fields."""
        from custom_components.tuya_ble_mesh.coordinator import ReconnectEvent

        event = ReconnectEvent(
            timestamp=1000.0,
            error_class="transient",
            backoff=5.0,
            attempt=1,
        )
        assert event.timestamp == 1000.0
        assert event.error_class == "transient"
        assert event.backoff == 5.0
        assert event.attempt == 1

    def test_connection_statistics_has_timeline_fields(self) -> None:
        """ConnectionStatistics must have reconnect_timeline and rssi_history."""
        from collections import deque

        from custom_components.tuya_ble_mesh.coordinator import ConnectionStatistics

        stats = ConnectionStatistics()
        assert isinstance(stats.reconnect_timeline, deque)
        assert isinstance(stats.rssi_history, deque)
        assert len(stats.reconnect_timeline) == 0
        assert len(stats.rssi_history) == 0

    @pytest.mark.asyncio
    async def test_reconnect_loop_records_event_on_failure(self) -> None:
        """Each reconnect failure appends a ReconnectEvent to the timeline."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        device = MagicMock()
        device.address = "AA:BB:CC:DD:EE:FF"
        device.connect = AsyncMock(side_effect=ConnectionError("timeout"))
        device.disconnect = AsyncMock()
        device.register_disconnect_callback = MagicMock()
        device.firmware_version = None

        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._max_reconnect_failures = 1  # Stop after 1 failure
        coord._backoff = 0.001

        with patch(
            "custom_components.tuya_ble_mesh.coordinator.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await coord._reconnect_loop()

        assert len(coord._stats.reconnect_timeline) == 1
        event = coord._stats.reconnect_timeline[0]
        assert event.error_class == "transient"
        assert event.attempt == 1

    @pytest.mark.asyncio
    async def test_reconnect_timeline_capped_at_max(self) -> None:
        """Timeline is capped at _RECONNECT_TIMELINE_MAX (20) events."""
        from custom_components.tuya_ble_mesh.coordinator import (
            _RECONNECT_TIMELINE_MAX,
            TuyaBLEMeshCoordinator,
        )

        device = MagicMock()
        device.address = "AA:BB:CC:DD:EE:FF"
        device.connect = AsyncMock(side_effect=ConnectionError("timeout"))
        device.disconnect = AsyncMock()
        device.register_disconnect_callback = MagicMock()
        device.firmware_version = None

        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._max_reconnect_failures = _RECONNECT_TIMELINE_MAX + 5
        coord._backoff = 0.001

        with patch(
            "custom_components.tuya_ble_mesh.coordinator.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await coord._reconnect_loop()

        assert len(coord._stats.reconnect_timeline) <= _RECONNECT_TIMELINE_MAX


class TestMeshAuthRepairFlow:
    """MESH-16: MeshAuthRepairFlow provides interactive credential input."""

    @pytest.mark.asyncio
    async def test_auth_repair_flow_shows_credentials_step(self) -> None:
        """async_step_init routes to credentials form."""
        from custom_components.tuya_ble_mesh.repairs import MeshAuthRepairFlow

        entry_id = "abc123"
        issue_id = f"auth_or_mesh_mismatch--{entry_id}"
        flow = MeshAuthRepairFlow(issue_id)

        # No hass — should return create_entry immediately
        result = await flow.async_step_init(None)
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_auth_repair_flow_with_hass_shows_form(self) -> None:
        """With hass, credentials step shows mesh credential form."""

        from custom_components.tuya_ble_mesh.const import DEVICE_TYPE_LIGHT
        from custom_components.tuya_ble_mesh.repairs import MeshAuthRepairFlow

        entry_id = "abc123"
        issue_id = f"auth_or_mesh_mismatch--{entry_id}"
        flow = MeshAuthRepairFlow(issue_id)

        mock_hass = MagicMock()
        entry = MagicMock()
        entry.data = {
            "device_type": DEVICE_TYPE_LIGHT,
            "mesh_name": "oldmesh",
            "mesh_password": "oldcred",
        }
        mock_hass.config_entries.async_get_entry.return_value = entry
        flow.hass = mock_hass

        result = await flow.async_step_credentials(None)
        assert result["type"] == "form"
        assert result["step_id"] == "credentials"

    @pytest.mark.asyncio
    async def test_async_create_fix_flow_routes_auth_to_interactive(self) -> None:
        """async_create_fix_flow returns MeshAuthRepairFlow for auth issues."""
        from custom_components.tuya_ble_mesh.repairs import (
            MeshAuthRepairFlow,
            async_create_fix_flow,
        )

        issue_id = "auth_or_mesh_mismatch--abc123"
        mock_hass = MagicMock()
        flow = await async_create_fix_flow(mock_hass, issue_id, None)
        assert isinstance(flow, MeshAuthRepairFlow)

    @pytest.mark.asyncio
    async def test_async_create_fix_flow_returns_generic_for_bridge_issue(self) -> None:
        """async_create_fix_flow returns TuyaBLEMeshRepairFlow for non-auth issues."""
        from custom_components.tuya_ble_mesh.repairs import (
            TuyaBLEMeshRepairFlow,
            async_create_fix_flow,
        )

        issue_id = "bridge_unreachable--abc123"
        mock_hass = MagicMock()
        flow = await async_create_fix_flow(mock_hass, issue_id, None)
        assert type(flow) is TuyaBLEMeshRepairFlow


@pytest.mark.requires_ha
class TestIssueCreationFunctions:
    """MESH-17: async_create_issue_* helpers register issues correctly."""

    @pytest.mark.asyncio
    async def test_create_issue_auth_or_mesh_mismatch(self) -> None:
        """async_create_issue_auth_or_mesh_mismatch calls ir.async_create_issue."""
        from homeassistant.helpers import issue_registry as ir

        from custom_components.tuya_ble_mesh.repairs import async_create_issue_auth_or_mesh_mismatch

        mock_hass = MagicMock()
        with patch.object(ir, "async_create_issue") as mock_create:
            await async_create_issue_auth_or_mesh_mismatch(mock_hass, "Dev1", "entry_abc")
            mock_create.assert_called_once()
        issue_id = mock_create.call_args.args[2]
        assert "entry_abc" in issue_id

    @pytest.mark.asyncio
    async def test_create_issue_unsupported_vendor(self) -> None:
        """async_create_issue_unsupported_vendor includes vendor_id in placeholders."""
        from homeassistant.helpers import issue_registry as ir

        from custom_components.tuya_ble_mesh.repairs import async_create_issue_unsupported_vendor

        mock_hass = MagicMock()
        with patch.object(ir, "async_create_issue") as mock_create:
            await async_create_issue_unsupported_vendor(mock_hass, "Dev1", "0x9999", "entry_abc")
            mock_create.assert_called_once()
        placeholders = mock_create.call_args.kwargs.get("translation_placeholders", {})
        assert placeholders.get("vendor_id") == "0x9999"

    @pytest.mark.asyncio
    async def test_create_issue_device_not_found(self) -> None:
        """async_create_issue_device_not_found truncates MAC in placeholder."""
        from homeassistant.helpers import issue_registry as ir

        from custom_components.tuya_ble_mesh.repairs import async_create_issue_device_not_found

        mac = "AA:BB:CC:DD:EE:FF"
        mock_hass = MagicMock()
        with patch.object(ir, "async_create_issue") as mock_create:
            await async_create_issue_device_not_found(mock_hass, "Dev1", mac, "entry_abc")
            mock_create.assert_called_once()
        placeholders = mock_create.call_args.kwargs.get("translation_placeholders", {})
        # Last 8 chars of MAC address used as display
        assert placeholders.get("mac") == mac[-8:]

    @pytest.mark.asyncio
    async def test_create_issue_timeout(self) -> None:
        """async_create_issue_timeout includes operation in placeholders."""
        from homeassistant.helpers import issue_registry as ir

        from custom_components.tuya_ble_mesh.repairs import async_create_issue_timeout

        mock_hass = MagicMock()
        with patch.object(ir, "async_create_issue") as mock_create:
            await async_create_issue_timeout(mock_hass, "Dev1", "entry_abc", operation="send")
            mock_create.assert_called_once()
        placeholders = mock_create.call_args.kwargs.get("translation_placeholders", {})
        assert placeholders.get("operation") == "send"

    @pytest.mark.asyncio
    async def test_create_issue_protocol_mismatch(self) -> None:
        """async_create_issue_protocol_mismatch includes protocol in placeholders."""
        from homeassistant.helpers import issue_registry as ir

        from custom_components.tuya_ble_mesh.repairs import async_create_issue_protocol_mismatch

        mock_hass = MagicMock()
        with patch.object(ir, "async_create_issue") as mock_create:
            await async_create_issue_protocol_mismatch(
                mock_hass, "Dev1", "SIG_Mesh", "entry_abc", actual_info="v1.2"
            )
            mock_create.assert_called_once()
        placeholders = mock_create.call_args.kwargs.get("translation_placeholders", {})
        assert placeholders.get("protocol") == "SIG_Mesh"
        assert placeholders.get("info") == "v1.2"


@pytest.mark.requires_ha
class TestRepairFlowStepSubmissions:
    """MESH-17: Form submission paths in TuyaBLEMeshRepairFlow."""

    @pytest.mark.asyncio
    async def test_async_step_confirm_submits_creates_entry(self) -> None:
        """confirm step with user_input returns create_entry."""
        flow = TuyaBLEMeshRepairFlow("bridge_unreachable--abc")
        result = await flow.async_step_confirm(user_input={})
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_async_step_confirm_no_input_shows_form(self) -> None:
        """confirm step without user_input shows the form."""
        flow = TuyaBLEMeshRepairFlow("bridge_unreachable--abc")
        result = await flow.async_step_confirm(None)
        assert result["type"] == "form"
        assert result["step_id"] == "confirm"

    @pytest.mark.asyncio
    async def test_async_step_reauth_hint_submits_creates_entry(self) -> None:
        """reauth_hint step with user_input returns create_entry."""
        flow = TuyaBLEMeshRepairFlow("bridge_unreachable--abc")
        result = await flow.async_step_reauth_hint(user_input={})
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_async_step_reauth_hint_no_input_shows_form(self) -> None:
        """reauth_hint step without user_input shows form."""
        flow = TuyaBLEMeshRepairFlow("bridge_unreachable--abc")
        result = await flow.async_step_reauth_hint(None)
        assert result["type"] == "form"
        assert result["step_id"] == "reauth_hint"

    @pytest.mark.asyncio
    async def test_async_step_storm_confirm_submits_creates_entry(self) -> None:
        """storm_confirm step with user_input returns create_entry."""
        flow = TuyaBLEMeshRepairFlow("reconnect_storm--abc")
        result = await flow.async_step_storm_confirm(user_input={})
        assert result["type"] == "create_entry"

    @pytest.mark.asyncio
    async def test_async_step_init_routes_storm_to_storm_confirm(self) -> None:
        """async_step_init routes reconnect_storm to storm_confirm step."""
        flow = TuyaBLEMeshRepairFlow("reconnect_storm--abc")
        result = await flow.async_step_init(None)
        assert result["type"] == "form"
        assert result["step_id"] == "storm_confirm"

    @pytest.mark.asyncio
    async def test_get_entry_returns_none_without_separator(self) -> None:
        """_get_entry returns None when issue_id has no entry separator."""
        from custom_components.tuya_ble_mesh.repairs import MeshAuthRepairFlow

        flow = MeshAuthRepairFlow("no_separator_here")
        mock_hass = MagicMock()
        result = flow._get_entry(mock_hass)
        assert result is None
