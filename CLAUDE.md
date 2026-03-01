# Malmbergs BT — Claude Code Project Context

> ⛔ **STOP. READ THIS ENTIRE FILE BEFORE WRITING ANY CODE.**
> This file defines the rules you MUST follow. Violations are not
> acceptable under any circumstances. No shortcuts. No exceptions.

---

## SUPREME SECURITY RULES

### ⛔ RULE 0: NEVER EXPOSE SECRETS IN TERMINAL OUTPUT

You (Claude Code) send terminal output back to an AI API as prompt context.
**ANYTHING visible in your terminal IS INCLUDED IN THE AI PROMPT.**

You MUST NEVER run commands that display secrets:

```bash
# ⛔ FORBIDDEN — these leak secrets to AI context:
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
git diff   # if diff contains secrets

# ✅ SAFE — verify existence without showing value:
test -f ~/.anthropic_key && echo "EXISTS" || echo "MISSING"
[ -n "$ANTHROPIC_API_KEY" ] && echo "SET" || echo "NOT SET"
op read "op://malmbergs-bt/item/field" > /dev/null 2>&1 && echo "OK" || echo "FAIL"
wc -c < ~/.anthropic_key
```

### ⛔ RULE 1: ALL SECRETS VIA 1PASSWORD

1Password is the ONLY source of secrets. Use `SecretsManager` from
`lib/tuya_ble_mesh/secrets.py`. Never read secrets from files, env
vars, or hardcoded values.

### ⛔ RULE 2: NEVER LOG/PRINT SECRETS IN CODE

```python
# ⛔ FORBIDDEN at every log level, even in tests:
_LOGGER.debug("Key: %s", key.hex())
print(key)
raise ValueError(f"Bad key: {key}")

# ✅ ONLY acceptable pattern:
_LOGGER.debug("Key [REDACTED], length: %d", len(key))
```

---

## STRUCTURAL RULES

Before creating ANY file or function, verify:

1. **S1:** `lib/` NEVER imports `homeassistant` or `custom_components`
2. **S3:** Raw BLE bytes parsed ONLY in `protocol.py`
3. **S4:** Crypto operations ONLY in `crypto.py`
4. **S5:** Async everywhere. No `time.sleep()`, no blocking I/O
5. **S6:** Type hints on EVERY function — parameters AND return type
6. **S7:** Custom exceptions only — never bare Exception/ValueError
7. **S8:** New devices via YAML profiles, not code changes
8. **S10:** Secrets accessed ONLY through `secrets.py`
9. **S11:** Headless design. No `input()`. No interactive prompts.

Full rules: `docs/ARCHITECTURE.md` section 0
Full security: `docs/SECURITY.md` section 0

---

## BEFORE EVERY COMMIT

```bash
bash scripts/run-checks.sh   # ALL must pass. No exceptions.
```

Runs: ruff, mypy --strict, bandit, safety, detect-secrets, pytest.
ANY failure → fix before committing.

---

## PROJECT OVERVIEW

HACS-compatible HA integration for Malmbergs BT Smart devices.
Fully local control via Tuya BLE Mesh. No cloud dependency.

### Key Documentation
- `PROJECT_SPEC.md` — Goals, acceptance criteria, constraints
- `docs/ARCHITECTURE.md` — Architecture + structural rules
- `docs/SECURITY.md` — Security rules (READ SECTION 0 FIRST)
- `docs/DOMAIN.md` — Tuya BLE Mesh protocol knowledge
- `docs/TESTING.md` — Test plan and cases

### Code Structure
- `lib/tuya_ble_mesh/` — Standalone BLE mesh library
- `custom_components/malmbergs_bt/` — HA integration wrapper
- `tests/` — Unit + integration + security tests
- `profiles/` — Device YAML profiles

### Hardware
- RPi 4 Bluetooth (hci0) — active BLE communication
- Adafruit nRF51822 BLE Sniffer (/dev/ttyUSB0, CP210x UART) — passive serial sniffer (NOT HCI)
- Shelly Plug S (192.168.1.50) — power control for headless device cycling (Gen1, no auth)
- Malmbergs LED Driver 9952126 (MAC: DC:23:4D:21:43:A5)
- NAS: //192.168.5.220/z-solutions → /mnt/solutions (CIFS/SMB 3.0, sec=none)

### Quick Start
```bash
source ~/malmbergs-ble/bin/activate && cd ~/malmbergs-bt
python scripts/scan.py           # Scan for devices (shows hardware status)
python scripts/sniff.py          # Passive BLE sniffer via serial (Adafruit)
python scripts/power_cycle.py    # Power cycle device via Shelly
python scripts/factory_reset.py  # Factory reset via rapid power cycling
bash scripts/run-checks.sh      # Full validation pipeline
```

### When Stuck
1. Re-read relevant docs (ARCHITECTURE, SECURITY, DOMAIN)
2. Check Tuya developer docs
3. Try a different approach
4. Document in `docs/DECISIONS.md`
5. Move to another task, return later
6. NEVER ask for a secret value. If 1Password fails → log to TODO.md

### Commits
Small, tested, working. `feat:`, `fix:`, `test:`, `security:`, `docs:`
