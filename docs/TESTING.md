# Testing — Tuya BLE Mesh

## Overview

All code must be tested before commit. The validation pipeline
(`scripts/run-checks.sh`) runs the full suite: linters, type checker,
security scanners, and pytest. Every check must pass.

```bash
bash scripts/run-checks.sh   # ALL must pass. No exceptions.
```

---

## Test Structure

```
tests/
├── __init__.py
├── unit/                    # Fast, isolated, no hardware
│   ├── __init__.py
│   ├── test_power.py        ← lib/ tests (21 tests)
│   ├── test_exceptions.py
│   ├── test_protocol.py
│   ├── test_crypto.py
│   ├── test_secrets.py
│   ├── test_scanner.py
│   ├── test_mesh.py
│   ├── test_provisioner.py
│   ├── test_command.py
│   ├── test_ha_const.py     ← HA integration tests
│   ├── test_ha_init.py
│   ├── test_ha_coordinator.py
│   ├── test_ha_config_flow.py
│   ├── test_ha_light.py
│   ├── test_ha_sensor.py
│   └── test_ha_diagnostics.py
├── integration/             # Requires hardware / network
│   └── __init__.py
└── security/                # Input fuzzing, secret leak detection
    └── test_input_fuzzing.py
```

### Unit Tests (`tests/unit/`)

- Run on every commit
- All I/O is mocked — no network, no BLE, no serial, no filesystem
- Fast: entire suite completes in seconds
- No hardware or external services required

### Integration Tests (`tests/integration/`)

- Run manually or via explicit marker
- Require real hardware (Shelly plug, BLE adapter, sniffer)
- Skipped automatically when hardware is unavailable
- Use `pytest.mark.integration` marker

### Security Tests (`tests/security/`)

- Input fuzzing with malformed data (implemented)
- Verify no secret values in log output
- Verify no hardcoded credentials
- Verify exception messages don't leak secrets
- Complement the static analysis tools (bandit, detect-secrets)

---

## Running Tests

```bash
# All tests (unit only by default)
pytest tests/

# Unit tests only
pytest tests/unit/

# Integration tests (requires hardware)
pytest tests/integration/ -m integration

# Specific test file
pytest tests/unit/test_power.py

# Verbose with output
pytest tests/unit/ -v -s

# Full validation pipeline (linting + types + security + tests)
bash scripts/run-checks.sh
```

---

## Established Patterns

These patterns are demonstrated by `tests/unit/test_power.py` and must
be followed for all new tests.

### Mock Helpers

Create module-level helper functions for building mock objects:

```python
def make_mock_response(
    status: int = 200,
    json_data: dict | None = None,
) -> MagicMock:
    """Create a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp
```

### Test Class Organization

Group tests by method or feature using classes. One class per logical unit:

```python
class TestPowerOn:
    """Test power_on method."""

    @pytest.mark.asyncio
    async def test_gen1_power_on(self) -> None: ...

    @pytest.mark.asyncio
    async def test_gen2_power_on(self) -> None: ...
```

### Async Tests

All async tests use `@pytest.mark.asyncio`:

```python
@pytest.mark.asyncio
async def test_power_cycle_success(self) -> None:
    ctrl = ShellyPowerController("192.168.1.50")
    ctrl._generation = 1
    # ... mock setup ...
    result = await ctrl.power_cycle(off_seconds=0.01)
    assert result is True
```

### lib/ Path Setup

Tests that import from `lib/tuya_ble_mesh/` add the path at the top:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.power import ShellyPowerController
```

### HA Integration Path Setup

Tests that import from `custom_components/tuya_ble_mesh/` add the
project root to sys.path:

```python
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)

from custom_components.tuya_ble_mesh.const import DOMAIN  # noqa: E402
```

### Type Hints

All test functions and helpers must have type hints (rule S6):

```python
def make_mock_session(responses: list[MagicMock]) -> MagicMock: ...

