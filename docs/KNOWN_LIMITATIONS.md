# Known Limitations

This document lists known limitations, constraints, and unsupported features of the Tuya BLE Mesh integration for Home Assistant. Understanding these limitations helps set realistic expectations and guides troubleshooting.

## Table of Contents

- [Bluetooth Limitations](#bluetooth-limitations)
- [Protocol Limitations](#protocol-limitations)
- [Hardware Limitations](#hardware-limitations)
- [Feature Limitations](#feature-limitations)
- [Platform Limitations](#platform-limitations)
- [Workarounds and Mitigations](#workarounds-and-mitigations)

---

## Bluetooth Limitations

### 1. Maximum Device Count per Adapter

**Limitation**: Home Assistant (via Bleak/BlueZ) supports approximately **5-7 concurrent BLE connections** per Bluetooth adapter.

**Impact**:
- If you have more than 5 Tuya BLE Mesh devices, you'll need multiple Bluetooth adapters or ESPHome BLE proxies
- Connection limits are enforced by the Linux kernel and BlueZ stack, not the integration

**Why**: BLE adapters have limited connection slots in hardware and firmware. The exact limit varies by chipset:
- Broadcom BCM43455 (RPi 4): ~5 connections
- Intel AX200/AX210: ~7 connections
- Nordic nRF52840: ~8 connections (as ESPHome proxy)

**Workaround**:
- Use **ESPHome BLE proxies** to distribute load across multiple ESP32 devices
- Add a **USB Bluetooth adapter** for additional connection capacity
- Use the **bridge daemon** mode with a dedicated Raspberry Pi

### 2. Bluetooth Range

**Limitation**: BLE range is typically **10-30 meters indoors**, heavily affected by walls, metal, and interference.

**Impact**:
- Devices far from the HA host or proxy may disconnect frequently
- RSSI below -80 dBm indicates weak signal and unreliable connectivity
- Concrete walls, metal studs, and water pipes severely attenuate BLE signals

**Workaround**:
- Deploy **ESPHome BLE proxies** throughout your home for extended coverage
- Use the **bridge daemon** on a centrally located Raspberry Pi
- Monitor RSSI sensors and relocate devices or proxies as needed

### 3. No Direct HA Bluetooth Access (Telink Mesh)

**Limitation**: Home Assistant **cannot directly communicate with Telink proprietary mesh** devices. A **bridge daemon** is required.

**Impact**:
- Telink Mesh devices (like Malmbergs 9952126 LED Driver) need a separate bridge process running on a Raspberry Pi or similar device
- HA communicates with the bridge over HTTP, which adds latency (typically 50-150ms)

**Why**: Telink's proprietary mesh protocol requires specific vendor SDK code that isn't part of the standard BlueZ stack.

**Workaround**:
- Run the **bridge daemon** (`bridge_daemon.py`) on a device with Bluetooth near your mesh devices
- The integration auto-detects and falls back to bridge mode when needed

### 4. ESPHome Proxy — SIG Mesh Only

**Limitation**: ESPHome BLE proxies **only support standard SIG Mesh devices**, not Telink proprietary mesh.

**Impact**:
- Devices advertising as `out_of_mesh*` (SIG Mesh) work with ESPHome proxies
- Devices using Telink mesh (vendor-specific protocol) require the bridge daemon

**Which devices are SIG vs. Telink**:
- **SIG Mesh**: Malmbergs Smart Plug S17 (9917072), devices with GATT services 0x1827/0x1828
- **Telink Mesh**: Malmbergs LED Driver (9952126), older devices with `fe07` service only

**Workaround**:
- Use ESPHome proxies for SIG Mesh devices
- Use bridge daemon for Telink Mesh devices
- The integration supports **both simultaneously** on the same HA instance

---

## Protocol Limitations

### 1. No OTA Firmware Updates

**Limitation**: The integration does **not support** over-the-air (OTA) firmware updates for Tuya BLE Mesh devices.

**Impact**:
- Firmware versions are tracked via the `update` entity but cannot be upgraded via HA
- Security patches or feature updates require using the Tuya Smart app (cloud) or manufacturer's tools

**Why**: Tuya's OTA protocol uses proprietary encryption and staging servers. The vendor SDK is required and not publicly documented.

**Workaround**:
- Use the **Tuya Smart app** temporarily to perform firmware updates
- Disconnect from cloud after updating and return to local control via this integration

### 2. CCT (Color Temperature) DP ID Variability

**Limitation**: The **Data Point (DP) ID for color temperature control** varies between device models and manufacturers.

**Impact**:
- Some devices may not respond to CCT commands even if they support warm/cool white
- Manual vendor ID trial-and-error may be needed (see troubleshooting docs)

**Known DP IDs**:
- DP `0x05`: Most common (Tuya standard)
- DP `0x06`: Alternate on some AwoX models
- DP `0x07`: Rare, seen on Dimond/retsimx devices

**Workaround**:
- Try different **vendor IDs** during setup: `0x1001` (Malmbergs), `0x0160` (AwoX), `0x0211` (Dimond)
- Check device-specific YAML profiles in `profiles/`
- Use diagnostics data to inspect actual DP responses

### 3. Limited RGB Support

**Limitation**: RGB color control is **protocol-supported but minimally tested** due to lack of RGB hardware.

**Impact**:
- RGB commands may not work on all devices
- Color accuracy and gamut mapping are not validated

**Status**: RGB DP structure is documented in `docs/PROTOCOL.md` but requires real hardware testing.

**Workaround**:
- Test with your specific RGB device model
- Report issues with device model details to help improve support

### 4. Group Addressing (Mesh Groups)

**Limitation**: BLE Mesh **group addresses** (multicast) are not fully implemented.

**Impact**:
- You cannot send a single command to multiple devices simultaneously via mesh groups
- Controlling multiple devices requires separate commands per device

**Why**: Group provisioning and subscription list management add protocol complexity. Single-device control works reliably and was prioritized.

**Workaround**:
- Use **Home Assistant light groups** (`light.group`) for bulk control
- HA sends sequential commands, typically fast enough for perceived simultaneity

---

## Hardware Limitations

### 1. Factory Reset Requires Manual Power Cycling

**Limitation**: Factory resetting a device requires **manual power cycling 5-10 times** in rapid succession.

**Impact**:
- No over-the-air factory reset command exists
- Physically inaccessible devices (ceiling lights) are difficult to reset
- Must be near the device to perform the reset sequence

**Procedure**:
1. Power off the device
2. Wait 1 second
3. Power on for 2 seconds
4. Power off for 1 second
5. Repeat steps 3-4 five to ten times
6. Device will blink/flash to confirm reset

**Workaround**:
- Use **Shelly smart plugs** or similar to automate power cycling via script (`scripts/factory_reset.py`)
- For ceiling fixtures, use a **smart circuit breaker** if feasible

### 2. BlueZ Version Requirements

**Limitation**: Requires **BlueZ 5.50 or newer** for reliable BLE Mesh support.

**Impact**:
- Older Debian/Ubuntu versions ship with BlueZ 5.43-5.48, which have mesh provisioning bugs
- Devices may fail to connect or disconnect randomly on older BlueZ versions

**Affected Platforms**:
- Ubuntu 18.04 LTS: BlueZ 5.48 (buggy)
- Debian 10 (Buster): BlueZ 5.50 (minimum acceptable)
- Raspberry Pi OS (32-bit, older): BlueZ 5.43 (upgrade required)

**Workaround**:
- Upgrade to **BlueZ 5.55+** (recommended)
- Use **Home Assistant OS** (ships with modern BlueZ)
- Manually compile BlueZ from source if distribution packages are outdated

### 3. USB Bluetooth Adapter Compatibility

**Limitation**: Not all USB Bluetooth adapters work reliably with BLE Mesh.

**Known Good Adapters**:
- ✅ Broadcom BCM20702A0 (generic USB dongles)
- ✅ Intel AX200/AX210 (M.2 cards)
- ✅ Nordic nRF52840 (via ESPHome)
- ✅ Raspberry Pi 4 built-in Bluetooth (BCM43455)

**Known Problematic Adapters**:
- ❌ CSR8510 chipset (spotty BLE 5.0 support)
- ❌ Realtek RTL8761B (firmware issues)
- ❌ Some no-name Chinese USB adapters (unreliable drivers)

**Workaround**:
- Use **tested adapters** from the known-good list
- Check `hciconfig` and `bluetoothctl` output for firmware errors
- Try **ESPHome BLE proxy** as an alternative to USB adapters

---

## Feature Limitations

### 1. No Scene Control via BLE Mesh

**Limitation**: BLE Mesh **scene recall** commands are not implemented.

**Impact**:
- You cannot activate pre-programmed device scenes (e.g., "Reading Mode") via HA
- Scenes must be built in Home Assistant using standard light/switch entities

**Why**: Tuya's scene model is proprietary and tied to the Tuya Smart app. The BLE protocol supports it, but mapping is undocumented.

**Workaround**:
- Use **Home Assistant scenes** and scripts instead
- See `docs/EXAMPLES.md` for scene examples

### 2. No Music Sync / Effects

**Limitation**: Dynamic effects (music sync, color cycling, etc.) are **not supported**.

**Impact**:
- Devices with built-in effects can only be controlled via the Tuya app
- HA can set static colors/brightness/CCT but not animated effects

**Workaround**:
- Use **Home Assistant automation scripts** to create simple effects (e.g., rainbow loop)
- See `docs/EXAMPLES.md` → "Identify Device via Service Call" for blink/rainbow examples

### 3. Energy Monitoring (Smart Plugs)

**Limitation**: Power/energy monitoring data is **not yet implemented** for smart plugs.

**Impact**:
- Plug devices show as switches (on/off) but don't report wattage, voltage, or kWh
- The protocol supports it (DP `0x04` typically), but data parsing isn't complete

**Status**: Planned for future release. See `ROADMAP.md`.

**Workaround**:
- Use **Shelly Plug S** or other HA-integrated smart plugs for energy monitoring
- Tuya mesh plugs work for on/off control only

### 4. No Cloud Account Sync

**Limitation**: This integration is **fully local**. It does not sync with the Tuya cloud or Tuya Smart app.

**Impact**:
- Devices configured in the Tuya app won't auto-appear in HA
- Automations created in the Tuya app are not visible in HA
- Firmware updates, OTA, and cloud features require temporary re-pairing with the app

**Why**: Cloud integration requires Tuya developer accounts, OAuth, and exposing your network to Tuya's servers. This integration prioritizes **privacy and local control**.

**Workaround**:
- Manually add devices to HA using their MAC addresses
- Use the Tuya app **only** for initial setup or firmware updates, then control locally

---

## Platform Limitations

### 1. Windows and macOS Support

**Limitation**: The integration is **developed and tested on Linux only**. Windows and macOS support is untested.

**Impact**:
- Bleak BLE library has Windows/macOS backends, but vendor-specific quirks may exist
- BlueZ-specific features (mesh provisioning) won't work on non-Linux platforms

**Status**: Community feedback welcome for Windows/macOS issues.

**Workaround**:
- Run **Home Assistant OS** (Linux-based) in a VM or on dedicated hardware
- Use **ESPHome BLE proxies** on ESP32 devices (platform-independent)

### 2. Home Assistant Supervised/Container Bluetooth Access

**Limitation**: Docker containers have **limited Bluetooth access** by default.

**Impact**:
- Bluetooth passthrough to containers requires extra configuration
- HA Supervised on Debian may need `--privileged` mode or device mapping

**Workaround**:
- Use **Home Assistant OS** (no container limitations)
- Use **ESPHome BLE proxies** instead of direct Bluetooth access
- Map `/dev/bus/usb` and use `--cap-add=NET_ADMIN` for USB Bluetooth dongles

---

## Workarounds and Mitigations

### Summary Table

| Limitation | Severity | Workaround Available | Planned Fix |
|------------|----------|----------------------|-------------|
| Max 5-7 devices per adapter | Medium | ✅ ESPHome proxies, USB adapters | No (hardware limit) |
| BLE range 10-30m | Medium | ✅ ESPHome proxies, bridge daemon | No (physics) |
| Bridge required for Telink Mesh | High | ✅ Auto-detection + fallback | No (vendor protocol) |
| ESPHome proxy = SIG Mesh only | Medium | ✅ Use both bridge + ESPHome | No (proxy limitation) |
| No OTA firmware updates | Low | ✅ Temporary Tuya app usage | Maybe (future) |
| CCT DP ID varies | Medium | ✅ Vendor ID trial-and-error | Partial (device profiles) |
| Manual factory reset only | Low | ✅ Automated power cycling script | No (protocol limit) |
| BlueZ 5.50+ required | High | ✅ Upgrade OS or BlueZ version | No (dependency) |
| No energy monitoring | Medium | ❌ Use separate power meter | ✅ Planned (v0.22+) |
| No cloud sync | Low | ✅ Manual device addition | No (by design) |
| Linux only | Low | ✅ Use HA OS or ESPHome proxy | Maybe (community) |

---

## Reporting New Limitations

If you discover a limitation not listed here:

1. Check existing issues: [GitHub Issues](https://github.com/11z4t/tuya-ble-mesh/issues)
2. Verify it's not a configuration issue: [Troubleshooting Guide](USER_GUIDE.md#troubleshooting)
3. Report with details:
   - Device model and vendor
   - HA version and OS
   - Integration logs (enable debug logging)
   - Expected vs. actual behavior

---

## Related Documentation

- [User Guide](USER_GUIDE.md) — Setup and troubleshooting
- [Supported Devices](SUPPORTED_DEVICES.md) — Tested hardware
- [ESPHome Proxy Guide](ESPHOME_PROXY.md) — Extend BLE coverage
- [Protocol Documentation](PROTOCOL.md) — Low-level protocol details
- [Examples](EXAMPLES.md) — Automation examples and workarounds
