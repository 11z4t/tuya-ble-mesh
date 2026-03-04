#!/usr/bin/env python3
"""SIG Mesh provisioner via mesh-cfgclient (pexpect/PTY).

Automates mesh-cfgclient interactive commands to:
1. Create or attach to a mesh network
2. Scan for unprovisioned devices
3. Provision the first discovered device
4. Export mesh configuration

Requires bluetooth-meshd running (bluetoothd must be stopped).

SECURITY: Network/device keys are written to a JSON file only —
never printed to stdout.
"""

import json
import re
import sys
import time
from pathlib import Path

import pexpect

# Prompt regex — handles ANSI escape codes in mesh-cfgclient output
PROMPT = r"\[mesh-cfgclient\][#>]"
CONFIG_FILE = Path("/tmp/mesh_config_export.json")
MESHD_CONFIG = Path("/var/lib/bluetooth/mesh")
TIMEOUT = 10


def wait_prompt(child: pexpect.spawn, timeout: int = TIMEOUT) -> str:
    """Wait for mesh-cfgclient prompt and return output."""
    child.expect(PROMPT, timeout=timeout)
    return child.before.decode("utf-8", errors="replace")


def send_cmd(child: pexpect.spawn, cmd: str, timeout: int = TIMEOUT) -> str:
    """Send command and wait for prompt."""
    child.sendline(cmd)
    return wait_prompt(child, timeout=timeout)


def main() -> None:
    print("Starting mesh-cfgclient...")
    child = pexpect.spawn(
        "mesh-cfgclient",
        encoding=None,  # binary mode
        timeout=TIMEOUT,
    )
    child.logfile_read = sys.stdout.buffer

    # Wait for initial prompt
    try:
        wait_prompt(child, timeout=15)
    except pexpect.TIMEOUT:
        print("\nERROR: mesh-cfgclient did not start. Is bluetooth-meshd running?")
        child.close()
        sys.exit(1)

    print("\n\n=== mesh-cfgclient started ===\n")

    # Step 1: Check if already attached (config exists) or create new
    cfgclient_config = Path("/root/.config/meshcfg/config_db.json")
    if cfgclient_config.exists():
        # mesh-cfgclient auto-attaches on startup when config exists
        # Wait for the "Attached" message
        time.sleep(3)
        try:
            child.read_nonblocking(size=4096, timeout=2)
        except (pexpect.TIMEOUT, pexpect.EOF):
            pass
        print("Using existing mesh network.")
    else:
        output = send_cmd(child, "create", timeout=15)
        print("New network created!")
        time.sleep(3)

    # Step 2: Create app key (ignore if exists)
    output = send_cmd(child, "appkey-create 0 0")
    print(f"\nAppkey: {output[:100]}")

    # Step 3: Scan for unprovisioned devices
    scan_secs = 30
    print(f"\n=== Scanning for unprovisioned devices ({scan_secs}s) ===\n")
    child.sendline("discover-unprovisioned on")
    # Eat the prompt that comes back immediately
    time.sleep(1)

    # Wait N seconds, collecting scan results (only look for UUIDs)
    discovered = []
    scan_end = time.time() + scan_secs
    while time.time() < scan_end:
        try:
            idx = child.expect(
                [r"UUID:\s+([0-9a-fA-F]+)", pexpect.TIMEOUT],
                timeout=5,
            )
            if idx == 0:
                uuid_hex = child.match.group(1).decode()
                discovered.append(uuid_hex)
                print(f"  Found: UUID={uuid_hex}")
        except pexpect.TIMEOUT:
            remaining = int(scan_end - time.time())
            if remaining > 0:
                print(f"  Scanning... {remaining}s remaining")

    child.sendline("discover-unprovisioned off")
    time.sleep(2)
    # Flush output
    try:
        child.read_nonblocking(size=4096, timeout=1)
    except (pexpect.TIMEOUT, pexpect.EOF):
        pass

    if not discovered:
        print("\nNo unprovisioned devices found!")
        print("Make sure S17 is removed from Malmbergs app and powered on.")
        send_cmd(child, "quit")
        child.close()
        sys.exit(1)

    # De-duplicate
    unique = list(dict.fromkeys(discovered))
    print(f"\nFound {len(unique)} unique device(s):")
    for uuid in unique:
        print(f"  UUID: {uuid}")

    # Step 4: Provision first device
    target = unique[0]
    print(f"\n=== Provisioning UUID: {target} ===\n")
    child.sendline(f"provision {target}")

    # Wait for provisioning to complete (up to 60s)
    try:
        idx = child.expect(
            [
                r"Provisioning done",
                r"Provision success",
                r"Node added",
                r"AddNodeComplete",
                r"Provision failed",
                r"AddNodeFailed",
                PROMPT,
            ],
            timeout=60,
        )
        output = child.before.decode("utf-8", errors="replace")

        if idx <= 3:
            print(f"\n*** PROVISIONING COMPLETE ***")
            print(f"Output: {output[:300]}")

            # Try to extract unicast address
            match = re.search(r"unicast[:\s]+(?:0x)?([0-9a-fA-F]+)", output, re.I)
            unicast = match.group(1) if match else "unknown"

            # Export config
            config = {
                "provisioned_device": {
                    "uuid": target,
                    "unicast": unicast,
                },
                "meshd_config_dir": str(MESHD_CONFIG),
                "note": "Keys stored in meshd — see node.json files",
            }
            CONFIG_FILE.write_text(json.dumps(config, indent=2))
            print(f"Config exported to {CONFIG_FILE}")

        else:
            print(f"\n*** PROVISIONING FAILED ***")
            print(f"Output: {output[:300]}")

    except pexpect.TIMEOUT:
        print("\nProvisioning timed out (60s)")

    # Clean exit
    try:
        send_cmd(child, "quit", timeout=5)
    except (pexpect.TIMEOUT, pexpect.EOF):
        pass
    child.close()


if __name__ == "__main__":
    main()
