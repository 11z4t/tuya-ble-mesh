# Security — Tuya BLE Mesh

## Section 0: Security Rules

These rules are mandatory and non-negotiable.
CLAUDE.md contains a summary; this is the authoritative reference.

### RULE 0: Never Expose Secrets in Terminal Output

Claude Code sends terminal output back to an AI API as prompt context.
**Anything visible in the terminal is included in the AI prompt and
transmitted to Anthropic's servers.** This makes terminal output a
secret-leaking channel.

#### Forbidden Commands

```bash
# These display secret values in terminal output — NEVER run them:
cat ~/.anthropic_key
echo $ANTHROPIC_API_KEY
echo $OP_SERVICE_ACCOUNT_TOKEN
env | grep KEY
env
printenv
set
cat .env
cat secrets.yaml
op item get "vault" --fields password
python -c "import os; print(os.environ['KEY'])"
grep -r password .env
git log -p -- .env
git diff                    # if diff may contain secrets
```

#### Safe Alternatives

```bash
# Verify existence without revealing the value:
test -f ~/.anthropic_key && echo "EXISTS" || echo "MISSING"
[ -n "$ANTHROPIC_API_KEY" ] && echo "SET" || echo "NOT SET"
op read "op://malmbergs-bt/item/field" > /dev/null 2>&1 && echo "OK" || echo "FAIL"
wc -c < ~/.anthropic_key
```

#### Why This Rule Exists

Unlike a normal development terminal, Claude Code's terminal is part of
an AI conversation. Every command's stdout and stderr becomes prompt
context sent over the network. A single `echo $SECRET` leaks the value
to the AI provider permanently. There is no way to retract it.

---

### RULE 1: All Secrets via 1Password

1Password is the **only** permitted source of secrets. The `SecretsManager`
class in `lib/tuya_ble_mesh/secrets.py` is the only permitted interface
for reading secrets in code.

#### Forbidden Patterns

```python
# NEVER do any of these:
key = os.environ["MESH_KEY"]
key = open("secrets.txt").read()
key = "hardcoded_key_value"
key = config["mesh_key"]  # from a plain config file
```

#### Required Pattern

```python
from tuya_ble_mesh.secrets import SecretsManager

secrets = SecretsManager()
mesh_key = await secrets.get("mesh-key")
```

#### 1Password Architecture

- **CLI tool:** `op` (v2.32.x)
- **Authentication:** Service account token via `OP_SERVICE_ACCOUNT_TOKEN`
  environment variable (set in tmux session, never displayed)
- **Vault:** `malmbergs-bt`
- **Access pattern:** `op read "op://malmbergs-bt/<item>/<field>"`

The `SecretsManager` wraps `op read` calls with:
- Async execution (via `asyncio.create_subprocess_exec`)
- Error handling (vault access failures, missing items)
- No secret values in logs or exceptions (see RULE 2)

#### If 1Password Is Unavailable

- Log the issue to `TODO.md`
- Do NOT fall back to environment variables, files, or hardcoded values
- Do NOT ask the user for the secret value
- Move to a different task and return later

---

### RULE 2: Never Log or Print Secrets in Code

Secret values MUST NOT appear in log output, print statements, exception
messages, or any other form of program output. This applies at all log
levels including DEBUG, and in all contexts including tests.

#### Forbidden Patterns

```python
# ALL of these are forbidden:
_LOGGER.debug("Key: %s", key.hex())
_LOGGER.info("Using token: %s", token)
print(f"Mesh key: {mesh_key}")
raise ValueError(f"Invalid key: {key}")
raise SecretsError(f"Key value was: {key}")
assert key == expected_key  # leaks both values in assertion error
```

#### Allowed Patterns

```python
# Metadata about secrets is OK — values are not:
_LOGGER.debug("Key loaded, length: %d bytes", len(key))
_LOGGER.info("Secret [REDACTED] retrieved from vault")
raise KeyDerivationError("Key derivation failed (key length: %d)" % len(key))
```

#### In Tests

```python
# FORBIDDEN — assertion errors would print the secret:
assert mesh_key == b"\x01\x02\x03..."

# ALLOWED — verify properties without exposing values:
assert len(mesh_key) == 16
assert isinstance(mesh_key, bytes)
```

---

## 1Password Integration

### Vault Structure

```
Vault: malmbergs-bt
├── tuya-cloud-credentials    (Tuya IoT Platform API keys)
│   ├── access_id
│   └── access_secret
├── mesh-key                  (device-specific mesh encryption key)
│   └── key
└── ...                       (additional items as needed)
```

### SecretsManager (planned)

