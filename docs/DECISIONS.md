# Architectural Decisions — Malmbergs BT

This log records key decisions, their rationale, and alternatives
considered. New entries go at the bottom. Use this when stuck or when
a decision needs to be revisited (see CLAUDE.md "When Stuck" step 4).

---

## ADR-001: Standalone Library + HA Wrapper

**Date:** 2026-03-01
**Status:** Accepted

### Decision

Separate the project into a standalone library (`lib/tuya_ble_mesh/`)
and a Home Assistant integration wrapper (`custom_components/malmbergs_bt/`).

### Context

The project needs to work both as an HA integration and as standalone
CLI tools for lab testing, sniffing, and device provisioning.

### Alternatives Considered

1. **Single HA integration** — All code in `custom_components/`. Simpler
   structure but forces HA dependency for CLI usage and makes testing harder.
2. **Separate pip package** — Publish the library independently. Premature;
   adds packaging overhead with no external consumers.

### Rationale

- Lab scripts (`scan.py`, `sniff.py`, `power_cycle.py`) use the library
  directly without HA installed
- Unit tests run without HA dependency (faster, simpler CI)
- Enforced via rule S1: `lib/` never imports `homeassistant`

---

## ADR-002: 1Password as Sole Secrets Source

**Date:** 2026-03-01
**Status:** Accepted

### Decision

All secrets are stored in and read from 1Password. No `.env` files, no
environment variables for secret values, no config file secrets.

### Context

Claude Code sends terminal output to an AI API. Any secret displayed in
the terminal leaks to the AI provider. Traditional approaches (`.env`,
environment variables) are unsafe because a simple `echo` or `env` command
exposes them.

### Alternatives Considered

1. **`.env` file** — Standard approach, but `cat .env` leaks everything
   to AI context. A single accident is irrecoverable.
2. **Encrypted secrets file** — Requires a decryption key, which has the
   same storage problem. Adds complexity without solving the root issue.
3. **Environment variables only** — `echo $VAR` or `env` leaks them.
   Also lost on reboot unless persisted somewhere (back to files).

### Rationale

- 1Password CLI (`op read`) retrieves secrets without displaying them if
  output is redirected (`> /dev/null`)
- Service account token is the only bootstrap secret (set once in tmux)
- Centralized rotation: change a secret in one place
- Audit trail via 1Password

---

## ADR-003: Shelly Smart Plug for Headless Power Control

**Date:** 2026-03-01
**Status:** Accepted

### Decision

Use a Shelly Plug S (HTTP-controlled smart plug) to power cycle the
Malmbergs LED Driver remotely, enabling headless factory reset and
device cycling.

### Context

The lab runs on a Raspberry Pi with no physical access to the LED driver
during development. Factory reset requires 3–5 rapid power cycles.
Manual power cycling is impractical.

### Alternatives Considered

1. **USB-controlled relay** — Requires additional hardware and a USB port.
   RPi has limited USB ports (sniffer already uses one).
2. **GPIO relay** — Requires wiring and a relay module. Higher effort,
   more fragile, requires GPIO access.
3. **Zigbee/Z-Wave plug** — Requires a coordinator. Adds protocol
   complexity unrelated to the BLE mesh goal.

### Rationale

- Shelly Plug S was already available on the network
- Simple HTTP REST API, no authentication required (Gen1, auth: false)
- Supports both Gen1 and Gen2 via auto-detection
- No additional hardware or wiring needed
- `aiohttp` (already a dependency for future Tuya cloud calls) handles HTTP

---

## ADR-004: Adafruit nRF51822 as Serial Sniffer (Not HCI)

**Date:** 2026-03-01
**Status:** Accepted

### Decision

Treat the Adafruit Bluefruit LE Sniffer as a serial device using the
Nordic nRF Sniffer SLIP protocol, not as an HCI Bluetooth adapter.

### Context

Initial assumption was that the Adafruit sniffer would appear as an HCI
device. It does not — it uses a CP210x UART bridge and speaks the Nordic
nRF Sniffer v2 protocol over SLIP-encoded serial at 460800 baud.

