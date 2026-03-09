# ESPHome BLE Proxy Support for Tuya BLE Mesh

This document describes how to use ESPHome BLE proxies with the Tuya BLE Mesh integration, enabling mesh communication through ESP32 devices running ESPHome.

## Overview

ESPHome's `bluetooth_proxy` component can act as a BLE mesh proxy, allowing Home Assistant to communicate with BLE Mesh devices through ESP32 boards instead of requiring a USB Bluetooth adapter directly on the HA server.

### Benefits

- **Extended Range**: Place ESP32 proxies throughout your home for better mesh coverage
- **Load Distribution**: Multiple proxies can share the BLE communication load
- **Reliability**: Redundant proxies provide failover capabilities
- **Cost-Effective**: ESP32 boards are inexpensive and widely available

## Architecture

```
Home Assistant (HA Core)
    ↓
Tuya BLE Mesh Integration
    ↓
ESPHome Bluetooth Proxy (ESP32)
    ↓ (BLE)
Tuya Mesh Devices
```

## ESPHome Configuration

### Basic Proxy Configuration

```yaml
# esphome-ble-proxy.yaml
esphome:
  name: ble-proxy-01
  platform: ESP32
  board: esp32dev

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

  # Enable fallback hotspot (captive portal) in case wifi connection fails
  ap:
    ssid: "BLE-Proxy-01 Fallback Hotspot"
    password: !secret ap_password

# Enable logging
logger:
  level: DEBUG
  logs:
    esp32_ble_tracker: VERBOSE
    bluetooth_proxy: VERBOSE

# Enable Home Assistant API
api:
  encryption:
    key: !secret api_encryption_key

ota:
  password: !secret ota_password

# BLE Tracker for scanning
esp32_ble_tracker:
  scan_parameters:
    interval: 1100ms
    window: 1100ms
    active: true

# Bluetooth Proxy component
bluetooth_proxy:
  active: true
```

### Advanced Configuration with Multiple Proxies

For optimal coverage, deploy multiple ESP32 proxies:

```yaml
# Add to each proxy configuration
bluetooth_proxy:
  active: true
  # Cache services to reduce memory usage
  cache_services: true

esp32_ble_tracker:
  scan_parameters:
    # Aggressive scanning for mesh devices
    interval: 320ms
    window: 30ms
    active: true
  # Filter for Tuya mesh devices (optional)
  on_ble_advertise:
    - mac_address: !lambda |-
        if (x.get_name().find("out_of_mesh") != std::string::npos) {
          ESP_LOGD("ble_adv", "Tuya mesh device found: %s", x.address_str().c_str());
          return true;
        }
        return false;
      then:
        - logger.log:
            format: "Tuya mesh device in range: %s"
            args: ['x.get_name().c_str()']
```

## Home Assistant Configuration

### Enable Bluetooth Integration

Ensure HA's Bluetooth integration is enabled and can see your ESPHome proxies:

1. Go to **Settings** → **Devices & Services**
2. Add **Bluetooth** integration if not already present
3. ESPHome proxies should auto-discover and appear as Bluetooth sources

### Configure Tuya BLE Mesh Integration

When setting up the Tuya BLE Mesh integration via config flow:

1. The integration will automatically detect available Bluetooth adapters, including ESPHome proxies
2. Select your preferred ESPHome proxy from the dropdown
3. Complete the configuration flow as normal

### Verify Proxy Connection

Check that HA sees the ESPHome proxy:

```bash
# In Home Assistant Developer Tools → Template
{{ states.sensor | selectattr('entity_id', 'search', 'esphome') | list }}
```

## BLE Proxy Provisioning

The integration supports provisioning through ESPHome proxies using the `bluetooth_proxy` callbacks:

### Integration Code Example

The `SIGMeshProvisioner` class supports ESPHome proxies via optional callbacks:

