"""Power control for BLE devices via Shelly smart plugs.

Supports both Gen1 (Shelly Plug S, Shelly 1) and Gen2 (Shelly Plus)
devices with automatic generation detection.

Shelly IP is configuration, NOT a secret.
Shelly auth credentials (if enabled) must go in 1Password.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from tuya_ble_mesh.const import DEFAULT_POWER_TIMEOUT
from tuya_ble_mesh.exceptions import PowerControlError

_LOGGER = logging.getLogger(__name__)


# --- Custom exceptions ---


class BridgeUnreachableError(PowerControlError):  # type: ignore[misc]
    """Bridge device is not reachable on the network."""


class BridgeCommandError(PowerControlError):  # type: ignore[misc]
    """Bridge device returned an error for a command."""


class BridgePowerController:
    """Controls power to BLE device via smart plug bridge.

    Supports Gen1 and Gen2 devices with auto-detection.
    """

    def __init__(self, host: str, timeout: float = DEFAULT_POWER_TIMEOUT) -> None:
        if timeout <= 0:
            msg = f"Timeout must be positive, got {timeout}"
            raise PowerControlError(msg)

        self._host = host
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        self._generation: int | None = None

    @property
    def host(self) -> str:
        """Return the bridge device host.

        Returns:
            str: Bridge device host.
        """
        return self._host

    @property
    def base_url(self) -> str:
        """Return the base URL for the bridge device.

        Returns:
            str: Base URL for the bridge device.
        """
        return f"http://{self._host}"

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _request(self, path: str) -> dict[str, Any]:
        """Make HTTP GET request to bridge device."""
        url = f"{self.base_url}{path}"
        session = await self._get_session()
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise BridgeCommandError(f"HTTP {resp.status} from {path}")
                return await resp.json(content_type=None)  # type: ignore[no-any-return]
        except aiohttp.ClientError as exc:
            raise BridgeUnreachableError(f"Cannot reach bridge at {self._host}: {exc}") from exc

    async def detect_generation(self) -> int:
        """Auto-detect bridge generation (1 or 2) via /shelly endpoint."""
        if self._generation is not None:
            return self._generation

        info = await self._request("/shelly")
        if "gen" in info and info["gen"] >= 2:
            self._generation = 2
        else:
            self._generation = 1

        _LOGGER.info("Bridge at %s: Gen%d", self._host, self._generation)
        return self._generation

    async def power_off(self) -> bool:
        """Turn off relay. Returns True if relay is confirmed off."""
        gen = await self.detect_generation()
        if gen == 1:
            result = await self._request("/relay/0?turn=off")
            return result.get("ison") is False
        else:
            await self._request("/rpc/Switch.Set?id=0&on=false")
            status = await self._request("/rpc/Switch.GetStatus?id=0")
            return status.get("output") is False

    async def power_on(self) -> bool:
        """Turn on relay. Returns True if relay is confirmed on."""
        gen = await self.detect_generation()
        if gen == 1:
            result = await self._request("/relay/0?turn=on")
            return result.get("ison") is True
        else:
            await self._request("/rpc/Switch.Set?id=0&on=true")
            status = await self._request("/rpc/Switch.GetStatus?id=0")
            return status.get("output") is True

    async def get_status(self) -> dict[str, Any]:
        """Get current relay status."""
        gen = await self.detect_generation()
        if gen == 1:
            return await self._request("/relay/0")
        else:
            return await self._request("/rpc/Switch.GetStatus?id=0")

    async def is_on(self) -> bool:
        """Check if relay is currently on."""
        gen = await self.detect_generation()
        status = await self.get_status()
        if gen == 1:
            return bool(status.get("ison", False))
        else:
            return bool(status.get("output", False))

    async def power_cycle(self, off_seconds: float = 5.0) -> bool:
        """Power cycle: off, wait, on. Returns True if successful."""
        _LOGGER.info("Power cycling (%.1fs off time)", off_seconds)

        if not await self.power_off():
            return False

        await asyncio.sleep(off_seconds)

        return await self.power_on()

    async def factory_reset_cycle(
        self,
        cycles: int = 5,
        interval: float = 1.0,
    ) -> bool:
        """Rapid power cycling for Malmbergs factory reset.

        Malmbergs devices factory reset when power cycled 3-5 times quickly.
        """
        _LOGGER.info("Factory reset: %d cycles, %.1fs interval", cycles, interval)

        for i in range(cycles):
            _LOGGER.info("Cycle %d/%d: OFF", i + 1, cycles)
            await self.power_off()
            await asyncio.sleep(interval)

            _LOGGER.info("Cycle %d/%d: ON", i + 1, cycles)
            await self.power_on()
            if i < cycles - 1:
                await asyncio.sleep(interval)

        return True

    async def is_reachable(self) -> bool:
        """Check if bridge device responds to /shelly."""
        try:
            await self._request("/shelly")
            return True
        except (BridgeUnreachableError, BridgeCommandError):
            return False

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> BridgePowerController:
        """Async context manager entry."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Async context manager exit — close HTTP session."""
        await self.close()
