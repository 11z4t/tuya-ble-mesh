# Contributing to Tuya BLE Mesh

Thank you for considering contributing! This document explains how.

## How to Contribute

### Reporting Bugs

1. Check [existing issues](https://github.com/11z4t/tuya-ble-mesh/issues) first
2. Use the **Bug Report** issue template
3. Include: HA version, integration version, device model, debug logs, steps to reproduce

### Suggesting Features

1. Check [existing discussions](https://github.com/11z4t/tuya-ble-mesh/discussions) first
2. Use the **Feature Request** issue template
3. Explain the use case and proposed solution

### Adding Device Support

If you want to add a new device:

1. Use the **Device Compatibility** issue template
2. Include: brand, model, MAC prefix, vendor ID, protocol type (Telink vs SIG)
3. If you have working YAML profile data, submit it directly as a PR in `profiles/`

---

## Development Workflow

### Setup

```bash
git clone https://github.com/11z4t/tuya-ble-mesh.git
cd tuya-ble-mesh

python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -e ".[test]"
```

### Making Changes

1. **Fork** the repository
2. **Create a branch**: `git checkout -b fix/your-fix-name` or `feat/your-feature`
3. **Write code** following the style guide below
4. **Run the full check pipeline** — this is mandatory:
   ```bash
   bash scripts/run-checks.sh
   ```
   This runs: ruff (lint + format), mypy (strict), bandit, detect-secrets, pytest.
5. **Commit** with a clear message: `fix:`, `feat:`, `test:`, `docs:`, `security:`
6. **Push** and open a Pull Request using the PR template

### Check Pipeline Details

| Tool | Purpose |
|------|---------|
| `ruff` | Lint + autoformat |
| `mypy --strict` | Type checking |
| `bandit` | Security scan |
| `detect-secrets` | Credential leak detection |
| `pytest` | 1900+ unit and integration tests |

All checks must pass. No exceptions. Fix failures before committing.

### Code Style

- **Python 3.12+** — use modern syntax
- **Type hints** on every function (parameters + return type) — enforced by mypy strict
- **ruff** for formatting — run `ruff format .` to auto-fix
- **Max line length**: 100 characters
- **Custom exceptions** only — never bare `Exception` or `ValueError`
- **Async everywhere** — no `time.sleep()`, no blocking I/O
- **No `print()`** — use `_LOGGER` from the `logging` module

### Structural Rules

Before adding code, check these rules (from `CLAUDE.md`):

- `lib/tuya_ble_mesh/` **must never** import `homeassistant` or `custom_components`
- Raw BLE bytes parsed **only** in `protocol.py`
- Crypto operations **only** in `crypto.py`
- New devices via **YAML profiles** in `profiles/`, not code changes
- Secrets accessed **only** through proper secret management — never hardcoded

### Testing

Write tests for all new functionality:

```bash
# Run all tests
python -m pytest tests/ -q

# Run unit tests only
python -m pytest tests/unit/ -q

# Run a specific test file
python -m pytest tests/unit/test_protocol.py -v

# Run with coverage
python -m pytest tests/ --cov=custom_components/tuya_ble_mesh --cov-report=term
```

Test structure:
- `tests/unit/` — unit tests (fast, no HA)
- `tests/integration/` — integration tests (mock HA)
- `tests/security/` — bandit + detect-secrets

### Adding a Device Profile

Device profiles live in `profiles/`. They are YAML files describing the device's DPS (Data Point Specification):

```yaml
name: my_device
platform: light
model: "MYDEV-001"
product_key: "abc123"
dp_power:
  id: "1"
  type: bool
dp_brightness:
  id: "2"
  type: int
  min_raw: 10
  max_raw: 1000
```

No code changes needed for new devices — just add a profile and submit a PR.

## Pull Request Checklist

Before submitting:
- [ ] `bash scripts/run-checks.sh` passes (all checks green)
- [ ] Tests added/updated for new functionality
- [ ] CHANGELOG.md updated under `[Unreleased]`
- [ ] README.md updated if user-facing behavior changed
- [ ] No secrets or credentials in code or tests

## Code Review Process

1. A maintainer reviews the PR
2. Feedback provided if needed
3. Update PR based on feedback
4. Approval and merge

## Community Guidelines

- Be respectful and constructive
- Help newcomers
- Stay on topic

## Questions?

- Open a [GitHub Discussion](https://github.com/11z4t/tuya-ble-mesh/discussions)
- Check the [Wiki](https://github.com/11z4t/tuya-ble-mesh/wiki) for architecture docs

Thank you for contributing!
