# Architecture — Tuya BLE Mesh

## Section 0: Structural Rules

These rules are mandatory. Every file and function must comply.
CLAUDE.md contains a summary; this is the authoritative reference.

### S1: Library Isolation

`lib/` is a standalone Python package. It MUST NOT import from
`homeassistant`, `custom_components`, or any Home Assistant dependency.

```python
# lib/tuya_ble_mesh/mesh.py

# FORBIDDEN:
from homeassistant.core import HomeAssistant
from custom_components.tuya_ble_mesh import something

# ALLOWED:
import asyncio
import aiohttp
from tuya_ble_mesh.power import ShellyPowerController
```

**Why:** The library must be usable outside HA — in scripts, tests, and
standalone tools. HA-specific code belongs exclusively in
`custom_components/tuya_ble_mesh/`.

### S3: BLE Byte Parsing in protocol.py Only

All raw BLE byte parsing and construction MUST live in
`lib/tuya_ble_mesh/protocol.py`. No other module may use `struct.pack`,
`struct.unpack`, or manual byte slicing on BLE protocol data.

```python
# FORBIDDEN in mesh.py, crypto.py, or anywhere else:
opcode = data[0]
length = struct.unpack("<H", data[1:3])[0]

# ALLOWED — call protocol.py:
from tuya_ble_mesh.protocol import parse_mesh_command
cmd = parse_mesh_command(data)
```

**Why:** Protocol logic is the most error-prone part of BLE development.
Centralizing it makes bugs easier to find and fixes easier to verify.

**Note:** `scripts/sniff.py` contains nRF Sniffer protocol parsing (SLIP
frames, sniffer headers). This is sniffer-specific protocol handling, not
Tuya BLE Mesh protocol. S3 applies to Tuya mesh protocol data in `lib/`.

### S4: Crypto in crypto.py Only

All cryptographic operations (AES, key derivation, nonce construction,
encrypt/decrypt) MUST live in `lib/tuya_ble_mesh/crypto.py`.

**Why:** Crypto code requires special review. Isolating it enables focused
security auditing and prevents accidental misuse across modules.

### S5: Async Everywhere

All I/O operations MUST be async. No `time.sleep()`, no blocking `requests`,
no synchronous file I/O in library or integration code.

```python
# FORBIDDEN:
import time
time.sleep(5)
requests.get(url)

# REQUIRED:
await asyncio.sleep(5)
async with session.get(url) as resp: ...
```

Serial I/O (e.g., in sniff.py) uses `run_in_executor()` to wrap synchronous
pyserial calls without blocking the event loop.

**Why:** Home Assistant runs on a single asyncio event loop. Blocking calls
freeze the entire system.

### S6: Type Hints on Everything

Every function MUST have type hints on all parameters AND the return type.
`mypy --strict` must pass.

```python
# FORBIDDEN:
def power_cycle(controller, off_time):
    ...

# REQUIRED:
async def power_cycle(controller: ShellyPowerController, off_time: float) -> bool:
    ...
```

**Why:** Static typing catches bugs before runtime and serves as inline
documentation. `--strict` mode ensures no gaps.

### S7: Custom Exceptions Only

Never raise bare `Exception`, `ValueError`, `RuntimeError`, or other
built-in exceptions. Define a module-specific exception hierarchy.

```python
# FORBIDDEN:
raise Exception("Shelly unreachable")
raise ValueError("bad response")

# REQUIRED:
class PowerControlError(Exception):
    """Base exception for power control operations."""

class ShellyUnreachableError(PowerControlError):
    """Shelly device is not reachable on the network."""

raise ShellyUnreachableError(f"Cannot reach Shelly at {host}")
```

Pattern: one base exception per module, specific subclasses per failure mode.
See `lib/tuya_ble_mesh/power.py` and `scripts/sniff.py` for examples.

**Why:** Custom exceptions enable precise `except` clauses. Callers can catch
exactly the failures they can handle instead of suppressing everything.

