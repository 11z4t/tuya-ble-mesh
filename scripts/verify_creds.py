#!/usr/bin/env python3
"""Live credential verification — pair with device and verify device proof.

Connects to the device, sends pair request, captures the device's response,
and verifies the device proof against many credential sets.

This tells us DEFINITIVELY whether our credentials match the device's.
"""

import asyncio
import contextlib
import pathlib
import sys

from bleak import BleakClient, BleakScanner

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

from tuya_ble_mesh.const import TARGET_DEVICE_MAC, TELINK_CHAR_PAIRING, TELINK_CHAR_STATUS
from tuya_ble_mesh.crypto import (
    generate_session_random,
    make_pair_packet,
    make_session_key,
    telink_aes_encrypt,
)


def _hex(data: bytes) -> str:
    return data.hex(" ")


def _xor_creds(name: str, password: str) -> bytes:
    """XOR name and password, both padded to 16 bytes."""
    n = name.encode("utf-8").ljust(16, b"\x00")[:16]
    p = password.encode("utf-8").ljust(16, b"\x00")[:16]
    return bytes(a ^ b for a, b in zip(n, p))


def _compute_device_proof(device_random: bytes, nxp: bytes) -> bytes:
    """Compute expected device proof for given credentials."""
    dr_padded = device_random + b"\x00" * 8
    return telink_aes_encrypt(dr_padded, nxp)[:8]


