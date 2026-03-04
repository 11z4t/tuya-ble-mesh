#!/usr/bin/env python3
"""SIG Mesh provisioner via bluetooth-meshd D-Bus API.

Discovers unprovisioned SIG Mesh devices, provisions them into our
network, and exports the mesh configuration (keys, addresses).

Requires bluetooth-meshd running (bluetoothd must be stopped).

SECURITY: Device keys and network keys are written to a JSON file
only — never printed to stdout.
"""

import asyncio
import json
import logging
import sys
import uuid as uuid_mod
from pathlib import Path

from dbus_next.aio import MessageBus
from dbus_next import BusType, Variant
from dbus_next.service import ServiceInterface, PropertyAccess, method, signal, dbus_property

_LOGGER = logging.getLogger(__name__)

MESH_SERVICE = "org.bluez.mesh"
MESH_PATH = "/org/bluez/mesh"
APP_PATH = "/org/mesh/cfgclient"
AGENT_PATH = f"{APP_PATH}/agent"
ELEMENT_PATH = f"{APP_PATH}/ele00"

TOKEN_FILE = Path("/tmp/mesh_provision_token.json")
CONFIG_FILE = Path("/tmp/mesh_config_export.json")

# SIG Mesh model IDs
CONFIG_SERVER = 0x0000
CONFIG_CLIENT = 0x0001


class Element0(ServiceInterface):
    """Element 0 — hosts Config Client model."""

    def __init__(self) -> None:
        super().__init__("org.bluez.mesh.Element1")
        self.received_messages: list[dict] = []

    @method()
    def MessageReceived(self, source: "q", key_index: "q", destination: "v", data: "ay") -> None:
        msg = {
            "source": source,
            "key_index": key_index,
            "data": bytes(data).hex(),
            "len": len(data),
        }
        self.received_messages.append(msg)
        _LOGGER.info("Message from 0x%04X: %d bytes", source, len(data))

    @method()
    def DevKeyMessageReceived(self, source: "q", remote: "b", net_index: "q", data: "ay") -> None:
        msg = {
            "source": source,
            "remote": remote,
            "net_index": net_index,
            "data": bytes(data).hex(),
            "len": len(data),
        }
        self.received_messages.append(msg)
        _LOGGER.info("DevKey message from 0x%04X: %d bytes", source, len(data))

    @method()
    def UpdateModelConfiguration(self, model_id: "q", config: "a{sv}") -> None:
        _LOGGER.info("Model 0x%04X config updated", model_id)

    @dbus_property(access=PropertyAccess.READ)
    def Index(self) -> "y":
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def Models(self) -> "a(qa{sv})":
        return [
            [CONFIG_SERVER, {}],
            [CONFIG_CLIENT, {}],
        ]


class ProvisionAgent(ServiceInterface):
    """Provisioning agent for OOB handling."""

    def __init__(self) -> None:
        super().__init__("org.bluez.mesh.ProvisionAgent1")

    @method()
    def PrivateKey(self) -> "ay":
        _LOGGER.info("PrivateKey requested (using default)")
        return b""

    @method()
    def PublicKey(self) -> "ay":
        _LOGGER.info("PublicKey requested (using default)")
        return b""

    @method()
    def DisplayString(self, value: "s") -> None:
        _LOGGER.info("Display: %s", value)

    @method()
    def DisplayNumeric(self, type_: "s", number: "u") -> None:
        _LOGGER.info("Display numeric %s: %d", type_, number)

    @method()
    def PromptNumeric(self, type_: "s") -> "u":
        _LOGGER.info("Prompt numeric %s (returning 0)", type_)
        return 0

    @method()
    def PromptStatic(self, type_: "s") -> "ay":
        _LOGGER.info("Prompt static %s (returning zeros)", type_)
        return b"\x00" * 16

    @method()
    def Cancel(self) -> None:
        _LOGGER.info("Provisioning cancelled")

    @dbus_property(access=PropertyAccess.READ)
    def Capabilities(self) -> "as":
        return []


class Application(ServiceInterface):
    """Mesh application registration."""

    def __init__(self) -> None:
        super().__init__("org.bluez.mesh.Application1")

    @method()
    def JoinComplete(self, token: "t") -> None:
        _LOGGER.info("Join complete, token: %016x", token)
        TOKEN_FILE.write_text(json.dumps({"token": f"{token:016x}"}))

    @method()
    def JoinFailed(self, reason: "s") -> None:
        _LOGGER.error("Join failed: %s", reason)

    @dbus_property(access=PropertyAccess.READ)
    def CompanyID(self) -> "q":
        return 0x05F1

    @dbus_property(access=PropertyAccess.READ)
    def ProductID(self) -> "q":
        return 0x0002

    @dbus_property(access=PropertyAccess.READ)
    def VersionID(self) -> "q":
        return 0x0001

    @dbus_property(access=PropertyAccess.READ)
    def CRPL(self) -> "q":
        return 0x7FFF