**Exception (pun intended):** Catching `ValueError` or `RuntimeError` from
stdlib/third-party libraries is acceptable when immediately converted to our
domain exceptions. We control what we RAISE, but not what external libraries raise.

```python
# ACCEPTABLE — catching stdlib exceptions to convert:
try:
    key_bytes = bytes.fromhex(hex_string)  # raises ValueError
except ValueError:
    raise SecretAccessError(f"Invalid hex: {hex_string}") from None

try:
    loop = asyncio.get_running_loop()  # raises RuntimeError if no loop
except RuntimeError:
    _LOGGER.debug("No event loop running")

# FORBIDDEN — raising builtin exceptions ourselves:
raise ValueError("bad key")  # Never do this
raise RuntimeError("something failed")  # Never do this
```

### S8: Devices via YAML Profiles

New device types are added by creating a YAML profile in `profiles/`, not by
modifying Python code. The library reads profiles at runtime.

```yaml
# profiles/led_driver_9952126.yaml
name: "Malmbergs LED Driver"
model: "9952126"
category: "dj"
capabilities:
  - dimming
  - color_temperature
```

**Why:** Device definitions change frequently as new products are added.
YAML profiles keep device knowledge separate from protocol logic.

### S10: Secrets via secrets.py Only

All secret access (API keys, mesh keys, credentials) MUST go through
`lib/tuya_ble_mesh/secrets.py` and its `SecretsManager` class.
See `docs/SECURITY.md` section 0 (RULE 1) for full details.

**Why:** Single point of access enables auditing, rotation, and ensures
1Password is the only secrets source.

### S11: Headless Design

No `input()`, no interactive prompts, no `getpass()`. All scripts must run
unattended via CLI arguments, environment configuration, or 1Password.

```python
# FORBIDDEN:
key = input("Enter mesh key: ")
password = getpass.getpass()

# REQUIRED:
# Use argparse for configuration:
parser.add_argument("--host", default="192.168.1.50")

# Use 1Password for secrets:
key = await secrets_manager.get("mesh-key")
```

**Why:** The system runs headless on a Raspberry Pi. There is no interactive
terminal during normal operation.

---

## Layer Architecture

```
┌─────────────────────────────────────────────┐
│  scripts/                                   │  CLI tools (scan, sniff, power_cycle)
│  Uses lib/ directly. No HA dependency.      │
├─────────────────────────────────────────────┤
│  custom_components/tuya_ble_mesh/            │  Home Assistant integration wrapper
│  Imports from lib/. Adapts to HA platform.  │
├─────────────────────────────────────────────┤
│  lib/tuya_ble_mesh/                         │  Standalone BLE mesh library
│  Zero HA imports. Pure Python + asyncio.    │
└─────────────────────────────────────────────┘
```

**Dependency direction:** `scripts/` and `custom_components/` depend on
`lib/`. Never the reverse. `lib/` has no knowledge of its consumers.

**Third-party dependencies (lib/):** `aiohttp`, `bleak`, `pyserial`,
`cryptography` (planned). No HA packages.

---

## Module Overview

### lib/tuya_ble_mesh/

| Module | Status | Responsibility |
|--------|--------|----------------|
| `power.py` | Implemented | Shelly smart plug control (Gen1 + Gen2 auto-detect) |
| `protocol.py` | Planned | Tuya BLE Mesh protocol parsing and construction (S3) |
| `crypto.py` | Planned | AES encryption, key derivation, nonce handling (S4) |
| `secrets.py` | Planned | 1Password integration via `SecretsManager` (S10) |
| `mesh.py` | Planned | Mesh network operations (connect, provision, command) |

### scripts/

| Script | Purpose |
|--------|---------|
| `scan.py` | BLE scanning with Tuya device detection (bleak) |
| `sniff.py` | Passive BLE sniffer via Adafruit nRF51822 serial (SLIP) |
| `power_cycle.py` | Power cycle device via Shelly, optional BLE verification |
| `factory_reset.py` | Rapid power cycling for Malmbergs factory reset |

