"""Shared SIG Mesh network: primitives and end-to-end over a simulated link."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

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
from tuya_ble_mesh.exceptions import (  # noqa: E402
    DisconnectedError,
    MeshConnectionError,
    ProtocolError,
    SIGMeshError,
)
from tuya_ble_mesh.sig_mesh_address_allocator import (  # noqa: E402
    AddressSpaceExhaustedError,
    allocate_unicast,
)
from tuya_ble_mesh.sig_mesh_network import MeshNetwork  # noqa: E402
from tuya_ble_mesh.sig_mesh_node import SIGMeshLightNode, SIGMeshNode  # noqa: E402
from tuya_ble_mesh.sig_mesh_proxy_selector import (  # noqa: E402
    ProxyCandidate,
    ProxySelector,
)
from tuya_ble_mesh.sig_mesh_replay import ReplayCache  # noqa: E402

MAC_A = "DC:23:00:00:00:0A"
MAC_B = "DC:23:00:00:00:0B"


# --- Primitives ---------------------------------------------------------------


class TestReplayCache:
    def test_accepts_increasing_rejects_duplicates(self) -> None:
        cache = ReplayCache()
        assert cache.check_and_record(0x10, 5, 0)
        assert not cache.check_and_record(0x10, 5, 0)
        assert not cache.check_and_record(0x10, 4, 0)
        assert cache.check_and_record(0x10, 6, 0)
        assert cache.check_and_record(0x11, 1, 0)

    def test_new_iv_index_accepts_lower_seq(self) -> None:
        cache = ReplayCache()
        assert cache.check_and_record(0x10, 1000, 0)
        assert cache.check_and_record(0x10, 1, 1)

    def test_eviction_and_forget(self) -> None:
        cache = ReplayCache(max_sources=2)
        cache.check_and_record(1, 1, 0)
        cache.check_and_record(2, 1, 0)
        cache.check_and_record(3, 1, 0)
        assert len(cache) == 2
        assert cache.check_and_record(1, 1, 0)  # evicted, so accepted again
        cache.forget(1)
        assert cache.check_and_record(1, 1, 0)


class TestAllocator:
    def test_first_free_and_gaps(self) -> None:
        assert allocate_unicast(6, []) == 0x0002
        used = [(0x0002, 6), (0x0010, 2)]
        assert allocate_unicast(6, used) == 0x0008
        assert allocate_unicast(8, used) == 0x0008
        assert allocate_unicast(9, used) == 0x0012

    def test_reserved_range_skipped(self) -> None:
        assert allocate_unicast(6, [(0x0002, 6), (0x00B0, 6)], first=0x00AD) == 0x00B6

    def test_exhausted_and_invalid(self) -> None:
        with pytest.raises(AddressSpaceExhaustedError):
            allocate_unicast(2, [(0x0002, 0x7FFE)])
        with pytest.raises(ProtocolError):
            allocate_unicast(0, [])


class TestSelector:
    def test_rssi_order_penalty_and_stickiness(self) -> None:
        sel = ProxySelector()

        def cands(t: float) -> list[ProxyCandidate]:
            return [ProxyCandidate(MAC_A, -80, t), ProxyCandidate(MAC_B, -75, t)]

        assert sel.rank(cands(100.0), now=100.0) == [MAC_B, MAC_A]
        sel.record_failure(MAC_B, now=100.0)
        assert sel.rank(cands(100.0), now=100.0) == [MAC_A, MAC_B]
        # Penalty decays away
        assert sel.rank(cands(1000.0), now=1000.0) == [MAC_B, MAC_A]
        sel.record_success(MAC_A)
        assert sel.rank(cands(1000.0), now=1000.0)[0] == MAC_A  # within stickiness margin

    def test_stale_and_excluded_dropped(self) -> None:
        sel = ProxySelector(excluded=frozenset({MAC_B}))
        cands = [ProxyCandidate(MAC_A, -50, 0.0), ProxyCandidate(MAC_B, -40, 1000.0)]
        assert sel.rank(cands, now=1000.0) == []


# --- End to end ---------------------------------------------------------------


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


def _candidates(*macs_rssi: tuple[str, int]) -> Any:
    def _provider() -> list[ProxyCandidate]:
        import time

        now = time.monotonic()
        return [ProxyCandidate(mac, rssi, now) for mac, rssi in macs_rssi]

    return _provider


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


def _network(provider: Any = None) -> MeshNetwork:
    return MeshNetwork(NET_KEY, APP_KEY, candidate_provider=provider)


class TestMeshNetworkEndToEnd:
    @pytest.mark.asyncio
    async def test_two_lights_one_link_routed_by_src(self, air: SimAir) -> None:
        net = _network(_candidates((MAC_A, -60), (MAC_B, -80)))
        a = SIGMeshLightNode(MAC_A, net, 0x0002, DEV_KEY_A, element_count=2)
        b = SIGMeshLightNode(MAC_B, net, 0x0004, DEV_KEY_B)
        seen_a: list[bool] = []
        seen_b: list[bool] = []
        a.register_onoff_callback(seen_a.append)
        b.register_onoff_callback(seen_b.append)

        await asyncio.gather(a.connect(), b.connect())
        assert air.connects == [MAC_A]  # single-flight: one link for both
        assert net.active_proxy == MAC_A
        assert a.is_connected and b.is_connected

        await b.send_onoff(True)
        await _settle()
        assert air.nodes[1].on is True
        assert b.light_state.is_on is True
        assert a.light_state.is_on is None
        assert seen_a == []

    @pytest.mark.asyncio
    async def test_config_replies_use_each_nodes_dev_key(self, air: SimAir) -> None:
        air.nodes[1].relay_state = 0x00
        net = _network(_candidates((MAC_A, -60)))
        a = SIGMeshNode(MAC_A, net, 0x0002, DEV_KEY_A, element_count=2)
        b = SIGMeshNode(MAC_B, net, 0x0004, DEV_KEY_B)
        await a.connect()
        await b.connect()
        res_a, res_b = await asyncio.gather(
            a.probe_node_features(response_timeout=0.05),
            b.probe_node_features(response_timeout=0.05),
        )
        assert res_a["relay"].startswith("enabled")
        assert res_b["relay"].startswith("disabled")

    @pytest.mark.asyncio
    async def test_relayed_duplicates_dropped(self, air: SimAir) -> None:
        air.duplicate_replies = True
        net = _network(_candidates((MAC_A, -60)))
        b = SIGMeshLightNode(MAC_B, net, 0x0004, DEV_KEY_B)
        # The first Status resolves the pending Set; an undropped duplicate
        # would arrive unsolicited and reach the onoff callbacks.
        unsolicited: list[bool] = []
        light_updates: list[Any] = []
        b.register_onoff_callback(unsolicited.append)
        b.register_light_callback(light_updates.append)
        await b.connect()
        await b.send_onoff(True)
        await _settle()
        assert unsolicited == []
        assert len(light_updates) == 1

    @pytest.mark.asyncio
    async def test_failover_to_next_candidate(self, air: SimAir) -> None:
        air.unreachable = {MAC_A}
        net = _network(_candidates((MAC_A, -50), (MAC_B, -70)))
        b = SIGMeshLightNode(MAC_B, net, 0x0004, DEV_KEY_B)
        await b.connect()
        assert air.connects == [MAC_A, MAC_B]
        assert net.active_proxy == MAC_B

    @pytest.mark.asyncio
    async def test_link_drop_fails_pending_and_notifies(self, air: SimAir) -> None:
        net = _network(_candidates((MAC_A, -60), (MAC_B, -70)))
        b = SIGMeshLightNode(MAC_B, net, 0x0004, DEV_KEY_B)
        drops: list[int] = []
        b.register_disconnect_callback(lambda: drops.append(1))
        states: list[bool] = []
        net.add_link_listener(states.append)
        await b.connect()

        air.nodes[1].dev_key = DEV_KEY_A  # node stops answering (wrong key)
        probe = asyncio.ensure_future(b.send_config_node_reset(response_timeout=5))
        await _settle()
        air.links[-1].drop()
        with pytest.raises((DisconnectedError, SIGMeshError)):
            await probe
        assert drops == [1]
        assert states == [True, False]
        assert not b.is_connected

        # Reconnect avoids the proxy that just dropped
        air.nodes[1].dev_key = DEV_KEY_B
        await b.connect()
        assert net.active_proxy == MAC_B

    @pytest.mark.asyncio
    async def test_no_candidates_raises(self, air: SimAir) -> None:
        net = _network(_candidates())
        node = SIGMeshNode(MAC_A, net, 0x0002, DEV_KEY_A)
        with pytest.raises(MeshConnectionError):
            await node.connect()

    @pytest.mark.asyncio
    async def test_disconnect_detaches_but_keeps_link(self, air: SimAir) -> None:
        net = _network(_candidates((MAC_A, -60)))
        a = SIGMeshNode(MAC_A, net, 0x0002, DEV_KEY_A, element_count=2)
        b = SIGMeshNode(MAC_B, net, 0x0004, DEV_KEY_B)
        await a.connect()
        await b.connect()
        await a.disconnect()
        assert net.is_connected
        assert not a.is_connected
        assert b.is_connected
        assert net.node_for(0x0002) is None

    @pytest.mark.asyncio
    async def test_overlapping_ranges_rejected(self, air: SimAir) -> None:
        net = _network(_candidates((MAC_A, -60)))
        a = SIGMeshNode(MAC_A, net, 0x0002, DEV_KEY_A, element_count=3)
        b = SIGMeshNode(MAC_B, net, 0x0004, DEV_KEY_B)
        await a.connect()
        with pytest.raises(SIGMeshError, match="overlaps"):
            await b.connect()

    @pytest.mark.asyncio
    async def test_shared_seq_and_node_reset(self, air: SimAir) -> None:
        net = _network(_candidates((MAC_A, -60)))
        a = SIGMeshNode(MAC_A, net, 0x0002, DEV_KEY_A, element_count=2)
        b = SIGMeshNode(MAC_B, net, 0x0004, DEV_KEY_B)
        await a.connect()
        await b.connect()
        before = net.get_seq()
        assert await b.send_config_node_reset() is True
        assert air.nodes[1].reset_done
        assert net.get_seq() > before
        assert a.get_seq() == b.get_seq() == net.get_seq()

    @pytest.mark.asyncio
    async def test_close_drops_link(self, air: SimAir) -> None:
        net = _network(_candidates((MAC_A, -60)))
        a = SIGMeshNode(MAC_A, net, 0x0002, DEV_KEY_A)
        await a.connect()
        await net.close()
        assert not net.is_connected
        with pytest.raises(MeshConnectionError):
            await net.ensure_connected()


class TestUseProxy:
    @pytest.mark.asyncio
    async def test_moves_link_to_requested_node(self, air: SimAir) -> None:
        net = _network(_candidates((MAC_A, -50), (MAC_B, -80)))
        a = SIGMeshLightNode(MAC_A, net, 0x0002, DEV_KEY_A, element_count=2)
        b = SIGMeshLightNode(MAC_B, net, 0x0004, DEV_KEY_B)
        states: list[bool] = []
        await a.connect()
        await b.connect()
        net.add_link_listener(states.append)
        assert net.active_proxy == MAC_A

        await net.use_proxy(MAC_B)
        assert net.active_proxy == MAC_B
        assert states == [False, True]
        await net.use_proxy(MAC_B)  # already there: no-op
        assert air.connects == [MAC_A, MAC_B]
        # Nodes reattach on their next connect and work over the new link
        await a.connect()
        await a.send_onoff(True)
        await _settle()
        assert air.nodes[0].on is True

    @pytest.mark.asyncio
    async def test_unreachable_node_raises(self, air: SimAir) -> None:
        air.unreachable = {MAC_B}
        net = _network(_candidates((MAC_A, -50)))
        with pytest.raises(MeshConnectionError):
            await net.use_proxy(MAC_B)