async def test_gen1_power_on(self) -> None: ...
```

### Time-Sensitive Tests

Use short intervals to keep tests fast:

```python
# Real code uses 5.0 seconds; tests use 0.01
result = await ctrl.power_cycle(off_seconds=0.01)
```

---

## Test Cases by Module

### power.py — ShellyPowerController (implemented)

| Test Class | Cases | What It Covers |
|------------|-------|----------------|
| TestShellyInit | 2 | Constructor, host/base_url properties |
| TestDetectGeneration | 3 | Gen1 detection, Gen2 detection, caching |
| TestPowerOn | 2 | Gen1 power on, Gen2 power on |
| TestPowerOff | 2 | Gen1 power off success, Gen1 power off failure |
| TestPowerCycle | 2 | Full cycle success, off-fails-aborts |
| TestFactoryReset | 1 | Rapid cycling (3 cycles) |
| TestIsReachable | 2 | Reachable, unreachable |
| TestErrorHandling | 2 | HTTP 500 → ShellyCommandError, connection error → ShellyUnreachableError |
| TestClose | 2 | Close with session, close without session |
| TestGetStatus | 3 | Gen1 status, is_on true, is_on false |
| **Total** | **21** | |

### HA Integration Tests (implemented)

| Test File | Classes | Cases | What It Covers |
|-----------|---------|-------|----------------|
| `test_ha_const.py` | 3 | ~15 | DOMAIN, PLATFORMS, config keys, mapping constants |
| `test_ha_init.py` | 2 | ~9 | async_setup_entry, async_unload_entry, sys.path, cleanup |
| `test_ha_coordinator.py` | 4 | ~17 | State init, listener add/remove, status updates, reconnect |
| `test_ha_config_flow.py` | 4 | ~13 | MAC validation, user step, bluetooth discovery, confirm step |
| `test_ha_light.py` | 5 | ~28 | Properties, brightness mapping, color temp mapping, roundtrips, lifecycle |
| `test_ha_sensor.py` | 3 | ~10 | RSSI sensor, firmware sensor, lifecycle |
| `test_ha_diagnostics.py` | 3 | ~9 | Redaction, security verification, no plaintext leaks |

**HA test patterns:**
- Import via `custom_components.tuya_ble_mesh.*` (project root on sys.path)
- Duck-typed entities — no HA base class, all HA types under TYPE_CHECKING
- Mock coordinators with `TuyaBLEMeshDeviceState` dataclass
- Lifecycle tests verify listener registration and cleanup

### protocol.py — BLE Protocol (planned)

| Category | Cases | What to Test |
|----------|-------|-------------|
| Packet parsing | ~10 | Parse valid Tuya mesh commands, handle truncated packets, reject invalid opcodes |
| Packet construction | ~5 | Build command frames, verify byte layout, roundtrip parse(build(x)) == x |
| DP encoding/decoding | ~8 | Each DP type (boolean, value, enum, string, raw, bitmap), edge cases |
| Error handling | ~3 | Malformed input → ProtocolError, not bare Exception |

### crypto.py — Encryption (planned)

| Category | Cases | What to Test |
|----------|-------|-------------|
| AES-CCM encrypt/decrypt | ~4 | Roundtrip, known test vectors, correct key length |
| Key derivation | ~3 | Correct output for known inputs, wrong key length → CryptoError |
| Nonce construction | ~2 | Correct format, uniqueness |
| Secret safety | ~3 | No key values in exceptions, no key values in log output |

### secrets.py — 1Password Integration (planned)

| Category | Cases | What to Test |
|----------|-------|-------------|
| SecretsManager.get | ~3 | Successful read (mock op CLI), missing item → SecretNotFoundError |
| Vault access failure | ~2 | op CLI not found, token not set → VaultAccessError |
| Secret safety | ~2 | Return value not logged, error message doesn't contain secret |

### mesh.py — Mesh Network (planned)

| Category | Cases | What to Test |
|----------|-------|-------------|
| Connection | ~4 | Connect success, device not found, timeout, disconnect |
| Provisioning | ~3 | Provision flow, already provisioned, provisioning failure |
| Command send/receive | ~5 | Send DP command, receive status, roundtrip, timeout, error response |
| Session lifecycle | ~3 | Open, reuse, close, double-close safe |

---

## Integration Test Plan

Integration tests run against real hardware in the lab. They are
marked and skipped when hardware is unavailable.

### Hardware Requirements

| Test Target | Required Hardware | Detection |
|-------------|-------------------|-----------|
| Shelly control | Shelly Plug S at 192.168.1.50 | HTTP ping to /shelly |
| BLE scanning | hci0 adapter (RPi 4 built-in) | `hciconfig hci0` |
| BLE sniffing | Adafruit nRF51822 on /dev/ttyUSB0 | Serial port exists |
| Device interaction | Malmbergs LED Driver (DC:23:4D:21:43:A5) | BLE scan detection |

### Planned Integration Tests

| Test | Hardware | What It Verifies |
|------|----------|-----------------|
| Shelly reachable | Shelly | HTTP connectivity to plug |
| Shelly power cycle | Shelly | Off → wait → on sequence |
| Shelly generation detect | Shelly | Correct Gen1/Gen2 identification |
| BLE scan finds device | hci0 + LED Driver | Device appears in scan results |
| BLE scan identifies Tuya | hci0 + LED Driver | Device name or UUID matches Tuya pattern |
| Sniffer opens port | Sniffer | Serial port opens at 460800 baud |
| Sniffer receives packets | Sniffer | At least one SLIP frame received within 10s |
| Factory reset cycle | Shelly + LED Driver | Device advertises as `out_of_mesh` after rapid cycling |

### Skip Pattern

```python
import pytest

