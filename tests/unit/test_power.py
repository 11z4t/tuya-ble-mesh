"""Unit tests for ShellyPowerController."""

import sys
import unittest.mock
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add lib/ to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.power import (
    BridgeCommandError,
    BridgeUnreachableError,
    ShellyPowerController,
)

# --- Helpers ---


def make_mock_response(
    status: int = 200,
    json_data: dict | None = None,
) -> MagicMock:
    """Create a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def make_mock_session(responses: list[MagicMock]) -> MagicMock:
    """Create a mock aiohttp.ClientSession that returns responses in order."""
    session = MagicMock()
    session.get = MagicMock(side_effect=responses)
    session.closed = False
    session.close = AsyncMock()
    return session


# --- Tests ---


class TestShellyInit:
    """Test ShellyPowerController initialization."""

    def test_init_default(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        assert ctrl.host == "192.168.1.50"
        assert ctrl.base_url == "http://192.168.1.50"

    def test_init_custom_host(self) -> None:
        ctrl = ShellyPowerController("10.0.0.1")
        assert ctrl.host == "10.0.0.1"
        assert ctrl.base_url == "http://10.0.0.1"

    @pytest.mark.asyncio
    async def test_session_creation(self) -> None:
        """Test that session is created when needed."""
        ctrl = ShellyPowerController("192.168.1.50")
        assert ctrl._session is None

        # Trigger session creation by making a request
        ctrl._generation = 1
        mock_resp = make_mock_response(json_data={"ison": True})
        mock_session = make_mock_session([mock_resp])

        # Patch ClientSession to return our mock
        with unittest.mock.patch("aiohttp.ClientSession", return_value=mock_session):
            await ctrl.power_on()
            # Session should have been created
            assert ctrl._session is not None


class TestDetectGeneration:
    """Test Shelly generation detection."""

    @pytest.mark.asyncio
    async def test_gen1_detection(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        mock_resp = make_mock_response(
            json_data={"type": "SHPLG-S", "mac": "AABBCC", "auth": False}
        )
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        gen = await ctrl.detect_generation()
        assert gen == 1

    @pytest.mark.asyncio
    async def test_gen2_detection(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        mock_resp = make_mock_response(json_data={"gen": 2, "type": "SHPLG-S", "mac": "AABBCC"})
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        gen = await ctrl.detect_generation()
        assert gen == 2

    @pytest.mark.asyncio
    async def test_generation_cached(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1
        # Should not make any HTTP request
        gen = await ctrl.detect_generation()
        assert gen == 1


class TestPowerOn:
    """Test power_on method."""

    @pytest.mark.asyncio
    async def test_gen1_power_on(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1

        mock_resp = make_mock_response(json_data={"ison": True, "has_timer": False})
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        result = await ctrl.power_on()
        assert result is True

    @pytest.mark.asyncio
    async def test_gen2_power_on(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 2

        mock_set_resp = make_mock_response(json_data={"was_on": False})
        mock_status_resp = make_mock_response(json_data={"output": True})
        mock_session = make_mock_session([mock_set_resp, mock_status_resp])
        ctrl._session = mock_session

        result = await ctrl.power_on()
        assert result is True


class TestPowerOff:
    """Test power_off method."""

    @pytest.mark.asyncio
    async def test_gen1_power_off(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1

        mock_resp = make_mock_response(json_data={"ison": False, "has_timer": False})
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        result = await ctrl.power_off()
        assert result is True

    @pytest.mark.asyncio
    async def test_gen1_power_off_failure(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1

        mock_resp = make_mock_response(
            json_data={"ison": True}  # Still on = failure
        )
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        result = await ctrl.power_off()
        assert result is False

    @pytest.mark.asyncio
    async def test_gen2_power_off(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 2

        mock_set_resp = make_mock_response(json_data={"was_on": True})
        mock_status_resp = make_mock_response(json_data={"output": False})
        mock_session = make_mock_session([mock_set_resp, mock_status_resp])
        ctrl._session = mock_session

        result = await ctrl.power_off()
        assert result is True


class TestPowerCycle:
    """Test power_cycle method."""

    @pytest.mark.asyncio
    async def test_power_cycle_success(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1

        off_resp = make_mock_response(json_data={"ison": False})
        on_resp = make_mock_response(json_data={"ison": True})
        mock_session = make_mock_session([off_resp, on_resp])
        ctrl._session = mock_session

        # Use a very short off time for testing
        result = await ctrl.power_cycle(off_seconds=0.01)
        assert result is True

    @pytest.mark.asyncio
    async def test_power_cycle_off_fails(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1

        off_resp = make_mock_response(json_data={"ison": True})  # off failed
        mock_session = make_mock_session([off_resp])
        ctrl._session = mock_session

        result = await ctrl.power_cycle(off_seconds=0.01)
        assert result is False


class TestFactoryReset:
    """Test factory_reset_cycle method."""

    @pytest.mark.asyncio
    async def test_factory_reset_success(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1

        # 3 cycles = 3 off + 3 on = 6 responses
        responses = []
        for _ in range(3):
            responses.append(make_mock_response(json_data={"ison": False}))
            responses.append(make_mock_response(json_data={"ison": True}))
        mock_session = make_mock_session(responses)
        ctrl._session = mock_session

        result = await ctrl.factory_reset_cycle(cycles=3, interval=0.01)
        assert result is True


class TestIsReachable:
    """Test is_reachable method."""

    @pytest.mark.asyncio
    async def test_reachable(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        mock_resp = make_mock_response(json_data={"type": "SHPLG-S"})
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        assert await ctrl.is_reachable() is True

    @pytest.mark.asyncio
    async def test_unreachable(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        import aiohttp

        mock_session = MagicMock()
        mock_get = MagicMock()
        mock_get.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("Connection refused"))
        mock_get.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_get)
        mock_session.closed = False
        mock_session.close = AsyncMock()
        ctrl._session = mock_session

        assert await ctrl.is_reachable() is False


class TestErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_http_error_raises_command_error(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1

        mock_resp = make_mock_response(status=500)
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        with pytest.raises(BridgeCommandError):
            await ctrl.power_on()

    @pytest.mark.asyncio
    async def test_connection_error_raises_unreachable(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1
        import aiohttp

        mock_session = MagicMock()
        mock_get = MagicMock()
        mock_get.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("refused"))
        mock_get.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = MagicMock(return_value=mock_get)
        mock_session.closed = False
        ctrl._session = mock_session

        with pytest.raises(BridgeUnreachableError):
            await ctrl.power_on()


class TestClose:
    """Test session cleanup."""

    @pytest.mark.asyncio
    async def test_close_session(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        ctrl._session = mock_session

        await ctrl.close()
        mock_session.close.assert_awaited_once()
        assert ctrl._session is None

    @pytest.mark.asyncio
    async def test_close_no_session(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        # Should not raise
        await ctrl.close()


class TestGetStatus:
    """Test get_status method."""

    @pytest.mark.asyncio
    async def test_gen1_status(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1

        status_data = {"ison": True, "has_timer": False, "power": 5.2}
        mock_resp = make_mock_response(json_data=status_data)
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        status = await ctrl.get_status()
        assert status["ison"] is True
        assert status["power"] == 5.2

    @pytest.mark.asyncio
    async def test_gen2_status(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 2

        status_data = {"output": True, "id": 0}
        mock_resp = make_mock_response(json_data=status_data)
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        status = await ctrl.get_status()
        assert status["output"] is True

    @pytest.mark.asyncio
    async def test_is_on_true(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1

        mock_resp = make_mock_response(json_data={"ison": True})
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        assert await ctrl.is_on() is True

    @pytest.mark.asyncio
    async def test_gen2_is_on_true(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 2

        mock_resp = make_mock_response(json_data={"output": True})
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        assert await ctrl.is_on() is True

    @pytest.mark.asyncio
    async def test_is_on_false(self) -> None:
        ctrl = ShellyPowerController("192.168.1.50")
        ctrl._generation = 1

        mock_resp = make_mock_response(json_data={"ison": False})
        mock_session = make_mock_session([mock_resp])
        ctrl._session = mock_session

        assert await ctrl.is_on() is False


class TestContextManager:
    """Test async context manager support."""

    @pytest.mark.asyncio
    async def test_context_manager_enter_and_exit(self) -> None:
        """Test that async context manager properly enters and exits."""
        ctrl = ShellyPowerController("192.168.1.50")
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        ctrl._session = mock_session

        async with ctrl as context_ctrl:
            assert context_ctrl is ctrl

        # Session should be closed after exiting context
        mock_session.close.assert_awaited_once()
        assert ctrl._session is None
