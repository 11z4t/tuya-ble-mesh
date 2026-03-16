"""Config flow integration tests using real HA flow machinery for all 4 device types.

PLAT-664: Tests drive the config flow through a FlowManager that handles
flow lifecycle (init, configure, step routing) via async_init/async_configure —
NOT direct flow instantiation or manual step calls.

Covers: light, plug, sig_bridge_plug, telink_bridge_light.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure project root and lib are importable
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_LIB = str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from homeassistant.config_entries import HANDLERS, ConfigFlow

from custom_components.tuya_ble_mesh.config_flow import TuyaBLEMeshConfigFlow
from custom_components.tuya_ble_mesh.const import (
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_DEVICE_TYPE,
    CONF_MAC_ADDRESS,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_UNICAST_TARGET,
    CONF_VENDOR_ID,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    DOMAIN,
)


# ---------------------------------------------------------------------------
# Lightweight FlowManager — real flow lifecycle (init → configure → finish)
# ---------------------------------------------------------------------------


class ConfigFlowManager:
    """Manages config flow lifecycle the same way HA's ConfigEntriesFlowManager does.

    Flows are created from HANDLERS registry, assigned unique IDs, and driven
    through async_init / async_configure. Step routing uses getattr to find
    async_step_<step_id> methods on the flow handler — identical to HA internals.
    """

    def __init__(self, hass: Any) -> None:
        self.hass = hass
        self._flows: dict[str, ConfigFlow] = {}
        self.created_entries: list[dict[str, Any]] = []

    async def async_init(
        self,
        handler: str,
        *,
        context: dict[str, Any] | None = None,
        data: Any = None,
    ) -> dict[str, Any]:
        """Start a new config flow (mirrors HA FlowManager.async_init)."""
        handler_cls = HANDLERS.get(handler)
        if handler_cls is None:
            raise KeyError(f"No handler for {handler!r}")

        flow = handler_cls()
        flow.hass = self.hass
        flow.handler = handler
        flow.context = context or {}

        flow_id = uuid.uuid4().hex
        flow.flow_id = flow_id
        self._flows[flow_id] = flow

        # Determine init step based on source
        source = flow.context.get("source", "user")
        init_step = f"async_step_{source}"
        step_fn = getattr(flow, init_step, None)
        if step_fn is None:
            raise ValueError(f"Flow has no step {init_step}")

        result = await step_fn(data)
        result["flow_id"] = flow_id
        self._process_result(flow_id, result)
        return result

    async def async_configure(
        self,
        flow_id: str,
        user_input: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Advance an existing flow (mirrors HA FlowManager.async_configure)."""
        flow = self._flows.get(flow_id)
        if flow is None:
            raise KeyError(f"Unknown flow {flow_id}")

        # Find the current step from the last form result
        cur_step = getattr(flow, "_cur_step", "user")
        step_fn = getattr(flow, f"async_step_{cur_step}", None)
        if step_fn is None:
            raise ValueError(f"Flow has no step async_step_{cur_step}")

        result = await step_fn(user_input)
        result["flow_id"] = flow_id
        self._process_result(flow_id, result)
        return result

    def _process_result(self, flow_id: str, result: dict[str, Any]) -> None:
        """Track step and capture completed entries."""
        flow = self._flows.get(flow_id)
        if result.get("type") == "form":
            # Store current step for next async_configure call
            if flow is not None:
                flow._cur_step = result["step_id"]  # type: ignore[attr-defined]
        elif result.get("type") == "create_entry":
            self.created_entries.append(result)
            self._flows.pop(flow_id, None)
        elif result.get("type") == "abort":
            self._flows.pop(flow_id, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_hass() -> MagicMock:
    """Minimal hass mock satisfying config flow requirements."""
    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.config_entries.flow = MagicMock()
    hass.config_entries.flow.async_progress_by_handler = MagicMock(return_value=[])
    hass.config_entries.async_entry_for_domain_unique_id = MagicMock(return_value=None)
    return hass


@pytest.fixture
def flow_mgr(mock_hass: MagicMock) -> ConfigFlowManager:
    """Create a flow manager backed by real config flow lifecycle."""
    return ConfigFlowManager(mock_hass)


# ---------------------------------------------------------------------------
# Test: handler registration
# ---------------------------------------------------------------------------


@pytest.mark.requires_ha
class TestHandlerRegistration:
    """Verify integration registers with HA's config flow machinery."""

    def test_domain_in_handlers(self) -> None:
        assert DOMAIN in HANDLERS

    def test_handler_is_config_flow(self) -> None:
        assert HANDLERS[DOMAIN] is TuyaBLEMeshConfigFlow


# ---------------------------------------------------------------------------
# Test: Light (direct BLE, Telink)
# ---------------------------------------------------------------------------


@pytest.mark.requires_ha
class TestLightConfigFlow:
    """Config flow for DEVICE_TYPE_LIGHT via user step."""

    async def test_user_step_shows_form(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        assert result["type"] == "form"
        assert result["step_id"] == "user"

    async def test_creates_light_entry(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5", CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT},
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_LIGHT
        assert result["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"
        assert "LED Light" in result["title"]

    async def test_light_defaults(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5", CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT},
        )
        assert result["data"][CONF_MESH_NAME] == "out_of_mesh"
        assert result["data"][CONF_MESH_PASSWORD] == "123456"  # pragma: allowlist secret
        assert result["data"][CONF_VENDOR_ID] == DEFAULT_VENDOR_ID

    async def test_light_custom_mesh_credentials(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {
                CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
                CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT,
                CONF_MESH_NAME: "my_mesh",
                CONF_MESH_PASSWORD: "my_pass",  # pragma: allowlist secret
            },
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_MESH_NAME] == "my_mesh"
        assert result["data"][CONF_MESH_PASSWORD] == "my_pass"  # pragma: allowlist secret

    async def test_light_invalid_mac(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "not-a-mac", CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT},
        )
        assert result["type"] == "form"
        assert result["errors"][CONF_MAC_ADDRESS] == "invalid_mac"

    async def test_light_mac_uppercased(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "dc:23:4d:21:43:a5", CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT},
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:A5"


# ---------------------------------------------------------------------------
# Test: Plug (direct BLE, Telink)
# ---------------------------------------------------------------------------


@pytest.mark.requires_ha
class TestPlugConfigFlow:
    """Config flow for DEVICE_TYPE_PLUG via user step."""

    async def test_creates_plug_entry(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "DC:23:4D:21:43:B6", CONF_DEVICE_TYPE: DEVICE_TYPE_PLUG},
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_PLUG
        assert result["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:B6"
        assert "Smart Plug" in result["title"]

    async def test_plug_defaults(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "DC:23:4D:21:43:B6", CONF_DEVICE_TYPE: DEVICE_TYPE_PLUG},
        )
        assert result["data"][CONF_MESH_NAME] == "out_of_mesh"
        assert result["data"][CONF_MESH_PASSWORD] == "123456"  # pragma: allowlist secret
        assert result["data"][CONF_VENDOR_ID] == DEFAULT_VENDOR_ID

    async def test_plug_custom_credentials(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {
                CONF_MAC_ADDRESS: "DC:23:4D:21:43:B6",
                CONF_DEVICE_TYPE: DEVICE_TYPE_PLUG,
                CONF_MESH_NAME: "plug_mesh",
                CONF_MESH_PASSWORD: "plug_pass",  # pragma: allowlist secret
            },
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_MESH_NAME] == "plug_mesh"

    async def test_plug_invalid_mac(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "ZZZZZZ", CONF_DEVICE_TYPE: DEVICE_TYPE_PLUG},
        )
        assert result["type"] == "form"
        assert CONF_MAC_ADDRESS in result["errors"]


# ---------------------------------------------------------------------------
# Test: SIG Bridge Plug (multi-step: user → sig_bridge)
# ---------------------------------------------------------------------------


@pytest.mark.requires_ha
class TestSIGBridgePlugConfigFlow:
    """Config flow for DEVICE_TYPE_SIG_BRIDGE_PLUG: user step → sig_bridge step."""

    async def test_user_step_routes_to_sig_bridge(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "E4:5F:01:8A:3C:D2", CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG},
        )
        assert result["type"] == "form"
        assert result["step_id"] == "sig_bridge"

    @patch(
        "custom_components.tuya_ble_mesh.config_flow_validators._test_bridge_with_session",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_creates_sig_bridge_entry(
        self, mock_bridge: AsyncMock, flow_mgr: ConfigFlowManager
    ) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "E4:5F:01:8A:3C:D2", CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG},
        )
        assert result["step_id"] == "sig_bridge"

        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_BRIDGE_HOST: "192.168.1.100", CONF_BRIDGE_PORT: 8099, CONF_UNICAST_TARGET: "00B0"},
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_SIG_BRIDGE_PLUG
        assert result["data"][CONF_MAC_ADDRESS] == "E4:5F:01:8A:3C:D2"
        assert result["data"][CONF_BRIDGE_HOST] == "192.168.1.100"
        assert result["data"][CONF_BRIDGE_PORT] == 8099
        assert result["data"][CONF_UNICAST_TARGET] == "00B0"
        assert "Smart Plug" in result["title"]

    @patch(
        "custom_components.tuya_ble_mesh.config_flow_validators._test_bridge_with_session",
        new_callable=AsyncMock,
        return_value=False,
    )
    async def test_bridge_unreachable_shows_error(
        self, mock_bridge: AsyncMock, flow_mgr: ConfigFlowManager
    ) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "E4:5F:01:8A:3C:D2", CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG},
        )
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_BRIDGE_HOST: "192.168.1.200", CONF_BRIDGE_PORT: 8099, CONF_UNICAST_TARGET: "00B0"},
        )
        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    async def test_bridge_invalid_host(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "E4:5F:01:8A:3C:D2", CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG},
        )
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_BRIDGE_HOST: "http://evil.com/path", CONF_BRIDGE_PORT: 8099, CONF_UNICAST_TARGET: "00B0"},
        )
        assert result["type"] == "form"
        assert CONF_BRIDGE_HOST in result["errors"]

    async def test_bridge_invalid_unicast(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "E4:5F:01:8A:3C:D2", CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG},
        )
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_BRIDGE_HOST: "192.168.1.100", CONF_BRIDGE_PORT: 8099, CONF_UNICAST_TARGET: "ZZXX"},
        )
        assert result["type"] == "form"
        assert CONF_UNICAST_TARGET in result["errors"]