### Alternatives Considered

1. **HCI-based sniffing** — Use `hcitool`/`btmon` with the sniffer as an
   HCI adapter. Does not work; the device is not an HCI adapter.
2. **Wireshark only** — Use Wireshark's nRF Sniffer plugin. Works but
   requires a GUI. Incompatible with headless RPi operation.

### Rationale

- Serial + SLIP is how the hardware actually works
- Custom `SnifferReader` class allows headless operation
- Raw packet access enables future protocol analysis
- `pyserial` wrapped in `run_in_executor()` keeps async compatibility (S5)
- Corrected in commit `b3012de`

---

## ADR-005: Async Everywhere (asyncio)

**Date:** 2026-03-01
**Status:** Accepted

### Decision

All I/O operations use `asyncio`. No `time.sleep()`, no blocking
`requests`, no synchronous serial I/O in the main thread.

### Context

Home Assistant runs on a single asyncio event loop. Blocking calls freeze
the entire system. Even lab scripts benefit from concurrent operations
(e.g., scanning BLE while controlling Shelly).

### Alternatives Considered

1. **Sync library + async wrapper** — Write sync code, wrap in
   `run_in_executor()` at the HA boundary. Duplicates effort and makes
   the library harder to compose.
2. **Threading** — Use threads for concurrency. Harder to reason about,
   doesn't compose with HA's event loop.

### Rationale

- HA requires it — no choice for the integration
- `bleak` (BLE) is natively async
- `aiohttp` (HTTP) is natively async
- `pyserial` (sync) is easily wrapped via `run_in_executor()`
- One concurrency model everywhere reduces cognitive load

---

## ADR-006: Custom Exceptions Over Built-in Exceptions

**Date:** 2026-03-01
**Status:** Accepted

### Decision

Every module defines its own exception hierarchy rooted in a base
exception. Never raise `Exception`, `ValueError`, `RuntimeError`, etc.

### Context

Catching broad exception types (`except Exception`) suppresses unrelated
errors. It also makes it impossible to handle specific failures differently
(e.g., retry on network timeout but abort on protocol error).

### Alternatives Considered

1. **Built-in exceptions** — `ValueError`, `IOError`, etc. Callers can't
   distinguish "Shelly unreachable" from an unrelated `IOError`.
2. **Single project-wide exception** — One `MalmbergsBTError` for
   everything. Too coarse; loses the ability to handle failures precisely.

### Rationale

- `ShellyUnreachableError` vs `ShellyCommandError` enables different
  recovery strategies (retry network vs report API error)
- `pytest.raises(SpecificError)` makes tests precise
- Pattern established in `power.py` and `sniff.py`, consistent across all
  planned modules

---

## ADR-007: YAML Device Profiles Over Hardcoded Device Definitions

**Date:** 2026-03-01
**Status:** Accepted

### Decision

Define device types (capabilities, DPS IDs, model info) in YAML files
under `profiles/`, not in Python code.

### Context

Malmbergs sells multiple BLE products. Each has different capabilities
(dimming, color temp, RGB, on/off only). Adding a new product should not
require code changes.

### Alternatives Considered

1. **Python classes per device** — One class per product model. Requires
   code changes and releases for each new product.
2. **Database** — Overkill for a small, static device catalog.
3. **JSON** — Works but less readable than YAML for human-edited
   configuration with comments.

### Rationale

- New devices added by non-developers (or AI) with a simple YAML file
- YAML supports comments for documenting DPS ID meanings
- Profile loading is simple: `yaml.safe_load()` at startup
- Keeps protocol code generic and profile data declarative

---

## ADR-008: bleak for BLE Communication

**Date:** 2026-03-01
**Status:** Accepted

### Decision

Use `bleak` as the BLE GATT client library.

### Context

The project needs to scan, connect, and communicate with BLE devices from
Python on a Raspberry Pi (Linux/BlueZ).

