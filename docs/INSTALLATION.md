# Installation Guide

This guide covers how to install and configure the Tuya BLE Mesh integration.

## Prerequisites

- **Home Assistant** 2024.1 or later
- **Bluetooth adapter** on your Home Assistant host OR ESPHome BLE Proxy
- **Tuya BLE Mesh devices** (e.g., Malmbergs BT Smart)

## Method 1: HACS (Recommended)

HACS (Home Assistant Community Store) provides the easiest installation and update path.

### Step 1: Install HACS

If you don't have HACS installed:

1. Visit [hacs.xyz](https://hacs.xyz) and follow the installation instructions
2. Restart Home Assistant
3. Complete HACS setup via the UI

### Step 2: Add Custom Repository

1. Open **HACS** in Home Assistant
2. Click the three-dot menu (top right) → **Custom repositories**
3. Add repository URL: `https://github.com/11z4t/tuya-ble-mesh`
4. Category: **Integration**
5. Click **Add**

### Step 3: Install Integration

1. In HACS, go to **Integrations**
2. Search for **"Tuya BLE Mesh"**
3. Click **Download**
4. Select the latest version
5. **Restart Home Assistant**

## Method 2: Manual Installation

For users who prefer manual control or don't use HACS.

### Step 1: Download

Download the latest release from GitHub:
```bash
wget https://github.com/11z4t/tuya-ble-mesh/releases/latest/download/tuya-ble-mesh.zip
```

Or clone the repository:
```bash
git clone https://github.com/11z4t/tuya-ble-mesh.git
```

### Step 2: Copy Files

Copy the `custom_components/tuya_ble_mesh` directory to your Home Assistant config:

```bash
# If downloaded as zip
unzip tuya-ble-mesh.zip
cp -r custom_components/tuya_ble_mesh /path/to/homeassistant/config/custom_components/

# If cloned via git
cp -r tuya-ble-mesh/custom_components/tuya_ble_mesh /path/to/homeassistant/config/custom_components/
```

### Step 3: Restart

Restart Home Assistant to load the new integration.

## Configuration

### Adding Devices

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for **"Tuya BLE Mesh"**
4. Follow the configuration wizard

### Configuration Options

| Option | Description | Default | Required |
|--------|-------------|---------|----------|
| **Device Type** | Light, Plug, or Bridge | Light | Yes |
| **MAC Address** | BLE MAC address (XX:XX:XX:XX:XX:XX) | - | Yes |
| **Bridge Host** | IP/hostname of bridge daemon | - | Yes* |
| **Bridge Port** | Bridge HTTP port | 8787 | No |
| **Mesh Name** | Mesh network name | out_of_mesh | No |
| **Mesh Password** | Mesh password | 123456 | No |
| **Vendor ID** | Vendor hex ID | 0x1001 | No |

*Not required if using ESPHome BLE Proxy with SIG Mesh devices.

### Connection Modes

#### Mode 1: Bridge Daemon (Raspberry Pi)

Best for Telink/Tuya proprietary mesh devices.

1. Set up a Raspberry Pi with Bluetooth
2. Install bridge daemon:
   ```bash
   cd ~/malmbergs-bt
   python scripts/ble_mesh_daemon.py --host 0.0.0.0 --port 8787
   ```
3. Configure integration with bridge IP and port

#### Mode 2: ESPHome BLE Proxy

Best for SIG Mesh standard devices. Simpler setup, no separate bridge needed.

1. Flash an ESP32 with ESPHome BLE Proxy firmware
2. Home Assistant will automatically discover BLE devices
3. Configure integration directly (no bridge host needed)

### Finding Device MAC Addresses

#### Option 1: Home Assistant Bluetooth Discovery

1. Go to **Settings** → **Devices & Services**
2. Check **Discovered** devices
3. Look for devices starting with `out_of_mesh*` or `tymesh*`

#### Option 2: Command Line (Linux/macOS)

```bash
# On Home Assistant host or Raspberry Pi
bluetoothctl
scan on
# Wait for devices to appear, note the MAC address
scan off
exit
```

#### Option 3: Mobile App

Use nRF Connect (iOS/Android):
1. Install nRF Connect from app store
2. Open app and scan for devices
3. Look for devices advertising `fe07` service UUID

### Vendor IDs

Different brands use different vendor IDs:

| Brand | Vendor ID |
|-------|-----------|
| Malmbergs BT Smart | `0x1001` (default) |
| AwoX | `0x0160` |
| Dimond/retsimx | `0x0211` |

If the default doesn't work, try the vendor ID for your brand.

## Post-Installation

### Verify Installation

1. Check **Settings** → **Devices & Services**
2. Your device should appear under **Tuya BLE Mesh**
3. Entities should be created:
   - `light.<device_name>` or `switch.<device_name>`
   - `sensor.<device_name>_signal` (RSSI)

### Test Control

1. Go to **Developer Tools** → **States**
2. Find your light/switch entity
3. Toggle it on/off to verify control works

### Check Logs

If issues occur, check logs:

1. Go to **Settings** → **System** → **Logs**
2. Search for `tuya_ble_mesh`
3. Enable debug logging if needed (see Troubleshooting below)

## Troubleshooting

### Integration Not Found After Install

**Solution:**
- Ensure you copied files to `config/custom_components/tuya_ble_mesh/`
- Verify `manifest.json` exists in that directory
- Restart Home Assistant completely (not just reload)

### Device Not Connecting

**Solution:**
1. Verify MAC address is correct (use `bluetoothctl scan on`)
2. Ensure device is in provisioning mode (look for `out_of_mesh*` name)
3. Check bridge daemon is running (if using bridge mode)
4. Verify Bluetooth adapter is working:
   ```bash
   hciconfig hci0 up
   bluetoothctl list
   ```

### Commands Not Working

**Solution:**
1. Try different vendor ID (see table above)
2. Enable debug logging:
   ```yaml
   # configuration.yaml
   logger:
     default: warning
     logs:
       custom_components.tuya_ble_mesh: debug
       tuya_ble_mesh: debug
   ```
3. Check logs for protocol errors

### Bridge Connection Failed

**Solution:**
1. Verify bridge daemon is running:
   ```bash
   curl http://BRIDGE_IP:8787/status
   ```
2. Check firewall rules allow port 8787
3. Ensure bridge host is reachable from HA:
   ```bash
   ping BRIDGE_IP
   ```

### Factory Reset Needed

If device is in unknown state:

1. Power cycle 5 times rapidly (within 10 seconds)
2. Device should enter provisioning mode (`out_of_mesh*`)
3. Re-add via config flow

## Updating

### Via HACS

1. Open **HACS** → **Integrations**
2. Find **Tuya BLE Mesh**
3. Click **Update** if available
4. **Restart Home Assistant**

### Manual Update

1. Download latest release
2. Replace `custom_components/tuya_ble_mesh/` with new version
3. Restart Home Assistant

## Uninstalling

1. Remove all devices via **Settings** → **Devices & Services**
2. Delete integration: click three-dot menu → **Delete**
3. (Optional) Delete `custom_components/tuya_ble_mesh/` folder
4. Restart Home Assistant

## Next Steps

- [Configure automations](SERVICES.md#using-services-in-automations)
- [Add more devices](SUPPORTED_DEVICES.md)
- [Enable debug features](TESTING.md)
- [Contribute](../CONTRIBUTING.md)

## Support

- [GitHub Issues](https://github.com/11z4t/tuya-ble-mesh/issues)
- [Home Assistant Community](https://community.home-assistant.io)
- [Documentation](https://github.com/11z4t/tuya-ble-mesh/tree/main/docs)
