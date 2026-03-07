"""Config flow for Tuya BLE Mesh integration.

Supports bluetooth discovery (out_of_mesh*, tymesh*, SIG Mesh Proxy/Provisioning)
and manual MAC entry. Bridge connectivity is validated before creating config entries.
SIG Mesh plugs are provisioned automatically — NetKey and AppKey are generated
and the device key is derived from the ECDH provisioning exchange.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Ensure lib/tuya_ble_mesh is importable from config flow context
_BUNDLED_LIB = str(Path(__file__).resolve().parent / "lib")
_DEV_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
for _lib_dir in (_BUNDLED_LIB, _DEV_LIB):
    if Path(_lib_dir).is_dir() and _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)
        break

import voluptuous as vol  # noqa: E402
from homeassistant import config_entries  # noqa: E402
from homeassistant.config_entries import ConfigFlow  # noqa: E402

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    CONF_APP_KEY,
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_DEV_KEY,
    CONF_DEVICE_TYPE,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
    CONF_UNICAST_OUR,
    CONF_UNICAST_TARGET,
    CONF_VENDOR_ID,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_IV_INDEX,
    DEFAULT_MESH_ADDRESS,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    DOMAIN,
    SIG_MESH_PROV_UUID,
    SIG_MESH_PROXY_UUID,
)

_LOGGER = logging.getLogger(__name__)

_MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_HEX_KEY_PATTERN = re.compile(r"^[0-9A-Fa-f]{32}$")
_BRIDGE_TEST_TIMEOUT = 5.0
_MESH_KEYS_PATH = "/tmp/mesh_keys.json"

# Unicast addresses used during provisioning
_UNICAST_PROVISIONER = 0x0001
_UNICAST_DEVICE_DEFAULT = 0x00B0

# GenericOnOff Server SIG Model ID
_MODEL_GENERIC_ONOFF_SERVER = 0x1000

# Seconds to wait for device to reboot as Proxy Service after provisioning
_POST_PROV_REBOOT_DELAY = 6.0


def _load_mesh_key_defaults() -> dict[str, str]:
    """Try to load default mesh keys from /tmp/mesh_keys.json.

    Returns:
        Dict with net_key, dev_key, app_key defaults (empty strings if missing).
    """
    defaults: dict[str, str] = {"net_key": "", "dev_key": "", "app_key": ""}
    try:
        import os

        if os.path.exists(_MESH_KEYS_PATH):
            with open(_MESH_KEYS_PATH) as f:
                data = json.load(f)
            defaults["net_key"] = data.get("net_key", "")
            defaults["dev_key"] = data.get("dev_key", "")
            defaults["app_key"] = data.get("app_key", "")
    except Exception:
        _LOGGER.debug("Could not load mesh key defaults", exc_info=True)
    return defaults


def _validate_hex_key(value: str) -> bool:
    """Validate a 32-char hex key string."""
    return bool(_HEX_KEY_PATTERN.match(value))


def _validate_mac(mac: str) -> str | None:
    """Validate a MAC address string.

    Args:
        mac: MAC address to validate.

    Returns:
        None if valid, error key string if invalid.
    """
    if not _MAC_PATTERN.match(mac):
        return "invalid_mac"
    return None


async def _test_bridge(host: str, port: int) -> bool:
    """Test if bridge daemon is reachable.

    Args:
        host: Bridge hostname/IP.
        port: Bridge port.

    Returns:
        True if bridge responds with status ok.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_BRIDGE_TEST_TIMEOUT,
        )
        try:
            request = f"GET /health HTTP/1.1\r\nHost: {host}\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()
            response = await asyncio.wait_for(
                reader.read(4096),
                timeout=_BRIDGE_TEST_TIMEOUT,
            )
            body = response.decode("utf-8", errors="replace")
            parts = body.split("\r\n\r\n", 1)
            if len(parts) > 1:
                data = json.loads(parts[1])
                return data.get("status") == "ok"
        finally:
            writer.close()
    except Exception:
        _LOGGER.debug("Bridge test failed for %s:%d", host, port, exc_info=True)
    return False


