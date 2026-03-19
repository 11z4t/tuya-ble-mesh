"""Unit tests for Telink BLE Mesh provisioner."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "custom_components"
        / "tuya_ble_mesh"
        / "lib"
    ),
)

from tuya_ble_mesh.const import (
    PAIR_OPCODE_FAILURE,
    PAIR_OPCODE_SET_NAME,
    PAIR_OPCODE_SET_OK,
    PAIR_OPCODE_SET_PASS,
    PAIR_OPCODE_SUCCESS,
    TELINK_CHAR_PAIRING,
    TELINK_CHAR_STATUS,
)
from tuya_ble_mesh.exceptions import ProvisioningError
from tuya_ble_mesh.provisioner import (
    enable_notifications,
    pair,
    provision,
    set_mesh_credentials,
)

MESH_NAME = b"out_of_mesh"
MESH_PASS = b"123456"
DEVICE_RANDOM = b"\x11\x22\x33\x44\x55\x66\x77\x88"
CLIENT_RANDOM = b"\xaa\xbb\xcc\xdd\xee\xff\x00\x11"


def _success_response() -> bytes:
    """Build a pair success response."""
    return bytes([PAIR_OPCODE_SUCCESS]) + DEVICE_RANDOM


def _failure_response() -> bytes:
    """Build a pair failure response."""
    return bytes([PAIR_OPCODE_FAILURE])


def _set_ok_response() -> bytes:
    """Build a credential set OK response."""
    return bytes([PAIR_OPCODE_SET_OK])


# --- pair() ---


class TestPair:
    """Test the pairing handshake."""

    @pytest.mark.asyncio
    async def test_successful_pair(self) -> None:
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=_success_response())

        with patch(
            "tuya_ble_mesh.provisioner.generate_session_random",
            return_value=CLIENT_RANDOM,
        ):
            session_key, returned_random = await pair(client, MESH_NAME, MESH_PASS)

        assert len(session_key) == 16
        assert returned_random == CLIENT_RANDOM

        # Verify write was called twice: 1) pair packet, 2) enable notifications
        assert client.write_gatt_char.call_count == 2

        # First call: pair packet to TELINK_CHAR_PAIRING
        first_call = client.write_gatt_char.call_args_list[0]
        assert first_call[0][0] == TELINK_CHAR_PAIRING
        assert len(first_call[0][1]) == 17  # pair packet size
        assert first_call[1]["response"] is True

        # Second call: enable notifications to TELINK_CHAR_STATUS
        second_call = client.write_gatt_char.call_args_list[1]
        assert second_call[0][0] == TELINK_CHAR_STATUS
        assert second_call[0][1] == b"\x01"
        assert second_call[1]["response"] is True

        # Verify read was called after enable notifications
        client.read_gatt_char.assert_called_once_with(TELINK_CHAR_PAIRING)

    @pytest.mark.asyncio
    async def test_pair_failure_raises(self) -> None:
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=_failure_response())

        with (
            patch(
                "tuya_ble_mesh.provisioner.generate_session_random",
                return_value=CLIENT_RANDOM,
            ),
            pytest.raises(ProvisioningError, match="rejected"),
        ):
            await pair(client, MESH_NAME, MESH_PASS)

    @pytest.mark.asyncio
    async def test_unexpected_opcode_raises(self) -> None:
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        # Return set_ok (0x07) instead of success (0x0D)
        client.read_gatt_char = AsyncMock(return_value=_set_ok_response())

        with (
            patch(
                "tuya_ble_mesh.provisioner.generate_session_random",
                return_value=CLIENT_RANDOM,
            ),
            pytest.raises(ProvisioningError, match="Unexpected"),
        ):
            await pair(client, MESH_NAME, MESH_PASS)

    @pytest.mark.asyncio
    async def test_session_key_is_deterministic(self) -> None:
        """Same inputs produce same session key."""
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=_success_response())

        with patch(
            "tuya_ble_mesh.provisioner.generate_session_random",
            return_value=CLIENT_RANDOM,
        ):
            key1, _ = await pair(client, MESH_NAME, MESH_PASS)
            key2, _ = await pair(client, MESH_NAME, MESH_PASS)

        assert key1 == key2

    @pytest.mark.asyncio
    async def test_session_key_never_in_exception(self) -> None:
        """Session key must not leak in exception messages."""
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=_failure_response())

        with (
            patch(
                "tuya_ble_mesh.provisioner.generate_session_random",
                return_value=CLIENT_RANDOM,
            ),
            pytest.raises(ProvisioningError) as exc_info,
        ):
            await pair(client, MESH_NAME, MESH_PASS)

        msg = str(exc_info.value)
        # No hex key material in message
        assert "\\x" not in msg
        assert CLIENT_RANDOM.hex() not in msg


# --- set_mesh_credentials() ---


class TestSetMeshCredentials:
    """Test credential setting."""

    @pytest.mark.asyncio
    async def test_successful_set(self) -> None:
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=_set_ok_response())

        session_key = b"\x00" * 16
        await set_mesh_credentials(client, session_key, b"new_mesh", b"new_pass")

        # Should write name packet and password packet
        assert client.write_gatt_char.call_count == 2
        calls = client.write_gatt_char.call_args_list

        # First call: name
        name_data = calls[0][0][1]
        assert name_data[0] == PAIR_OPCODE_SET_NAME
        assert len(name_data) == 17  # opcode + 16B encrypted

        # Second call: password
        pass_data = calls[1][0][1]
        assert pass_data[0] == PAIR_OPCODE_SET_PASS
        assert len(pass_data) == 17

    @pytest.mark.asyncio
    async def test_set_failure_raises(self) -> None:
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=_failure_response())

        with pytest.raises(ProvisioningError, match="failed"):
            await set_mesh_credentials(client, b"\x00" * 16, b"name", b"pass")


# --- enable_notifications() ---


class TestEnableNotifications:
    """Test notification enabling."""

    @pytest.mark.asyncio
    async def test_writes_to_status_char(self) -> None:
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()

        await enable_notifications(client)

        client.write_gatt_char.assert_called_once_with(TELINK_CHAR_STATUS, b"\x01", response=True)


# --- provision() ---


class TestProvision:
    """Test complete provisioning flow."""

    @pytest.mark.asyncio
    async def test_provision_without_new_credentials(self) -> None:
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        # First read: pair response, then notification enable doesn't read
        client.read_gatt_char = AsyncMock(return_value=_success_response())

        with patch(
            "tuya_ble_mesh.provisioner.generate_session_random",
            return_value=CLIENT_RANDOM,
        ):
            session_key = await provision(client, MESH_NAME, MESH_PASS)

        assert len(session_key) == 16
        # pair write + notification enable write = 2 writes
        assert client.write_gatt_char.call_count == 2

    @pytest.mark.asyncio
    async def test_provision_with_new_credentials(self) -> None:
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        # Pair success, then set_ok for credentials
        client.read_gatt_char = AsyncMock(side_effect=[_success_response(), _set_ok_response()])

        with patch(
            "tuya_ble_mesh.provisioner.generate_session_random",
            return_value=CLIENT_RANDOM,
        ):
            session_key = await provision(
                client,
                MESH_NAME,
                MESH_PASS,
                new_name=b"my_mesh",
                new_password=b"secret",
            )

        assert len(session_key) == 16
        # pair write + name write + pass write + notification enable = 4 writes
        assert client.write_gatt_char.call_count == 4

    @pytest.mark.asyncio
    async def test_provision_pair_failure(self) -> None:
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        client.read_gatt_char = AsyncMock(return_value=_failure_response())

        with (
            patch(
                "tuya_ble_mesh.provisioner.generate_session_random",
                return_value=CLIENT_RANDOM,
            ),
            pytest.raises(ProvisioningError),
        ):
            await provision(client, MESH_NAME, MESH_PASS)