```python
from homeassistant.components import bluetooth

# Get BLE device from HA's Bluetooth integration
def get_ble_device(address: str):
    """Retrieve BLEDevice from HA Bluetooth integration."""
    return bluetooth.async_ble_device_from_address(hass, address.upper())

# Connect using HA's bleak-retry-connector
async def connect_ble_device(device):
    """Connect to BLE device using HA's connector."""
    from bleak_retry_connector import establish_connection
    return await establish_connection(
        client_class=BleakClient,
        device=device,
        name=device.address,
        max_attempts=5,
    )

# Provision through ESPHome proxy
provisioner = SIGMeshProvisioner(
    net_key=net_key,
    app_key=app_key,
    unicast_addr=0x00B0,
    ble_device_callback=get_ble_device,
    ble_connect_callback=connect_ble_device,
)

result = await provisioner.provision("DC:23:4F:10:52:C4")
```

### Error Handling

Common issues when using ESPHome proxies:

#### Connection Timeouts

**Problem**: Provisioning fails with timeout errors.

**Solution**:
- Increase `timeout` parameter in `provision()` call to 20-30 seconds
- Move ESP32 proxy closer to the target device
- Check WiFi connectivity between HA and ESP32

```python
result = await provisioner.provision(
    address="DC:23:4F:10:52:C4",
    timeout=30.0,  # Increase from default 15s
    max_retries=7,  # Increase retry attempts
)
```

#### Device Not Found

**Problem**: BLE scan doesn't find the device.

**Solution**:
- Verify device is advertising (reset if needed)
- Check ESP32 logs for BLE scan results
- Ensure `active: true` in `esp32_ble_tracker` config
- Verify MAC address format (uppercase, colon-separated)

#### Authentication Failures

**Problem**: Provisioning fails at confirmation step.

**Solution**:
- Ensure device supports No OOB authentication
- Check that device hasn't been previously provisioned
- Factory reset the device and retry

## Performance Considerations

### Scanning Efficiency

ESP32 proxies have limited memory. Optimize scanning:

```yaml
esp32_ble_tracker:
  scan_parameters:
    # Balanced settings for mesh devices
    interval: 1100ms
    window: 1100ms
    active: true
  # Limit tracked devices
  continuous: false
```

### Multiple Device Provisioning

When provisioning multiple devices through the same proxy:

1. Add delays between provisioning attempts (5-10 seconds)
2. Monitor ESP32 memory usage (enable logging)
3. Restart proxy if memory gets low (`< 30KB free`)

```python
for device in devices_to_provision:
    try:
        result = await provisioner.provision(device.address)
        _LOGGER.info("Provisioned %s successfully", device.address)
    except ProvisioningError as exc:
        _LOGGER.error("Failed to provision %s: %s", device.address, exc)

    # Delay between devices
    await asyncio.sleep(10)
```

## Troubleshooting

### Enable Debug Logging

#### ESPHome Side

```yaml
logger:
  level: VERBOSE
  logs:
    esp32_ble_tracker: VERBOSE
    bluetooth_proxy: VERBOSE
    component: DEBUG
```

#### Home Assistant Side

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.tuya_ble_mesh: debug
    homeassistant.components.bluetooth: debug
    homeassistant.components.esphome: debug