async def main() -> None:
    mac = TARGET_DEVICE_MAC
    print(f"\n{'=' * 60}")
    print(f"  Credential Verification — Live Test")
    print(f"  Target: {mac}")
    print(f"{'=' * 60}\n")

    # Try with factory defaults first
    mesh_name = b"out_of_mesh"
    mesh_pass = b"123456"

    # Connect
    print("[1] Connecting...")
    for attempt in range(1, 6):
        try:
            dev = await BleakScanner.find_device_by_address(mac, timeout=15)
            if dev is None:
                print(f"  Not found ({attempt})")
                continue
            client = BleakClient(dev, timeout=15)
            await client.connect()
            print(f"  Connected (attempt {attempt})")
            break
        except Exception as e:
            print(f"  {attempt}: {type(e).__name__}")
            with contextlib.suppress(Exception):
                p = await asyncio.create_subprocess_exec(
                    "bluetoothctl", "remove", mac,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(p.wait(), timeout=5)
            await asyncio.sleep(3)
    else:
        print("  FAILED to connect")
        return

    try:
        # Step 2: Send pair request
        print("\n[2] Sending pair request...")
        client_random = generate_session_random()
        pair_pkt = make_pair_packet(mesh_name, mesh_pass, client_random)

        print(f"  Client random: {_hex(client_random)}")
        print(f"  Pair packet:   {_hex(pair_pkt)}")

        await client.write_gatt_char(TELINK_CHAR_PAIRING, pair_pkt, response=True)
        print("  Written OK")

        # Read response
        resp = bytes(await client.read_gatt_char(TELINK_CHAR_PAIRING))
        print(f"  Response: {_hex(resp)}")

        if resp[0] == 0x0E:
            print("  AUTH FAILED (0x0E)")
            return
        if resp[0] != 0x0D:
            print(f"  UNEXPECTED opcode: 0x{resp[0]:02X}")
            return

        print("  Device accepted (0x0D)")

        device_random = resp[1:9]
        device_proof_raw = resp[9:17]  # may be less than 8 bytes
        print(f"  Device random: {_hex(device_random)}")
        print(f"  Device proof:  {_hex(device_proof_raw)}")
        print(f"  Full response: {_hex(resp)}")

        # Step 3: Verify device proof against many credentials
        print(f"\n[3] Verifying device proof against credentials...")

        credentials = [
            ("out_of_mesh", "123456"),
            ("out_of_mesh", "123"),
            ("out_of_mesh", "1234"),
            ("out_of_mesh", "12345"),
            ("out_of_mesh", "12345678"),
            ("out_of_mesh", "000000"),
            ("out_of_mesh", "password"),
            ("out_of_mesh", ""),
            ("telink_mesh1", "123"),
            ("telink_mesh1", "123456"),
            ("unpaired", "1234"),
            ("unpaired", "123456"),
            ("Malmbergs", "123456"),
            ("Malmbergs", "123"),
            ("malmbergs", "123456"),
            ("malmbergs", "123"),
            ("LMSH", "123456"),
            ("mesh", "123456"),
            ("light", "123456"),
            ("BTSmart", "123456"),
            ("9952126", "123456"),
            ("out_of_mesh", "654321"),
            ("out_of_mesh", "111111"),
            ("out_of_mesh", "999999"),
        ]

        match_found = False
        for name, pwd in credentials:
            nxp = _xor_creds(name, pwd)
            expected = _compute_device_proof(device_random, nxp)
            match = expected == device_proof_raw
            if match:
                print(f"  *** MATCH: '{name}' / '{pwd}' ***")
                match_found = True

                # Derive session key
                sk = make_session_key(
                    name.encode("utf-8"),
                    pwd.encode("utf-8"),
                    client_random,
                    device_random,
                )
                print(f"  Session key length: {len(sk)}")

                # Also verify: send a status enable and try a command
                print("\n[4] Testing with matched credentials...")
                await client.write_gatt_char(TELINK_CHAR_STATUS, b"\x01", response=True)
                print("  Status enable written")
                await asyncio.sleep(1)

                # Build and send power OFF command
                import os
                import struct
                from tuya_ble_mesh.crypto import crypt_payload, make_checksum

                seq = os.urandom(3)
                a = bytearray.fromhex(mac.replace(":", ""))
                a.reverse()
                nonce = bytes(a[0:4] + b"\x01" + seq)
                dest = struct.pack("<H", 0)
                vendor_id = bytes([0x11, 0x02])  # retsimx vendor
                payload = (dest + b"\xd0" + vendor_id + b"\x01").ljust(15, b"\x00")
                check = make_checksum(sk, nonce, payload)
                enc = crypt_payload(sk, nonce, payload)
                cmd = seq + check[0:2] + enc
                print(f"  Power ON cmd: {_hex(cmd)}")

                from tuya_ble_mesh.const import TELINK_CHAR_COMMAND
                await client.write_gatt_char(TELINK_CHAR_COMMAND, cmd, response=False)
                print("  Sent (Write Command, no response)")
                await asyncio.sleep(3)
                print("  Reagerade lampan?")
                break

        if not match_found:
            print("\n  NO MATCH in standard credential set!")
            print("  Trying numeric brute-force 1-4 digits...")
            import itertools
            for length in range(1, 5):
                for combo in itertools.product("0123456789", repeat=length):
                    pwd = "".join(combo)
                    nxp = _xor_creds("out_of_mesh", pwd)
                    expected = _compute_device_proof(device_random, nxp)
                    if expected == device_proof_raw:
                        print(f"  *** MATCH: 'out_of_mesh' / '{pwd}' ***")
                        match_found = True
                        break
                if match_found:
                    break
                print(f"    {length} digits: no match")

        if not match_found:
            print("\n  CREDENTIALS UNKNOWN — device uses non-standard defaults")
            print(f"  Device random: {_hex(device_random)}")
            print(f"  Device proof:  {_hex(device_proof_raw)}")
            print("  Save these for offline analysis.")

            # Also try: verify with the PCAP capture data
            print("\n  Comparing with PCAP capture proofs...")
            pcap_cr = bytes.fromhex("0001020304050607")
            pcap_cp = bytes.fromhex("f5131ca084b13f9f")
            pcap_dr = bytes.fromhex("4d680a60df0a09ea")
            pcap_dp = bytes.fromhex("e2dd5b6840e7fc27")

            # If the device always responds with the same computation,
            # the proofs should match for the same credentials
            print(f"  PCAP client random: {_hex(pcap_cr)}")
            print(f"  PCAP client proof:  {_hex(pcap_cp)}")
            print(f"  PCAP device random: {_hex(pcap_dr)}")
            print(f"  PCAP device proof:  {_hex(pcap_dp)}")

    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()
        print("\n  Disconnected.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
