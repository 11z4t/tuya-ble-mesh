"""Provisioning PDU exchange for SIG Mesh provisioner (PB-GATT).

Implements the full provisioning protocol exchange (Mesh Profile 5.4.2):
  1. Invite / Capabilities
  2. Start
  3. Public Key exchange + ECDH
  4. Confirmation exchange + verification
  5. Random exchange
  6. Provisioning Data (encrypted) + Complete

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
import logging
import os
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDH,
    SECP256R1,
    EllipticCurvePublicNumbers,
)

from tuya_ble_mesh.const import (
    PROVISIONING_CAPABILITIES_TIMEOUT,
    PROVISIONING_COMPLETE_TIMEOUT,
    PROVISIONING_CONFIRMATION_TIMEOUT,
    PROVISIONING_PAIR_TIMEOUT,
    PROVISIONING_PUBLIC_KEY_TIMEOUT,
    PROVISIONING_RANDOM_TIMEOUT,
    PROVISIONING_RECV_TIMEOUT,
)
from tuya_ble_mesh.exceptions import ProvisioningError
from tuya_ble_mesh.logging_context import MeshLogAdapter
from tuya_ble_mesh.sig_mesh_crypto import aes_cmac, k1, mesh_aes_ccm_encrypt, s1

if TYPE_CHECKING:
    from bleak import BleakClient
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# PB-GATT characteristics (Mesh Profile 7.1)
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

# No OOB authentication value (16 zero bytes)
_NO_OOB_AUTH: bytes = b"\x00" * 16

# Attention duration for Invite PDU (seconds)
_ATTENTION_DURATION = 5

# Delay after Start PDU to let device initialize provisioning state (seconds)
_POST_START_PDU_DELAY = 0.5

# Provisioning poll interval (seconds)
_PROVISIONING_POLL_INTERVAL = 0.05

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
    each in a Proxy PDU header with SAR field.

    Args:
        pdu: Provisioning PDU bytes.
        mtu: BLE MTU size.

    Returns:
        List of segments ready to write to PROV_DATA_IN.
    """
    max_payload = mtu - 4
    segments: list[bytes] = []
    if len(pdu) <= max_payload:
        # Single-segment: SAR=0b00 (Complete), Type=0x03 (Provisioning)
        segments.append(bytes([(_SAR_COMPLETE << 6) | 0x03]) + pdu)
    else:
        # Multi-segment: First, Continuation, Last
        chunks = [pdu[i : i + max_payload] for i in range(0, len(pdu), max_payload)]
        for i, chunk in enumerate(chunks):
            if i == 0:
                sar = _SAR_FIRST
            elif i == len(chunks) - 1:
                sar = _SAR_LAST
            else:
                sar = _SAR_CONTINUATION
            segments.append(bytes([(sar << 6) | 0x03]) + chunk)
    return segments


