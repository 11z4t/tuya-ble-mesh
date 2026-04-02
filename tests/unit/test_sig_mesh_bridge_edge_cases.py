"""Unit tests for sig_mesh_bridge.py — edge cases not covered by test_sig_mesh_bridge.py.

Covers:
  BridgeHTTPMixin._get_session: 65-67 (create when None/closed)
  BridgeHTTPMixin._close_session: 72-73 (close when open)
  BridgeHTTPMixin._http_get: 77-85 (success + timeout error)
  BridgeHTTPMixin._http_post: 94-104 (success + timeout error)
  SIGMeshBridgeDevice.connect: 262 (retry sleep)
  SIGMeshBridgeDevice.send_power: 299-310 (SIGMeshError retry), 319-322 (CancelledError)
  SIGMeshBridgeDevice._send_and_wait: 369-385 (timeout → disconnect callbacks)
  TelinkBridgeDevice.rssi: 487 (always None)
  TelinkBridgeDevice.connect: 547 (retry sleep)
  TelinkBridgeDevice._fire_disconnect: 562-565 (CancelledError re-raise)
  TelinkBridgeDevice._fire_status: 584-587 (CancelledError re-raise)
  TelinkBridgeDevice._send_telink_cmd: 714-726 (retry loop)
  TelinkBridgeDevice._send_telink_cmd_once: 757-759, 761 (SIGMeshError paths)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

from tuya_ble_mesh.exceptions import MeshConnectionError, SIGMeshError
from tuya_ble_mesh.sig_mesh_bridge import SIGMeshBridgeDevice, TelinkBridgeDevice


def _make_sig() -> SIGMeshBridgeDevice:
    return SIGMeshBridgeDevice("DC:23:4F:10:52:C4", 0x00B0, "192.168.5.10", 8099)


def _make_telink() -> TelinkBridgeDevice:
    return TelinkBridgeDevice("DC:23:4D:21:43:A5", "192.168.5.10", 8099)


def _mock_session(
    *,
    json_return: dict | None = None,
    side_effect: Exception | None = None,
) -> MagicMock:
    """Build a mock aiohttp ClientSession for context-manager usage."""
    resp = AsyncMock()
    if side_effect is not None:
        resp.json = AsyncMock(side_effect=side_effect)
    else:
        resp.json = AsyncMock(return_value=json_return or {})

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)

    session = MagicMock()
    session.closed = False
    session.get = MagicMock(return_value=cm)
    session.post = MagicMock(return_value=cm)
    return session


# ── BridgeHTTPMixin._get_session ───────────────────────────────────────────────


class TestGetSession:
    """Lines 65-67: session created when None or closed."""

    @pytest.mark.asyncio
    async def test_creates_session_when_none(self) -> None:
        """Line 65-67: _session is None → new session created."""
        dev = _make_sig()
        assert dev._session is None
        with patch("tuya_ble_mesh.sig_mesh_bridge.aiohttp.ClientSession") as mock_cls:
            mock_session = MagicMock()
            mock_session.closed = False
            mock_cls.return_value = mock_session
            session = await dev._get_session()
        assert session is mock_session
        assert dev._session is mock_session

    @pytest.mark.asyncio
    async def test_creates_new_session_when_closed(self) -> None:
        """Lines 65-67: _session.closed is True → new session created."""
        dev = _make_sig()
        old_session = MagicMock()
        old_session.closed = True
        dev._session = old_session
        with patch("tuya_ble_mesh.sig_mesh_bridge.aiohttp.ClientSession") as mock_cls:
            new_session = MagicMock()
            new_session.closed = False
            mock_cls.return_value = new_session
            session = await dev._get_session()
        assert session is new_session
        assert session is not old_session


# ── BridgeHTTPMixin._close_session ────────────────────────────────────────────


class TestCloseSession:
    """Lines 72-73: session closed and set to None."""

    @pytest.mark.asyncio
    async def test_closes_open_session(self) -> None:
        dev = _make_sig()
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.close = AsyncMock()
        dev._session = mock_session
        await dev._close_session()
        mock_session.close.assert_called_once()
        assert dev._session is None

    @pytest.mark.asyncio
    async def test_skips_when_already_closed(self) -> None:
        dev = _make_sig()
        mock_session = MagicMock()
        mock_session.closed = True
        mock_session.close = AsyncMock()
        dev._session = mock_session
        await dev._close_session()
        mock_session.close.assert_not_called()


# ── BridgeHTTPMixin._http_get ──────────────────────────────────────────────────


class TestHttpGet:
    """Lines 77-85: _http_get success and error paths."""

    @pytest.mark.asyncio
    async def test_success_returns_json(self) -> None:
        """Lines 77-82: successful GET returns parsed JSON."""
        dev = _make_sig()
        dev._session = _mock_session(json_return={"status": "ok"})
        result = await dev._http_get("/health")
        assert result == {"status": "ok"}

    @pytest.mark.asyncio
    async def test_timeout_raises_mesh_connection_error(self) -> None:
        """Lines 83-85: TimeoutError → MeshConnectionError."""
        dev = _make_sig()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=TimeoutError())
        cm.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=cm)
        dev._session = mock_session
        with pytest.raises(MeshConnectionError, match="Bridge HTTP GET"):
            await dev._http_get("/health")

    @pytest.mark.asyncio
    async def test_client_error_raises_mesh_connection_error(self) -> None:
        """Lines 83-85: aiohttp.ClientError → MeshConnectionError."""
        import aiohttp

        dev = _make_sig()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=aiohttp.ClientConnectionError("fail"))
        cm.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.get = MagicMock(return_value=cm)
        dev._session = mock_session
        with pytest.raises(MeshConnectionError, match="Bridge HTTP GET"):
            await dev._http_get("/health")


# ── BridgeHTTPMixin._http_post ─────────────────────────────────────────────────


class TestHttpPost:
    """Lines 94-104: _http_post success and error paths."""

    @pytest.mark.asyncio
    async def test_success_returns_json(self) -> None:
        """Lines 94-101: successful POST returns parsed JSON."""
        dev = _make_sig()
        dev._session = _mock_session(json_return={"result": "ok"})
        result = await dev._http_post("/command", {"action": "on"})
        assert result == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_timeout_raises_mesh_connection_error(self) -> None:
        """Lines 102-104: TimeoutError → MeshConnectionError."""
        dev = _make_sig()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=TimeoutError())
        cm.__aexit__ = AsyncMock(return_value=False)
        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = MagicMock(return_value=cm)
        dev._session = mock_session
        with pytest.raises(MeshConnectionError, match="Bridge HTTP POST"):
            await dev._http_post("/command", {})


# ── SIGMeshBridgeDevice.connect retry ─────────────────────────────────────────


class TestSIGConnectRetry:
    """Line 262: connect retries with sleep on error."""

    @pytest.mark.asyncio
    async def test_connect_retries_with_sleep(self) -> None:
        """Line 262: first attempt fails → sleep → second attempt succeeds."""
        dev = _make_sig()
        results = [
            MeshConnectionError("fail"),
            {"status": "ok"},
        ]

        async def fake_http_get(path: str, timeout: float = 5.0) -> dict:
            val = results.pop(0)
            if isinstance(val, Exception):
                raise val
            return val  # type: ignore[return-value]

        with (
            patch.object(dev, "_http_get", side_effect=fake_http_get),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await dev.connect(max_retries=2)

        assert dev._connected is True


# ── SIGMeshBridgeDevice.send_power retry ──────────────────────────────────────


class TestSendPowerRetry:
    """Lines 299-310: SIGMeshError on first attempt → backoff → retry succeeds."""

    @pytest.mark.asyncio
    async def test_sigmesherror_retried_then_succeeds(self) -> None:
        """Lines 299-310: first _send_and_wait raises SIGMeshError, second succeeds."""
        dev = _make_sig()
        dev._connected = True
        call_count = 0

        async def fake_send_and_wait(action: str) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise SIGMeshError("transient")
            return {"success": True, "status": "ON"}

        with (
            patch.object(dev, "_send_and_wait", side_effect=fake_send_and_wait),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await dev.send_power(True, max_retries=2)

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_onoff_callback_cancelled_error_reraised(self) -> None:
        """Lines 319-322: CancelledError from onoff callback propagates."""
        dev = _make_sig()
        dev._connected = True

        def raise_cancelled(on: bool) -> None:
            raise asyncio.CancelledError()

        dev.register_onoff_callback(raise_cancelled)

        with (
            patch.object(
                dev,
                "_send_and_wait",
                new_callable=AsyncMock,
                return_value={"success": True, "status": "ON"},
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await dev.send_power(True)


# ── SIGMeshBridgeDevice._send_and_wait timeout ────────────────────────────────


class TestSendAndWaitTimeout:
    """Lines 369-385: all poll attempts exhausted → disconnect callbacks fired."""

    @pytest.mark.asyncio
    async def test_poll_timeout_fires_disconnect_callbacks(self) -> None:
        """Lines 377-384: timeout → _connected=False, disconnect callbacks called."""
        dev = _make_sig()
        dev._connected = True

        called: list[bool] = []

        def on_disconnect() -> None:
            called.append(True)

        dev.register_disconnect_callback(on_disconnect)

        with (
            patch.object(
                dev,
                "_http_post",
                new_callable=AsyncMock,
                return_value={},
            ),
            patch.object(
                dev,
                "_http_get",
                new_callable=AsyncMock,
                return_value={"action": "on"},  # No "timestamp" key → never matches
            ),
            patch("tuya_ble_mesh.sig_mesh_bridge._MAX_POLL_ATTEMPTS", 2),
            patch("tuya_ble_mesh.sig_mesh_bridge._POLL_INTERVAL", 0),
        ):
            result = await dev._send_and_wait("on")

        assert result["success"] is False
        assert dev._connected is False
        assert called == [True]

    @pytest.mark.asyncio
    async def test_poll_timeout_disconnect_callback_cancelled_reraised(self) -> None:
        """Lines 381-382: CancelledError from disconnect callback propagates."""
        dev = _make_sig()
        dev._connected = True

        def raise_cancelled() -> None:
            raise asyncio.CancelledError()

        dev.register_disconnect_callback(raise_cancelled)

        with (
            patch.object(dev, "_http_post", new_callable=AsyncMock, return_value={}),
            patch.object(
                dev,
                "_http_get",
                new_callable=AsyncMock,
                return_value={"action": "on"},
            ),
            patch("tuya_ble_mesh.sig_mesh_bridge._MAX_POLL_ATTEMPTS", 1),
            patch("tuya_ble_mesh.sig_mesh_bridge._POLL_INTERVAL", 0),
            pytest.raises(asyncio.CancelledError),
        ):
            await dev._send_and_wait("on")


# ── TelinkBridgeDevice.rssi ────────────────────────────────────────────────────


class TestTelinkRssi:
    """Line 487: rssi always returns None."""

    def test_rssi_is_none(self) -> None:
        dev = _make_telink()
        assert dev.rssi is None


# ── TelinkBridgeDevice.connect retry ──────────────────────────────────────────


class TestTelinkConnectRetry:
    """Line 547: connect retries with sleep on error."""

    @pytest.mark.asyncio
    async def test_connect_retries_with_sleep(self) -> None:
        """Line 547: first attempt fails → sleep → second attempt succeeds."""
        dev = _make_telink()
        results: list = [
            MeshConnectionError("fail"),
            {"status": "ok"},
        ]

        async def fake_http_get(path: str, timeout: float = 5.0) -> dict:
            val = results.pop(0)
            if isinstance(val, Exception):
                raise val
            return val  # type: ignore[return-value]

        with (
            patch.object(dev, "_http_get", side_effect=fake_http_get),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await dev.connect(max_retries=2)

        assert dev._connected is True
        assert dev._firmware_version == "bridge-telink"


# ── TelinkBridgeDevice._fire_disconnect ───────────────────────────────────────


class TestFireDisconnect:
    """Lines 562-565: CancelledError from disconnect callback propagates."""

    def test_cancelled_error_reraised(self) -> None:
        """Lines 562-565: CancelledError propagates out of _fire_disconnect."""
        dev = _make_telink()

        def raise_cancelled() -> None:
            raise asyncio.CancelledError()

        dev.register_disconnect_callback(raise_cancelled)
        with pytest.raises(asyncio.CancelledError):
            dev._fire_disconnect()


# ── TelinkBridgeDevice._fire_status ───────────────────────────────────────────


class TestFireStatus:
    """Lines 584-587: CancelledError from status callback propagates."""

    def test_cancelled_error_reraised(self) -> None:
        """Lines 584-587: CancelledError propagates out of _fire_status."""
        dev = _make_telink()

        def raise_cancelled(status: object) -> None:
            raise asyncio.CancelledError()

        dev.register_status_callback(raise_cancelled)
        with pytest.raises(asyncio.CancelledError):
            dev._fire_status()


# ── TelinkBridgeDevice._send_telink_cmd retry ─────────────────────────────────


class TestSendTelinkCmdRetry:
    """Lines 714-726: SIGMeshError on first attempt → retry succeeds."""

    @pytest.mark.asyncio
    async def test_sigmesherror_retried_then_succeeds(self) -> None:
        """Lines 714-726: _send_telink_cmd_once raises on first attempt, succeeds on second."""
        dev = _make_telink()
        dev._connected = True
        call_count = 0

        async def fake_once(action: str, params: dict | None = None) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise SIGMeshError("transient")
            return {"success": True}

        with (
            patch.object(dev, "_send_telink_cmd_once", side_effect=fake_once),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            result = await dev._send_telink_cmd("power", max_retries=2)

        assert call_count == 2
        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_raises_after_all_retries_exhausted(self) -> None:
        """Lines 724-726: safety-net raise after all retry loops fail."""
        dev = _make_telink()
        dev._connected = True

        with (
            patch.object(
                dev,
                "_send_telink_cmd_once",
                side_effect=SIGMeshError("always fail"),
            ),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(SIGMeshError, match="always fail"),
        ):
            await dev._send_telink_cmd("power", max_retries=2)


# ── TelinkBridgeDevice._send_telink_cmd_once ──────────────────────────────────


class TestSendTelinkCmdOnce:
    """Lines 757-759, 761: SIGMeshError paths in _send_telink_cmd_once."""

    @pytest.mark.asyncio
    async def test_error_result_raises_sigmesherror(self) -> None:
        """Lines 757-759: result has success=False → SIGMeshError raised."""
        dev = _make_telink()

        with (
            patch.object(dev, "_http_post", new_callable=AsyncMock, return_value={}),
            patch.object(
                dev,
                "_http_get",
                new_callable=AsyncMock,
                return_value={
                    "action": "power",
                    "device_type": "telink",
                    "timestamp": 1,
                    "success": False,
                    "error": "device busy",
                },
            ),
            patch("tuya_ble_mesh.sig_mesh_bridge._POLL_INTERVAL", 0),
            pytest.raises(SIGMeshError, match="device busy"),
        ):
            await dev._send_telink_cmd_once("power")

    @pytest.mark.asyncio
    async def test_sigmesherror_from_poll_reraised(self) -> None:
        """Line 761: SIGMeshError caught inside poll loop is re-raised."""
        dev = _make_telink()

        call_count = 0

        async def fake_http_get(path: str, timeout: float = 5.0) -> dict:
            nonlocal call_count
            call_count += 1
            # First call triggers error result → SIGMeshError raised internally
            return {
                "action": "power",
                "device_type": "telink",
                "timestamp": 1,
                "success": False,
                "error": "test error",
            }

        with (
            patch.object(dev, "_http_post", new_callable=AsyncMock, return_value={}),
            patch.object(dev, "_http_get", side_effect=fake_http_get),
            patch("tuya_ble_mesh.sig_mesh_bridge._POLL_INTERVAL", 0),
            pytest.raises(SIGMeshError, match="test error"),
        ):
            await dev._send_telink_cmd_once("power")