### custom_components/tuya_ble_mesh/

| Module | Status | Responsibility |
|--------|--------|----------------|
| `__init__.py` | Implemented | Integration setup (`async_setup_entry`/`async_unload_entry`), `sys.path` for lib/ |
| `const.py` | Implemented | DOMAIN, PLATFORMS, config keys, brightness/color temp mapping constants |
| `manifest.json` | Implemented | HACS metadata, bluetooth discovery patterns, dependencies |
| `config_flow.py` | Implemented | Bluetooth discovery + manual setup, MAC validation |
| `coordinator.py` | Implemented | Push-based BLE device lifecycle (NOT DataUpdateCoordinator) |
| `light.py` | Implemented | Light entity — on/off, brightness, color temp (duck-typed) |
| `sensor.py` | Implemented | RSSI (dBm) + firmware version sensors (duck-typed) |
| `diagnostics.py` | Implemented | Config entry diagnostics with mesh_name/mesh_password redaction |
| `icon.svg` | Implemented | SVG placeholder icon with "TBM" text |
| `strings.json` | Implemented | English UI strings |
| `translations/sv.json` | Implemented | Swedish translation |

### tests/

| Directory | Purpose |
|-----------|---------|
| `tests/unit/` | Fast, isolated tests with mocked I/O |
| `tests/integration/` | Tests against real hardware (Shelly, BLE) |
| `tests/security/` | Implemented — input fuzzing, secret leak detection |

---

## Exception Hierarchy

All module exceptions inherit from `TuyaBLEMeshError` (the project-wide
base exception). Each module adds its own subtree:

```
TuyaBLEMeshError (project-wide base)
├── PowerControlError
│   ├── ShellyUnreachableError
│   └── ShellyCommandError
├── SnifferError
│   ├── SnifferNotFoundError
│   ├── SnifferProtocolError
│   └── SnifferTimeoutError
├── MeshError
│   ├── MeshConnectionError
│   ├── MeshProvisionError
│   └── MeshCommandError
├── CryptoError
│   ├── KeyDerivationError
│   └── DecryptionError
├── SecretsError
│   ├── SecretNotFoundError
│   └── VaultAccessError
├── ProtocolError
│   ├── InvalidPacketError
│   └── ChecksumError
└── ScanError
    ├── AdapterNotFoundError
    └── ScanTimeoutError
```

Callers catch the base exception for general handling or specific subclasses
for targeted recovery.

---

## Test Strategy

- **Unit tests** (`tests/unit/`): Mock all I/O. Test logic in isolation.
  See `test_power.py` for the established pattern using `unittest.mock`.
- **Integration tests** (`tests/integration/`): Run against real hardware.
  Guarded by markers/fixtures that skip when hardware is unavailable.
- **Security tests** (`tests/security/`): Planned. Verify no secret leakage
  in logs, no hardcoded credentials, bandit compliance.
- **CI pipeline** (`scripts/run-checks.sh`): ruff, mypy --strict, bandit,
  safety, detect-secrets, pytest. All must pass before commit.

See `docs/TESTING.md` for detailed test plan and cases.

---

## Hardware

| Component | Interface | Role |
|-----------|-----------|------|
| RPi 4 Bluetooth (hci0) | HCI / bleak | Active BLE communication |
| Adafruit nRF51822 Sniffer | Serial /dev/ttyUSB0 (CP210x, 460800 baud) | Passive BLE packet capture |
| Shelly Plug S (192.168.1.50) | HTTP REST (Gen1, no auth) | Remote power control for device cycling |
| Malmbergs LED Driver 9952126 | BLE (DC:23:4D:21:43:A5) | Target device under development |
| NAS (192.168.5.220) | CIFS/SMB 3.0 → /mnt/solutions | Shared storage |

See `docs/SETUP_STATUS.md` for current hardware and software status.
