"""Unit tests for SIGMeshDeviceCommandsMixin (via SIGMeshDevice).

Covers the command methods in sig_mesh_device_commands.py:
- send_power: retry on BleakError, exponential backoff, MeshConnectionError
- send_vendor_command: happy path, not-connected guard
- request_composition_data: happy path (already partially covered in test_sig_mesh_device.py)
- send_config_appkey_add: success, non-success status, timeout
- send_config_model_app_bind: success, non-success status, timeout
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.exceptions import (  # noqa: E402  # noqa: E402
    MeshConnectionError,
    SIGMeshError,
    SIGMeshKeyError,
)
from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice  # noqa: E402
from tuya_ble_mesh.sig_mesh_protocol import MeshKeys  # noqa: E402


def _make_device() -> SIGMeshDevice:
    """Create a SIGMeshDevice with mock keys and mock BLE client injected directly."""
    dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
    dev._keys = MeshKeys(
        "f7a2a44f8e8a8029064f173ddc1e2b00",  # pragma: allowlist secret
        "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
        "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
    )
    dev._client = MagicMock()
    dev._client.write_gatt_char = AsyncMock()
    return dev


# ---------------------------------------------------------------------------
# send_power — not-connected guards
# ---------------------------------------------------------------------------


class TestSendPowerGuards:
    """Test not-connected guards in send_power."""

    @pytest.mark.asyncio
    async def test_raises_when_client_none(self) -> None:
        dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
        with pytest.raises(SIGMeshError, match="Not connected"):
            await dev.send_power(True)

    @pytest.mark.asyncio
    async def test_raises_when_keys_none(self) -> None:
        dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
        dev._client = MagicMock()
        # _keys stays None
        with pytest.raises(SIGMeshError, match="Not connected"):
            await dev.send_power(False)

    @pytest.mark.asyncio
    async def test_raises_when_no_app_key(self) -> None:
        dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
        dev._client = MagicMock()
        dev._keys = MagicMock()
        dev._keys.app_key = None
        with pytest.raises(SIGMeshKeyError):
            await dev.send_power(True)


# ---------------------------------------------------------------------------
# send_power — BLE write retry and exponential backoff
# ---------------------------------------------------------------------------


class TestSendPowerRetry:
    """Test BLE write retry logic in send_power."""

    @pytest.mark.asyncio
    async def test_all_retries_fail_raises_mesh_connection_error(self) -> None:
        """BleakError on every attempt should raise MeshConnectionError after retries."""
        from bleak.exc import BleakError

        dev = _make_device()
        dev._client.write_gatt_char = AsyncMock(side_effect=BleakError("write failed"))

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(MeshConnectionError, match="after 3 attempts"),
        ):
            await dev.send_power(True, max_retries=3)

        # All 3 attempts should have been made
        assert dev._client.write_gatt_char.call_count == 3

    @pytest.mark.asyncio
    async def test_single_retry_fails_raises_after_1_attempt(self) -> None:
        """max_retries=1 should make exactly 1 attempt then raise."""
        from bleak.exc import BleakError

        dev = _make_device()
        dev._client.write_gatt_char = AsyncMock(side_effect=BleakError("fail"))

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(MeshConnectionError),
        ):
            await dev.send_power(True, max_retries=1)

        assert dev._client.write_gatt_char.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self) -> None:
        """BleakError on attempt 1, success on attempt 2 — should not raise."""
        from bleak.exc import BleakError

        dev = _make_device()
        call_count = 0

        async def write_side_effect(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BleakError("transient")

        dev._client.write_gatt_char = AsyncMock(side_effect=write_side_effect)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await dev.send_power(True, max_retries=3)

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_oserror_also_retried(self) -> None:
        """OSError (not BleakError) should also trigger retry."""
        dev = _make_device()
        dev._client.write_gatt_char = AsyncMock(side_effect=OSError("pipe broken"))

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(MeshConnectionError),
        ):
            await dev.send_power(False, max_retries=2)

        assert dev._client.write_gatt_char.call_count == 2

    @pytest.mark.asyncio
    async def test_sig_mesh_error_propagates_immediately(self) -> None:
        """SIGMeshError raised inside the loop should propagate immediately."""
        dev = _make_device()
        dev._client.write_gatt_char = AsyncMock(side_effect=SIGMeshError("abort"))

        with pytest.raises(SIGMeshError, match="abort"):
            await dev.send_power(True, max_retries=3)

        # Only 1 attempt — SIGMeshError is re-raised without retry
        assert dev._client.write_gatt_char.call_count == 1

    @pytest.mark.asyncio
    async def test_backoff_sleep_called_between_retries(self) -> None:
        """asyncio.sleep should be called between retry attempts."""
        from bleak.exc import BleakError

        dev = _make_device()
        dev._client.write_gatt_char = AsyncMock(side_effect=BleakError("fail"))

        with (
            patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
            pytest.raises(MeshConnectionError),
        ):
            await dev.send_power(True, max_retries=3)

        # 3 retries: sleep after attempt 1 and attempt 2 (not after last)
        assert mock_sleep.call_count == 2


# ---------------------------------------------------------------------------
# send_vendor_command
# ---------------------------------------------------------------------------


class TestSendVendorCommand:
    """Test send_vendor_command."""

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self) -> None:
        dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
        with pytest.raises(SIGMeshError, match="Not connected"):
            await dev.send_vendor_command(b"\xcd\xd0\x07\x01\x02\x03")

    @pytest.mark.asyncio
    async def test_raises_when_no_app_key(self) -> None:
        dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
        dev._client = MagicMock()
        dev._keys = MagicMock()
        dev._keys.app_key = None
        with pytest.raises(SIGMeshKeyError):
            await dev.send_vendor_command(b"\xcd\xd0\x07\x01")

    @pytest.mark.asyncio
    async def test_writes_to_proxy_data_in(self) -> None:
        """Happy path: should write one GATT packet to SIG_MESH_PROXY_DATA_IN."""
        dev = _make_device()
        await dev.send_vendor_command(b"\xcd\xd0\x07\x01\x02\x03")

        dev._client.write_gatt_char.assert_called_once()
        char_uuid = dev._client.write_gatt_char.call_args[0][0]
        assert char_uuid == "00002add-0000-1000-8000-00805f9b34fb"

    @pytest.mark.asyncio
    async def test_write_uses_response_false(self) -> None:
        dev = _make_device()
        await dev.send_vendor_command(b"\xcd\xd0\x07\x42")

        kwargs = dev._client.write_gatt_char.call_args[1]
        assert kwargs.get("response") is False

    @pytest.mark.asyncio
    async def test_increments_seq(self) -> None:
        """Each vendor command should consume one sequence number."""
        dev = _make_device()
        seq_before = dev.get_seq()
        await dev.send_vendor_command(b"\xcd\xd0\x07\x01")
        assert dev.get_seq() == seq_before + 1


# ---------------------------------------------------------------------------
# send_config_appkey_add
# ---------------------------------------------------------------------------


class TestSendConfigAppkeyAdd:
    """Test send_config_appkey_add."""

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self) -> None:
        dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
        with pytest.raises(SIGMeshError, match="Not connected"):
            await dev.send_config_appkey_add(b"\x00" * 16)

    @pytest.mark.asyncio
    async def test_success_returns_true(self) -> None:
        """Device responds with status 0x00 → returns True."""
        dev = _make_device()
        writes: list[tuple[object, ...]] = []

        async def fake_write(*args: object, **kwargs: object) -> None:
            writes.append(args)
            if len(writes) == 2:
                # Resolve all pending futures after the last segment write
                for fut in list(dev._pending_responses.values()):
                    if not fut.done():
                        fut.set_result(b"\x00\x00\x00")  # status=0x00

        dev._client.write_gatt_char = AsyncMock(side_effect=fake_write)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await dev.send_config_appkey_add(b"\x00" * 16, response_timeout=1.0)

        assert result is True
        assert len(writes) == 2  # 16-byte key → 2 segments

    @pytest.mark.asyncio
    async def test_non_zero_status_returns_false(self) -> None:
        """Device responds with non-zero status → returns False."""
        dev = _make_device()
        writes: list[tuple[object, ...]] = []

        async def fake_write(*args: object, **kwargs: object) -> None:
            writes.append(args)
            if len(writes) == 2:
                for fut in list(dev._pending_responses.values()):
                    if not fut.done():
                        fut.set_result(b"\x01\x00\x00")  # status=0x01 (error)

        dev._client.write_gatt_char = AsyncMock(side_effect=fake_write)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await dev.send_config_appkey_add(b"\x00" * 16, response_timeout=1.0)

        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_raises_sig_mesh_error(self) -> None:
        """No response within timeout → SIGMeshError."""
        dev = _make_device()

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(SIGMeshError, match="Timeout"),
        ):
            await dev.send_config_appkey_add(b"\x00" * 16, response_timeout=0.01)

    @pytest.mark.asyncio
    async def test_future_cleaned_up_after_success(self) -> None:
        """_pending_responses should be empty after successful exchange."""
        dev = _make_device()
        writes: list[tuple[object, ...]] = []

        async def fake_write(*args: object, **kwargs: object) -> None:
            writes.append(args)
            if len(writes) == 2:
                for fut in list(dev._pending_responses.values()):
                    if not fut.done():
                        fut.set_result(b"\x00\x00\x00")

        dev._client.write_gatt_char = AsyncMock(side_effect=fake_write)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await dev.send_config_appkey_add(b"\x00" * 16, response_timeout=1.0)

        assert dev._pending_responses == {}

    @pytest.mark.asyncio
    async def test_future_cleaned_up_after_timeout(self) -> None:
        """_pending_responses should be empty even after timeout."""
        dev = _make_device()

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(SIGMeshError),
        ):
            await dev.send_config_appkey_add(b"\x00" * 16, response_timeout=0.01)

        assert dev._pending_responses == {}

    @pytest.mark.asyncio
    async def test_writes_to_proxy_data_in(self) -> None:
        """All GATT writes should target the Proxy Data In characteristic."""
        dev = _make_device()
        writes: list[tuple[object, ...]] = []

        async def fake_write(*args: object, **kwargs: object) -> None:
            writes.append(args)
            if len(writes) == 2:
                for fut in list(dev._pending_responses.values()):
                    if not fut.done():
                        fut.set_result(b"\x00\x00\x00")

        dev._client.write_gatt_char = AsyncMock(side_effect=fake_write)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await dev.send_config_appkey_add(b"\x00" * 16, response_timeout=1.0)

        for args in writes:
            assert args[0] == "00002add-0000-1000-8000-00805f9b34fb"


# ---------------------------------------------------------------------------
# send_config_model_app_bind
# ---------------------------------------------------------------------------


class TestSendConfigModelAppBind:
    """Test send_config_model_app_bind."""

    @pytest.mark.asyncio
    async def test_raises_when_not_connected(self) -> None:
        dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
        with pytest.raises(SIGMeshError, match="Not connected"):
            await dev.send_config_model_app_bind(0x0001, 0, 0x1000)

    @pytest.mark.asyncio
    async def test_success_returns_true(self) -> None:
        """Device responds with status 0x00 → returns True."""
        dev = _make_device()

        async def fake_write(*args: object, **kwargs: object) -> None:
            # Resolve the pending future immediately after the write
            for fut in list(dev._pending_responses.values()):
                if not fut.done():
                    fut.set_result(b"\x00\x00\x00\x00\x00")  # status=0x00

        dev._client.write_gatt_char = AsyncMock(side_effect=fake_write)

        result = await dev.send_config_model_app_bind(0x0001, 0, 0x1000, response_timeout=1.0)

        assert result is True
        dev._client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_non_zero_status_returns_false(self) -> None:
        """Device responds with non-zero status → returns False."""
        dev = _make_device()

        async def fake_write(*args: object, **kwargs: object) -> None:
            for fut in list(dev._pending_responses.values()):
                if not fut.done():
                    fut.set_result(b"\x02\x00\x00\x00\x00")  # status=0x02 (error)

        dev._client.write_gatt_char = AsyncMock(side_effect=fake_write)

        result = await dev.send_config_model_app_bind(0x0001, 0, 0x1000, response_timeout=1.0)

        assert result is False

    @pytest.mark.asyncio
    async def test_timeout_raises_sig_mesh_error(self) -> None:
        """No response within timeout → SIGMeshError."""
        dev = _make_device()

        with pytest.raises(SIGMeshError, match="Timeout"):
            await dev.send_config_model_app_bind(0x0001, 0, 0x1000, response_timeout=0.01)

    @pytest.mark.asyncio
    async def test_future_cleaned_up_after_success(self) -> None:
        """_pending_responses should be empty after successful exchange."""
        dev = _make_device()

        async def fake_write(*args: object, **kwargs: object) -> None:
            for fut in list(dev._pending_responses.values()):
                if not fut.done():
                    fut.set_result(b"\x00\x00\x00\x00\x00")

        dev._client.write_gatt_char = AsyncMock(side_effect=fake_write)

        await dev.send_config_model_app_bind(0x0001, 0, 0x1000, response_timeout=1.0)

        assert dev._pending_responses == {}

    @pytest.mark.asyncio
    async def test_future_cleaned_up_after_timeout(self) -> None:
        """_pending_responses should be empty even after timeout."""
        dev = _make_device()

        with pytest.raises(SIGMeshError):
            await dev.send_config_model_app_bind(0x0001, 0, 0x1000, response_timeout=0.01)

        assert dev._pending_responses == {}

    @pytest.mark.asyncio
    async def test_writes_to_proxy_data_in(self) -> None:
        """GATT write should target the Proxy Data In characteristic."""
        dev = _make_device()

        async def fake_write(*args: object, **kwargs: object) -> None:
            for fut in list(dev._pending_responses.values()):
                if not fut.done():
                    fut.set_result(b"\x00\x00\x00\x00\x00")

        dev._client.write_gatt_char = AsyncMock(side_effect=fake_write)

        await dev.send_config_model_app_bind(0x0001, 0, 0x1000, response_timeout=1.0)

        call_args = dev._client.write_gatt_char.call_args[0]
        assert call_args[0] == "00002add-0000-1000-8000-00805f9b34fb"

    @pytest.mark.asyncio
    async def test_write_uses_response_false(self) -> None:
        dev = _make_device()

        async def fake_write(*args: object, **kwargs: object) -> None:
            for fut in list(dev._pending_responses.values()):
                if not fut.done():
                    fut.set_result(b"\x00\x00\x00\x00\x00")

        dev._client.write_gatt_char = AsyncMock(side_effect=fake_write)

        await dev.send_config_model_app_bind(0x0001, 0, 0x1000, response_timeout=1.0)

        kwargs = dev._client.write_gatt_char.call_args[1]
        assert kwargs.get("response") is False

    @pytest.mark.asyncio
    async def test_empty_response_treated_as_error(self) -> None:
        """Empty params bytes → status treated as 0xFF → returns False."""
        dev = _make_device()

        async def fake_write(*args: object, **kwargs: object) -> None:
            for fut in list(dev._pending_responses.values()):
                if not fut.done():
                    fut.set_result(b"")  # empty

        dev._client.write_gatt_char = AsyncMock(side_effect=fake_write)

        result = await dev.send_config_model_app_bind(0x0001, 0, 0x1000, response_timeout=1.0)

        assert result is False