class ProvisionerExchangeMixin:
    """Mixin providing provisioning PDU exchange for SIG Mesh provisioner.

    This mixin must be used with a class that provides:
    - self._our_pub_key_bytes: bytes - Provisioner's public key (64 bytes)
    - self._private_key: EllipticCurvePrivateKey - Provisioner's ECDH private key
    - self._net_key: bytes - Network key (16 bytes)
    - self._app_key: bytes - Application key (16 bytes)
    - self._net_key_index: int - Network key index
    - self._iv_index: int - Mesh IV index
    - self._flags: int - Provisioning flags
    - self._unicast_addr: int - Unicast address to assign
    """

    _our_pub_key_bytes: bytes
    _private_key: EllipticCurvePrivateKey
    _net_key: bytes
    _app_key: bytes
    _net_key_index: int
    _iv_index: int
    _flags: int
    _unicast_addr: int

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
        # CF-2: Use asyncio.Lock to protect rx_buffer and rx_sar_buffer from race conditions
        rx_lock = asyncio.Lock()
        rx_event: asyncio.Event = asyncio.Event()
        rx_buffer: bytearray = bytearray()
        rx_sar_buffer: bytearray = bytearray()

        def _on_notify(_sender: object, data: bytearray) -> None:
            """Handle Provisioning Data Out notifications with SAR reassembly.

            CF-2: Schedule async processing to use lock protection.
            """
            if not data:
                return
            # CF-2: Schedule async handler to use lock
            data_copy = bytes(data)
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(_process_notify(data_copy))
                # Store task reference to prevent garbage collection
                task.add_done_callback(lambda _: None)
            except RuntimeError:
                # No running event loop (shutdown)
                _LOGGER.debug("No running event loop for provisioning notify")

        async def _process_notify(data: bytes) -> None:
            """Process notification with lock protection (CF-2)."""
            nonlocal rx_buffer, rx_sar_buffer
            async with rx_lock:
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
            """Send a provisioning PDU to the device over GATT.

            Wraps the PDU into MTU-sized segments and writes each segment to
            the provisioning data-in characteristic.  A brief inter-segment
            delay is applied when the PDU spans more than one segment to
            avoid overwhelming the device's receive buffer.

            Args:
                pdu: Raw provisioning PDU bytes to transmit.
            """
            segments = _wrap_provisioning_pdu(pdu, client.mtu_size)
            for seg in segments:
                await client.write_gatt_char(PROV_DATA_IN, seg, response=False)
                if len(segments) > 1:
                    await asyncio.sleep(_PROVISIONING_POLL_INTERVAL)

        async def recv_prov(
            recv_timeout: float = PROVISIONING_RECV_TIMEOUT, step_name: str = "PDU"
        ) -> bytes:
            """Receive provisioning PDU with timeout and context.

            CF-2: Read rx_buffer with lock protection.
            """
            rx_event.clear()
            try:
                await asyncio.wait_for(rx_event.wait(), timeout=recv_timeout)
                # CF-2: Lock access to rx_buffer to prevent race with concurrent notify
                async with rx_lock:
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

        # BlueZ requires pairing (bonding) before CCCD writes work on some devices.
        # Without this, start_notify fails with "org.bluez.Error.Failed: Failed to subscribe".
        try:
            if hasattr(client, "pair"):
                _LOGGER.info("Provisioning: pairing (BlueZ bond) before GATT subscribe")
                await asyncio.wait_for(client.pair(), timeout=PROVISIONING_PAIR_TIMEOUT)
        except (TimeoutError, OSError) as pair_exc:
            _LOGGER.warning(
                "BlueZ pair() failed (%s: %s) — trying start_notify anyway",
                type(pair_exc).__name__,
                pair_exc,
            )

        await client.start_notify(PROV_DATA_OUT, _on_notify)

        # ---- Step 1: Invite ----
        _LOGGER.info("Provisioning: Invite (attention=%ds)", _ATTENTION_DURATION)
        invite_params = bytes([_ATTENTION_DURATION])
        await send_prov(bytes([_PROV_INVITE]) + invite_params)

        # ---- Step 2: Capabilities ----
        caps_pdu = await recv_prov(
            recv_timeout=PROVISIONING_CAPABILITIES_TIMEOUT, step_name="Capabilities"
        )
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
        await asyncio.sleep(_POST_START_PDU_DELAY)

        # ---- Step 4: Public Key exchange ----
        _LOGGER.info("Provisioning: PublicKey exchange (%d bytes)", len(self._our_pub_key_bytes))
        await send_prov(bytes([_PROV_PUBLIC_KEY]) + self._our_pub_key_bytes)

        dev_pub_pdu = await recv_prov(
            recv_timeout=PROVISIONING_PUBLIC_KEY_TIMEOUT, step_name="PublicKey"
        )
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

        dev_conf_pdu = await recv_prov(
            recv_timeout=PROVISIONING_CONFIRMATION_TIMEOUT, step_name="Confirmation"
        )
        check_pdu(dev_conf_pdu, _PROV_CONFIRMATION, "Confirmation")
        dev_confirmation = dev_conf_pdu[1:]
        _LOGGER.info("Provisioning: Device confirmation received (%d bytes)", len(dev_confirmation))

        # ---- Step 6: Random exchange ----
        _LOGGER.info("Provisioning: Random exchange")
        await send_prov(bytes([_PROV_RANDOM]) + random_provisioner)

        dev_random_pdu = await recv_prov(
            recv_timeout=PROVISIONING_RANDOM_TIMEOUT, step_name="Random"
        )
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
        result_pdu = await recv_prov(
            recv_timeout=PROVISIONING_COMPLETE_TIMEOUT, step_name="Complete/Failed"
        )
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
