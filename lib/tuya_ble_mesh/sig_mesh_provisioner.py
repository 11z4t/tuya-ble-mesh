"""SIG Mesh PB-GATT Provisioner (Mesh Profile Section 5.4).

Implements provisioning via the PB-GATT bearer:
- Provisioning Service UUID: 0x1827
- Data In: 0x2ADB (write)
- Data Out: 0x2ADC (notify)

Full provisioning exchange:
  Invite → Capabilities → Start → PublicKey → Confirmation
  → Random → ProvisioningData → Complete

Uses FIPS P-256 (ECDH) with No OOB authentication (most common for Tuya
devices).  All provisioning-specific crypto derivations follow the
Mesh Profile Specification Section 5.4.2.

Rule S3: All byte parsing for provisioning PDUs is done in exchange mixin.
Rule S4: Crypto via sig_mesh_crypto (aes_cmac, k1, s1, mesh_aes_ccm_encrypt).
Rule S5: Async everywhere.
Rule S6: Type hints on every function.
Rule S7: ProvisioningError for all failures.

SECURITY: Generated key material is NEVER logged, printed, or included
in exception messages. Only lengths, PDU types, and counts are safe to log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from bleak import BleakClient
from bleak.exc import BleakError
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256R1,
    generate_private_key,
)

from tuya_ble_mesh.const import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_PROVISIONING_TIMEOUT,
)
from tuya_ble_mesh.exceptions import ProvisioningError
from tuya_ble_mesh.logging_context import MeshLogAdapter, mesh_operation
from tuya_ble_mesh.sig_mesh_provisioner_connection import ProvisionerConnectionMixin
from tuya_ble_mesh.sig_mesh_provisioner_exchange import (
    ProvisionerExchangeMixin,
    ProvisioningResult,
    _wrap_provisioning_pdu,
)

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# PB-GATT characteristics (Mesh Profile 7.1)
PROV_DATA_OUT = "00002adc-0000-1000-8000-00805f9b34fb"

# BLE adapter slot release delay after disconnect (seconds)
_BLE_SLOT_RELEASE_DELAY = 1.0  # Increased from 0.5s — see PLAT-506


# Re-export for backward compatibility
__all__ = ["ProvisioningResult", "SIGMeshProvisioner", "_wrap_provisioning_pdu"]


class SIGMeshProvisioner(ProvisionerConnectionMixin, ProvisionerExchangeMixin):
    """PB-GATT provisioner for SIG Mesh devices.

    Implements the full provisioning protocol (Mesh Profile 5.4).
    Uses FIPS P-256 ECDH key exchange and No OOB authentication.

    Usage::

        net_key = os.urandom(16)
        app_key = os.urandom(16)
        provisioner = SIGMeshProvisioner(net_key, app_key, 0x00B0)
        result = await provisioner.provision("DC:23:4F:10:52:C4")
        # result.dev_key contains the device key
    """

    def __init__(
        self,
        net_key: bytes,
        app_key: bytes,
        unicast_addr: int,
        *,
        net_key_index: int = 0,
        iv_index: int = 0,
        flags: int = 0,
        ble_device_callback: Any | None = None,
        ble_connect_callback: Callable[[Any], Awaitable[BleakClient]] | None = None,
        adapter: str | None = None,
    ) -> None:
        """Initialize the provisioner.

        Args:
            net_key: 16-byte network key to provision into the device.
            app_key: 16-byte application key (saved in result for later use).
            unicast_addr: Unicast address to assign to the device.
            net_key_index: Network key index (0-4095, default 0).
            iv_index: Mesh IV Index (default 0).
            flags: Provisioning flags (bit 0=Key Refresh, bit 1=IV Update).
            ble_device_callback: Optional callback(address) → BLEDevice for
                HA Bluetooth proxy support. If None, uses BleakScanner.
            ble_connect_callback: Optional async callback(BLEDevice) →
                connected BleakClient. If provided, used instead of
                BleakClient.connect() directly. Pass a callback that uses
                bleak-retry-connector to avoid the "BleakClient.connect()
                called without bleak-retry-connector" warning in HA.
            adapter: BLE adapter name (e.g. "hci0"). If set, forces scan
                and connect via this specific adapter, bypassing HA's
                habluetooth routing.

        Raises:
            ProvisioningError: If key lengths are invalid.
        """
        if len(net_key) != 16:
            msg = f"net_key must be 16 bytes, got {len(net_key)}"
            raise ProvisioningError(msg)
        if len(app_key) != 16:
            msg = f"app_key must be 16 bytes, got {len(app_key)}"
            raise ProvisioningError(msg)

        self._net_key = net_key
        self._app_key = app_key
        self._unicast_addr = unicast_addr
        self._net_key_index = net_key_index
        self._iv_index = iv_index
        self._flags = flags
        self._ble_device_callback = ble_device_callback
        self._ble_connect_callback = ble_connect_callback
        self._adapter = adapter

        # Generate ECDH P-256 key pair
        self._private_key = generate_private_key(SECP256R1())
        pub = self._private_key.public_key()
        pub_numbers = pub.public_numbers()
        self._our_pub_key_bytes: bytes = pub_numbers.x.to_bytes(32, "big") + pub_numbers.y.to_bytes(
            32, "big"
        )

    async def provision(
        self,
        address: str,
        timeout: float = DEFAULT_PROVISIONING_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> ProvisioningResult:
        """Execute full PB-GATT provisioning with a device.

        Connects to the Provisioning Service (UUID 0x1827), performs the
        full exchange, and disconnects. After this call succeeds, the device
        will switch to the Proxy Service (UUID 0x1828) after a brief reboot.

        Args:
            address: BLE MAC address (e.g. ``"DC:23:4F:10:52:C4"``).
            timeout: Per-attempt BLE connection timeout in seconds.
            max_retries: Maximum BLE connection attempts.

        Returns:
            ProvisioningResult with derived device key and provisioning data.

        Raises:
            ProvisioningError: If provisioning fails at any step.
        """
        async with mesh_operation(address.upper(), "provision"):
            # PLAT-506: Force cleanup of any stale BLE connections before provisioning
            await self._cleanup_stale_connections(address)

            client = await self._connect(address, timeout, max_retries)
            try:
                return await self._run_exchange(client)
            finally:
                # HF-2: Suppress only expected BLE exceptions, not all exceptions
                with contextlib.suppress(BleakError, OSError):
                    await client.stop_notify(PROV_DATA_OUT)
                with contextlib.suppress(BleakError, OSError):
                    await client.disconnect()
                _LOGGER.info("Provisioning session disconnected from %s", address.upper())
                # PLAT-506: Give BLE adapter time to release connection slot
                await asyncio.sleep(_BLE_SLOT_RELEASE_DELAY)
