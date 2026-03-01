# Project Specification — Malmbergs BT

## 1. Goal

Build a HACS-compatible Home Assistant integration that provides fully
local control of Malmbergs BT Smart lighting products via Tuya BLE Mesh.
No cloud dependency. No Tuya account required for daily operation.

---

## 2. Background

Malmbergs Elektriska sells BLE-controlled lighting under the "BT Smart"
brand. These products use Tuya BLE Mesh firmware internally. The official
Tuya Smart app controls them via cloud, which introduces latency, requires
an internet connection, and depends on Tuya's servers remaining available.

This project replaces cloud control with direct BLE communication from a
Raspberry Pi running Home Assistant, keeping all data and control local.

---

## 3. Acceptance Criteria

The project is complete when all of the following are met:

### Must Have (MVP)

- [ ] **AC-1:** Discover Malmbergs BT Smart devices via BLE scanning
- [ ] **AC-2:** Provision (pair) a device locally without Tuya Cloud
      dependency, OR extract keys via one-time cloud/app interaction
- [ ] **AC-3:** Turn a light on and off via Home Assistant UI
- [ ] **AC-4:** Set brightness (0–100%) via Home Assistant UI
- [ ] **AC-5:** Set color temperature (warm–cool) via Home Assistant UI
- [ ] **AC-6:** Device state survives HA restart (reconnects automatically)
- [ ] **AC-7:** Installable via HACS (custom repository)
- [ ] **AC-8:** All secrets stored in 1Password, never in config files
- [ ] **AC-9:** All checks pass: ruff, mypy --strict, bandit, safety,
      detect-secrets, pytest

### Should Have

- [ ] **AC-10:** Support multiple devices on the same mesh network
- [ ] **AC-11:** Device auto-discovery via HA Bluetooth integration
- [ ] **AC-12:** Transition effects (gradual brightness/temperature changes)
- [ ] **AC-13:** Entity attributes show firmware version and signal strength

### Could Have

- [ ] **AC-14:** Support additional Malmbergs BT product types (plugs,
      switches) via YAML profiles without code changes
- [ ] **AC-15:** Mesh relay support (devices forward messages to extend range)
- [ ] **AC-16:** Factory reset a device from HA (via Shelly power cycling)

### Won't Have (out of scope)

- Cloud-based control or Tuya Cloud integration for daily use
- Support for non-Malmbergs Tuya devices (may work, but not tested)
- OTA firmware updates
- Zigbee, Z-Wave, or WiFi device support
- Mobile app

---

## 4. Constraints

### Hardware

| Component | Requirement |
|-----------|-------------|
| Platform | Raspberry Pi 4 (aarch64) |
| BLE adapter | Built-in hci0 (RPi 4 Bluetooth) |
| BLE sniffer | Adafruit nRF51822 via serial (/dev/ttyUSB0) — development only |
| Power control | Shelly Plug S (192.168.1.50) — development only |
| Target device | Malmbergs LED Driver 9952126 (DC:23:4D:21:43:A5) |

### Software

| Component | Requirement |
|-----------|-------------|
| Python | 3.11+ (lab runs 3.13.5) |
| Home Assistant | 2024.1+ (current at time of development) |
| BLE library | bleak (async, cross-platform) |
| Secrets | 1Password CLI v2 via service account |
| OS | Raspberry Pi OS (Debian-based, aarch64) |

### Architectural Constraints

These are non-negotiable. See `docs/ARCHITECTURE.md` section 0 for details.

- **S1:** `lib/` never imports HA or `custom_components`
- **S3:** BLE byte parsing only in `protocol.py`
- **S4:** Crypto only in `crypto.py`
- **S5:** Async everywhere — no blocking I/O
- **S6:** Type hints on all functions — `mypy --strict` must pass
- **S7:** Custom exceptions only — no bare `Exception`
- **S8:** Devices via YAML profiles, not code changes
- **S10:** Secrets via `secrets.py` and 1Password only
- **S11:** Headless design — no `input()` or interactive prompts

### Security Constraints

Non-negotiable. See `docs/SECURITY.md` section 0 for details.

