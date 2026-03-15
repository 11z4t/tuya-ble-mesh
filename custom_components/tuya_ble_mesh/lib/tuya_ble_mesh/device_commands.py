"""High-level command methods for BLE mesh device.

Provides convenient wrappers for common Telink BLE mesh commands:
- Power on/off (0xD2 compact DP)
- Brightness control (white and color modes)
- Color temperature and RGB color
- Light mode switching
- Mesh address assignment and reset
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tuya_ble_mesh.const import (
    COMPACT_DP_BRIGHTNESS,
    COMPACT_DP_POWER,
    DP_TYPE_VALUE,
    TELINK_CMD_COLOR,
    TELINK_CMD_COLOR_BRIGHTNESS,
    TELINK_CMD_DP_WRITE,
    TELINK_CMD_LIGHT_MODE,
    TELINK_CMD_MESH_ADDRESS,
    TELINK_CMD_MESH_RESET,
    TELINK_CMD_WHITE_TEMP,
)
from tuya_ble_mesh.exceptions import ProtocolError
from tuya_ble_mesh.protocol import encode_compact_dp

if TYPE_CHECKING:
    from collections.abc import Awaitable

_LOGGER = logging.getLogger(__name__)


class DeviceCommandsMixin:
    """Mixin providing high-level command methods for MeshDevice.

    This mixin must be used with a class that provides:
    - self._address: str - BLE MAC address
    - self.send_command(opcode: int, params: bytes) -> Awaitable[None]
    """

    _address: str
    send_command: callable[[int, bytes], Awaitable[None]]

    async def send_power(self, on: bool) -> None:
        """Turn the device on or off.

        Uses 0xD2 compact DP with dp_id 121 (confirmed from HCI snoop).

        Args:
            on: True to turn on, False to turn off.
        """
        params = encode_compact_dp(COMPACT_DP_POWER, DP_TYPE_VALUE, 1 if on else 0)
        await self.send_command(TELINK_CMD_DP_WRITE, params)
        _LOGGER.info("Power %s sent to %s", "ON" if on else "OFF", self._address)

    async def send_brightness(self, level: int) -> None:
        """Set the white brightness level.

        Uses 0xD2 compact DP with dp_id 122 (confirmed from HCI snoop).

        Args:
            level: Brightness percentage (1-100).

        Raises:
            ProtocolError: If level is out of range.
        """
        if not 1 <= level <= 100:
            msg = f"Brightness must be 1..100, got {level}"
            raise ProtocolError(msg)
        params = encode_compact_dp(COMPACT_DP_BRIGHTNESS, DP_TYPE_VALUE, level)
        await self.send_command(TELINK_CMD_DP_WRITE, params)
        _LOGGER.info("Brightness %d%% sent to %s", level, self._address)

    async def send_color_temp(self, temp: int) -> None:
        """Set the white color temperature.

        Args:
            temp: Color temperature value (0-255).

        Raises:
            ProtocolError: If temp is out of range.
        """
        if not 0 <= temp <= 0xFF:
            msg = f"Color temp must be 0..255, got {temp}"
            raise ProtocolError(msg)
        await self.send_command(TELINK_CMD_WHITE_TEMP, bytes([temp]))
        _LOGGER.info("Color temp %d sent to %s", temp, self._address)

    async def send_color(self, red: int, green: int, blue: int) -> None:
        """Set the RGB color.

        Args:
            red: Red channel (0-255).
            green: Green channel (0-255).
            blue: Blue channel (0-255).

        Raises:
            ProtocolError: If any channel is out of range.
        """
        for name, val in [("red", red), ("green", green), ("blue", blue)]:
            if not 0 <= val <= 0xFF:
                msg = f"{name} must be 0..255, got {val}"
                raise ProtocolError(msg)
        await self.send_command(TELINK_CMD_COLOR, bytes([red, green, blue]))
        _LOGGER.info("Color (%d,%d,%d) sent to %s", red, green, blue, self._address)

    async def send_color_brightness(self, level: int) -> None:
        """Set the color mode brightness level.

        Args:
            level: Brightness value (0-255).

        Raises:
            ProtocolError: If level is out of range.
        """
        if not 0 <= level <= 0xFF:
            msg = f"Color brightness must be 0..255, got {level}"
            raise ProtocolError(msg)
        await self.send_command(TELINK_CMD_COLOR_BRIGHTNESS, bytes([level]))
        _LOGGER.info("Color brightness %d sent to %s", level, self._address)

    async def send_light_mode(self, mode: int) -> None:
        """Set the light mode.

        Args:
            mode: Light mode (0=white, 1=color, etc.).

        Raises:
            ProtocolError: If mode is out of range.
        """
        if not 0 <= mode <= 0xFF:
            msg = f"Light mode must be 0..255, got {mode}"
            raise ProtocolError(msg)
        await self.send_command(TELINK_CMD_LIGHT_MODE, bytes([mode]))
        _LOGGER.info("Light mode %d sent to %s", mode, self._address)

    async def send_mesh_address(self, new_address: int) -> None:
        """Assign a new mesh address to the device.

        Args:
            new_address: New mesh unicast address (1-0x7FFF).

        Raises:
            ProtocolError: If address is out of range.
        """
        if not 1 <= new_address <= 0x7FFF:
            msg = f"Mesh address must be 1..0x7FFF, got {new_address}"
            raise ProtocolError(msg)
        params = new_address.to_bytes(2, "little")
        await self.send_command(TELINK_CMD_MESH_ADDRESS, params)
        _LOGGER.info("Mesh address 0x%04X sent to %s", new_address, self._address)

    async def send_mesh_reset(self) -> None:
        """Reset the device mesh settings (remove from network)."""
        await self.send_command(TELINK_CMD_MESH_RESET, b"")
        _LOGGER.info("Mesh reset sent to %s", self._address)