```python
class SecretsManager:
    """Reads secrets exclusively from 1Password."""

    async def get(self, item: str, field: str = "password") -> str:
        """Read a secret from 1Password vault."""
        # Calls: op read "op://malmbergs-bt/{item}/{field}"
        # Returns the value, never logs it
        ...
```

All modules that need secrets import `SecretsManager` rather than
interacting with `op` directly.

---

## Threat Model

### Scope

This is a **local-only** Home Assistant integration. The threat model
reflects a home lab environment, not a public-facing service.

### Assets to Protect

| Asset | Sensitivity | Storage |
|-------|------------|---------|
| Tuya mesh encryption keys | High | 1Password vault |
| Tuya cloud API credentials | High | 1Password vault |
| BLE communication content | Medium | In-memory only |
| Device MACs and network topology | Low | Code/config (not secret) |

### Attack Vectors

| Vector | Mitigation |
|--------|------------|
| AI context leakage (RULE 0) | Never display secrets in terminal |
| Hardcoded credentials | 1Password only (RULE 1), detect-secrets in CI |
| Log file exposure | Never log secret values (RULE 2) |
| BLE eavesdropping | Tuya mesh encryption (AES), limited range |
| Network sniffing (Shelly) | Local network only, Shelly Gen1 has no auth |
| Supply chain (dependencies) | `safety` check in CI pipeline |

### Out of Scope

- Internet-facing attacks (no cloud dependency)
- Physical device tampering
- Side-channel attacks on the RPi

---

## HA Config Entry Storage

Home Assistant stores config entry data in plaintext JSON at
`.storage/core.config_entries`. This is HA's standard model — all
integrations (Z-Wave, Zigbee, etc.) store network keys this way.

**What is stored:**
- MAC address (not a secret)
- Mesh name and mesh password (factory defaults: out_of_mesh / 123456)
- Vendor ID and device type (not secrets)

**Risk acceptance:**
- Config entries are only accessible to HA admin users
- The mesh password protects BLE communication within ~10m range
- Factory default credentials (123456) provide no real security
- This matches HA's security model for local integrations

**Mitigation:**
- Config flow warns users that credentials are stored in config DB
- If stronger protection is needed, use file system encryption on
  the HA storage directory
- SecretsManager (1Password) remains the canonical interface for
  standalone scripts outside HA

---

## Logging and Redaction

### Allowed in Logs

- IP addresses of local devices (not secrets — they are configuration)
- MAC addresses (device identifiers, not secrets)
- BLE RSSI values, channel numbers, packet lengths
- Error messages describing what failed (not why in terms of key values)
- Secret metadata: lengths, types, whether they loaded successfully

### Forbidden in Logs

- Key material (hex, base64, raw bytes)
- Passwords or tokens
- 1Password item contents
- BLE payload contents that may contain encrypted key material

### Pattern

```python
import logging
_LOGGER = logging.getLogger(__name__)

# Good:
_LOGGER.info("Connected to device %s", mac_address)
_LOGGER.debug("Mesh key loaded, %d bytes", len(key))
_LOGGER.error("Decryption failed for %s", device_id)

# Bad:
_LOGGER.debug("Key: %s", key.hex())
_LOGGER.info("Sending payload: %s", payload.hex())  # may contain keys
```

---

## Crypto Key Handling

### Principles

1. Keys exist in memory only as long as needed
2. Keys are never written to disk outside 1Password
3. Keys are never logged, printed, or included in exceptions
4. All crypto operations are in `crypto.py` (rule S4)

### Tuya BLE Mesh Keys

Tuya BLE Mesh uses AES-based encryption. The mesh key is derived during
provisioning and used for all subsequent communication.

- **Storage:** 1Password vault (`mesh-key` item)
- **Format:** 16 bytes (AES-128)
- **Usage:** Loaded via `SecretsManager`, passed to `crypto.py` functions
- **Lifecycle:** Loaded once per session, held in memory, cleared on shutdown

---

## Validation Pipeline

`scripts/run-checks.sh` runs the full validation suite. All checks must
pass before any commit.

| Tool | Purpose |
|------|---------|
| `ruff` | Linting and formatting (replaces flake8 + black + isort) |
| `mypy --strict` | Static type checking — enforces S6 |
| `bandit` | Security-focused static analysis (detects hardcoded secrets, unsafe functions) |
| `safety` | Checks dependencies for known vulnerabilities |
| `detect-secrets` | Scans for accidentally committed secrets (high-entropy strings, API keys) |
| `pytest` | Unit, integration, and security tests |

### Running

```bash
bash scripts/run-checks.sh   # ALL must pass
```

Any failure blocks the commit. Fix the issue, do not skip the check.
