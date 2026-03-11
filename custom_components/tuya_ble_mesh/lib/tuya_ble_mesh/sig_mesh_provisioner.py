"""SIG Mesh PB-GATT Provisioner (Mesh Profile Section 5.4).

Implements provisioning via the PB-GATT bearer:
- Provisioning Service UUID: 0x1827
- Data In: 0x2ADB (write)
- Data Out: 0x2ADC (notify)

Full provisioning exchange:
  Invite → Capabilities → Start → PublicKey → Confirmation
  → Random → ProvisioningData → Complete

Uses FIPS P-256 (ECDH) with No OOB authentication (most common for Tuya
devices).  All provisioning-specific crypto derivations follow the
Mesh Profile Specification Section 5.4.2.

Rule S3: All byte parsing for provisioning PDUs is done here.
Rule S4: Crypto via sig_mesh_crypto (aes_cmac, k1, s1, mesh_aes_ccm_encrypt).
Rule S5: Async everywhere.
Rule S6: Type hints on every function.
Rule S7: ProvisioningError for all failures.

SECURITY: Generated key material is NEVER logged, printed, or included
in exception messages. Only lengths, PDU types, and counts are safe to log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from bleak import BleakClient, BleakScanner
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDH,
    SECP256R1,
    EllipticCurvePublicNumbers,
    generate_private_key,
)

from tuya_ble_mesh.exceptions import ProvisioningError
from tuya_ble_mesh.logging_context import MeshLogAdapter, mesh_operation
from tuya_ble_mesh.sig_mesh_crypto import aes_cmac, k1, mesh_aes_ccm_encrypt, s1

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# PB-GATT characteristics (Mesh Profile 7.1)
PROV_SERVICE = "00001827-0000-1000-8000-00805f9b34fb"
PROV_DATA_IN = "00002adb-0000-1000-8000-00805f9b34fb"
PROV_DATA_OUT = "00002adc-0000-1000-8000-00805f9b34fb"

# Provisioning PDU types (Mesh Profile 5.4.1)
_PROV_INVITE = 0x00
_PROV_CAPABILITIES = 0x01
_PROV_START = 0x02
_PROV_PUBLIC_KEY = 0x03
_PROV_CONFIRMATION = 0x05
_PROV_RANDOM = 0x06
_PROV_DATA = 0x07
_PROV_COMPLETE = 0x08
_PROV_FAILED = 0x09

# Proxy PDU SAR types (PB-GATT bearer segmentation)
_SAR_COMPLETE = 0x00
_SAR_FIRST = 0x01
_SAR_CONTINUATION = 0x02
_SAR_LAST = 0x03
_PROXY_TYPE_PROVISIONING = 0x03

# No OOB authentication value (16 zero bytes)
_NO_OOB_AUTH: bytes = b"\x00" * 16

# Attention duration for Invite PDU (seconds)
_ATTENTION_DURATION = 5

# Error code → name mapping for PROV_FAILED
_PROV_ERROR_NAMES: dict[int, str] = {
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


@dataclass(frozen=True)
class ProvisioningResult:
    """Result of a successful PB-GATT provisioning exchange.

    Contains all key material and provisioning metadata needed to
    communicate with the provisioned device.

    SECURITY: Key fields (dev_key, net_key, app_key) MUST NOT be logged,
    printed, or included in exception messages.
    """

    dev_key: bytes  # 16-byte device key (ECDH-derived)
    net_key: bytes  # 16-byte network key (provisioner-generated)
    app_key: bytes  # 16-byte application key (provisioner-generated)
    unicast_addr: int  # Assigned unicast address
    iv_index: int  # Mesh IV index
    num_elements: int  # Number of elements reported by device


def _wrap_provisioning_pdu(pdu: bytes, mtu: int) -> list[bytes]:
    """Wrap a provisioning PDU in Proxy PDU segments.

    Splits the PDU into chunks of at most ``mtu - 4`` bytes and wraps
    each with the SAR/type header.

    Args:
        pdu: Provisioning PDU bytes (type byte + params).
        mtu: BLE ATT MTU size.

    Returns:
        List of Proxy PDU bytes, each ready to write to PROV_DATA_IN.
    """
    # Each proxy PDU: 1 SAR/type byte + payload. ATT overhead = 3 bytes.
    max_chunk = max(1, mtu - 4)
    pdu_type = _PROXY_TYPE_PROVISIONING

    if len(pdu) <= max_chunk:
        return [bytes([(_SAR_COMPLETE << 6) | pdu_type]) + pdu]

    chunks: list[bytes] = []
    offset = 0
    is_first = True
    while offset < len(pdu):
        chunk = pdu[offset : offset + max_chunk]
        remaining = len(pdu) - offset - len(chunk)
        if is_first:
            sar = _SAR_FIRST
            is_first = False
        elif remaining == 0:
            sar = _SAR_LAST
        else:
            sar = _SAR_CONTINUATION
        chunks.append(bytes([(sar << 6) | pdu_type]) + chunk)
        offset += len(chunk)
    return chunks


class SIGMeshProvisioner:
    """PB-GATT provisioner for SIG Mesh devices.

    Implements the full provisioning protocol (Mesh Profile 5.4).
    Uses FIPS P-256 ECDH key exchange and No OOB authentication.

    Usage::

        net_key = os.urandom(16)
        app_key = os.urandom(16)
        provisioner = SIGMeshProvisioner(net_key, app_key, 0x00B0)
        result = await provisioner.provision("DC:23:4F:10:52:C4")
        # result.dev_key contains the device key
    """

    def __init__(
        self,
        net_key: bytes,
        app_key: bytes,
        unicast_addr: int,
        *,
        net_key_index: int = 0,
        iv_index: int = 0,
        flags: int = 0,
        ble_device_callback: Any | None = None,
        ble_connect_callback: Callable[[Any], Awaitable[BleakClient]] | None = None,
    ) -> None:
        """Initialize the provisioner.

        Args:
            net_key: 16-byte network key to provision into the device.
            app_key: 16-byte application key (saved in result for later use).
            unicast_addr: Unicast address to assign to the device.
            net_key_index: Network key index (0-4095, default 0).
            iv_index: Mesh IV Index (default 0).
            flags: Provisioning flags (bit 0=Key Refresh, bit 1=IV Update).
            ble_device_callback: Optional callback(address) → BLEDevice for
                HA Bluetooth proxy support. If None, uses BleakScanner.
            ble_connect_callback: Optional async callback(BLEDevice) →
                connected BleakClient. If provided, used instead of
                BleakClient.connect() directly. Pass a callback that uses
                bleak-retry-connector to avoid the "BleakClient.connect()
                called without bleak-retry-connector" warning in HA.

        Raises:
            ProvisioningError: If key lengths are invalid.
        """
        if len(net_key) != 16:
            msg = f"net_key must be 16 bytes, got {len(net_key)}"
            raise ProvisioningError(msg)
        if len(app_key) != 16:
            msg = f"app_key must be 16 bytes, got {len(app_key)}"
            raise ProvisioningError(msg)

        self._net_key = net_key
        self._app_key = app_key
        self._unicast_addr = unicast_addr
        self._net_key_index = net_key_index
        self._iv_index = iv_index
        self._flags = flags
        self._ble_device_callback = ble_device_callback
        self._ble_connect_callback = ble_connect_callback

        # Generate ECDH P-256 key pair
        self._private_key = generate_private_key(SECP256R1())
        pub = self._private_key.public_key()
        pub_numbers = pub.public_numbers()
        self._our_pub_key_bytes: bytes = pub_numbers.x.to_bytes(32, "big") + pub_numbers.y.to_bytes(
            32, "big"
        )

    async def provision(
        self,
        address: str,
        timeout: float = 15.0,
        max_retries: int = 5,
    ) -> ProvisioningResult:
        """Execute full PB-GATT provisioning with a device.

        Connects to the Provisioning Service (UUID 0x1827), performs the
        full exchange, and disconnects. After this call succeeds, the device
        will switch to the Proxy Service (UUID 0x1828) after a brief reboot.

        Args:
            address: BLE MAC address (e.g. ``"DC:23:4F:10:52:C4"``).
            timeout: Per-attempt BLE connection timeout in seconds.
            max_retries: Maximum BLE connection attempts.

        Returns:
            ProvisioningResult with derived device key and provisioning data.

        Raises:
            ProvisioningError: If provisioning fails at any step.
        """
        async with mesh_operation(address.upper(), "provision"):
            client = await self._connect(address, timeout, max_retries)
            try:
                return await self._run_exchange(client)
            finally:
                with contextlib.suppress(Exception):
                    await client.stop_notify(PROV_DATA_OUT)
                with contextlib.suppress(Exception):
                    await client.disconnect()
                _LOGGER.info("Provisioning session disconnected from %s", address.upper())
                # PLAT-506: Give BLE adapter time to release connection slot
                await asyncio.sleep(0.5)

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    async def _connect(
        self,
        address: str,
        timeout: float,
        max_retries: int,
    ) -> BleakClient:
        """Find and connect to the device's Provisioning Service.

        Args:
            address: BLE MAC address.
            timeout: Per-attempt timeout.
            max_retries: Maximum attempts.

        Returns:
            Connected BleakClient.

        Raises:
            ProvisioningError: If all attempts fail.
        """
        address = address.upper()
        last_exc: Exception | None = None
        scan_failures = 0
        connect_failures = 0
        out_of_slots_failures = 0

        for attempt in range(1, max_retries + 1):
            try:
                _LOGGER.info(
                    "PB-GATT connect to %s (attempt %d/%d, timeout=%.1fs)",
                    address,
                    attempt,
                    max_retries,
                    timeout,
                )

                # Step 1: Find device via BLE scan
                if self._ble_device_callback is not None:
                    device = self._ble_device_callback(address)
                else:
                    _LOGGER.debug("Scanning for device %s (timeout=%.1fs)", address, timeout)
                    device = await asyncio.wait_for(
                        BleakScanner.find_device_by_address(address, timeout=timeout),
                        timeout=timeout + 5.0,  # Add buffer for scan overhead
                    )

                if device is None:
                    scan_failures += 1
                    _LOGGER.warning(
                        "Device %s not found in BLE scan (attempt %d/%d)",
                        address,
                        attempt,
                        max_retries,
                    )
                    # Exponential backoff for scan retries
                    backoff = min(2.0 * (1.5 ** (attempt - 1)), 10.0)
                    await asyncio.sleep(backoff)
                    continue

                _LOGGER.debug("Device %s found, attempting connection...", address)

                # Step 2: Connect to device
                if self._ble_connect_callback is not None:
                    # Use caller-supplied connector (e.g. bleak-retry-connector for HA)
                    client = await asyncio.wait_for(
                        self._ble_connect_callback(device),
                        timeout=timeout + 10.0,
                    )
                else:
                    client = BleakClient(device, timeout=timeout)
                    await asyncio.wait_for(
                        client.connect(),
                        timeout=timeout,
                    )

                # Step 3: Verify connection and check services
                if not client.is_connected:
                    connect_failures += 1
                    msg = "BleakClient reported connected but is_connected=False"
                    raise ProvisioningError(msg)

                # Verify Provisioning Service is present
                try:
                    services = await asyncio.wait_for(
                        client.get_services(),
                        timeout=5.0,
                    )
                    if not any(str(s.uuid) == PROV_SERVICE for s in services.services.values()):
                        msg = f"Device {address} does not expose Provisioning Service (0x1827)"
                        raise ProvisioningError(msg)
                except TimeoutError:
                    _LOGGER.warning("Service enumeration timed out, continuing anyway")

                _LOGGER.info(
                    "PB-GATT connected to %s (MTU=%d, services=%d)",
                    address,
                    client.mtu_size,
                    len(services.services) if "services" in locals() else 0,
                )
                return client

            except ProvisioningError:
                raise
            except TimeoutError as exc:
                last_exc = exc
                connect_failures += 1
                _LOGGER.warning(
                    "Connect attempt %d/%d timed out after %.1fs",
                    attempt,
                    max_retries,
                    timeout,
                )
                # PLAT-506: Longer backoff to allow connection slot release
                backoff = min(3.0 * (1.5 ** (attempt - 1)), 15.0)
                await asyncio.sleep(backoff)
            except Exception as exc:
                last_exc = exc
                connect_failures += 1

                # PLAT-506: Special handling for out-of-slots errors
                exc_str = str(exc).lower()
                is_slot_error = (
                    "out of connection slots" in exc_str
                    or "bleakoutofconnectionslotserror" in exc_str
                    or "no backend with an available connection slot" in exc_str
                )

                if is_slot_error:
                    out_of_slots_failures += 1
                    _LOGGER.warning(
                        "Connect attempt %d/%d failed: BLE adapter out of connection slots. "
                        "Waiting for slots to be released...",
                        attempt,
                        max_retries,
                    )
                    # Longer backoff when slots are exhausted
                    backoff = min(5.0 * (1.5 ** (attempt - 1)), 20.0)
                    await asyncio.sleep(backoff)
                else:
                    _LOGGER.warning(
                        "Connect attempt %d/%d failed: %s: %s",
                        attempt,
                        max_retries,
                        type(exc).__name__,
                        str(exc),
                    )
                    # Standard backoff for other errors
                    backoff = min(3.0 * (1.5 ** (attempt - 1)), 15.0)
                    await asyncio.sleep(backoff)

        # Build detailed error message
        error_details = (
            f"scan_failures={scan_failures}, "
            f"connect_failures={connect_failures}, "
            f"out_of_slots={out_of_slots_failures}"
        )
        msg = (
            f"Failed to connect to {address} after {max_retries} attempts ({error_details}). "
        )
        if out_of_slots_failures > 0:
            msg += (
                "BLE adapter ran out of connection slots. "
                "Try: 1) Reduce number of concurrent BLE connections, "
                "2) Restart Bluetooth service, or 3) Use a different BLE adapter. "
            )
        else:
            msg += (
                "Check device is in range, not already provisioned, and advertising. "
            )
        msg += f"Last error: {type(last_exc).__name__ if last_exc else 'unknown'}"
        raise ProvisioningError(msg) from last_exc

    async def _run_exchange(self, client: BleakClient) -> ProvisioningResult:
        """Run the provisioning PDU exchange with a connected client.

        Implements Mesh Profile 5.4.2:
          1. Invite / Capabilities
          2. Start
          3. Public Key exchange + ECDH
          4. Confirmation exchange + verification
          5. Random exchange
          6. Provisioning Data (encrypted) + Complete

        Args:
            client: Connected BleakClient (must support PROV_DATA_OUT notify).

        Returns:
            ProvisioningResult.

        Raises:
            ProvisioningError: On any protocol or crypto failure.
        """
        rx_event: asyncio.Event = asyncio.Event()
        rx_buffer: bytearray = bytearray()
        rx_sar_buffer: bytearray = bytearray()

        def _on_notify(_sender: object, data: bytearray) -> None:
            """Handle Provisioning Data Out notifications with SAR reassembly."""
            if not data:
                return
            nonlocal rx_buffer, rx_sar_buffer
            sar = (data[0] >> 6) & 0x03
            payload = bytes(data[1:])
            if sar == _SAR_COMPLETE:
                rx_buffer = bytearray(payload)
                rx_event.set()
            elif sar == _SAR_FIRST:
                rx_sar_buffer = bytearray(payload)
            elif sar == _SAR_CONTINUATION:
                rx_sar_buffer.extend(payload)
            elif sar == _SAR_LAST:
                rx_sar_buffer.extend(payload)
                rx_buffer = rx_sar_buffer
                rx_sar_buffer = bytearray()
                rx_event.set()

        async def send_prov(pdu: bytes) -> None:
            segments = _wrap_provisioning_pdu(pdu, client.mtu_size)
            for seg in segments:
                await client.write_gatt_char(PROV_DATA_IN, seg, response=False)
                if len(segments) > 1:
                    await asyncio.sleep(0.05)

        async def recv_prov(recv_timeout: float = 10.0, step_name: str = "PDU") -> bytes:
            """Receive provisioning PDU with timeout and context."""
            rx_event.clear()
            try:
                await asyncio.wait_for(rx_event.wait(), timeout=recv_timeout)
                return bytes(rx_buffer)
            except TimeoutError as exc:
                msg = (
                    f"Timeout waiting for {step_name} (waited {recv_timeout:.1f}s). "
                    f"Device may be unresponsive or out of range. "
                    f"Try moving closer to the device or increasing timeout."
                )
                raise ProvisioningError(msg) from exc

        def check_pdu(pdu: bytes, expected_type: int, step_name: str = "PDU") -> None:
            """Validate PDU type with detailed error messages."""
            if not pdu:
                msg = f"Received empty PDU for {step_name}"
                raise ProvisioningError(msg)

            if pdu[0] == _PROV_FAILED:
                code = pdu[1] if len(pdu) > 1 else 0xFF
                name = _PROV_ERROR_NAMES.get(code, f"0x{code:02X}")
                msg = (
                    f"Device sent ProvisioningFailed during {step_name}: {name}. "
                    f"This may indicate: device already provisioned, OOB mismatch, "
                    f"insufficient resources, or unsupported configuration."
                )
                raise ProvisioningError(msg)

            if pdu[0] != expected_type:
                msg = (
                    f"Protocol error at {step_name}: expected PDU type 0x{expected_type:02X}, "
                    f"got 0x{pdu[0]:02X}. Device may not support standard SIG Mesh provisioning."
                )
                raise ProvisioningError(msg)

        await client.start_notify(PROV_DATA_OUT, _on_notify)

        # ---- Step 1: Invite ----
        _LOGGER.info("Provisioning: Invite (attention=%ds)", _ATTENTION_DURATION)
        invite_params = bytes([_ATTENTION_DURATION])
        await send_prov(bytes([_PROV_INVITE]) + invite_params)

        # ---- Step 2: Capabilities ----
        caps_pdu = await recv_prov(recv_timeout=10.0, step_name="Capabilities")
        check_pdu(caps_pdu, _PROV_CAPABILITIES, "Capabilities")
        device_caps = caps_pdu[1:]  # 11 bytes for ConfirmationInputs
        num_elements = caps_pdu[1] if len(caps_pdu) > 1 else 1
        _LOGGER.info("Provisioning: Capabilities received (elements=%d)", num_elements)

        # ---- Step 3: Start (No OOB) ----
        _LOGGER.info("Provisioning: Start")
        start_params = bytes(
            [
                0x00,  # Algorithm: FIPS P-256 (mandatory)
                0x00,  # Public Key: No OOB
                0x00,  # Authentication Method: No OOB
                0x00,  # Authentication Action: 0
                0x00,  # Authentication Size: 0
            ]
        )
        await send_prov(bytes([_PROV_START]) + start_params)
        await asyncio.sleep(0.5)

        # ---- Step 4: Public Key exchange ----
        _LOGGER.info("Provisioning: PublicKey exchange (%d bytes)", len(self._our_pub_key_bytes))
        await send_prov(bytes([_PROV_PUBLIC_KEY]) + self._our_pub_key_bytes)

        dev_pub_pdu = await recv_prov(recv_timeout=15.0, step_name="PublicKey")
        check_pdu(dev_pub_pdu, _PROV_PUBLIC_KEY, "PublicKey")
        device_pub_key_bytes = dev_pub_pdu[1:]
        if len(device_pub_key_bytes) != 64:
            msg = (
                f"Invalid device public key length: expected 64 bytes, "
                f"got {len(device_pub_key_bytes)}. "
                f"Device may not support FIPS P-256 ECDH (required for SIG Mesh)."
            )
            raise ProvisioningError(msg)

        # ECDH shared secret computation (Mesh Profile 5.4.2.2)
        try:
            dev_x = int.from_bytes(device_pub_key_bytes[:32], "big")
            dev_y = int.from_bytes(device_pub_key_bytes[32:], "big")
            dev_pub = EllipticCurvePublicNumbers(dev_x, dev_y, SECP256R1()).public_key()
            shared_secret = self._private_key.exchange(ECDH(), dev_pub)
        except ValueError as exc:
            msg = (
                f"Invalid device public key: point not on curve. "
                f"Device sent malformed ECDH public key: {exc}"
            )
            raise ProvisioningError(msg) from exc
        except Exception as exc:
            msg = f"ECDH key exchange failed: {type(exc).__name__}: {exc}"
            raise ProvisioningError(msg) from exc

        _LOGGER.info("Provisioning: ECDH shared secret (%d bytes) [REDACTED]", len(shared_secret))

        # ---- Step 5: Confirmation exchange ----
        _LOGGER.info("Provisioning: Confirmation exchange")
        # ConfirmationInputs = Invite(1B) || Caps(11B) || Start(5B)
        #                    || PubKeyProv(64B) || PubKeyDev(64B)
        conf_inputs = (
            invite_params
            + device_caps
            + start_params
            + self._our_pub_key_bytes
            + device_pub_key_bytes
        )
        conf_salt = s1(conf_inputs)
        conf_key = k1(shared_secret, conf_salt, b"prck")

        random_provisioner = os.urandom(16)
        conf_provisioner = aes_cmac(conf_key, random_provisioner + _NO_OOB_AUTH)
        await send_prov(bytes([_PROV_CONFIRMATION]) + conf_provisioner)

        dev_conf_pdu = await recv_prov(recv_timeout=10.0, step_name="Confirmation")
        check_pdu(dev_conf_pdu, _PROV_CONFIRMATION, "Confirmation")
        dev_confirmation = dev_conf_pdu[1:]
        _LOGGER.info("Provisioning: Device confirmation received (%d bytes)", len(dev_confirmation))

        # ---- Step 6: Random exchange ----
        _LOGGER.info("Provisioning: Random exchange")
        await send_prov(bytes([_PROV_RANDOM]) + random_provisioner)

        dev_random_pdu = await recv_prov(recv_timeout=10.0, step_name="Random")
        check_pdu(dev_random_pdu, _PROV_RANDOM, "Random")
        random_device = dev_random_pdu[1:]

        # Verify device confirmation (Mesh Profile 5.4.2.4)
        expected_conf = aes_cmac(conf_key, random_device + _NO_OOB_AUTH)
        if expected_conf != dev_confirmation:
            msg = (
                "Device confirmation mismatch (authentication failed). "
                "This indicates a crypto error or OOB authentication mismatch. "
                "For devices requiring OOB, this integration currently only supports No OOB mode."
            )
            raise ProvisioningError(msg)
        _LOGGER.info("Provisioning: Device confirmation verified OK")

        # Derive session keys (Mesh Profile 5.4.2.5)
        prov_salt = s1(conf_salt + random_provisioner + random_device)
        session_key = k1(shared_secret, prov_salt, b"prsk")
        session_nonce = k1(shared_secret, prov_salt, b"prsn")[3:]  # last 13 bytes
        dev_key = k1(shared_secret, prov_salt, b"prdk")

        # ---- Step 7: Provisioning Data ----
        _LOGGER.info("Provisioning: Sending encrypted provisioning data")
        # Format: NetKey(16) || NetKeyIndex(2 BE) || Flags(1)
        #       || IVIndex(4 BE) || UnicastAddr(2 BE) = 25 bytes
        prov_data = (
            self._net_key
            + struct.pack(">H", self._net_key_index)
            + bytes([self._flags])
            + struct.pack(">I", self._iv_index)
            + struct.pack(">H", self._unicast_addr)
        )
        # Encrypt with AES-CCM, 8-byte MIC (Mesh Profile 5.4.2.6)
        encrypted_data = mesh_aes_ccm_encrypt(session_key, session_nonce, prov_data, mic_len=8)
        await send_prov(bytes([_PROV_DATA]) + encrypted_data)

        # Wait for Complete or Failed
        result_pdu = await recv_prov(recv_timeout=15.0, step_name="Complete/Failed")
        if result_pdu[0] == _PROV_FAILED:
            code = result_pdu[1] if len(result_pdu) > 1 else 0xFF
            name = _PROV_ERROR_NAMES.get(code, f"0x{code:02X}")
            msg = (
                f"Device rejected provisioning data: {name}. "
                f"Common causes: device already provisioned, address conflict, "
                f"insufficient storage, or invalid IV index."
            )
            raise ProvisioningError(msg)
        if result_pdu[0] != _PROV_COMPLETE:
            msg = (
                f"Expected ProvisioningComplete (0x08), got 0x{result_pdu[0]:02X}. "
                f"Device sent unexpected response after receiving provisioning data."
            )
            raise ProvisioningError(msg)

        _LOGGER.info(
            "Provisioning COMPLETE: unicast=0x%04X elements=%d ivIdx=%d",
            self._unicast_addr,
            num_elements,
            self._iv_index,
        )
        return ProvisioningResult(
            dev_key=dev_key,
            net_key=self._net_key,
            app_key=self._app_key,
            unicast_addr=self._unicast_addr,
            iv_index=self._iv_index,
            num_elements=num_elements,
        )
