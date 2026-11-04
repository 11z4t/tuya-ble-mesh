"""Phase 4: provisioning discovered devices into the shared network."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_mesh import DEV_KEY_A, DEV_KEY_B, SimAir, SimNode, make_fake_link_class  # noqa: E402
from test_mesh_network_ha import MAC_A, MAC_B, FakeEntry, _hass, _setup, _sub  # noqa: E402
from tuya_ble_mesh import sig_mesh_network  # noqa: E402

from custom_components.tuya_ble_mesh import config_flow_network  # noqa: E402
from custom_components.tuya_ble_mesh.config_flow_network import (  # noqa: E402
    CONF_CONFIG_STATE,
    CONFIG_STATE_COMPLETE,
    CONFIG_STATE_PENDING,
    async_add_node_to_network,
    async_complete_pending_config,
    async_ensure_network_entry,
)
from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    CONF_DEV_KEY,
    CONF_ELEMENT_COUNT,
    CONF_MAC_ADDRESS,
    CONF_UNICAST_TARGET,
    DEVICE_TYPE_SIG_LIGHT,
    SUBENTRY_TYPE_NODE,
)
from custom_components.tuya_ble_mesh.mesh_network import MeshNetworkRuntimeData  # noqa: E402

MAC_NEW = "DC:23:00:00:00:0C"
DEV_KEY_NEW = "00112233445566778899aabbccddeeff"  # pragma: allowlist secret


@pytest.fixture
def air(monkeypatch: pytest.MonkeyPatch) -> SimAir:
    sim = SimAir(
        nodes=[
            SimNode(MAC_A, 0x0002, DEV_KEY_A, element_count=2),
            SimNode(MAC_B, 0x0004, DEV_KEY_B),
        ]
    )
    monkeypatch.setattr(sig_mesh_network, "GattProxyLink", make_fake_link_class(sim))
    monkeypatch.setattr(config_flow_network, "POST_PROVISION_REBOOT_DELAY", 0)
    return sim


@pytest.fixture
def entry() -> FakeEntry:
    return FakeEntry(
        {"sub_a": _sub(MAC_A, "0002", DEV_KEY_A, 2), "sub_b": _sub(MAC_B, "0004", DEV_KEY_B)}
    )


class FakeProvisioner:
    """Stands in for SIGMeshProvisioner: asks the allocator, returns a result."""

    instances: ClassVar[list[FakeProvisioner]] = []
    elements = 6

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        FakeProvisioner.instances.append(self)

    async def provision(self, mac: str) -> Any:
        unicast = self.kwargs["unicast_allocator"](self.elements)
        return SimpleNamespace(
            unicast_addr=unicast,
            num_elements=self.elements,
            dev_key=bytes.fromhex(DEV_KEY_NEW),
        )


def _patches(configure: Any) -> Any:
    from contextlib import ExitStack

    stack = ExitStack()
    FakeProvisioner.instances.clear()
    stack.enter_context(
        patch("tuya_ble_mesh.sig_mesh_provisioner.SIGMeshProvisioner", FakeProvisioner)
    )
    stack.enter_context(
        patch(
            "custom_components.tuya_ble_mesh.config_flow_sig.ha_ble_callbacks",
            return_value=(None, None),
        )
    )
    stack.enter_context(
        patch.object(config_flow_network, "async_configure_network_node", side_effect=configure)
    )
    return stack


async def _ready(entry: FakeEntry) -> MagicMock:
    hass = _hass([entry])
    await _setup(entry, hass)
    hass.config_entries.async_add_subentry = MagicMock(return_value=True)
    hass.config_entries.async_update_subentry = MagicMock(return_value=True)
    return hass


class TestAddNodeToNetwork:
    @pytest.mark.asyncio
    async def test_provisions_with_network_keys_and_free_address(
        self, air: SimAir, entry: FakeEntry
    ) -> None:
        hass = await _ready(entry)
        seen: list[Any] = []

        async def _configure(device: Any, app_key: bytes, device_type: str) -> dict[str, Any]:
            await device.connect()  # attaches to the shared link
            seen.append((device.unicast, device.element_count, device.is_connected))
            return {CONF_CONFIG_STATE: CONFIG_STATE_COMPLETE, "model_elements": {"1307": 0}}

        with _patches(_configure):
            data = await async_add_node_to_network(hass, MAC_NEW, DEVICE_TYPE_SIG_LIGHT, "New")

        prov = FakeProvisioner.instances[0]
        assert prov.kwargs["net_key"] == bytes.fromhex(entry.data["net_key"])
        assert prov.kwargs["app_key"] == bytes.fromhex(entry.data["app_key"])
        # A occupies 0x0002-0x0003, B 0x0004: first free 6-element range is 0x0005
        assert data[CONF_UNICAST_TARGET] == "0005"
        assert data[CONF_ELEMENT_COUNT] == 6
        assert data[CONF_DEV_KEY] == DEV_KEY_NEW
        assert data[CONF_CONFIG_STATE] == CONFIG_STATE_COMPLETE
        assert seen == [(0x0005, 6, True)]

        sub = hass.config_entries.async_add_subentry.call_args.args[1]
        assert sub.subentry_type == SUBENTRY_TYPE_NODE
        assert sub.unique_id == MAC_NEW
        assert sub.title == "New"
        assert dict(sub.data)[CONF_MAC_ADDRESS] == MAC_NEW
        # The temporary node view detached again
        assert entry.runtime_data.manager.network.node_for(0x0005) is None

    @pytest.mark.asyncio
    async def test_falls_back_to_direct_link(self, air: SimAir, entry: FakeEntry) -> None:
        hass = await _ready(entry)
        attempts: list[str | None] = []

        async def _configure(device: Any, app_key: bytes, device_type: str) -> dict[str, Any]:
            attempts.append(device.network.active_proxy)
            if len(attempts) == 1:
                raise TimeoutError("relay dropped segments")
            return {CONF_CONFIG_STATE: CONFIG_STATE_COMPLETE}

        with _patches(_configure):
            data = await async_add_node_to_network(hass, MAC_NEW, DEVICE_TYPE_SIG_LIGHT, "New")

        assert attempts == [MAC_A, MAC_NEW]
        assert data[CONF_CONFIG_STATE] == CONFIG_STATE_COMPLETE

    @pytest.mark.asyncio
    async def test_config_failure_stored_as_pending(self, air: SimAir, entry: FakeEntry) -> None:
        hass = await _ready(entry)

        async def _configure(device: Any, app_key: bytes, device_type: str) -> dict[str, Any]:
            raise TimeoutError("no reply")

        with _patches(_configure):
            data = await async_add_node_to_network(hass, MAC_NEW, DEVICE_TYPE_SIG_LIGHT, "New")

        assert data[CONF_CONFIG_STATE] == CONFIG_STATE_PENDING
        hass.config_entries.async_add_subentry.assert_called_once()

    @pytest.mark.asyncio
    async def test_readding_known_node_updates_its_subentry(
        self, air: SimAir, entry: FakeEntry
    ) -> None:
        hass = await _ready(entry)
        FakeProvisioner.elements = 1

        async def _configure(device: Any, app_key: bytes, device_type: str) -> dict[str, Any]:
            return {CONF_CONFIG_STATE: CONFIG_STATE_COMPLETE}

        try:
            with _patches(_configure):
                data = await async_add_node_to_network(hass, MAC_B, DEVICE_TYPE_SIG_LIGHT, "B")
        finally:
            FakeProvisioner.elements = 6

        # B's own old range (0x0004) counts as free again
        assert data[CONF_UNICAST_TARGET] == "0004"
        hass.config_entries.async_add_subentry.assert_not_called()
        updated_sub = hass.config_entries.async_update_subentry.call_args.args[1]
        assert updated_sub is entry.subentries["sub_b"]


class TestEnsureNetworkEntry:
    @pytest.mark.asyncio
    async def test_creates_network_when_missing(self) -> None:
        entries: list[Any] = []
        hass = _hass(entries)
        created = FakeEntry({})

        async def _init(domain: str, *, context: dict[str, Any], data: Any) -> dict[str, Any]:
            assert context == {"source": "create_network"}
            created.runtime_data = MeshNetworkRuntimeData(
                manager=MagicMock(), nodes={}, hub_device_info={}
            )
            entries.append(created)
            return {"type": "create_entry"}

        hass.config_entries.flow.async_init = AsyncMock(side_effect=_init)
        assert await async_ensure_network_entry(hass) is created

    @pytest.mark.asyncio
    async def test_unloaded_network_times_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config_flow_network, "NETWORK_READY_TIMEOUT", 0.01)
        hass = _hass([FakeEntry({})])  # exists but never loads
        with pytest.raises(config_flow_network.NetworkUnavailableError):
            await async_ensure_network_entry(hass)


class TestPendingConfig:
    @pytest.mark.asyncio
    async def test_completes_and_updates_subentry(self, air: SimAir) -> None:
        pending = _sub(MAC_B, "0004", DEV_KEY_B)
        pending.data[CONF_CONFIG_STATE] = CONFIG_STATE_PENDING
        entry = FakeEntry({"sub_b": pending})
        hass = _hass([entry])
        hass.config_entries.async_update_subentry = MagicMock()
        with patch.object(
            config_flow_network,
            "async_configure_network_node",
            new=AsyncMock(return_value={CONF_CONFIG_STATE: CONFIG_STATE_COMPLETE}),
        ):
            assert await async_complete_pending_config(hass, entry, "sub_b", MagicMock())
        new_data = hass.config_entries.async_update_subentry.call_args.kwargs["data"]
        assert new_data[CONF_CONFIG_STATE] == CONFIG_STATE_COMPLETE
        assert new_data[CONF_MAC_ADDRESS] == MAC_B

    @pytest.mark.asyncio
    async def test_complete_nodes_untouched(self) -> None:
        entry = FakeEntry({"sub_b": _sub(MAC_B, "0004", DEV_KEY_B)})
        hass = _hass([entry])
        assert not await async_complete_pending_config(hass, entry, "sub_b", MagicMock())


class TestConfigFlowStep:
    def _flow(self) -> Any:
        flow = MagicMock()
        flow.hass = _hass([])
        flow._discovery_info = {"address": MAC_NEW, "name": "Smart Light 00:0C"}
        flow.async_abort = lambda reason: {"type": "abort", "reason": reason}
        flow.async_show_form = lambda **kw: {"type": "form", **kw}
        return flow

    @pytest.mark.asyncio
    async def test_discovery_adds_to_network(self) -> None:
        from custom_components.tuya_ble_mesh import config_flow_sig

        add = AsyncMock(return_value={})
        with (
            patch.object(config_flow_sig, "_use_shared_network", return_value=True),
            patch.object(config_flow_network, "async_add_node_to_network", add),
        ):
            result = await config_flow_sig.async_step_sig_light(self._flow(), {})
        assert result == {"type": "abort", "reason": "added_to_network"}
        add.assert_awaited_once()
        assert add.await_args.args[1:] == (MAC_NEW, DEVICE_TYPE_SIG_LIGHT, "Smart Light 00:0C")

    @pytest.mark.asyncio
    async def test_provisioning_error_shows_form(self) -> None:
        from tuya_ble_mesh.exceptions import ProvisioningError

        from custom_components.tuya_ble_mesh import config_flow_sig

        with (
            patch.object(config_flow_sig, "_use_shared_network", return_value=True),
            patch.object(
                config_flow_network,
                "async_add_node_to_network",
                AsyncMock(side_effect=ProvisioningError("bad confirm")),
            ),
        ):
            result = await config_flow_sig.async_step_sig_light(self._flow(), {})
        assert result["type"] == "form"
        assert result["errors"] == {"base": "provisioning_failed"}


class TestTimeRequestsDuringConfig:
    @pytest.mark.asyncio
    async def test_timestamp_request_answered_then_unregistered(self) -> None:
        import asyncio

        from tuya_ble_mesh.sig_mesh_protocol import TUYA_VENDOR_OPCODE

        from custom_components.tuya_ble_mesh.config_flow_network import answer_time_requests

        callbacks: list[Any] = []
        device = MagicMock()
        device.register_vendor_callback = callbacks.append
        device.unregister_vendor_callback = callbacks.remove
        device.send_vendor_command = AsyncMock()

        stop = answer_time_requests(device)
        callbacks[0](TUYA_VENDOR_OPCODE, bytes([0x02, 0x00]))  # timestamp request
        callbacks[0](0x8204, b"\x01")  # unrelated
        await asyncio.sleep(0)
        device.send_vendor_command.assert_awaited_once()
        assert device.send_vendor_command.await_args.args[0][:3] == bytes.fromhex("cad007")
        stop()
        assert callbacks == []


class TestPendingRetryOnSetup:
    @pytest.mark.asyncio
    async def test_setup_finishes_pending_node(
        self, air: SimAir, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from custom_components.tuya_ble_mesh import network_setup

        monkeypatch.setattr(network_setup, "PENDING_CONFIG_RETRY_S", 0.01)
        pending = _sub(MAC_B, "0004", DEV_KEY_B)
        pending.data[CONF_CONFIG_STATE] = CONFIG_STATE_PENDING
        entry = FakeEntry({"sub_a": _sub(MAC_A, "0002", DEV_KEY_A, 2), "sub_b": pending})
        complete = AsyncMock(return_value=True)
        with patch.object(config_flow_network, "async_complete_pending_config", complete):
            await _setup(entry, _hass([entry]))
        complete.assert_awaited_once()
        assert complete.await_args.args[2] == "sub_b"


class TestDiscoveryOfNetworkNodes:
    @pytest.mark.asyncio
    async def test_online_node_not_offered_offline_node_is(
        self, air: SimAir, entry: FakeEntry
    ) -> None:
        from dataclasses import replace

        from custom_components.tuya_ble_mesh.config_flow_discovery import (
            _is_online_network_node,
        )

        hass = await _ready(entry)
        assert _is_online_network_node(hass, MAC_A.lower())
        assert not _is_online_network_node(hass, MAC_NEW)
        node_a = entry.runtime_data.nodes["sub_a"].coordinator
        node_a._state = replace(node_a._state, available=False)
        assert not _is_online_network_node(hass, MAC_A)
