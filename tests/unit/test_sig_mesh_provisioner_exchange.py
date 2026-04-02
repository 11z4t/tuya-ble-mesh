"""Tests for sig_mesh_provisioner_exchange — full PDU exchange flow.

Covers lines 217, 225-227, 233-246, 282-311, 315-324, 338-492.

Timing rule: fire notify callbacks ONLY after the corresponding write has been
captured from write_queue. This guarantees that recv_prov() has already created
its event and is awaiting before we trigger the notification.

Write counts (MTU=23, max_payload=19):
  Invite:       1 byte type + 1 byte param = 2 bytes → 1 write
  Start:        1 + 5 = 6 bytes → 1 write
  PublicKey:    1 + 64 = 65 bytes → 4 writes (FIRST + 2xCONT + LAST)
  Confirmation: 1 + 16 = 17 bytes → 1 write
  Random:       1 + 16 = 17 bytes → 1 write
  Data:         1 + 33 = 34 bytes → 2 writes (FIRST + LAST)
  Total:        1 + 1 + 4 + 1 + 1 + 2 = 10 writes
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ec import (
    ECDH,
    SECP256R1,
    EllipticCurvePublicNumbers,
    generate_private_key,
)

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "custom_components"
        / "tuya_ble_mesh"
        / "lib"
    ),
)

from tuya_ble_mesh.exceptions import ProvisioningError
from tuya_ble_mesh.sig_mesh_crypto import aes_cmac, k1, s1
from tuya_ble_mesh.sig_mesh_provisioner import ProvisioningResult, SIGMeshProvisioner

# PDU types
_PROV_INVITE = 0x00
_PROV_CAPABILITIES = 0x01
_PROV_START = 0x02
_PROV_PUBLIC_KEY = 0x03
_PROV_CONFIRMATION = 0x05
_PROV_RANDOM = 0x06
_PROV_DATA = 0x07
_PROV_COMPLETE = 0x08
_PROV_FAILED = 0x09

# SAR types
_SAR_COMPLETE = 0x00
_SAR_FIRST = 0x01
_SAR_CONTINUATION = 0x02
_SAR_LAST = 0x03

# Proxy PDU type for provisioning bearer
_PROXY_TYPE_PROVISIONING = 0x03

# Standard Capabilities PDU (11 bytes after type, 1 element, no OOB)
_CAPS_PAYLOAD = bytes(
    [
        0x01,  # num_elements = 1
        0x00,
        0x00,  # algorithms (FIPS P-256 EC)
        0x00,  # public_key_type (no OOB)
        0x00,  # static_oob_type (none)
        0x00,  # output_oob_size = 0
        0x00,
        0x00,  # output_oob_action = 0
        0x00,  # input_oob_size = 0
        0x00,
        0x00,  # input_oob_action = 0
    ]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prov() -> SIGMeshProvisioner:
    return SIGMeshProvisioner(b"\x00" * 16, b"\x01" * 16, 0x00B0)


def _make_client_with_queue() -> tuple[MagicMock, asyncio.Queue[bytes], list[Any]]:
    """Create a mock BleakClient with a write_queue and callback holder."""
    client: MagicMock = MagicMock()
    client.mtu_size = 23
    client.pair = AsyncMock()
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock()

    cb_holder: list[Any] = [None]
    write_queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def on_start_notify(char_uuid: str, cb: Any) -> None:
        cb_holder[0] = cb

    async def on_write(char_uuid: str, data: Any, response: bool = False) -> None:
        await write_queue.put(bytes(data))

    client.start_notify = AsyncMock(side_effect=on_start_notify)
    client.write_gatt_char = AsyncMock(side_effect=on_write)

    return client, write_queue, cb_holder


def _wrap_complete(pdu_type: int, payload: bytes = b"") -> bytearray:
    """Build a single-segment (SAR_COMPLETE) proxy PDU notification."""
    pdu = bytes([pdu_type]) + payload
    return bytearray([(_SAR_COMPLETE << 6) | _PROXY_TYPE_PROVISIONING]) + pdu


async def _drain(queue: asyncio.Queue[bytes], n: int, timeout: float = 5.0) -> list[bytes]:
    """Drain n items from queue with timeout."""
    items = []
    for _ in range(n):
        items.append(await asyncio.wait_for(queue.get(), timeout=timeout))
    return items


async def _reassemble(queue: asyncio.Queue[bytes], timeout: float = 5.0) -> bytes:
    """Reassemble a possibly multi-segment PDU from the write queue."""
    assembled = bytearray()
    while True:
        raw = await asyncio.wait_for(queue.get(), timeout=timeout)
        sar = (raw[0] >> 6) & 0x03
        chunk = raw[1:]
        if sar == _SAR_COMPLETE:
            return bytes(chunk)
        elif sar == _SAR_FIRST:
            assembled = bytearray(chunk)
        elif sar == _SAR_CONTINUATION:
            assembled.extend(chunk)
        elif sar == _SAR_LAST:
            assembled.extend(chunk)
            return bytes(assembled)


# ---------------------------------------------------------------------------
# _process_notify / SAR reassembly paths
# ---------------------------------------------------------------------------


class TestProcessNotifyPaths:
    """Test SAR reassembly and notify-path edge cases (lines 215-246)."""

    @pytest.mark.asyncio
    async def test_sar_complete_single_pdu_received(self) -> None:
        """SAR_COMPLETE: notification with single segment is received correctly.

        Verifies: line 235-237 (rx_buffer = payload; rx_event.set() for COMPLETE).
        """
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)  # consume Invite
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))
            await _drain(write_queue, 5)  # consume Start + 4xPubKey
            cb[0](None, _wrap_complete(_PROV_FAILED, bytes([0x02])))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match=r"ProvisioningFailed|InvalidFormat"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task

    @pytest.mark.asyncio
    async def test_sar_first_continuation_last_reassembly(self) -> None:
        """SAR_FIRST → SAR_CONTINUATION → SAR_LAST are correctly reassembled.

        Verifies: lines 238-246 (rx_sar_buffer accumulation).
        """
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)  # Invite

            # Send Capabilities via multi-segment SAR (chunk_size=4 → 3 segments)
            full_pdu = bytes([_PROV_CAPABILITIES]) + _CAPS_PAYLOAD  # 12 bytes
            chunk_size = 4
            chunks = [full_pdu[i : i + chunk_size] for i in range(0, len(full_pdu), chunk_size)]
            for i, chunk in enumerate(chunks):
                if i == 0:
                    sar = _SAR_FIRST
                elif i == len(chunks) - 1:
                    sar = _SAR_LAST
                else:
                    sar = _SAR_CONTINUATION
                header = (sar << 6) | _PROXY_TYPE_PROVISIONING
                cb[0](None, bytearray([header]) + chunk)
                await asyncio.sleep(0)  # yield so _process_notify tasks run in order

            await _drain(write_queue, 5)  # Start + 4xPubKey
            cb[0](None, _wrap_complete(_PROV_FAILED, bytes([0x01])))  # InvalidPDU

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            # If SAR reassembly fails, check_pdu raises Protocol error
            # If it succeeds, we reach PublicKey step and get ProvisioningFailed
            with pytest.raises(ProvisioningError):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task

    @pytest.mark.asyncio
    async def test_empty_notify_data_silently_ignored(self) -> None:
        """Empty notify data returns immediately without setting rx_event (line 217).

        The empty notification must NOT corrupt the protocol state.
        """
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)  # Invite

            # Fire empty notification — should be ignored
            cb[0](None, bytearray())
            await asyncio.sleep(0)

            # Fire valid Capabilities — exchange should continue normally
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))

            await _drain(write_queue, 5)  # Start + 4xPubKey
            cb[0](None, _wrap_complete(_PROV_FAILED, bytes([0x02])))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            # Should NOT fail at Capabilities (empty data was ignored); fails at PublicKey
            with pytest.raises(ProvisioningError, match=r"ProvisioningFailed|InvalidFormat"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task

    @pytest.mark.asyncio
    async def test_no_running_event_loop_in_notify_callback(self) -> None:
        """RuntimeError from asyncio.get_running_loop() is caught, not propagated (line 225-227).

        The exchange subsequently times out waiting for the PDU.
        """
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        error_caught = [False]

        async def drive() -> None:
            await _drain(write_queue, 1)  # Invite
            # Patch get_running_loop to raise RuntimeError in notify callback
            with patch(
                "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.get_running_loop",
                side_effect=RuntimeError("No running event loop"),
            ):
                cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))
                error_caught[0] = True

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_exchange.PROVISIONING_CAPABILITIES_TIMEOUT",
                0.1,
            ),
        ):
            task = asyncio.create_task(drive())
            with pytest.raises((ProvisioningError, asyncio.TimeoutError)):
                await asyncio.wait_for(prov._run_exchange(client), timeout=3.0)
            await task

        assert error_caught[0], "drive() should have fired the callback"


# ---------------------------------------------------------------------------
# check_pdu validation (lines 290-311)
# ---------------------------------------------------------------------------


class TestCheckPdu:
    """Test check_pdu for empty PDU, PROV_FAILED, and type mismatch."""

    @pytest.mark.asyncio
    async def test_empty_pdu_raises(self) -> None:
        """Empty PDU (after SAR strip) raises ProvisioningError (lines 292-294)."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)
            # SAR_COMPLETE with no PDU bytes after header → payload is empty
            cb[0](None, bytearray([(_SAR_COMPLETE << 6) | _PROXY_TYPE_PROVISIONING]))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="empty PDU"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=5.0)
            await task

    @pytest.mark.asyncio
    async def test_prov_failed_known_error_code(self) -> None:
        """PROV_FAILED with a known code includes the name (lines 296-304)."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(_PROV_FAILED, bytes([0x03])))  # UnexpectedPDU

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="UnexpectedPDU"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=5.0)
            await task

    @pytest.mark.asyncio
    async def test_prov_failed_unknown_error_code(self) -> None:
        """PROV_FAILED with unknown code shows hex in error message (line 298)."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(_PROV_FAILED, bytes([0xEE])))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="0xEE"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=5.0)
            await task

    @pytest.mark.asyncio
    async def test_prov_failed_no_payload(self) -> None:
        """PROV_FAILED with no code byte defaults to 0xFF (line 297)."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(_PROV_FAILED))  # no payload

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="ProvisioningFailed"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=5.0)
            await task

    @pytest.mark.asyncio
    async def test_wrong_pdu_type_raises_protocol_error(self) -> None:
        """Wrong PDU type raises ProvisioningError 'Protocol error' (lines 306-311)."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(0xAB, b"\x00" * 4))  # 0xAB ≠ 0x01 (Capabilities)

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="Protocol error"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=5.0)
            await task


