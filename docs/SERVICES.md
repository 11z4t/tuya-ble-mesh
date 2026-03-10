# Services

This integration provides Home Assistant services for advanced device control.

## tuya_ble_mesh.provision_device

Provision a new SIG Mesh device into the mesh network.

**Service Data:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mac_address` | string | Yes | Device MAC address (e.g., `DC:23:4F:10:52:C4`) |
| `bridge_host` | string | Yes | IP/hostname of bridge daemon |
| `bridge_port` | integer | No | Bridge port (default: 8787) |
| `net_key` | string | No | Network key (hex, default: auto-generated) |
| `app_key` | string | No | Application key (hex, default: auto-generated) |
| `unicast_addr` | integer | No | Unicast address (default: auto-assigned) |

**Example:**

```yaml
service: tuya_ble_mesh.provision_device
data:
  mac_address: "DC:23:4F:10:52:C4"
  bridge_host: "192.168.1.100"
  bridge_port: 8787
```

**Returns:**

On success, the device will be added to Home Assistant and entities will be created automatically.

**Errors:**

- `HomeAssistantError` if provisioning fails
- `ValueError` if MAC address is invalid

## tuya_ble_mesh.factory_reset

Factory reset a device (removes it from the mesh network).

**Service Data:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `entity_id` | string | Yes | Entity ID of the device to reset (e.g., `light.malmbergs_led_driver`) |

**Example:**

```yaml
service: tuya_ble_mesh.factory_reset
data:
  entity_id: light.malmbergs_led_driver
```

**Warning:** This removes the device from the mesh network. You will need to re-provision it to use it again.

## tuya_ble_mesh.set_mesh_address

Change the mesh address of a provisioned device.

**Service Data:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `entity_id` | string | Yes | Entity ID of the device |
| `new_address` | integer | Yes | New unicast address (0x0001 - 0x7FFF) |

**Example:**

```yaml
service: tuya_ble_mesh.set_mesh_address
data:
  entity_id: light.malmbergs_led_driver
  new_address: 0x0010
```

**Note:** Address must not conflict with other devices in the mesh network.

## tuya_ble_mesh.refresh_rssi

Force an immediate RSSI (signal strength) refresh.

**Service Data:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `entity_id` | string | Yes | Entity ID of the device |

**Example:**

```yaml
service: tuya_ble_mesh.refresh_rssi
data:
  entity_id: light.malmbergs_led_driver
```

**Note:** RSSI updates automatically at adaptive intervals (30-300 seconds). Use this only for debugging or manual monitoring.

## Using Services in Automations

### Example: Provision Device on Button Press

```yaml
automation:
  - alias: "Provision New Light on Button Press"
    trigger:
      - platform: state
        entity_id: input_button.provision_new_device
    action:
      - service: tuya_ble_mesh.provision_device
        data:
          mac_address: "{{ states('input_text.device_mac') }}"
          bridge_host: "192.168.1.100"
```

### Example: Monitor Signal Strength

```yaml
automation:
  - alias: "Alert on Low RSSI"
    trigger:
      - platform: numeric_state
        entity_id: sensor.malmbergs_led_driver_signal
        below: -80
    action:
      - service: notify.mobile_app
        data:
          message: "Light signal strength is weak ({{ states('sensor.malmbergs_led_driver_signal') }} dBm)"
```

### Example: Factory Reset on Config Change

```yaml
automation:
  - alias: "Factory Reset Device"
    trigger:
      - platform: state
        entity_id: input_boolean.reset_device
        to: "on"
    action:
      - service: tuya_ble_mesh.factory_reset
        data:
          entity_id: light.malmbergs_led_driver
      - service: input_boolean.turn_off
        target:
          entity_id: input_boolean.reset_device
```

## Developer Reference

Services are registered in `custom_components/tuya_ble_mesh/__init__.py`:

```python
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.services.async_register(
        DOMAIN,
        "provision_device",
        handle_provision_device,
        schema=PROVISION_SCHEMA,
    )
```

Service handlers are async functions that:
1. Validate input data against a voluptuous schema
2. Retrieve the coordinator from `hass.data`
3. Call the appropriate device method
4. Return or raise `HomeAssistantError`

### Adding a New Service

1. Define schema in `const.py`:
   ```python
   import voluptuous as vol
   MY_SERVICE_SCHEMA = vol.Schema({
       vol.Required("entity_id"): cv.entity_id,
       vol.Optional("param", default=123): int,
   })
   ```

2. Create handler in `__init__.py`:
   ```python
   async def handle_my_service(call: ServiceCall) -> None:
       entity_id = call.data["entity_id"]
       param = call.data["param"]
       # ... implementation
   ```

3. Register in `async_setup_entry()`:
   ```python
   hass.services.async_register(
       DOMAIN, "my_service", handle_my_service, schema=MY_SERVICE_SCHEMA
   )
   ```

4. Document in this file

5. Add tests in `tests/unit/test_services.py`
