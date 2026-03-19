"""Connection handling for SIG Mesh provisioner (PB-GATT).

Implements BLE device scanning, connection establishment, and connection
cleanup for the provisioning bearer. Handles retry logic, exponential backoff,
and BlueZ connection slot management.

Rule S5: Async everywhere.
Rule S6: Type hints on every function.
Rule S7: ProvisioningError for all failures.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

from tuya_ble_mesh.const import (
    PROVISIONING_SCAN_TIMEOUT_BUFFER,
    PROVISIONING_SERVICE_DISCOVERY_TIMEOUT,
    SCAN_TIMEOUT_BUFFER,
)
from tuya_ble_mesh.exceptions import ProvisioningError
from tuya_ble_mesh.logging_context import MeshLogAdapter

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# PB-GATT Provisioning Service UUID (Mesh Profile 7.1)
PROV_SERVICE = "00001827-0000-1000-8000-00805f9b34fb"

# Timeout for bluetoothctl subprocess operations (seconds)
_BLUETOOTHCTL_TIMEOUT = 5.0

# BLE adapter slot release delay after disconnect (seconds)
_BLE_SLOT_RELEASE_DELAY = 1.0  # Increased from 0.5s — see

# BlueZ device cache processing delay after bluetoothctl remove (seconds)
_BLUEZ_CACHE_SETTLE_DELAY = 0.5

# Exponential backoff parameters for retries
_SCAN_RETRY_BACKOFF_BASE = 2.0
_SCAN_RETRY_BACKOFF_EXPONENT = 1.5
_SCAN_RETRY_MAX_BACKOFF = 10.0

_CONNECT_RETRY_BACKOFF_BASE = 3.0
_CONNECT_RETRY_BACKOFF_EXPONENT = 1.5
_CONNECT_RETRY_MAX_BACKOFF = 15.0

_SLOTS_RETRY_BACKOFF_BASE = 5.0
_SLOTS_RETRY_BACKOFF_EXPONENT = 1.5
_SLOTS_RETRY_MAX_BACKOFF = 20.0


class ProvisionerConnectionMixin:
    """Mixin providing BLE connection handling for SIG Mesh provisioner.

    This mixin must be used with a class that provides:
    - self._adapter: Optional[str] - BLE adapter name
    - self._ble_device_callback: Optional[Callable[[str], BLEDevice]]
    - self._ble_connect_callback: Optional[Callable[[BLEDevice], Awaitable[BleakClient]]]
    """

    _adapter: str | None
    _ble_device_callback: Callable[[str], BLEDevice] | None
    _ble_connect_callback: Callable[[Any], Awaitable[BleakClient]] | None

    async def _cleanup_stale_connections(self, address: str) -> None:
        """Clean up any stale BLE connections for this device.

        Uses bluetoothctl to remove the device from BlueZ cache, forcing
        a fresh connection. This prevents BleakOutOfConnectionSlotsError
        when old connections weren't properly released.

        Args:
            address: BLE MAC address to clean up.
        """
        address = address.upper()
        try:
            _LOGGER.debug("Removing stale BlueZ device entry for %s", address)
            # Use async subprocess to avoid blocking the event loop
            process = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                "remove",
                address,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=_BLUETOOTHCTL_TIMEOUT
                )
                if process.returncode == 0:
                    _LOGGER.debug("Removed stale device %s from BlueZ", address)
                else:
                    stderr_text = stderr.decode().strip() if stderr else "no error"
                    _LOGGER.debug(
                        "Device %s not in BlueZ cache (or remove failed): %s",
                        address,
                        stderr_text,
                    )
            except TimeoutError:
                _LOGGER.debug("bluetoothctl remove timed out for %s", address)
                process.kill()
                await process.wait()
            # Give BlueZ time to process the removal
            await asyncio.sleep(_BLUEZ_CACHE_SETTLE_DELAY)
        except FileNotFoundError:
            _LOGGER.debug("bluetoothctl not found, skipping cleanup")
        except (TimeoutError, OSError) as exc:
            _LOGGER.debug(
                "Failed to clean up stale connection for %s: %s",
                address,
                exc,
            )

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
        client: BleakClient | None = None

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
                    scan_kwargs: dict[str, Any] = {"timeout": timeout}
                    if self._adapter is not None:
                        scan_kwargs["adapter"] = self._adapter
                    _LOGGER.debug(
                        "Scanning for device %s (timeout=%.1fs, adapter=%s)",
                        address,
                        timeout,
                        self._adapter or "default",
                    )
                    device = await asyncio.wait_for(
                        BleakScanner.find_device_by_address(address, **scan_kwargs),
                        timeout=timeout + SCAN_TIMEOUT_BUFFER,
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
                    backoff = min(
                        _SCAN_RETRY_BACKOFF_BASE * (_SCAN_RETRY_BACKOFF_EXPONENT ** (attempt - 1)),
                        _SCAN_RETRY_MAX_BACKOFF,
                    )
                    await asyncio.sleep(backoff)
                    continue

                _LOGGER.debug("Device %s found, attempting connection...", address)

                # Step 2: Connect to device
                if self._ble_connect_callback is not None:
                    # Use caller-supplied connector (e.g. bleak-retry-connector for HA)
                    client = await asyncio.wait_for(
                        self._ble_connect_callback(device),
                        timeout=timeout + PROVISIONING_SCAN_TIMEOUT_BUFFER,
                    )
                else:
                    client_kwargs: dict[str, Any] = {"timeout": timeout}
                    if self._adapter is not None:
                        client_kwargs["adapter"] = self._adapter
                    client = BleakClient(device, **client_kwargs)
                    await asyncio.wait_for(
                        client.connect(),
                        timeout=timeout,
                    )

                # Step 3: Verify connection and check services
                if client is None:
                    raise ProvisioningError("BLE connect callback returned None client")
                if not client.is_connected:
                    connect_failures += 1
                    msg = "BleakClient reported connected but is_connected=False"
                    raise ProvisioningError(msg)

                # Verify Provisioning Service is present
                services = None
                try:
                    # HA's HaBleakClientWrapper uses .services property, not .get_services()
                    if hasattr(client, "get_services"):
                        services = await asyncio.wait_for(
                            client.get_services(),
                            timeout=PROVISIONING_SERVICE_DISCOVERY_TIMEOUT,
                        )
                    else:
                        services = client.services
                    if services and not any(
                        str(s.uuid) == PROV_SERVICE
                        for s in (
                            services.services.values()
                            if hasattr(services, "services")
                            else services
                        )
                    ):
                        msg = f"Device {address} does not expose Provisioning Service (0x1827)"
                        raise ProvisioningError(msg)
                except TimeoutError:
                    _LOGGER.warning("Service enumeration timed out, continuing anyway")

                svc_count = (
                    len(services.services)
                    if services is not None and hasattr(services, "services")
                    else 0
                )
                _LOGGER.info(
                    "PB-GATT connected to %s (MTU=%d, services=%d)",
                    address,
                    client.mtu_size,
                    svc_count,
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
                #  Ensure client is disconnected before retry
                if client is not None:
                    with contextlib.suppress(OSError, asyncio.TimeoutError):
                        await client.disconnect()
                    client = None
                #  Longer backoff to allow connection slot release
                backoff = min(
                    _CONNECT_RETRY_BACKOFF_BASE
                    * (_CONNECT_RETRY_BACKOFF_EXPONENT ** (attempt - 1)),
                    _CONNECT_RETRY_MAX_BACKOFF,
                )
                await asyncio.sleep(backoff)
            except (OSError, BleakError) as exc:
                last_exc = exc
                connect_failures += 1

                #  Special handling for out-of-slots errors
                exc_str = str(exc).lower()
                is_slot_error = (
                    "out of connection slots" in exc_str
                    or "bleakoutofconnectionslotserror" in exc_str
                    or "no backend with an available connection slot" in exc_str
                )

                #  Ensure client is disconnected before retry
                if client is not None:
                    with contextlib.suppress(OSError, asyncio.TimeoutError):
                        await client.disconnect()
                    client = None

                if is_slot_error:
                    out_of_slots_failures += 1
                    _LOGGER.warning(
                        "Connect attempt %d/%d failed: BLE adapter out of connection slots. "
                        "Cleaning up and waiting for slots to be released...",
                        attempt,
                        max_retries,
                    )
                    #  Force cleanup to free connection slot
                    await self._cleanup_stale_connections(address)
                    # Longer backoff when slots are exhausted
                    backoff = min(
                        _SLOTS_RETRY_BACKOFF_BASE
                        * (_SLOTS_RETRY_BACKOFF_EXPONENT ** (attempt - 1)),
                        _SLOTS_RETRY_MAX_BACKOFF,
                    )
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
                    backoff = min(
                        _CONNECT_RETRY_BACKOFF_BASE
                        * (_CONNECT_RETRY_BACKOFF_EXPONENT ** (attempt - 1)),
                        _CONNECT_RETRY_MAX_BACKOFF,
                    )
                    await asyncio.sleep(backoff)

        # Build detailed error message
        error_details = (
            f"scan_failures={scan_failures}, "
            f"connect_failures={connect_failures}, "
            f"out_of_slots={out_of_slots_failures}"
        )
        msg = f"Failed to connect to {address} after {max_retries} attempts ({error_details}). "
        if out_of_slots_failures > 0:
            msg += (
                "BLE adapter ran out of connection slots. "
                "Try: 1) Reduce number of concurrent BLE connections, "
                "2) Restart Bluetooth service, or 3) Use a different BLE adapter. "
            )
        else:
            msg += "Check device is in range, not already provisioned, and advertising. "
        msg += f"Last error: {type(last_exc).__name__ if last_exc else 'unknown'}"
        raise ProvisioningError(msg) from last_exc