- **RULE 0:** Never expose secrets in terminal output (AI context leakage)
- **RULE 1:** All secrets via 1Password
- **RULE 2:** Never log or print secret values

---

## 5. Technical Approach

### 5.1 Layer Architecture

```
scripts/               CLI tools for lab use (scan, sniff, power_cycle)
custom_components/     HA integration wrapper (light platform)
lib/tuya_ble_mesh/     Standalone BLE mesh library (zero HA dependency)
profiles/              YAML device capability definitions
```

See `docs/ARCHITECTURE.md` for full details.

### 5.2 GATT Services

Confirmed via GATT enumeration on 2026-03-01 (see `docs/PROTOCOL.md`):

| Service | UUID | Role |
|---------|------|------|
| Generic Access Profile | `0x1800` | Device name, appearance |
| Tuya Custom (Telink) | `00010203-...-0d1910` | Proprietary mesh service |
| Device Information | `0x180A` | Firmware (1.6), model, manufacturer |

**Not present:** SIG Mesh Provisioning (0x1827), SIG Mesh Proxy (0x1828).

### 5.3 Data Points (DPs)

Expected DPs for the Malmbergs LED Driver (category `dj`):

| DP ID | Function | Type | Range | HA Entity Attribute |
|-------|----------|------|-------|---------------------|
| 1 | Power | Boolean | on/off | `is_on` |
| 2 | Mode | Enum | 0=white | `color_mode` |
| 3 | Brightness | Value | 10–1000 | `brightness` (mapped to 0–255) |
| 4 | Color temp | Value | 0–1000 | `color_temp_kelvin` (mapped) |

See `docs/DOMAIN.md` section 6 for full DP specification.

### 5.4 Critical Unknowns

These must be resolved during Phase 1 before protocol implementation:

| # | Unknown | Risk | Fallback |
|---|---------|------|----------|
| 1 | SIG Mesh or proprietary Tuya Mesh? | Medium | Support both variants |
| 2 | Does provisioning require cloud token? | High | MITM Tuya app, extract keys once |
| 3 | Are encryption keys cloud-derived? | High | One-time cloud interaction + store in 1Password |
| 4 | Exact DPS IDs for this device model? | Low | Standard Tuya lighting DPS |

See `docs/DOMAIN.md` section 10 for the full investigation plan.

---

## 6. Development Phases

### Phase 0: Lab Setup (complete)

- [x] RPi 4 with Bluetooth and Python environment
- [x] BLE scanning and device detection (`scripts/scan.py`)
- [x] Passive BLE sniffing via serial sniffer (`scripts/sniff.py`)
- [x] Shelly power control for headless device cycling (`lib/tuya_ble_mesh/power.py`)
- [x] Factory reset via rapid power cycling (`scripts/factory_reset.py`)
- [x] Unit test framework with mocked I/O (`tests/unit/test_power.py`)
- [x] Project documentation (ARCHITECTURE, SECURITY, DOMAIN, TESTING, DECISIONS)
- [x] Validation pipeline (`scripts/run-checks.sh`, `pyproject.toml`) — all 7 checks passing

### Phase 1: Protocol Research (in progress)

- [x] Connect to device and enumerate GATT services/characteristics
- [x] Determine mesh variant (SIG Mesh vs proprietary) — **Tuya Proprietary (Telink)**
- [x] Capture and decode advertising data format
- [x] Read Device Information Service (firmware, chipset) — **FW 1.6, Telink**
- [x] Attempt local provisioning — plaintext write failed, encrypted handshake needed
- [ ] If cloud required: MITM Tuya app, extract mesh keys — likely not needed
- [x] Document all findings in `docs/DOMAIN.md` and `docs/PROTOCOL.md`
- [ ] Implement `lib/tuya_ble_mesh/protocol.py` with verified protocol

### Phase 2: Core Library