shelly_available = pytest.mark.skipif(
    not can_reach_shelly(),
    reason="Shelly Plug S not reachable at 192.168.1.50",
)

@pytest.mark.integration
@shelly_available
async def test_shelly_power_cycle() -> None:
    """Test power cycle against real Shelly device."""
    ...
```

---

## Security Test Plan

Security tests verify that the codebase doesn't leak secrets through
logs, exceptions, or output. They complement the static tools
(bandit, detect-secrets) with runtime checks.

### Static Analysis (in CI pipeline)

| Tool | What It Catches |
|------|----------------|
| `bandit` | Hardcoded passwords, use of `exec`/`eval`, insecure hash functions |
| `detect-secrets` | High-entropy strings, API key patterns, AWS keys |
| `safety` | Known vulnerabilities in dependencies |
| `ruff` | Code quality, import ordering, unused variables |
| `mypy --strict` | Missing type hints, type errors |

### Planned Runtime Security Tests

| Test | What It Verifies |
|------|-----------------|
| No secrets in PowerControlError messages | Exception `.args` contain no key material |
| No secrets in SnifferError messages | Exception `.args` contain no key material |
| SecretsManager never logs values | Mock logger, verify no secret in any call args |
| Crypto functions never log keys | Mock logger, call encrypt/decrypt, check log output |
| Exception messages safe for AI context | All custom exceptions produce redacted messages |

### Secret Safety Assertion Pattern

```python
def test_exception_does_not_leak_key() -> None:
    """Verify exception message doesn't contain key material."""
    fake_key = b"\xde\xad\xbe\xef" * 4
    try:
        # trigger an error with a key in scope
        raise KeyDerivationError("Key derivation failed")
    except KeyDerivationError as exc:
        msg = str(exc)
        assert fake_key.hex() not in msg
        assert "dead" not in msg.lower()
```

---

## Validation Pipeline

`scripts/run-checks.sh` executes these steps in order. All must pass.

| Step | Tool | What It Checks |
|------|------|---------------|
| 1 | `ruff check .` | Linting (style, imports, common errors) |
| 2 | `ruff format --check .` | Code formatting |
| 3 | `mypy --strict lib/ tests/` | Static type checking |
| 4 | `bandit -r lib/ -c pyproject.toml` | Security static analysis |
| 5 | `safety check` | Dependency vulnerability scan |
| 6 | `detect-secrets scan` | Committed secrets detection |
| 7 | `pytest tests/unit/` | Unit test suite |

A single failure in any step blocks the commit. Fix the issue — do not
skip the check or add exceptions.

---

## Writing New Tests

Checklist for adding tests to a new module:

1. Create `tests/unit/test_<module>.py`
2. Add `sys.path.insert` for `lib/` imports
3. Group tests in classes by method or feature
4. Mock all I/O (network, BLE, serial, filesystem, 1Password)
5. Use `@pytest.mark.asyncio` for async tests
6. Add type hints to all functions (rule S6)
7. Use custom assertions — never assert on raw secret values (rule RULE 2)
8. Keep timing parameters short (0.01s instead of 5.0s)
9. Test both success and failure paths
10. Test that errors raise the correct custom exception (rule S7)
