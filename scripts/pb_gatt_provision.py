#!/usr/bin/env python3
"""PB-GATT provisioner for SIG Mesh devices.

Provisions an unprovisioned SIG Mesh device via PB-GATT bearer (GATT
characteristics 2ADB/2ADC on service 1827). Works with bluetoothd + bleak.

Implements Bluetooth Mesh Profile Section 5.2-5.4:
  1. Invite → Capabilities exchange
  2. Start (algorithm, OOB selection)
  3. ECDH P-256 public key exchange
  4. Confirmation + Random exchange
  5. Encrypted provisioning data (NetKey, unicast address, etc.)

After provisioning, the device switches from Provisioning Service (1827)
to Proxy Service (1828) and accepts mesh commands.

SECURITY: Generated keys are saved to a JSON file only — never printed.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import os
import struct
import sys
from pathlib import Path

from bleak import BleakClient, BleakScanner
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDH,
    SECP256R1,
    generate_private_key,
)
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from tuya_ble_mesh.sig_mesh_crypto import aes_cmac, k2, k3, k4, s1

_LOGGER = logging.getLogger(__name__)

# PB-GATT characteristics
PROV_SERVICE = "00001827-0000-1000-8000-00805f9b34fb"
PROV_DATA_IN = "00002adb-0000-1000-8000-00805f9b34fb"
PROV_DATA_OUT = "00002adc-0000-1000-8000-00805f9b34fb"

# Provisioning PDU types (Mesh Profile 5.4.1)
PROV_INVITE = 0x00
PROV_CAPABILITIES = 0x01
PROV_START = 0x02
PROV_PUBLIC_KEY = 0x03
PROV_INPUT_COMPLETE = 0x04
PROV_CONFIRMATION = 0x05
PROV_RANDOM = 0x06
PROV_DATA = 0x07
PROV_COMPLETE = 0x08
PROV_FAILED = 0x09

# Proxy PDU SAR types
PROXY_SAR_COMPLETE = 0x00
PROXY_SAR_FIRST = 0x01
PROXY_SAR_CONTINUATION = 0x02
PROXY_SAR_LAST = 0x03
PROXY_TYPE_PROVISIONING = 0x03

# Output file
OUTPUT_FILE = Path("/tmp/mesh_keys.json")


def _make_proxy_pdu(pdu_type: int, payload: bytes) -> list[bytes]:
    """Wrap provisioning PDU in Proxy PDU(s) with SAR segmentation.

    MTU is typically 23 bytes → max 22 bytes payload per PDU.
    First byte: (SAR << 6) | pdu_type
    """
    max_payload = 19  # Conservative for 23-byte MTU
    if len(payload) <= max_payload:
        return [bytes([(PROXY_SAR_COMPLETE << 6) | pdu_type]) + payload]

    segments = []
    offset = 0
    first = True
    while offset < len(payload):
        remaining = len(payload) - offset
        if first:
            chunk = payload[offset : offset + max_payload]
            sar = PROXY_SAR_FIRST
            first = False
        elif remaining <= max_payload:
            chunk = payload[offset:]
            sar = PROXY_SAR_LAST
        else:
            chunk = payload[offset : offset + max_payload]
            sar = PROXY_SAR_CONTINUATION
        segments.append(bytes([(sar << 6) | pdu_type]) + chunk)
        offset += len(chunk)
    return segments


def _prov_pdu(pdu_type: int, params: bytes = b"") -> bytes:
    """Build a provisioning PDU (type byte + params)."""
    return bytes([pdu_type]) + params


class PBGATTProvisioner:
    """PB-GATT provisioner for a single device."""

    def __init__(
        self,
        net_key: bytes,
        unicast_addr: int,
        *,
        net_key_index: int = 0,
        iv_index: int = 0,
        flags: int = 0,
    ) -> None:
        self.net_key = net_key
        self.unicast_addr = unicast_addr
        self.net_key_index = net_key_index
        self.iv_index = iv_index
        self.flags = flags

        self._client: BleakClient | None = None
        self._rx_event = asyncio.Event()
        self._rx_buffer = bytearray()
        self._rx_sar_buffer = bytearray()
        self._rx_complete = False

        # ECDH
        self._private_key = generate_private_key(SECP256R1())
        pub = self._private_key.public_key()
        pub_numbers = pub.public_numbers()
        self._public_key_bytes = pub_numbers.x.to_bytes(32, "big") + pub_numbers.y.to_bytes(
            32, "big"
        )

        # Provisioning state
        self._device_uuid: bytes = b""
        self._device_capabilities: bytes = b""
        self._device_public_key: bytes = b""
        self._confirmation_key: bytes = b""
        self._prov_salt: bytes = b""
        self._random_provisioner: bytes = b""
        self._random_device: bytes = b""
        self._auth_value: bytes = b"\x00" * 16  # No OOB
        self._dev_key: bytes = b""

    def _on_notify(self, _sender: object, data: bytearray) -> None:
        """Handle Provisioning Data Out notifications."""
        if not data:
            return

        sar = (data[0] >> 6) & 0x03
        _pdu_type = data[0] & 0x3F
        payload = bytes(data[1:])

        if sar == PROXY_SAR_COMPLETE:
            self._rx_buffer = bytearray(payload)
            self._rx_complete = True
            self._rx_event.set()
        elif sar == PROXY_SAR_FIRST:
            self._rx_sar_buffer = bytearray(payload)
            self._rx_complete = False
        elif sar == PROXY_SAR_CONTINUATION:
            self._rx_sar_buffer.extend(payload)
        elif sar == PROXY_SAR_LAST:
            self._rx_sar_buffer.extend(payload)
            self._rx_buffer = self._rx_sar_buffer
            self._rx_sar_buffer = bytearray()
            self._rx_complete = True
            self._rx_event.set()

    async def _send_prov(self, pdu: bytes) -> None:
        """Send a provisioning PDU via PB-GATT."""
        if self._client is None:
            raise RuntimeError("Not connected")

        segments = _make_proxy_pdu(PROXY_TYPE_PROVISIONING, pdu)
        for seg in segments:
            await self._client.write_gatt_char(PROV_DATA_IN, seg, response=False)
            if len(segments) > 1:
                await asyncio.sleep(0.05)

    async def _recv_prov(self, timeout: float = 10.0) -> bytes:
        """Receive a provisioning PDU response."""
        self._rx_event.clear()
        self._rx_complete = False
        await asyncio.wait_for(self._rx_event.wait(), timeout=timeout)
        return bytes(self._rx_buffer)

    async def provision(self, address: str, timeout: float = 15.0) -> dict:
        """Execute full PB-GATT provisioning.

        Returns dict with keys: net_key, dev_key, unicast, iv_index, etc.
        """
        print(f"[1] Connecting to {address}...")
        for attempt in range(1, 6):
            try:
                print(f"    [{attempt}/5] Scanning...")
                dev = await BleakScanner.find_device_by_address(address, timeout=timeout)
                if dev is None:
                    print(f"    [{attempt}/5] Not found in scan")
                    await asyncio.sleep(3)
                    continue
                print(f"    [{attempt}/5] Found, connecting...")
                self._client = BleakClient(dev, timeout=timeout)
                await self._client.connect()
                print(f"    Connected (MTU={self._client.mtu_size})")
                break
            except Exception as exc:
                print(f"    [{attempt}/5] {type(exc).__name__}: {exc}")
                self._client = None
                if attempt == 5:
                    raise RuntimeError("Failed to connect after 5 attempts") from exc
                await asyncio.sleep(3)

        try:
            # Subscribe to notifications
            await self._client.start_notify(PROV_DATA_OUT, self._on_notify)

            # Step 1: Invite
            print("[2] Sending Provisioning Invite...")
            attention_duration = 5  # seconds
            await self._send_prov(_prov_pdu(PROV_INVITE, bytes([attention_duration])))

            # Step 2: Receive Capabilities
            caps = await self._recv_prov(timeout=10)
            if caps[0] != PROV_CAPABILITIES:
                raise RuntimeError(f"Expected Capabilities, got 0x{caps[0]:02X}")
            self._device_capabilities = caps[1:]
            num_elements = caps[1]
            algorithms = (caps[2] << 8) | caps[3]
            pub_key_type = caps[4]
            oob_types = caps[5]
            output_oob_size = caps[6]
            output_oob_action = (caps[7] << 8) | caps[8]
            _input_oob_size = caps[9]
            _input_oob_action = (caps[10] << 8) | caps[11]
            print(f"    Capabilities: {num_elements} elements, algorithms=0x{algorithms:04X}")
            out_oob = f"{output_oob_size}/{output_oob_action:04X}"
            print(f"    PubKey={pub_key_type} OOB={oob_types} OutOOB={out_oob}")

            # Step 3: Start
            print("[3] Sending Provisioning Start...")
            # Algorithm 0 = FIPS P-256 (mandatory)
            # No OOB public key, No OOB auth
            start_params = bytes(
                [
                    0x00,  # Algorithm: FIPS P-256
                    0x00,  # Public Key: No OOB
                    0x00,  # Authentication Method: No OOB
                    0x00,  # Authentication Action: 0
                    0x00,  # Authentication Size: 0
                ]
            )
            await self._send_prov(_prov_pdu(PROV_START, start_params))
            await asyncio.sleep(0.5)

            # Step 4: Exchange public keys
            print("[4] Exchanging ECDH public keys...")
            await self._send_prov(_prov_pdu(PROV_PUBLIC_KEY, self._public_key_bytes))

            dev_pub_pdu = await self._recv_prov(timeout=15)
            if dev_pub_pdu[0] == PROV_FAILED:
                raise RuntimeError(f"Device failed: error code 0x{dev_pub_pdu[1]:02X}")
            if dev_pub_pdu[0] != PROV_PUBLIC_KEY:
                raise RuntimeError(f"Expected PublicKey, got 0x{dev_pub_pdu[0]:02X}")
            self._device_public_key = dev_pub_pdu[1:]
            if len(self._device_public_key) != 64:
                raise RuntimeError(f"Bad device public key length: {len(self._device_public_key)}")
            print(f"    Device public key received ({len(self._device_public_key)} bytes)")

            # Compute ECDH shared secret
            dev_x = int.from_bytes(self._device_public_key[:32], "big")
            dev_y = int.from_bytes(self._device_public_key[32:], "big")
            from cryptography.hazmat.primitives.asymmetric.ec import (
                EllipticCurvePublicNumbers,
            )

            dev_pub = EllipticCurvePublicNumbers(dev_x, dev_y, SECP256R1()).public_key()
            shared_secret = self._private_key.exchange(ECDH(), dev_pub)
            print(f"    ECDH shared secret computed ({len(shared_secret)} bytes)")

            # Step 5: Compute confirmation
            print("[5] Computing confirmation values...")

            # ConfirmationInputs = Invite || Capabilities || Start || PubKeyProv || PubKeyDev
            # (Mesh Profile Section 5.4.2.4)
            invite_pdu = bytes([attention_duration])
            conf_inputs = (
                invite_pdu
                + self._device_capabilities
                + start_params
                + self._public_key_bytes
                + self._device_public_key
            )

            # ConfirmationSalt = s1(ConfirmationInputs)
            conf_salt = s1(conf_inputs)

            # ConfirmationKey = k1(ECDHSecret, ConfirmationSalt, "prck")
            from tuya_ble_mesh.sig_mesh_crypto import k1

            conf_key = k1(shared_secret, conf_salt, b"prck")
            self._confirmation_key = conf_key

            # Generate provisioner random
            self._random_provisioner = os.urandom(16)

            # Confirmation = AES-CMAC(ConfirmationKey, Random || AuthValue)
            conf_provisioner = aes_cmac(conf_key, self._random_provisioner + self._auth_value)

            # Send confirmation
            await self._send_prov(_prov_pdu(PROV_CONFIRMATION, conf_provisioner))

            # Receive device confirmation
            dev_conf_pdu = await self._recv_prov(timeout=10)
            if dev_conf_pdu[0] == PROV_FAILED:
                raise RuntimeError(f"Device failed at confirmation: 0x{dev_conf_pdu[1]:02X}")
            if dev_conf_pdu[0] != PROV_CONFIRMATION:
                raise RuntimeError(f"Expected Confirmation, got 0x{dev_conf_pdu[0]:02X}")
            dev_confirmation = dev_conf_pdu[1:]
            print(f"    Device confirmation received ({len(dev_confirmation)} bytes)")

            # Step 6: Exchange random values
            print("[6] Exchanging random values...")
            await self._send_prov(_prov_pdu(PROV_RANDOM, self._random_provisioner))

            dev_random_pdu = await self._recv_prov(timeout=10)
            if dev_random_pdu[0] == PROV_FAILED:
                raise RuntimeError(f"Device failed at random: 0x{dev_random_pdu[1]:02X}")
            if dev_random_pdu[0] != PROV_RANDOM:
                raise RuntimeError(f"Expected Random, got 0x{dev_random_pdu[0]:02X}")
            self._random_device = dev_random_pdu[1:]

            # Verify device confirmation
            expected_conf = aes_cmac(conf_key, self._random_device + self._auth_value)
            if expected_conf != dev_confirmation:
                raise RuntimeError("Device confirmation mismatch! Auth may be wrong.")
            print("    Device confirmation verified OK")

            # Step 7: Send provisioning data
            print("[7] Sending provisioning data...")

            # ProvisioningSalt = s1(ConfirmationSalt || RandomProvisioner || RandomDevice)
            prov_salt = s1(conf_salt + self._random_provisioner + self._random_device)
            self._prov_salt = prov_salt

            # SessionKey = k1(ECDHSecret, ProvisioningSalt, "prsk")
            session_key = k1(shared_secret, prov_salt, b"prsk")

            # SessionNonce = k1(ECDHSecret, ProvisioningSalt, "prsn")[3:] (13 bytes)
            session_nonce = k1(shared_secret, prov_salt, b"prsn")[3:]

            # DeviceKey = k1(ECDHSecret, ProvisioningSalt, "prdk")
            self._dev_key = k1(shared_secret, prov_salt, b"prdk")

            # Provisioning Data (25 bytes):
            # NetKey(16) || NetKeyIndex(2) || Flags(1) || IVIndex(4) || UnicastAddr(2)
            prov_data = (
                self.net_key
                + struct.pack(">H", self.net_key_index)
                + bytes([self.flags])
                + struct.pack(">I", self.iv_index)
                + struct.pack(">H", self.unicast_addr)
            )

            # Encrypt with AES-CCM (8-byte MIC)
            aesccm = AESCCM(session_key, tag_length=8)
            encrypted_data = aesccm.encrypt(session_nonce, prov_data, b"")

            await self._send_prov(_prov_pdu(PROV_DATA, encrypted_data))

            # Wait for Complete or Failed
            result_pdu = await self._recv_prov(timeout=15)
            if result_pdu[0] == PROV_COMPLETE:
                print("\n=== PROVISIONING COMPLETE ===")
                print(f"    Unicast address: 0x{self.unicast_addr:04X}")
                print(f"    Elements: {num_elements}")
                print(f"    IV Index: {self.iv_index}")

                result = {
                    "mac": address,
                    "unicast": f"0x{self.unicast_addr:04X}",
                    "elements": num_elements,
                    "iv_index": self.iv_index,
                    "net_key": self.net_key.hex(),
                    "dev_key": self._dev_key.hex(),
                    "net_key_index": self.net_key_index,
                }
                return result

            elif result_pdu[0] == PROV_FAILED:
                error_codes = {
                    0x00: "Prohibited",
                    0x01: "InvalidPDU",
                    0x02: "InvalidFormat",
                    0x03: "UnexpectedPDU",
                    0x04: "ConfirmationFailed",
                    0x05: "OutOfResources",
                    0x06: "DecryptionFailed",
                    0x07: "UnexpectedError",
                    0x08: "CannotAssignAddresses",
                }
                code = result_pdu[1] if len(result_pdu) > 1 else 0xFF
                name = error_codes.get(code, f"Unknown(0x{code:02X})")
                raise RuntimeError(f"Provisioning failed: {name}")
            else:
                raise RuntimeError(f"Unexpected response: 0x{result_pdu[0]:02X}")

        finally:
            with contextlib.suppress(Exception):
                await self._client.stop_notify(PROV_DATA_OUT)
            with contextlib.suppress(Exception):
                await self._client.disconnect()
            print("    Disconnected")


async def run(args: argparse.Namespace) -> None:
    """Main provisioning flow."""
    # Generate or load network key
    if args.net_key:
        net_key = bytes.fromhex(args.net_key)
    elif OUTPUT_FILE.exists():
        existing = json.loads(OUTPUT_FILE.read_text())
        net_key = bytes.fromhex(existing.get("net_key", ""))
        if net_key:
            print(f"Using existing NetKey from {OUTPUT_FILE}")
        else:
            net_key = os.urandom(16)
            print("Generated new NetKey")
    else:
        net_key = os.urandom(16)
        print("Generated new NetKey")

    provisioner = PBGATTProvisioner(
        net_key=net_key,
        unicast_addr=args.unicast,
        iv_index=args.iv_index,
    )

    result = await provisioner.provision(args.mac, timeout=args.timeout)

    # Generate app key
    app_key = os.urandom(16)
    result["app_key"] = app_key.hex()

    # Derive network keys for reference
    nid, _enc_key, _priv_key = k2(net_key, b"\x00")
    _network_id = k3(net_key)
    aid = k4(app_key)
    result["nid"] = f"0x{nid:02X}"
    result["aid"] = f"0x{aid:02X}"

    # Save to file
    OUTPUT_FILE.write_text(json.dumps(result, indent=2))
    print(f"\nKeys saved to {OUTPUT_FILE}")
    print(f"  NID=0x{nid:02X} AID=0x{aid:02X}")
    print("\nNext steps:")
    print("  1. Power cycle the device (it will switch to Proxy Service)")
    print(f"  2. Run: python scripts/mesh_proxy_cmd.py setup --mac {args.mac}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="PB-GATT provisioner for SIG Mesh devices",
    )
    parser.add_argument(
        "--mac",
        default="DC:23:4F:10:52:C4",
        help="BLE MAC address (default: DC:23:4F:10:52:C4)",
    )
    parser.add_argument(
        "--unicast",
        type=lambda x: int(x, 16),
        default=0x00B0,
        help="Unicast address to assign (hex, default: 00B0)",
    )
    parser.add_argument(
        "--iv-index",
        type=int,
        default=0,
        help="IV Index (default: 0)",
    )
    parser.add_argument(
        "--net-key",
        help="Network key in hex (default: generate random)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="BLE connection timeout (default: 15s)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as exc:
        print(f"\nFATAL: {type(exc).__name__}: {exc}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
