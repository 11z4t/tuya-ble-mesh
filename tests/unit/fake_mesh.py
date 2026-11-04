"""Simulated SIG Mesh nodes + proxy link for MeshNetwork tests.

The simulated nodes use the library's own crypto, so tests exercise real
network/upper-transport encryption, per-node device keys and routing.
Test-only keys below are random constants, not real network keys.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from tuya_ble_mesh.exceptions import MeshConnectionError
from tuya_ble_mesh.sig_mesh_protocol import (
    MeshKeys,
    decrypt_access_payload,
    decrypt_network_pdu,
    encrypt_network_pdu,
    make_access_unsegmented,
    make_proxy_pdu,
    parse_access_opcode,
    parse_proxy_pdu,
)

NET_KEY = "7dd7364cd842ad18c17c2b820c84c3d6"  # pragma: allowlist secret
APP_KEY = "63964771734fbd76e3b40519d1d94a48"  # pragma: allowlist secret
DEV_KEY_A = "9d6dd0e96eb25dc19a40ed9914f8f03f"  # pragma: allowlist secret
DEV_KEY_B = "1ab2c3d4e5f60718293a4b5c6d7e8f90"  # pragma: allowlist secret


@dataclass
class SimNode:
    """A simulated mesh node that answers a few Get/Set messages."""

    mac: str
    unicast: int
    dev_key: str
    element_count: int = 1
    on: bool = False
    relay_state: int = 0x01
    seq: int = 100
    received: list[int] = field(default_factory=list)
    reset_done: bool = False

    def keys(self) -> MeshKeys:
        return MeshKeys(NET_KEY, self.dev_key, APP_KEY)

    def handle(self, proxy_pdu: bytes) -> list[bytes]:
        """Process a proxy PDU written by the client; return reply proxy PDUs."""
        keys = self.keys()
        net = decrypt_network_pdu(
            keys.enc_key, keys.priv_key, keys.nid, parse_proxy_pdu(proxy_pdu).payload
        )
        if net is None or not self.unicast <= net.dst < self.unicast + self.element_count:
            return []
        msg = decrypt_access_payload(keys, net.src, net.dst, net.seq, net.transport_pdu)
        if msg is None or msg.access_payload is None:
            return []
        opcode, params = parse_access_opcode(msg.access_payload)
        self.received.append(opcode)
        reply: bytes | None = None
        if opcode in (0x8201,):
            reply = bytes([0x82, 0x04, int(self.on)])
        elif opcode in (0x8202,):
            self.on = bool(params[0])
            reply = bytes([0x82, 0x04, int(self.on)])
        elif opcode == 0x8026:
            reply = bytes([0x80, 0x28, self.relay_state, 0x21])
        elif opcode == 0x8049:
            self.reset_done = True
            reply = bytes([0x80, 0x4A])
        if reply is None:
            return []
        return [self.reply(net.dst, net.src, reply, akf=msg.akf)]

    def reply(self, src: int, dst: int, access: bytes, *, akf: int) -> bytes:
        keys = self.keys()
        self.seq += 1
        key = keys.app_key if akf else keys.dev_key
        assert key is not None
        transport = make_access_unsegmented(
            key, src, dst, self.seq, 0, access, akf=akf, aid=keys.aid if akf else 0
        )
        net = encrypt_network_pdu(
            keys.enc_key,
            keys.priv_key,
            keys.nid,
            ctl=0,
            ttl=4,
            seq=self.seq,
            src=src,
            dst=dst,
            transport_pdu=transport,
        )
        return make_proxy_pdu(net)


@dataclass
class SimAir:
    """All simulated nodes plus link behaviour knobs."""

    nodes: list[SimNode]
    unreachable: set[str] = field(default_factory=set)
    duplicate_replies: bool = False
    connects: list[str] = field(default_factory=list)
    links: list[Any] = field(default_factory=list)
    connect_delay: float = 0.0


def make_fake_link_class(air: SimAir) -> type:
    """Return a GattProxyLink stand-in bound to ``air``."""

    class FakeLink:
        def __init__(
            self, address: str, on_notify: Any, on_disconnect: Any, **_kwargs: Any
        ) -> None:
            self.address = address.upper()
            self._on_notify = on_notify
            self._on_disconnect = on_disconnect
            self._up = False
            air.links.append(self)

        @property
        def is_connected(self) -> bool:
            return self._up

        @property
        def rssi(self) -> int | None:
            return -60 if self._up else None

        async def connect(self, timeout: float) -> None:
            air.connects.append(self.address)
            if air.connect_delay:
                await asyncio.sleep(air.connect_delay)
            if self.address in air.unreachable:
                raise MeshConnectionError(f"{self.address} unreachable")
            self._up = True

        async def write(self, proxy_pdu: bytes) -> None:
            if not self._up:
                raise MeshConnectionError("down")
            loop = asyncio.get_running_loop()
            for node in air.nodes:
                for reply in node.handle(proxy_pdu):
                    copies = 2 if air.duplicate_replies else 1
                    for _ in range(copies):
                        loop.call_soon(self._on_notify, reply)

        async def disconnect(self) -> None:
            self._up = False

        def drop(self) -> None:
            self._up = False
            self._on_disconnect(self)

    return FakeLink