class Provisioner(ServiceInterface):
    """Provisioner interface for scanning/provisioning unprovisioned devices."""

    def __init__(self) -> None:
        super().__init__("org.bluez.mesh.Provisioner1")
        self.discovered: list[dict] = []
        self.prov_result: asyncio.Future | None = None

    @method()
    def ScanResult(self, rssi: "n", data: "ay", options: "a{sv}") -> None:
        uuid_bytes = bytes(data)
        uuid_hex = uuid_bytes.hex()
        entry = {"rssi": rssi, "uuid": uuid_hex, "options": str(options)}
        self.discovered.append(entry)
        _LOGGER.info("Unprovisioned device: UUID=%s RSSI=%d", uuid_hex, rssi)
        print(f"  Found unprovisioned: UUID={uuid_hex} RSSI={rssi}")

    @method()
    def RequestProvData(self, count: "y") -> "qq":
        _LOGGER.info("RequestProvData: %d elements", count)
        # Assign unicast address 0x00AA (first in our range)
        net_index = 0
        unicast = 0x00AA
        print(f"  Assigning unicast 0x{unicast:04X} to provisioned device")
        return [net_index, unicast]

    @method()
    def AddNodeComplete(self, uuid: "ay", unicast: "q", count: "y") -> None:
        uuid_hex = bytes(uuid).hex()
        _LOGGER.info(
            "Provisioning COMPLETE: UUID=%s unicast=0x%04X elements=%d",
            uuid_hex,
            unicast,
            count,
        )
        print(f"\n  *** PROVISIONING COMPLETE ***")
        print(f"  UUID: {uuid_hex}")
        print(f"  Unicast: 0x{unicast:04X}")
        print(f"  Elements: {count}")
        if self.prov_result and not self.prov_result.done():
            self.prov_result.set_result({"uuid": uuid_hex, "unicast": unicast, "count": count})

    @method()
    def AddNodeFailed(self, uuid: "ay", reason: "s") -> None:
        uuid_hex = bytes(uuid).hex()
        _LOGGER.error("Provisioning FAILED: UUID=%s reason=%s", uuid_hex, reason)
        print(f"\n  *** PROVISIONING FAILED: {reason} ***")
        if self.prov_result and not self.prov_result.done():
            self.prov_result.set_exception(RuntimeError(reason))


async def run() -> None:
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Create and export our D-Bus objects
    app = Application()
    agent = ProvisionAgent()
    element = Element0()
    provisioner = Provisioner()

    bus.export(APP_PATH, app)
    bus.export(AGENT_PATH, agent)
    bus.export(APP_PATH, provisioner)
    bus.export(ELEMENT_PATH, element)

    # Get mesh network interface
    introspection = await bus.introspect(MESH_SERVICE, MESH_PATH)
    proxy = bus.get_proxy_object(MESH_SERVICE, MESH_PATH, introspection)
    network = proxy.get_interface("org.bluez.mesh.Network1")

    # Check if we have a saved token
    token = None
    if TOKEN_FILE.exists():
        saved = json.loads(TOKEN_FILE.read_text())
        token = int(saved["token"], 16)
        print(f"Using saved token: {saved['token']}")

    # Try to attach with existing token, or create new network
    node_path = None
    if token is not None:
        try:
            result = await network.call_attach(APP_PATH, token)
            node_path = result[0]
            print(f"Attached to existing network: {node_path}")
        except Exception as e:
            print(f"Attach failed ({type(e).__name__}), creating new network...")
            token = None

    if token is None:
        print("Creating new mesh network...")
        app_uuid = uuid_mod.uuid4().bytes
        await network.call_create_network(APP_PATH, app_uuid)
        # Wait for JoinComplete callback
        for _ in range(50):
            await asyncio.sleep(0.2)
            if TOKEN_FILE.exists():
                saved = json.loads(TOKEN_FILE.read_text())
                token = int(saved["token"], 16)
                break
        if token is None:
            print("ERROR: Network creation failed (no token)")
            return

        result = await network.call_attach(APP_PATH, token)
        node_path = result[0]
        print(f"Created and attached: {node_path}")

    # Get management interface for the node
    node_intro = await bus.introspect(MESH_SERVICE, node_path)
    node_proxy = bus.get_proxy_object(MESH_SERVICE, node_path, node_intro)
    mgmt = node_proxy.get_interface("org.bluez.mesh.Management1")
    node = node_proxy.get_interface("org.bluez.mesh.Node1")

    # Create app key if needed
    try:
        await mgmt.call_create_app_key(0, 0)
        print("App key 0 created")
    except Exception:
        print("App key 0 already exists")

    # Step 1: Scan for unprovisioned devices
    print(f"\n{'=' * 50}")
    print("Scanning for unprovisioned devices (30s)...")
    print(f"{'=' * 50}")

    await mgmt.call_unprovisioned_scan(0, {})
    await asyncio.sleep(30)
    await mgmt.call_unprovisioned_scan_cancel()

    if not provisioner.discovered:
        print("\nNo unprovisioned devices found!")
        print("Make sure the S17 is removed from Malmbergs app and powered on.")
        return

    # Show discovered devices
    print(f"\nFound {len(provisioner.discovered)} device(s):")
    unique = {}
    for d in provisioner.discovered:
        unique[d["uuid"]] = d
    for uuid, d in unique.items():
        print(f"  UUID: {uuid}  RSSI: {d['rssi']}")

    # Step 2: Provision first discovered device
    target_uuid = list(unique.keys())[0]
    print(f"\nProvisioning UUID: {target_uuid}...")

    provisioner.prov_result = asyncio.get_event_loop().create_future()
    uuid_bytes = bytes.fromhex(target_uuid)
    await mgmt.call_add_node(uuid_bytes, {})

    # Wait for provisioning result
    try:
        result = await asyncio.wait_for(provisioner.prov_result, timeout=60.0)
        print(f"\nProvisioned successfully!")

        # Export configuration
        config = {
            "provisioned_device": result,
            "network_token": f"{token:016x}",
            "node_path": node_path,
            "meshd_config": "/root/.config/meshcfg/config_db.json",
        }
        CONFIG_FILE.write_text(json.dumps(config, indent=2))
        print(f"Config exported to {CONFIG_FILE}")

    except asyncio.TimeoutError:
        print("\nProvisioning timed out (60s)")
    except RuntimeError as e:
        print(f"\nProvisioning failed: {e}")


def main() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as e:
        print(f"\nFATAL: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