- [ ] Implement `lib/tuya_ble_mesh/crypto.py` — AES-CCM encrypt/decrypt
- [ ] Implement `lib/tuya_ble_mesh/secrets.py` — 1Password SecretsManager
- [ ] Implement `lib/tuya_ble_mesh/mesh.py` — connect, provision, send commands
- [ ] Create `profiles/led_driver_9952126.yaml` — device profile
- [ ] CLI tool: `scripts/mesh_control.py` — standalone light control
- [ ] Verify AC-1 through AC-5 work via CLI (no HA yet)

### Phase 3: HA Integration

- [ ] Implement `custom_components/tuya_ble_mesh/` — HA integration
- [ ] Config flow: discover and add devices
- [ ] Light platform: on/off, brightness, color temperature
- [ ] Connection management: auto-reconnect on HA restart
- [ ] HACS manifest and repository structure
- [ ] Verify AC-6, AC-7

### Phase 4: Polish

- [ ] Multi-device support (AC-10)
- [ ] HA Bluetooth auto-discovery (AC-11)
- [ ] Transition effects (AC-12)
- [ ] Entity attributes — firmware, RSSI (AC-13)
- [ ] Additional device profiles (AC-14)

---

## 7. HA Integration Structure

### Planned Files

```
custom_components/tuya_ble_mesh/
├── __init__.py          # Integration setup, config entry
├── manifest.json        # HACS metadata, dependencies
├── config_flow.py       # Discovery and configuration UI
├── light.py             # Light platform (on/off, brightness, CT)
├── const.py             # Constants (domain, platforms)
├── coordinator.py       # Data update coordinator
└── strings.json         # UI strings
```

### HA Entity Mapping

| HA Feature | Source | Notes |
|------------|--------|-------|
| `is_on` | DP 1 (Boolean) | Direct mapping |
| `brightness` | DP 3 (Value 10–1000) | Map to 0–255 |
| `color_temp_kelvin` | DP 4 (Value 0–1000) | Map to kelvin range |
| `color_mode` | DP 2 (Enum) | Likely always `COLOR_TEMP` for CW device |
| `supported_color_modes` | Device profile | From YAML: `{ColorMode.COLOR_TEMP}` |

### HACS Requirements

- `manifest.json` with correct `domain`, `version`, `dependencies`
- Repository structure matching HACS custom component layout
- `hacs.json` at repository root with `name` and `homeassistant` version

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Provisioning requires Tuya Cloud | Medium | High — blocks cloud-free goal | One-time key extraction via app MITM; store keys in 1Password |
| Encryption keys rotate | Low | High — would require periodic cloud access | Monitor key stability over time; implement re-keying if needed |
| Device firmware update breaks protocol | Low | Medium — device may become incompatible | Block OTA in integration; document known-good firmware |
| BLE range insufficient for mesh | Low | Medium — some devices unreachable | Use mesh relay (Phase 4); position RPi centrally |
| Tuya changes mesh protocol | Very Low | High — integration stops working | Pin to known firmware; community monitoring |
| 1Password service unavailable | Low | Low — can't retrieve new keys | Keys cached in memory during session; only affects cold start |

---

## 9. Success Metrics

| Metric | Target |
|--------|--------|
| Light on/off latency | < 500 ms from HA command to device response |
| Brightness change latency | < 500 ms |
| Connection reliability | Device stays connected for 24h+ without intervention |
| Reconnection time | < 30 seconds after HA restart |
| Code quality | All CI checks pass (ruff, mypy --strict, bandit, safety, detect-secrets, pytest) |
| Test coverage | > 80% line coverage for `lib/tuya_ble_mesh/` |

---

## 10. Related Documents

| Document | Purpose |
|----------|---------|
| `CLAUDE.md` | Development rules and quick reference |
| `docs/ARCHITECTURE.md` | Structural rules, layer architecture, module overview |
| `docs/SECURITY.md` | Security rules, 1Password integration, threat model |
| `docs/DOMAIN.md` | Tuya BLE Mesh protocol knowledge |
| `docs/TESTING.md` | Test plan, patterns, validation pipeline |
| `docs/DECISIONS.md` | Architectural decision records |
| `docs/SETUP_STATUS.md` | Current hardware and software status |
| `TODO.md` | Outstanding manual tasks (1Password setup, NAS mount) |