```

### Check Proxy Status

Monitor ESP32 proxy health from HA:

```python
# Developer Tools → Template
{% set proxy = states.binary_sensor | selectattr('entity_id', 'search', 'ble_proxy_01_status') | first %}
Proxy Status: {{ proxy.state }}
Last Update: {{ proxy.last_changed }}
```

### Common Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| `Device not found in BLE scan` | Out of range or not advertising | Move proxy closer, reset device |
| `Failed to connect after N attempts` | Connection instability | Check WiFi, increase timeout |
| `ECDH key exchange failed` | Crypto library issue | Update ESPHome/HA to latest |
| `Device confirmation mismatch` | Authentication failure | Factory reset device |
| `BleakClient.connect() called without bleak-retry-connector` | Missing callback | Use `ble_connect_callback` parameter |

## Best Practices

### Proxy Placement

- Place proxies centrally within BLE range (10-15 meters)
- Avoid obstacles (walls, metal objects) between proxy and devices
- Use multiple proxies for large homes (one per floor recommended)

### Network Reliability

- Use wired Ethernet for ESP32 if possible (via ESP32-POE boards)
- Ensure strong WiFi signal at proxy locations (> -70 dBm)
- Reserve DHCP addresses for proxies (static IPs preferred)

### Security

- Enable API encryption in ESPHome (`encryption:` with secret key)
- Use strong OTA passwords
- Isolate ESP32 proxies on separate VLAN (optional but recommended)
- Regularly update ESPHome firmware

## Hardware Recommendations

### Recommended ESP32 Boards

| Board | Pros | Cons | Use Case |
|-------|------|------|----------|
| **ESP32-DevKitC** | Cheap, widely available | No PoE, USB power | Basic proxy |
| **ESP32-POE** | Ethernet + PoE | More expensive | Production deployment |
| **ESP32-C3** | Low power, USB-C | Less RAM | Single-room proxy |
| **ESP32-S3** | High performance | Overkill for proxy | High-density mesh |

### Power Considerations

- USB power: 5V/1A sufficient for BLE proxy
- PoE: Use 802.3af compliant injectors
- Battery: Not recommended (BLE scanning drains quickly)

## Integration with Coordinator

The `TuyaBLEMeshCoordinator` automatically handles ESPHome proxy connections when configured via HA's Bluetooth integration:

```python
# custom_components/tuya_ble_mesh/coordinator.py
async def _ensure_connection(self) -> bool:
    """Establish BLE connection through HA Bluetooth (may use ESPHome proxy)."""
    try:
        # HA Bluetooth integration handles proxy routing automatically
        device = bluetooth.async_ble_device_from_address(
            self.hass,
            self.address.upper()
        )

        if device is None:
            _LOGGER.warning("Device %s not found in Bluetooth scan", self.address)
            return False

        # Connect using bleak-retry-connector (ESPHome-compatible)
        self._client = await establish_connection(
            client_class=BleakClient,
            device=device,
            name=self.address,
            max_attempts=3,
        )
        return True
    except Exception as exc:
        _LOGGER.error("Connection failed: %s", exc)
        return False
```

## Monitoring and Metrics

### ESPHome Sensors

Add monitoring sensors to track proxy health:

```yaml
sensor:
  - platform: wifi_signal
    name: "BLE Proxy WiFi Signal"
    update_interval: 60s

  - platform: uptime
    name: "BLE Proxy Uptime"

  - platform: template
    name: "BLE Proxy Free Heap"
    lambda: |-
      return heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    unit_of_measurement: 'bytes'
    update_interval: 10s

binary_sensor:
  - platform: status
    name: "BLE Proxy Status"
```

### Home Assistant Automations

Alert on proxy failures:

```yaml
automation:
  - alias: "BLE Proxy Offline Alert"
    trigger:
      - platform: state
        entity_id: binary_sensor.ble_proxy_01_status
        to: 'off'
        for: '00:05:00'
    action:
      - service: notify.mobile_app
        data:
          title: "BLE Proxy Offline"
          message: "BLE Proxy 01 has been offline for 5 minutes"
```

## References

- [ESPHome Bluetooth Proxy Documentation](https://esphome.io/components/bluetooth_proxy.html)
- [ESPHome BLE Tracker](https://esphome.io/components/esp32_ble_tracker.html)
- [Home Assistant Bluetooth Integration](https://www.home-assistant.io/integrations/bluetooth/)
- [bleak-retry-connector](https://github.com/Bluetooth-Devices/bleak-retry-connector)

## Changelog

- **2026-03-09**: Initial documentation created
- Document covers ESPHome proxy setup, provisioning, troubleshooting, and best practices

---

**Maintained by**: VM 903 (Thor)
**Last Updated**: 2026-03-09
