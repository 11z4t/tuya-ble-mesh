"""HA side of the shared SIG Mesh network: entry + node subentries.

Runs the real MeshNetworkManager / network setup / coordinators / platform
setup against simulated nodes (tests/unit/fake_mesh.py).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_mesh import (  # noqa: E402
    APP_KEY,
    DEV_KEY_A,
    DEV_KEY_B,
    NET_KEY,
    SimAir,
    SimNode,
    make_fake_link_class,
)
from tuya_ble_mesh import sig_mesh_network  # noqa: E402

from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    CONF_APP_KEY,
    CONF_DEV_KEY,
    CONF_DEVICE_TYPE,
    CONF_ELEMENT_COUNT,
    CONF_ENTRY_KIND,
    CONF_MAC_ADDRESS,
    CONF_NET_KEY,
    CONF_NETWORK_UUID,
    CONF_PROVISIONER_ADDR,
    CONF_UNICAST_TARGET,
    DEVICE_TYPE_SIG_LIGHT,
    ENTRY_KIND_SIG_NETWORK,
    SUBENTRY_TYPE_NODE,
)
from custom_components.tuya_ble_mesh.entry_nodes import iter_entry_nodes  # noqa: E402
from custom_components.tuya_ble_mesh.mesh_network import (  # noqa: E402
    allocate_node_address,
    is_network_entry,
    used_unicast_ranges,
)

MAC_A = "DC:23:00:00:00:0A"
MAC_B = "DC:23:00:00:00:0B"


def _sub(mac: str, unicast: str, dev_key: str, elements: int = 1) -> SimpleNamespace:
    return SimpleNamespace(
        subentry_type=SUBENTRY_TYPE_NODE,
        title=f"Light {mac[-5:]}",
        data={
            CONF_MAC_ADDRESS: mac,
            CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_LIGHT,
            CONF_UNICAST_TARGET: unicast,
            CONF_ELEMENT_COUNT: elements,
            CONF_DEV_KEY: dev_key,
        },
    )


class FakeEntry:
    """Enough of a ConfigEntry for network setup."""

    def __init__(self, subentries: dict[str, Any]) -> None:
        self.entry_id = "net_entry"
        self.title = "SIG Mesh Network"
        self.data = {
            CONF_ENTRY_KIND: ENTRY_KIND_SIG_NETWORK,
            CONF_NETWORK_UUID: "abc123",
            CONF_NET_KEY: NET_KEY,
            CONF_APP_KEY: APP_KEY,
            CONF_PROVISIONER_ADDR: 1,
        }
        self.options: dict[str, Any] = {}
        self.subentries = subentries
        self.tasks: list[asyncio.Task[Any]] = []
        self.unload_callbacks: list[Any] = []
        self.update_listeners: list[Any] = []

    def async_create_background_task(
        self, hass: Any, coro: Any, name: str, eager_start: bool = True
    ) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    def async_on_unload(self, cb: Any) -> None:
        self.unload_callbacks.append(cb)

    def add_update_listener(self, listener: Any) -> Any:
        self.update_listeners.append(listener)
        return lambda: None


def _hass(entries: list[Any]) -> MagicMock:
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_entries = MagicMock(return_value=entries)
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock()
    hass.async_add_import_executor_job = AsyncMock()
    return hass


def _legacy_sig_entry() -> SimpleNamespace:
    return SimpleNamespace(
        data={CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_LIGHT, CONF_MAC_ADDRESS: "DC:23:53:DA:B3:D4"},
        entry_id="legacy",
        subentries={},
    )


# --- Allocation / helpers ------------------------------------------------------


class TestAllocation:
    def test_network_entry_detection(self) -> None:
        assert is_network_entry(FakeEntry({}))
        assert not is_network_entry(_legacy_sig_entry())

    def test_allocates_after_existing_nodes(self) -> None:
        entry = FakeEntry({"s1": _sub(MAC_A, "0002", DEV_KEY_A, 6)})
        hass = _hass([entry])
        assert used_unicast_ranges(hass, entry) == [(1, 1), (0x0002, 6)]
        assert allocate_node_address(hass, entry, 6) == 0x0008

    def test_legacy_range_reserved_until_migrated(self) -> None:
        entry = FakeEntry({})
        entry.options["reserved_ranges"] = [(0x0002, 0x00AE)]
        hass = _hass([entry, _legacy_sig_entry()])
        # 0x0002..0x00AF taken by the option, 0x00B0..0x00B5 legacy-reserved
        assert allocate_node_address(hass, entry, 6) == 0x00B6
        hass = _hass([entry])
        assert allocate_node_address(hass, entry, 6) == 0x00B0


# --- Full setup against simulated nodes ----------------------------------------


@pytest.fixture
def air(monkeypatch: pytest.MonkeyPatch) -> SimAir:
    sim = SimAir(
        nodes=[
            SimNode(MAC_A, 0x0002, DEV_KEY_A, element_count=2),
            SimNode(MAC_B, 0x0004, DEV_KEY_B),
        ]
    )
    monkeypatch.setattr(sig_mesh_network, "GattProxyLink", make_fake_link_class(sim))
    return sim


def _service_info(rssi: int) -> Any:
    import time

    return SimpleNamespace(rssi=rssi, time=time.monotonic())


async def _setup(entry: FakeEntry, hass: MagicMock) -> None:
    from custom_components.tuya_ble_mesh.network_setup import async_setup_network_entry

    infos = {MAC_A: _service_info(-60), MAC_B: _service_info(-75)}
    with (
        patch(
            "homeassistant.components.bluetooth.async_last_service_info",
            side_effect=lambda _h, mac, connectable=True: infos.get(mac),
        ),
        patch("custom_components.tuya_ble_mesh._async_register_services", new=AsyncMock()),
    ):
        assert await async_setup_network_entry(hass, entry) is True
        await asyncio.gather(*entry.tasks)
        for _ in range(10):
            await asyncio.sleep(0)


@pytest.fixture
def entry() -> FakeEntry:
    return FakeEntry(
        {
            "sub_a": _sub(MAC_A, "0002", DEV_KEY_A, 2),
            "sub_b": _sub(MAC_B, "0004", DEV_KEY_B),
        }
    )


class TestNetworkSetup:
    @pytest.mark.asyncio
    async def test_both_nodes_up_over_one_link(self, air: SimAir, entry: FakeEntry) -> None:
        hass = _hass([entry])
        await _setup(entry, hass)

        runtime = entry.runtime_data
        assert air.connects == [MAC_A]  # strongest node, one link
        assert runtime.manager.network.active_proxy == MAC_A
        nodes = iter_entry_nodes(entry)
        assert {n.subentry_id for n in nodes} == {"sub_a", "sub_b"}
        assert all(n.runtime.coordinator.state.available for n in nodes)
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()

        # Control node B through the link carried by node A
        node_b = next(n for n in nodes if n.subentry_id == "sub_b")
        await node_b.runtime.coordinator.device.send_onoff(True)
        assert air.nodes[1].on is True

        from custom_components.tuya_ble_mesh.network_setup import async_unload_network_entry

        assert await async_unload_network_entry(hass, entry) is True
        assert not runtime.manager.network.is_connected

    @pytest.mark.asyncio
    async def test_seq_reservation_persisted_before_use(
        self, air: SimAir, entry: FakeEntry
    ) -> None:
        hass = _hass([entry])
        await _setup(entry, hass)
        manager = entry.runtime_data.manager
        saved = manager._store._data
        assert saved["seq"] >= manager.network.get_seq()

        from custom_components.tuya_ble_mesh.network_setup import async_unload_network_entry

        await async_unload_network_entry(hass, entry)
        assert manager._store._data["seq"] >= manager.network.get_seq()

    @pytest.mark.asyncio
    async def test_removed_subentry_gets_node_reset(self, air: SimAir, entry: FakeEntry) -> None:
        from custom_components.tuya_ble_mesh.network_setup import (
            _async_network_update_listener,
        )

        hass = _hass([entry])
        await _setup(entry, hass)
        del entry.subentries["sub_b"]
        await _async_network_update_listener(hass, entry)
        assert air.nodes[1].reset_done is True
        assert air.nodes[0].reset_done is False
        hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)

    @pytest.mark.asyncio
    async def test_bad_subentry_skipped(self, air: SimAir) -> None:
        bad = _sub(MAC_B, "0004", "")
        entry = FakeEntry({"sub_a": _sub(MAC_A, "0002", DEV_KEY_A, 2), "bad": bad})
        hass = _hass([entry])
        await _setup(entry, hass)
        assert set(entry.runtime_data.nodes) == {"sub_a"}

    @pytest.mark.asyncio
    async def test_platforms_add_entities_under_subentries(
        self, air: SimAir, entry: FakeEntry
    ) -> None:
        from custom_components.tuya_ble_mesh import binary_sensor, light

        hass = _hass([entry])
        await _setup(entry, hass)

        calls: list[tuple[list[Any], dict[str, Any]]] = []

        def _add(entities: list[Any], **kwargs: Any) -> None:
            calls.append((entities, kwargs))

        await light.async_setup_entry(hass, entry, _add)
        assert sorted(c[1]["config_subentry_id"] for c in calls) == ["sub_a", "sub_b"]
        uids = sorted(e.unique_id for c in calls for e in c[0])
        assert uids == [f"{MAC_A}_light", f"{MAC_B}_light"]

        calls.clear()
        await binary_sensor.async_setup_entry(hass, entry, _add)
        hub_calls = [c for c in calls if "config_subentry_id" not in c[1]]
        assert len(hub_calls) == 1
        hub_entity = hub_calls[0][0][0]
        assert hub_entity._attr_unique_id == "abc123_mesh_link"
        assert hub_entity.is_on is True

    @pytest.mark.asyncio
    async def test_diagnostics_redacts_keys(self, air: SimAir, entry: FakeEntry) -> None:
        from custom_components.tuya_ble_mesh.diagnostics import (
            async_get_config_entry_diagnostics,
        )

        hass = _hass([entry])
        await _setup(entry, hass)
        diag = await async_get_config_entry_diagnostics(hass, entry)
        text = repr(diag)
        for secret in (NET_KEY, APP_KEY, DEV_KEY_A, DEV_KEY_B):
            assert secret not in text
        assert MAC_A not in text
        assert diag["network"]["connected"] is True
        assert {n["unicast"] for n in diag["nodes"]} == {"0x0002", "0x0004"}


# --- Config flow -----------------------------------------------------------------


class TestConfigFlow:
    @pytest.mark.asyncio
    async def test_create_network_entry(self) -> None:
        from custom_components.tuya_ble_mesh.config_flow import TuyaBLEMeshConfigFlow

        flow = TuyaBLEMeshConfigFlow()
        flow.hass = _hass([])
        form = await flow.async_step_create_network(None)
        assert form["type"] == "form"
        result = await flow.async_step_create_network({})
        assert result["type"] == "create_entry"
        data = result["data"]
        assert data[CONF_ENTRY_KIND] == ENTRY_KIND_SIG_NETWORK
        assert len(bytes.fromhex(data[CONF_NET_KEY])) == 16
        assert len(bytes.fromhex(data[CONF_APP_KEY])) == 16
        assert data[CONF_NET_KEY] != data[CONF_APP_KEY]

    @pytest.mark.asyncio
    async def test_only_one_network(self) -> None:
        from custom_components.tuya_ble_mesh.config_flow import TuyaBLEMeshConfigFlow

        flow = TuyaBLEMeshConfigFlow()
        flow.hass = _hass([FakeEntry({})])
        result = await flow.async_step_create_network(None)
        assert result == {"type": "abort", "reason": "network_already_exists"}

    def test_subentry_types_only_on_network_entries(self) -> None:
        from custom_components.tuya_ble_mesh.config_flow import (
            MeshNodeSubentryFlow,
            TuyaBLEMeshConfigFlow,
        )

        assert TuyaBLEMeshConfigFlow.async_get_supported_subentry_types(FakeEntry({})) == {
            SUBENTRY_TYPE_NODE: MeshNodeSubentryFlow
        }
        assert TuyaBLEMeshConfigFlow.async_get_supported_subentry_types(_legacy_sig_entry()) == {}

    @pytest.mark.asyncio
    async def test_node_subentry_flow_points_to_discovery(self) -> None:
        from custom_components.tuya_ble_mesh.config_flow import MeshNodeSubentryFlow

        result = await MeshNodeSubentryFlow().async_step_user()
        assert result["reason"] == "add_via_discovery"
