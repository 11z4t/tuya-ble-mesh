"""GATT Mesh Proxy link (service 0x1828) shared by a whole SIG Mesh network.

``GattProxyLink`` owns one BLE connection to one proxy node: connect,
subscribe to Proxy Data Out, paced writes to Proxy Data In, and disconnect
notification. It knows nothing about keys or nodes — ``MeshNetwork`` feeds
it encrypted proxy PDUs and receives raw notifications.

SECURITY: only addresses and lengths are logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakDBusError, BleakError

from tuya_ble_mesh.exceptions import MeshConnectionError

_LOGGER = logging.getLogger(__name__)

SIG_MESH_PROXY_SERVICE = "00001828-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROXY_DATA_IN = "00002add-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROXY_DATA_OUT = "00002ade-0000-1000-8000-00805f9b34fb"

# ESPHome proxies drop bursts of write-without-response; keep writes apart
MIN_WRITE_INTERVAL = 0.02

BleDeviceCallback = Callable[[str], Any]
NotifyHandler = Callable[[bytes], None]
DisconnectHandler = Callable[["GattProxyLink"], None]


def find_proxy_characteristics(client: Any) -> tuple[Any, Any] | None:
    """Return the (Data In, Data Out) characteristics of the Mesh Proxy service.

    Stale service caches can expose the service twice; the last one is live.
    Returns None when services cannot be enumerated (callers then address the
    characteristics by UUID).
    """
    try:
        services = list(client.services)
    except (TypeError, AttributeError, BleakError):
        return None
    found: tuple[Any, Any] | None = None
    for service in services:
        if str(getattr(service, "uuid", "")).lower() != SIG_MESH_PROXY_SERVICE:
            continue
        data_in = data_out = None
        for char in getattr(service, "characteristics", []):
            uuid = str(getattr(char, "uuid", "")).lower()
            if uuid == SIG_MESH_PROXY_DATA_IN:
                data_in = char
            elif uuid == SIG_MESH_PROXY_DATA_OUT:
                data_out = char
        if data_in is not None and data_out is not None:
            found = (data_in, data_out)
    return found


class GattProxyLink:
    """One GATT connection to a SIG Mesh proxy node."""

    def __init__(
        self,
        address: str,
        on_notify: NotifyHandler,
        on_disconnect: DisconnectHandler,
        *,
        ble_device_callback: BleDeviceCallback | None = None,
    ) -> None:
        """Create an unconnected link.

        Args:
            address: Proxy node MAC.
            on_notify: Called with each raw Proxy Data Out notification.
            on_disconnect: Called once when an established link drops.
            ble_device_callback: ``address -> BLEDevice`` resolver (HA
                bluetooth); when None, BleakScanner is used.
        """
        self.address = address.upper()
        self._on_notify = on_notify
        self._on_disconnect = on_disconnect
        self._ble_device_callback = ble_device_callback
        self._client: Any = None
        self._data_in: Any = SIG_MESH_PROXY_DATA_IN
        self._data_out: Any = SIG_MESH_PROXY_DATA_OUT
        self._write_lock = asyncio.Lock()
        self._last_write = 0.0
        self._closing = False

    @property
    def is_connected(self) -> bool:
        """Return True while the GATT connection is up."""
        return self._client is not None and bool(getattr(self._client, "is_connected", False))

    @property
    def rssi(self) -> int | None:
        """Return the connection RSSI if the backend reports it."""
        return getattr(self._client, "rssi", None) if self._client is not None else None

    async def connect(self, timeout: float) -> None:
        """Connect and subscribe to Proxy Data Out.

        Raises:
            MeshConnectionError: If the node can't be found or connected.
        """
        self._closing = False
        if self._ble_device_callback is not None:
            device = self._ble_device_callback(self.address)
        else:
            device = await BleakScanner.find_device_by_address(self.address, timeout=timeout)
        if device is None:
            msg = f"Proxy node {self.address} not found"
            raise MeshConnectionError(msg)
        try:
            if self._ble_device_callback is not None:
                from bleak_retry_connector import (
                    BleakClientWithServiceCache,
                    establish_connection,
                )

                client: Any = await asyncio.wait_for(
                    establish_connection(
                        BleakClientWithServiceCache,
                        device,
                        f"SIG Mesh proxy {self.address}",
                        disconnected_callback=self._handle_disconnect,
                        max_attempts=2,
                    ),
                    timeout=timeout,
                )
            else:
                client = BleakClient(
                    device, timeout=timeout, disconnected_callback=self._handle_disconnect
                )
                await client.connect()
            chars = find_proxy_characteristics(client)
            if chars is not None:
                self._data_in, self._data_out = chars
            await client.start_notify(self._data_out, self._handle_notify)
        except (BleakError, BleakDBusError, OSError, TimeoutError) as exc:
            msg = f"Proxy link to {self.address} failed: {type(exc).__name__}"
            raise MeshConnectionError(msg) from exc
        self._client = client
        _LOGGER.info("Mesh proxy link up via %s", self.address)

    async def write(self, proxy_pdu: bytes) -> None:
        """Write one proxy PDU, spaced at least ``MIN_WRITE_INTERVAL`` apart.

        Raises:
            MeshConnectionError: If the link is down or the write fails.
        """
        async with self._write_lock:
            if self._client is None:
                msg = f"Proxy link to {self.address} is down"
                raise MeshConnectionError(msg)
            gap = MIN_WRITE_INTERVAL - (time.monotonic() - self._last_write)
            if gap > 0:
                await asyncio.sleep(gap)
            try:
                await self._client.write_gatt_char(self._data_in, proxy_pdu, response=False)
            except (BleakError, BleakDBusError, OSError) as exc:
                msg = f"Write to proxy {self.address} failed: {type(exc).__name__}"
                raise MeshConnectionError(msg) from exc
            finally:
                self._last_write = time.monotonic()

    async def disconnect(self) -> None:
        """Close the link without firing ``on_disconnect``."""
        self._closing = True
        client, self._client = self._client, None
        if client is None:
            return
        with contextlib.suppress(BleakError, BleakDBusError, OSError, EOFError):
            await client.stop_notify(self._data_out)
        with contextlib.suppress(BleakError, BleakDBusError, OSError, EOFError):
            await client.disconnect()
        _LOGGER.info("Mesh proxy link via %s closed", self.address)

    def _handle_notify(self, _sender: Any, data: bytearray) -> None:
        self._on_notify(bytes(data))

    def _handle_disconnect(self, _client: Any) -> None:
        was_up = self._client is not None
        self._client = None
        if self._closing or not was_up:
            return
        _LOGGER.warning("Mesh proxy link via %s dropped", self.address)
        self._on_disconnect(self)