class TuyaBLEMeshOptionsFlow(config_entries.OptionsFlow):
    """Handle options for a Tuya BLE Mesh entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Manage device options.

        Args:
            user_input: User-provided option values.

        Returns:
            Flow result dict.
        """
        if user_input is not None:
            # Merge new data into config entry
            new_data = {**self._config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
            return self.async_create_entry(title="", data={})

        device_type = self._config_entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)

        # Build schema based on device type
        if device_type in (
            DEVICE_TYPE_SIG_BRIDGE_PLUG,
            DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
        ):
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_BRIDGE_HOST,
                        default=self._config_entry.data.get(CONF_BRIDGE_HOST, ""),
                    ): str,
                    vol.Optional(
                        CONF_BRIDGE_PORT,
                        default=self._config_entry.data.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT),
                    ): int,
                }
            )
        elif device_type == DEVICE_TYPE_SIG_PLUG:
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_UNICAST_TARGET,
                        default=self._config_entry.data.get(CONF_UNICAST_TARGET, "00B0"),
                    ): str,
                    vol.Optional(
                        CONF_IV_INDEX,
                        default=self._config_entry.data.get(CONF_IV_INDEX, DEFAULT_IV_INDEX),
                    ): int,
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_MESH_NAME,
                        default=self._config_entry.data.get(CONF_MESH_NAME, "out_of_mesh"),
                    ): str,
                    vol.Optional(
                        CONF_MESH_PASSWORD,
                        default=self._config_entry.data.get(
                            CONF_MESH_PASSWORD,
                            "123456",  # pragma: allowlist secret
                        ),
                    ): str,
                    vol.Optional(
                        CONF_MESH_ADDRESS,
                        default=self._config_entry.data.get(
                            CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS
                        ),
                    ): int,
                }
            )

        return self.async_show_form(step_id="init", data_schema=schema)


class TuyaBLEMeshConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tuya BLE Mesh."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TuyaBLEMeshOptionsFlow:
        """Return the options flow handler."""
        return TuyaBLEMeshOptionsFlow(config_entry)

    def __init__(self) -> None:
        super().__init__()
        self._discovery_info: dict[str, Any] | None = None
        # Stored provisioning keys set by _run_provision
        self._prov_net_key: str = ""
        self._prov_dev_key: str = ""
        self._prov_app_key: str = ""

    async def async_step_bluetooth(
        self,
        discovery_info: BluetoothServiceInfoBleak,
    ) -> dict[str, Any]:
        """Handle bluetooth discovery.

        Args:
            discovery_info: Bluetooth service info from HA bluetooth integration.

        Returns:
            Flow result dict.
        """
        address: str = discovery_info.address
        name: str = discovery_info.name or ""

        _LOGGER.info("Bluetooth discovery: %s (%s)", name, address)

        # Check if already configured
        await self.async_set_unique_id(address)

        self._discovery_info = {
            "address": address,
            "name": name,
        }

        # Auto-detect SIG Mesh devices by service UUID.
        # 0x1827 = Provisioning Service (unprovisioned device)
        # 0x1828 = Proxy Service (already provisioned)
        service_uuids = getattr(discovery_info, "service_uuids", [])
        if SIG_MESH_PROV_UUID in service_uuids or SIG_MESH_PROXY_UUID in service_uuids:
            _LOGGER.info("SIG Mesh device detected: %s", address)
            return await self.async_step_sig_plug()

        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Confirm bluetooth discovery and choose device type.

        Args:
            user_input: User confirmation input.

        Returns:
            Flow result dict.
        """
        if user_input is not None and self._discovery_info is not None:
            mac = self._discovery_info["address"]
            device_type = user_input.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)
            short_mac = mac[-8:]
            title = (
                f"BLE Mesh Plug {short_mac}"
                if device_type == DEVICE_TYPE_PLUG
                else f"BLE Mesh Light {short_mac}"
            )
            return self.async_create_entry(
                title=title,
                data={
                    CONF_MAC_ADDRESS: mac,
                    CONF_MESH_NAME: user_input.get(CONF_MESH_NAME, "out_of_mesh"),
                    CONF_MESH_PASSWORD: user_input.get(CONF_MESH_PASSWORD, "123456"),
                    CONF_VENDOR_ID: DEFAULT_VENDOR_ID,
                    CONF_DEVICE_TYPE: device_type,
                    CONF_MESH_ADDRESS: user_input.get(CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS),
                },
            )

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_TYPE, default=DEVICE_TYPE_LIGHT): vol.In(
                        {DEVICE_TYPE_LIGHT: "Light", DEVICE_TYPE_PLUG: "Plug"}
                    ),
                    vol.Optional(CONF_MESH_NAME, default="out_of_mesh"): str,
                    vol.Optional(CONF_MESH_PASSWORD, default="123456"): str,
                    vol.Optional(CONF_MESH_ADDRESS, default=DEFAULT_MESH_ADDRESS): int,
                }
            ),
            description_placeholders={
                "name": self._discovery_info.get("name", "") if self._discovery_info else "",
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Handle manual setup.

        Args:
            user_input: User-provided configuration data.

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = user_input.get(CONF_MAC_ADDRESS, "")
            mac_error = _validate_mac(mac)
            if mac_error:
                errors[CONF_MAC_ADDRESS] = mac_error
            else:
                device_type = user_input.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)
                if device_type == DEVICE_TYPE_SIG_BRIDGE_PLUG:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"SIG Bridge Plug {mac[-8:]}",
                    }
                    return await self.async_step_sig_bridge(None)
                if device_type == DEVICE_TYPE_TELINK_BRIDGE_LIGHT:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"Telink Bridge Light {mac[-8:]}",
                    }
                    return await self.async_step_telink_bridge(None)
                if device_type == DEVICE_TYPE_SIG_PLUG:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"SIG Mesh {mac[-8:]}",
                    }
                    return await self.async_step_sig_plug(None)
                short = mac[-8:]
                type_label = "Plug" if device_type == DEVICE_TYPE_PLUG else "Light"
                await self.async_set_unique_id(mac.upper())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"BLE Mesh {type_label} {short}",
                    data={
                        CONF_MAC_ADDRESS: mac.upper(),
                        CONF_MESH_NAME: user_input.get(CONF_MESH_NAME, "out_of_mesh"),
                        CONF_MESH_PASSWORD: user_input.get(CONF_MESH_PASSWORD, "123456"),
                        CONF_VENDOR_ID: user_input.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID),
                        CONF_DEVICE_TYPE: device_type,
                        CONF_MESH_ADDRESS: user_input.get(CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC_ADDRESS): str,
                    vol.Required(CONF_DEVICE_TYPE, default=DEVICE_TYPE_LIGHT): vol.In(
                        {
                            DEVICE_TYPE_SIG_BRIDGE_PLUG: "SIG Mesh Plug (via bridge)",
                            DEVICE_TYPE_TELINK_BRIDGE_LIGHT: "Telink Light (via bridge)",
                            DEVICE_TYPE_LIGHT: "Light (direct BLE, requires adapter)",
                            DEVICE_TYPE_PLUG: "Plug (direct BLE, requires adapter)",
                            DEVICE_TYPE_SIG_PLUG: "SIG Mesh Plug (direct BLE)",
                        }
                    ),
                    vol.Optional(CONF_MESH_NAME, default="out_of_mesh"): str,
                    vol.Optional(CONF_MESH_PASSWORD, default="123456"): str,
                    vol.Optional(CONF_VENDOR_ID, default=DEFAULT_VENDOR_ID): str,
                    vol.Optional(CONF_MESH_ADDRESS, default=DEFAULT_MESH_ADDRESS): int,
                }
            ),
            description_placeholders={},
            errors=errors,
        )

    async def async_step_sig_plug(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Handle SIG Mesh plug — auto-provisions and generates all keys.

        The device is provisioned via PB-GATT (Service UUID 0x1827).
        A random NetKey and AppKey are generated. The DevKey is derived
        from the ECDH exchange during provisioning.  After provisioning,
        AppKey is added and bound to the GenericOnOff Server model via
        the Proxy Service (UUID 0x1828).

        Args:
            user_input: Empty dict when user confirms provisioning (no fields).

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}

        if user_input is not None and self._discovery_info is not None:
            mac = self._discovery_info["address"]
            try:
                net_key_hex, dev_key_hex, app_key_hex = await self._run_provision(mac)
            except Exception as exc:
                _LOGGER.warning("Provisioning failed for %s: %s", mac, type(exc).__name__)
                errors["base"] = "provisioning_failed"
            else:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"SIG Mesh {mac[-8:]}",
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_PLUG,
                        CONF_UNICAST_TARGET: f"{_UNICAST_DEVICE_DEFAULT:04X}",
                        CONF_UNICAST_OUR: f"{_UNICAST_PROVISIONER:04X}",
                        CONF_IV_INDEX: DEFAULT_IV_INDEX,
                        CONF_NET_KEY: net_key_hex,
                        CONF_DEV_KEY: dev_key_hex,
                        CONF_APP_KEY: app_key_hex,
                    },
                )

        return self.async_show_form(
            step_id="sig_plug",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": (self._discovery_info.get("name", "") if self._discovery_info else ""),
            },
            errors=errors,
        )

    async def _run_provision(self, mac: str) -> tuple[str, str, str]:
        """Generate keys, provision the device, configure AppKey and model bind.

        Phase 1: PB-GATT provisioning (Service 0x1827).
        Phase 2: Wait for device to reboot into Proxy Service (0x1828).
        Phase 3: Add AppKey and bind to GenericOnOff Server model.

        Args:
            mac: BLE MAC address of the unprovisioned device.

        Returns:
            Tuple of (net_key_hex, dev_key_hex, app_key_hex).

        Raises:
            ProvisioningError: If PB-GATT provisioning fails.
            Any exception from Phase 3 is logged but not re-raised.
        """
        from tuya_ble_mesh.secrets import DictSecretsManager
        from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice
        from tuya_ble_mesh.sig_mesh_provisioner import SIGMeshProvisioner

        # Generate fresh random keys (SECURITY: never logged)
        net_key = os.urandom(16)
        app_key = os.urandom(16)

        _LOGGER.info(
            "Auto-provisioning SIG Mesh device %s (unicast=0x%04X)",
            mac,
            _UNICAST_DEVICE_DEFAULT,
        )

        # Phase 1: PB-GATT provisioning
        provisioner = SIGMeshProvisioner(
            net_key=net_key,
            app_key=app_key,
            unicast_addr=_UNICAST_DEVICE_DEFAULT,
            iv_index=DEFAULT_IV_INDEX,
        )
        result = await provisioner.provision(mac)

        _LOGGER.info(
            "PB-GATT provisioning succeeded for %s (%d elements)",
            mac,
            result.num_elements,
        )

        # Phase 2: Wait for device to reboot and switch to Proxy Service
        _LOGGER.info(
            "Waiting %.0fs for %s to reboot as Proxy Service...", _POST_PROV_REBOOT_DELAY, mac
        )
        await asyncio.sleep(_POST_PROV_REBOOT_DELAY)

        # Phase 3: Post-provisioning config via GATT Proxy
        op_prefix = "cfg"
        target_hex = f"{_UNICAST_DEVICE_DEFAULT:04x}"
        dev_key_name = f"{op_prefix}-dev-key-{target_hex}/password"
        secrets_dict = {
            f"{op_prefix}-net-key/password": net_key.hex(),  # pragma: allowlist secret
            dev_key_name: result.dev_key.hex(),  # pragma: allowlist secret
            f"{op_prefix}-app-key/password": app_key.hex(),  # pragma: allowlist secret
        }
        device = SIGMeshDevice(
            mac,
            _UNICAST_DEVICE_DEFAULT,
            _UNICAST_PROVISIONER,
            DictSecretsManager(secrets_dict),
            op_item_prefix=op_prefix,
            iv_index=DEFAULT_IV_INDEX,
        )
        try:
            await device.connect(timeout=20.0, max_retries=5)
            appkey_ok = await device.send_config_appkey_add(app_key)
            if not appkey_ok:
                _LOGGER.warning("AppKey Add returned non-success for %s", mac)
            await asyncio.sleep(0.5)
            bind_ok = await device.send_config_model_app_bind(
                _UNICAST_DEVICE_DEFAULT, 0, _MODEL_GENERIC_ONOFF_SERVER
            )
            if not bind_ok:
                _LOGGER.warning(
                    "Model App Bind returned non-success for %s (model=0x%04X)",
                    mac,
                    _MODEL_GENERIC_ONOFF_SERVER,
                )
        except Exception:
            _LOGGER.warning(
                "Post-provisioning config failed for %s (device provisioned but AppKey not bound)",
                mac,
                exc_info=True,
            )
        finally:
            await device.disconnect()

        return net_key.hex(), result.dev_key.hex(), app_key.hex()

    async def async_step_sig_bridge(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle SIG Mesh Bridge plug configuration.

        Args:
            user_input: User-provided bridge parameters.

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}
        if user_input is not None and self._discovery_info is not None:
            host = user_input.get(CONF_BRIDGE_HOST, "")
            port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
            if not await _test_bridge(host, port):
                errors["base"] = "cannot_connect"
            else:
                mac = self._discovery_info["address"]
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"SIG Bridge Plug {mac[-8:]}",
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG,
                        CONF_UNICAST_TARGET: user_input.get(CONF_UNICAST_TARGET, "00B0"),
                        CONF_BRIDGE_HOST: host,
                        CONF_BRIDGE_PORT: port,
                    },
                )

        return self.async_show_form(
            step_id="sig_bridge",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_HOST): str,
                    vol.Optional(CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT): int,
                    vol.Optional(CONF_UNICAST_TARGET, default="00B0"): str,
                }
            ),
            description_placeholders={
                "name": (self._discovery_info.get("name", "") if self._discovery_info else ""),
            },
            errors=errors,
        )

    async def async_step_telink_bridge(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle Telink Bridge light configuration.

        Args:
            user_input: User-provided bridge parameters.

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}
        if user_input is not None and self._discovery_info is not None:
            host = user_input.get(CONF_BRIDGE_HOST, "")
            port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
            if not await _test_bridge(host, port):
                errors["base"] = "cannot_connect"
            else:
                mac = self._discovery_info["address"]
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Telink Bridge Light {mac[-8:]}",
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_DEVICE_TYPE: DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
                        CONF_BRIDGE_HOST: host,
                        CONF_BRIDGE_PORT: port,
                    },
                )

        return self.async_show_form(
            step_id="telink_bridge",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_HOST): str,
                    vol.Optional(CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT): int,
                }
            ),
            description_placeholders={
                "name": (self._discovery_info.get("name", "") if self._discovery_info else ""),
            },
            errors=errors,
        )