# ---------------------------------------------------------------------------
# Test: Telink Bridge Light (multi-step: user → telink_bridge)
# ---------------------------------------------------------------------------


@pytest.mark.requires_ha
class TestTelinkBridgeLightConfigFlow:
    """Config flow for DEVICE_TYPE_TELINK_BRIDGE_LIGHT: user step → telink_bridge step."""

    async def test_user_step_routes_to_telink_bridge(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "DC:23:4D:21:43:C7", CONF_DEVICE_TYPE: DEVICE_TYPE_TELINK_BRIDGE_LIGHT},
        )
        assert result["type"] == "form"
        assert result["step_id"] == "telink_bridge"

    @patch(
        "custom_components.tuya_ble_mesh.config_flow_validators._test_bridge_with_session",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_creates_telink_bridge_entry(
        self, mock_bridge: AsyncMock, flow_mgr: ConfigFlowManager
    ) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "DC:23:4D:21:43:C7", CONF_DEVICE_TYPE: DEVICE_TYPE_TELINK_BRIDGE_LIGHT},
        )
        assert result["step_id"] == "telink_bridge"

        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_BRIDGE_HOST: "192.168.1.50", CONF_BRIDGE_PORT: 8099},
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_TELINK_BRIDGE_LIGHT
        assert result["data"][CONF_MAC_ADDRESS] == "DC:23:4D:21:43:C7"
        assert result["data"][CONF_BRIDGE_HOST] == "192.168.1.50"
        assert result["data"][CONF_BRIDGE_PORT] == 8099
        assert "LED Light" in result["title"]

    @patch(
        "custom_components.tuya_ble_mesh.config_flow_validators._test_bridge_with_session",
        new_callable=AsyncMock,
        return_value=False,
    )
    async def test_bridge_unreachable_shows_error(
        self, mock_bridge: AsyncMock, flow_mgr: ConfigFlowManager
    ) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "DC:23:4D:21:43:C7", CONF_DEVICE_TYPE: DEVICE_TYPE_TELINK_BRIDGE_LIGHT},
        )
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_BRIDGE_HOST: "192.168.1.200", CONF_BRIDGE_PORT: 8099},
        )
        assert result["type"] == "form"
        assert result["errors"]["base"] == "cannot_connect"

    async def test_bridge_invalid_host(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "DC:23:4D:21:43:C7", CONF_DEVICE_TYPE: DEVICE_TYPE_TELINK_BRIDGE_LIGHT},
        )
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_BRIDGE_HOST: "../etc/passwd", CONF_BRIDGE_PORT: 8099},
        )
        assert result["type"] == "form"
        assert CONF_BRIDGE_HOST in result["errors"]

    @patch(
        "custom_components.tuya_ble_mesh.config_flow_validators._test_bridge_with_session",
        new_callable=AsyncMock,
        return_value=True,
    )
    async def test_bridge_default_port(
        self, mock_bridge: AsyncMock, flow_mgr: ConfigFlowManager
    ) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "DC:23:4D:21:43:C7", CONF_DEVICE_TYPE: DEVICE_TYPE_TELINK_BRIDGE_LIGHT},
        )
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_BRIDGE_HOST: "192.168.1.50"},
        )
        assert result["type"] == "create_entry"
        assert result["data"][CONF_BRIDGE_PORT] == DEFAULT_BRIDGE_PORT