### Alternatives Considered

1. **pygatt** — Older library, less actively maintained, limited async
   support.
2. **bluepy** — Linux only, no async, abandoned upstream.
3. **Direct D-Bus/BlueZ** — Maximum control but enormous implementation
   effort for GATT operations.

### Rationale

- Cross-platform (Linux, macOS, Windows) — useful for development
- Natively async (fits S5)
- Active maintenance and large community
- Used by multiple existing Tuya BLE projects (`ha_tuya_ble`, etc.)
- Already installed and verified in the lab environment

---

## ADR-009: AI Context Security Model

**Date:** 2026-03-01
**Status:** Accepted

### Decision

Treat the development terminal as an untrusted output channel. Any
command output may be sent to an external AI API. Design all tooling
and practices around this constraint.

### Context

This project is developed using Claude Code, an AI coding assistant that
includes terminal output in its API context. This is fundamentally
different from traditional development where terminal output is local.

### Alternatives Considered

1. **Ignore the risk** — Treat the terminal as trusted. Unacceptable;
   a single `echo $KEY` permanently leaks the secret.
2. **Disable Claude Code for secret-touching work** — Impractical;
   almost all code touches secrets indirectly.
3. **Scrub terminal output** — Complex, error-prone, and doesn't protect
   against new command patterns.

### Rationale

- Rules 0–2 (SECURITY.md) encode this model
- 1Password + `op read` with redirection is the only safe secret retrieval
- All exceptions redact secret values
- All logging avoids secret values
- This is a project-wide constraint, not an afterthought

---

## ADR-010: Mesh Variant — Tuya Proprietary (Telink-based)

**Date:** 2026-03-01
**Status:** Accepted

### Decision

Target the Tuya Proprietary Mesh protocol with Telink-based GATT UUIDs
for the Malmbergs LED Driver 9952126. Do not implement SIG Mesh provisioning.

### Context

The device could use either SIG Mesh (standard Bluetooth Mesh) or Tuya's
proprietary mesh protocol. The provisioning flow, encryption, and command
protocol differ significantly between the two variants. We needed to
determine which variant before implementing the protocol layer.

### Evidence

GATT enumeration on 2026-03-01 confirmed:

- **No** SIG Mesh Provisioning Service (0x1827) present
- **No** SIG Mesh Proxy Service (0x1828) present
- Tuya custom service present using Telink UUID base
  (`00010203-0405-0607-0809-0a0b0c0d1910`)
- Characteristic pattern matches documented Tuya proprietary mesh (1910–1914)
- Device advertises as `out_of_mesh` (proprietary mesh default name)
- No 0xFE07 service UUID in advertising data
- Device Information: firmware 1.6, product ID "model id 123"

### Alternatives Considered

1. **SIG Mesh implementation** — Would use `bluetooth_mesh` library with
   PB-GATT provisioning. Rejected: the device does not expose SIG Mesh
   services, so this path is not available.
2. **Dual implementation** — Support both variants. Rejected: unnecessary
   complexity when the hardware clearly uses one variant. Can be revisited
   if other Malmbergs devices use SIG Mesh.

### Rationale

- Hardware evidence is unambiguous — only proprietary Tuya services present
- Reference implementations exist for the same Telink UUID base
  (`python-awox-mesh-light`, `retsimx/tlsr8266_mesh`)
- Simpler than SIG Mesh (no ECDH, no NetKey/AppKey hierarchy)
- The `classify_mesh_variant()` function in `explore_device.py` detects
  both variants, so future SIG Mesh devices can be identified if needed

---

## Template

Use this template when adding new decisions:

```markdown
## ADR-NNN: Title

**Date:** YYYY-MM-DD
**Status:** Proposed | Accepted | Superseded by ADR-XXX | Deprecated

### Decision

What was decided.

### Context

Why a decision was needed.

### Alternatives Considered

1. **Option A** — Description. Why rejected.
2. **Option B** — Description. Why rejected.

### Rationale

Why this option was chosen.
```