# ---------------------------------------------------------------------------
# BlueZ pair() handling (lines 315-324)
# ---------------------------------------------------------------------------


class TestBlePairHandling:
    """Test pair() failure modes (line 316-324)."""

    @pytest.mark.asyncio
    async def test_pair_timeout_warning_then_continues(self) -> None:
        """TimeoutError from pair() is logged as warning, exchange continues (line 319-324)."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()
        client.pair = AsyncMock(side_effect=TimeoutError("pair timeout"))

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))
            await _drain(write_queue, 5)
            cb[0](None, _wrap_complete(_PROV_FAILED, bytes([0x02])))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            # Exchange continues despite pair() timeout → reaches PublicKey → PROV_FAILED
            with pytest.raises(ProvisioningError, match=r"ProvisioningFailed|InvalidFormat"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task

    @pytest.mark.asyncio
    async def test_pair_oserror_warning_then_continues(self) -> None:
        """OSError from pair() is logged as warning, exchange continues."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()
        client.pair = AsyncMock(side_effect=OSError("pair failed"))

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))
            await _drain(write_queue, 5)
            cb[0](None, _wrap_complete(_PROV_FAILED, bytes([0x02])))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match=r"ProvisioningFailed|InvalidFormat"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task

    @pytest.mark.asyncio
    async def test_client_without_pair_skips_pairing(self) -> None:
        """Client without pair() attribute skips pairing silently (line 316)."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()
        del client.pair  # remove pair entirely

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))
            await _drain(write_queue, 5)
            cb[0](None, _wrap_complete(_PROV_FAILED, bytes([0x02])))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match=r"ProvisioningFailed|InvalidFormat"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task


# ---------------------------------------------------------------------------
# Mid-protocol error paths (lines 338-492)
# ---------------------------------------------------------------------------


class TestMidProtocolErrors:
    """Test error conditions in the main provisioning protocol steps."""

    @pytest.mark.asyncio
    async def test_capabilities_timeout(self) -> None:
        """Timeout waiting for Capabilities raises ProvisioningError (line 334)."""
        prov = _make_prov()
        client, _wq, _cb = _make_client_with_queue()

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_exchange.PROVISIONING_CAPABILITIES_TIMEOUT",
                0.05,
            ),
            pytest.raises(ProvisioningError, match="Timeout"),
        ):
            await asyncio.wait_for(prov._run_exchange(client), timeout=5.0)

    @pytest.mark.asyncio
    async def test_public_key_timeout(self) -> None:
        """Timeout waiting for device PublicKey raises ProvisioningError (line 360)."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))
            # Consume Start + PubKey writes, then do nothing (timeout on PublicKey recv)
            await _drain(write_queue, 5)

        with (
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
            ),
            patch(
                "tuya_ble_mesh.sig_mesh_provisioner_exchange.PROVISIONING_PUBLIC_KEY_TIMEOUT", 0.05
            ),
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="Timeout"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=5.0)
            await task

    @pytest.mark.asyncio
    async def test_invalid_device_public_key_length(self) -> None:
        """Device PublicKey with wrong length raises ProvisioningError (lines 365-371)."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))
            await _drain(write_queue, 5)  # Start + 4xPubKey
            # Send device PublicKey with wrong length (32 instead of 64)
            cb[0](None, _wrap_complete(_PROV_PUBLIC_KEY, b"\xcc" * 32))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="Invalid device public key length"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task

    @pytest.mark.asyncio
    async def test_ecdh_key_not_on_curve(self) -> None:
        """Device PublicKey not on SECP256R1 raises ProvisioningError (lines 374-384)."""
        prov = _make_prov()
        client, write_queue, cb = _make_client_with_queue()

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))
            await _drain(write_queue, 5)
            # 64 bytes of zeros = invalid SECP256R1 point
            cb[0](None, _wrap_complete(_PROV_PUBLIC_KEY, b"\x00" * 64))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="Invalid device public key"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task

    @pytest.mark.asyncio
    async def test_confirmation_mismatch(self) -> None:
        """Wrong device confirmation raises ProvisioningError (lines 426-432)."""
        prov = _make_prov()
        device_priv = generate_private_key(SECP256R1())
        client, write_queue, cb = _make_client_with_queue()

        pub_nums = device_priv.public_key().public_numbers()
        dev_pub_bytes = pub_nums.x.to_bytes(32, "big") + pub_nums.y.to_bytes(32, "big")

        async def drive() -> None:
            await _drain(write_queue, 1)
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))
            await _drain(write_queue, 5)  # Start + 4xPubKey
            cb[0](None, _wrap_complete(_PROV_PUBLIC_KEY, dev_pub_bytes))
            await _drain(write_queue, 1)  # Confirmation write
            # Send WRONG confirmation (all zeros)
            cb[0](None, _wrap_complete(_PROV_CONFIRMATION, b"\x00" * 16))
            await _drain(write_queue, 1)  # Random write
            # Send any random (won't match wrong confirmation)
            cb[0](None, _wrap_complete(_PROV_RANDOM, b"\xbb" * 16))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="confirmation mismatch"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task

    @pytest.mark.asyncio
    async def test_prov_failed_after_data(self) -> None:
        """PROV_FAILED after Data PDU raises ProvisioningError (lines 460-468)."""
        prov = _make_prov()
        device_priv = generate_private_key(SECP256R1())
        client, write_queue, cb = _make_client_with_queue()

        pub_nums = device_priv.public_key().public_numbers()
        dev_pub_bytes = pub_nums.x.to_bytes(32, "big") + pub_nums.y.to_bytes(32, "big")

        async def drive() -> None:
            invite_pdu = await _reassemble(write_queue)
            invite_params = invite_pdu[1:]
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))

            start_pdu = await _reassemble(write_queue)
            start_params = start_pdu[1:]

            prov_pub_pdu = await _reassemble(write_queue)
            prov_pub_key_bytes = prov_pub_pdu[1:]

            # Compute correct ECDH + confirmation
            prov_x = int.from_bytes(prov_pub_key_bytes[:32], "big")
            prov_y = int.from_bytes(prov_pub_key_bytes[32:], "big")
            prov_pub = EllipticCurvePublicNumbers(prov_x, prov_y, SECP256R1()).public_key()
            shared = device_priv.exchange(ECDH(), prov_pub)

            cb[0](None, _wrap_complete(_PROV_PUBLIC_KEY, dev_pub_bytes))

            conf_inputs = (
                invite_params + _CAPS_PAYLOAD + start_params + prov_pub_key_bytes + dev_pub_bytes
            )
            conf_salt = s1(conf_inputs)
            conf_key = k1(shared, conf_salt, b"prck")

            await _reassemble(write_queue)  # Provisioner Confirmation
            device_random = b"\xcc" * 16
            dev_conf = aes_cmac(conf_key, device_random + b"\x00" * 16)
            cb[0](None, _wrap_complete(_PROV_CONFIRMATION, dev_conf))

            await _reassemble(write_queue)  # Provisioner Random
            cb[0](None, _wrap_complete(_PROV_RANDOM, device_random))

            await _reassemble(write_queue)  # Data (2 segments)
            # Send PROV_FAILED after Data
            cb[0](None, _wrap_complete(_PROV_FAILED, bytes([0x04])))  # InsufficientResources

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="rejected provisioning data"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task

    @pytest.mark.asyncio
    async def test_unexpected_pdu_after_data(self) -> None:
        """Unknown PDU type after Data raises ProvisioningError (lines 469-474)."""
        prov = _make_prov()
        device_priv = generate_private_key(SECP256R1())
        client, write_queue, cb = _make_client_with_queue()

        pub_nums = device_priv.public_key().public_numbers()
        dev_pub_bytes = pub_nums.x.to_bytes(32, "big") + pub_nums.y.to_bytes(32, "big")

        async def drive() -> None:
            invite_pdu = await _reassemble(write_queue)
            invite_params = invite_pdu[1:]
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))

            start_pdu = await _reassemble(write_queue)
            start_params = start_pdu[1:]
            prov_pub_pdu = await _reassemble(write_queue)
            prov_pub_key_bytes = prov_pub_pdu[1:]

            prov_x = int.from_bytes(prov_pub_key_bytes[:32], "big")
            prov_y = int.from_bytes(prov_pub_key_bytes[32:], "big")
            prov_pub = EllipticCurvePublicNumbers(prov_x, prov_y, SECP256R1()).public_key()
            shared = device_priv.exchange(ECDH(), prov_pub)

            cb[0](None, _wrap_complete(_PROV_PUBLIC_KEY, dev_pub_bytes))

            conf_inputs = (
                invite_params + _CAPS_PAYLOAD + start_params + prov_pub_key_bytes + dev_pub_bytes
            )
            conf_salt = s1(conf_inputs)
            conf_key = k1(shared, conf_salt, b"prck")

            await _reassemble(write_queue)  # Confirmation
            device_random = b"\xdd" * 16
            dev_conf = aes_cmac(conf_key, device_random + b"\x00" * 16)
            cb[0](None, _wrap_complete(_PROV_CONFIRMATION, dev_conf))

            await _reassemble(write_queue)  # Random
            cb[0](None, _wrap_complete(_PROV_RANDOM, device_random))

            await _reassemble(write_queue)  # Data
            # Send unknown PDU type
            cb[0](None, _wrap_complete(0xAA))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(drive())
            with pytest.raises(ProvisioningError, match="Expected ProvisioningComplete"):
                await asyncio.wait_for(prov._run_exchange(client), timeout=10.0)
            await task


# ---------------------------------------------------------------------------
# Full exchange happy path (lines 338-492)
# ---------------------------------------------------------------------------


class TestFullExchangeHappyPath:
    """Full provisioning exchange with simulated device — real ECDH crypto."""

    async def _run_full(
        self,
        prov: SIGMeshProvisioner,
        device_priv: Any,
    ) -> ProvisioningResult:
        client, write_queue, cb = _make_client_with_queue()

        pub_nums = device_priv.public_key().public_numbers()
        dev_pub_bytes = pub_nums.x.to_bytes(32, "big") + pub_nums.y.to_bytes(32, "big")

        async def device_sim() -> None:
            # Step 1: Invite
            invite_pdu = await _reassemble(write_queue)
            assert invite_pdu[0] == _PROV_INVITE
            invite_params = invite_pdu[1:]

            # Step 2: Send Capabilities
            cb[0](None, _wrap_complete(_PROV_CAPABILITIES, _CAPS_PAYLOAD))

            # Step 3: Receive Start
            start_pdu = await _reassemble(write_queue)
            assert start_pdu[0] == _PROV_START
            start_params = start_pdu[1:]

            # Step 4: Receive Provisioner PublicKey
            prov_pub_pdu = await _reassemble(write_queue)
            assert prov_pub_pdu[0] == _PROV_PUBLIC_KEY
            prov_pub_key_bytes = prov_pub_pdu[1:]
            assert len(prov_pub_key_bytes) == 64

            # Compute ECDH from provisioner's public key
            prov_x = int.from_bytes(prov_pub_key_bytes[:32], "big")
            prov_y = int.from_bytes(prov_pub_key_bytes[32:], "big")
            prov_pub = EllipticCurvePublicNumbers(prov_x, prov_y, SECP256R1()).public_key()
            shared = device_priv.exchange(ECDH(), prov_pub)

            # Send Device PublicKey
            cb[0](None, _wrap_complete(_PROV_PUBLIC_KEY, dev_pub_bytes))

            # Compute ConfirmationKey
            conf_inputs = (
                invite_params + _CAPS_PAYLOAD + start_params + prov_pub_key_bytes + dev_pub_bytes
            )
            conf_salt = s1(conf_inputs)
            conf_key = k1(shared, conf_salt, b"prck")

            # Step 5: Receive Provisioner Confirmation
            conf_pdu = await _reassemble(write_queue)
            assert conf_pdu[0] == _PROV_CONFIRMATION

            # Send Device Confirmation
            device_random = b"\xbb" * 16
            dev_conf = aes_cmac(conf_key, device_random + b"\x00" * 16)
            cb[0](None, _wrap_complete(_PROV_CONFIRMATION, dev_conf))

            # Step 6: Receive Provisioner Random
            rand_pdu = await _reassemble(write_queue)
            assert rand_pdu[0] == _PROV_RANDOM

            # Send Device Random
            cb[0](None, _wrap_complete(_PROV_RANDOM, device_random))

            # Step 7: Receive Data + send Complete
            data_pdu = await _reassemble(write_queue)
            assert data_pdu[0] == _PROV_DATA
            assert len(data_pdu) == 34  # 1 type + 25 data + 8 MIC

            cb[0](None, _wrap_complete(_PROV_COMPLETE))

        with patch(
            "tuya_ble_mesh.sig_mesh_provisioner_exchange.asyncio.sleep", new_callable=AsyncMock
        ):
            task = asyncio.create_task(device_sim())
            result = await asyncio.wait_for(prov._run_exchange(client), timeout=15.0)
            await task

        return result

    @pytest.mark.asyncio
    async def test_happy_path_default_params(self) -> None:
        """Complete exchange returns correct ProvisioningResult with default params."""
        prov = _make_prov()
        device_priv = generate_private_key(SECP256R1())

        result = await self._run_full(prov, device_priv)

        assert isinstance(result, ProvisioningResult)
        assert result.unicast_addr == 0x00B0
        assert result.net_key == b"\x00" * 16
        assert result.app_key == b"\x01" * 16
        assert len(result.dev_key) == 16
        assert result.iv_index == 0
        assert result.num_elements == 1

    @pytest.mark.asyncio
    async def test_happy_path_custom_params(self) -> None:
        """Complete exchange with custom net_key_index and iv_index (lines 447-450)."""
        prov = SIGMeshProvisioner(
            b"\xaa" * 16,
            b"\xbb" * 16,
            0x1234,
            net_key_index=7,
            iv_index=42,
            flags=1,
        )
        device_priv = generate_private_key(SECP256R1())

        result = await self._run_full(prov, device_priv)

        assert result.unicast_addr == 0x1234
        assert result.net_key == b"\xaa" * 16
        assert result.app_key == b"\xbb" * 16
        assert result.iv_index == 42
