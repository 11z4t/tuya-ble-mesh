# User Guide — Tuya BLE Mesh Integration

This guide walks you through setting up and using the Tuya BLE Mesh integration for Home Assistant.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [First-Time Setup](#first-time-setup)
4. [Adding Devices](#adding-devices)
5. [Configuring Devices](#configuring-devices)
6. [Using Your Devices](#using-your-devices)
7. [Troubleshooting](#troubleshooting)
8. [Advanced Configuration](#advanced-configuration)

---

## Prerequisites

### Hardware Requirements

**One of the following:**
- Home Assistant installation with built-in Bluetooth (HA Yellow, Raspberry Pi 3/4)
- ESP32 device with ESPHome and BLE Proxy enabled
- Separate Raspberry Pi running the bridge daemon

**Supported devices:**
- Malmbergs BT Smart lights and plugs
- AwoX BLE Mesh lights
- Dimond/retsimx BLE Mesh devices
- Any Tuya BLE Mesh device advertising service UUID `fe07`, `0x1827`, or `0x1828`

### Software Requirements

- Home Assistant 2024.1 or later
- HACS (recommended for installation)

---

## Installation

### Method 1: HACS (Recommended)

1. Open HACS in Home Assistant
2. Click **Integrations**
3. Click the three-dot menu → **Custom repositories**
4. Add repository URL: `https://github.com/11z4t/tuya-ble-mesh`
5. Select category: **Integration**
6. Click **Add**
7. Search for **"Tuya BLE Mesh"**
8. Click **Download**
9. Restart Home Assistant

### Method 2: Manual Installation

1. Download the latest release from GitHub
2. Extract `custom_components/tuya_ble_mesh/` directory
3. Copy to your Home Assistant `config/custom_components/` directory
4. Restart Home Assistant

---

## First-Time Setup

### Option A: Auto-Discovery (Easiest)

If your HA has Bluetooth support:

1. Power on your Tuya BLE Mesh device
2. Wait for Home Assistant to discover it (check **Settings** → **Devices & Services**)
3. Click **Configure** on the integration notification
4. Follow the setup wizard

### Option B: Manual Setup

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for **"Tuya BLE Mesh"**
4. Select device type (Light or Plug)
5. Enter required information (see below)

---

## Adding Devices

### Finding Your Device's MAC Address

**Method 1: Home Assistant Bluetooth Scanner**
1. Go to **Settings** → **System** → **Hardware**
2. Click **Bluetooth**
3. Look for devices named `out_of_mesh` or `tymesh`
4. Note the MAC address (format: `XX:XX:XX:XX:XX:XX`)

**Method 2: BLE Scanner App (Mobile)**
1. Install a BLE scanner app (e.g., nRF Connect)
2. Scan for nearby devices
3. Look for devices with service UUID `fe07`
4. Note the MAC address

### Required Configuration Fields

| Field | Description | Example |
|-------|-------------|---------|
| **Device Type** | Light or Plug | Light |
| **MAC Address** | Device Bluetooth address | `DC:23:4D:21:43:A5` |
| **Mesh Name** | Network identifier | `out_of_mesh` |
| **Mesh Password** | Network password | `123456` |

### Optional Fields

| Field | Description | Default | When to Change |
|-------|-------------|---------|----------------|
| **Vendor ID** | Manufacturer code | `0x1001` | If commands don't work, try `0x0160` (AwoX) or `0x0211` (Dimond) |
| **Bridge Host** | Bridge daemon IP | — | Only if using external bridge |
| **Bridge Port** | Bridge daemon port | `8787` | Only if using external bridge |

---

## Configuring Devices

### Changing Device Options

1. Go to **Settings** → **Devices & Services**
2. Click on **Tuya BLE Mesh** integration
3. Click the device you want to configure
4. Click **⚙ Configure** (if available via config flow options)

### Mesh Credentials

**Default values** work for most devices:
- Mesh Name: `out_of_mesh`
- Mesh Password: `123456`

**If previously paired with Tuya app**, you may need to:
1. Check mesh credentials in the Tuya Smart app
2. Update them in Home Assistant configuration
3. Or factory reset the device

---

## Using Your Devices

### Controlling Lights

**Turn on/off:**
```yaml
service: light.turn_on
target:
  entity_id: light.malmbergs_led_driver
```

**Set brightness (1-100%):**
```yaml
service: light.turn_on
target:
  entity_id: light.malmbergs_led_driver
data:
  brightness_pct: 75
```

**Set color temperature (warm to cool):**
```yaml
service: light.turn_on
target:
  entity_id: light.malmbergs_led_driver
data:
  color_temp: 300  # mireds (153-500)
```

**Set RGB color (if supported):**
```yaml
service: light.turn_on
target:
  entity_id: light.malmbergs_led_driver
data:
  rgb_color: [255, 128, 0]
```

### Controlling Switches (Plugs)

**Turn on/off:**
```yaml
service: switch.turn_on
target:
  entity_id: switch.malmbergs_plug_s17
```

### Using Services

**Identify device (flash LED):**
```yaml
service: tuya_ble_mesh.identify
data:
  device_id: <device_id>
```

**Enable debug logging:**
```yaml
service: tuya_ble_mesh.set_log_level
data:
  level: debug
```

---

## Troubleshooting

### Problem: Device Not Discovered

**Solutions:**
1. **Check Bluetooth adapter:**
   - Go to **Settings** → **System** → **Hardware** → **Bluetooth**
   - Verify adapter is detected and enabled
2. **Factory reset device:**
   - Power cycle 5-10 times rapidly (1 sec on, 1 sec off)
   - Device should start flashing rapidly
   - Look for `out_of_mesh` in BLE scan
3. **Reduce distance:**
   - Move device closer to HA (within 5 meters)
   - Or add ESPHome BLE proxy nearby
4. **Check power:**
   - Ensure device has stable power supply
   - Low voltage can prevent BLE advertising

### Problem: Commands Don't Work

**Solutions:**
1. **Verify mesh credentials:**
   - Default is `out_of_mesh` / `123456`
   - If previously paired, check Tuya app settings
2. **Try different vendor ID:**
   - Malmbergs: `0x1001` (default)
   - AwoX: `0x0160`
   - Dimond: `0x0211`
3. **Check device type:**
   - Ensure you selected "Light" for lights and "Plug" for switches
4. **Review logs:**
   ```yaml
   service: tuya_ble_mesh.set_log_level
   data:
     level: debug
   ```
   Then check **Settings** → **System** → **Logs**

### Problem: Connection Drops Frequently

**Solutions:**
1. **Reduce BLE congestion:**
   - Limit concurrent BLE devices per adapter (max 3-5 recommended)
   - Disable unused Bluetooth integrations
2. **Improve signal strength:**
   - Add ESPHome BLE proxies closer to devices
   - Check RSSI sensor: `sensor.<device>_signal` (should be > -70 dBm)
3. **Check power stability:**
   - Weak power supplies can cause connection issues
   - Try different power source
4. **Update firmware:**
   - Ensure Home Assistant is up to date
   - Update ESPHome proxies if using them

### Problem: Device Stuck in "Unavailable"

**Solutions:**
1. **Restart integration:**
   - Go to **Settings** → **Devices & Services** → **Tuya BLE Mesh**
   - Click **…** → **Reload**
2. **Restart device:**
   - Power cycle the physical device
   - Wait 30 seconds for reconnection
3. **Re-add device:**
   - Remove device from integration
   - Add it again with correct MAC and credentials
4. **Check Home Assistant Bluetooth:**
   - Restart Home Assistant
   - Verify Bluetooth adapter is working

---

## Advanced Configuration

### Using ESPHome BLE Proxy

1. **Flash ESP32 with ESPHome:**
   ```yaml
   esphome:
     name: ble-proxy-01

   esp32:
     board: esp32dev

   wifi:
     ssid: !secret wifi_ssid
     password: !secret wifi_password

   api:
     encryption:
       key: !secret api_key

   bluetooth_proxy:
     active: true
   ```

2. **Add to Home Assistant:**
   - ESPHome device will be auto-discovered
   - Click **Configure** to add it

3. **Place strategically:**
   - Position ESP32 devices near Tuya BLE Mesh lights
   - One proxy can handle 3-5 devices

### Using Bridge Daemon

**When to use:** Your HA installation has no Bluetooth hardware.

1. **Set up Raspberry Pi:**
   ```bash
   # On the RPi
   git clone https://github.com/11z4t/tuya-ble-mesh.git
   cd tuya-ble-mesh
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Run bridge daemon:**
   ```bash
   python scripts/ble_mesh_daemon.py --host 0.0.0.0 --port 8787
   ```

3. **Configure in HA:**
   - Add integration manually
   - Enter **Bridge Host** (RPi IP address)
   - Enter **Bridge Port** (default `8787`)

### Factory Reset Procedure

**For devices that won't respond:**

1. **Power cycling method:**
   - Turn device OFF for 2 seconds
   - Turn device ON for 1 second
   - Repeat 5-10 times
   - Device will flash rapidly when reset

2. **Using Shelly plug (automated):**
   ```bash
   # If you have Shelly plug for automation
   python scripts/power_cycle.py --device <device_mac>
   ```

3. **Verify reset:**
   - Device should advertise as `out_of_mesh`
   - Look for it in BLE scanner

---

## Best Practices

### Placement
- Keep devices within 5-10 meters of Bluetooth adapter or proxy
- Avoid metal obstacles between devices and adapter
- Use multiple ESPHome proxies for large homes

### Naming
- Use descriptive names: `Bedroom Ceiling Light` instead of `Light 1`
- Include room name for easier automation

### Automation Tips
- Use `light.turn_on` with `transition` for smooth dimming:
  ```yaml
  service: light.turn_on
  target:
    entity_id: light.bedroom_ceiling
  data:
    brightness_pct: 50
    transition: 2  # seconds
  ```

### Performance
- Limit BLE mesh devices per adapter (3-5 max)
- Use wired ESPHome proxies for better reliability
- Monitor RSSI sensors to identify weak connections

---

## Getting Help

- **GitHub Issues:** https://github.com/11z4t/tuya-ble-mesh/issues
- **Home Assistant Community:** https://community.home-assistant.io
- **Enable debug logging:**
  ```yaml
  service: tuya_ble_mesh.set_log_level
  data:
    level: debug
  ```
  Then check logs at **Settings** → **System** → **Logs**

---

## Next Steps

- [Architecture Documentation](ARCHITECTURE.md) — Learn how the integration works internally
- [Protocol Documentation](PROTOCOL.md) — Deep dive into Tuya BLE Mesh protocol
- [Contributing Guide](../CONTRIBUTING.md) — Help improve the integration