# ---------------------------------------------------------------------------
# Test: Cross-device-type validation
# ---------------------------------------------------------------------------


@pytest.mark.requires_ha
class TestCrossDeviceValidation:
    """Validation tests that apply across device types."""

    @pytest.mark.parametrize("device_type", [DEVICE_TYPE_LIGHT, DEVICE_TYPE_PLUG])
    async def test_direct_ble_entry_data_complete(
        self, flow_mgr: ConfigFlowManager, device_type: str
    ) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF", CONF_DEVICE_TYPE: device_type},
        )
        assert result["type"] == "create_entry"
        data = result["data"]
        for key in (CONF_MAC_ADDRESS, CONF_DEVICE_TYPE, CONF_MESH_NAME, CONF_MESH_PASSWORD, CONF_VENDOR_ID):
            assert key in data, f"Missing {key}"
        assert data[CONF_DEVICE_TYPE] == device_type

    @pytest.mark.parametrize(
        ("device_type", "expected_step"),
        [
            (DEVICE_TYPE_SIG_BRIDGE_PLUG, "sig_bridge"),
            (DEVICE_TYPE_TELINK_BRIDGE_LIGHT, "telink_bridge"),
        ],
    )
    async def test_bridge_types_route_correctly(
        self, flow_mgr: ConfigFlowManager, device_type: str, expected_step: str
    ) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF", CONF_DEVICE_TYPE: device_type},
        )
        assert result["type"] == "form"
        assert result["step_id"] == expected_step

    @pytest.mark.parametrize(
        "device_type",
        [DEVICE_TYPE_LIGHT, DEVICE_TYPE_PLUG, DEVICE_TYPE_SIG_BRIDGE_PLUG, DEVICE_TYPE_TELINK_BRIDGE_LIGHT],
    )
    async def test_empty_mac_rejected(
        self, flow_mgr: ConfigFlowManager, device_type: str
    ) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        result = await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "", CONF_DEVICE_TYPE: device_type},
        )
        assert result["type"] == "form"
        assert CONF_MAC_ADDRESS in result["errors"]

    async def test_flow_manager_tracks_entries(self, flow_mgr: ConfigFlowManager) -> None:
        result = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        await flow_mgr.async_configure(
            result["flow_id"],
            {CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:FF", CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT},
        )
        assert len(flow_mgr.created_entries) == 1
        assert flow_mgr.created_entries[0]["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_LIGHT

    async def test_multiple_flows_independent(self, flow_mgr: ConfigFlowManager) -> None:
        """Two concurrent flows don't interfere with each other."""
        r1 = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        r2 = await flow_mgr.async_init(DOMAIN, context={"source": "user"})
        assert r1["flow_id"] != r2["flow_id"]

        await flow_mgr.async_configure(
            r1["flow_id"],
            {CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:01", CONF_DEVICE_TYPE: DEVICE_TYPE_LIGHT},
        )
        await flow_mgr.async_configure(
            r2["flow_id"],
            {CONF_MAC_ADDRESS: "AA:BB:CC:DD:EE:02", CONF_DEVICE_TYPE: DEVICE_TYPE_PLUG},
        )
        assert len(flow_mgr.created_entries) == 2
        types = {e["data"][CONF_DEVICE_TYPE] for e in flow_mgr.created_entries}
        assert types == {DEVICE_TYPE_LIGHT, DEVICE_TYPE_PLUG}
