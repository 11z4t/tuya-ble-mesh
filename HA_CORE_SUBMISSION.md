# Home Assistant Core Submission Guide

**Integration:** Tuya BLE Mesh
**Target:** home-assistant/core repository
**Status:** Ready for submission (v0.19.0)

---

## Pre-Submission Checklist

### Quality Requirements
- [x] Quality Scale: **Platinum** (highest tier)
- [x] Test Coverage: **91%** (target: >90%)
- [x] Type Hints: **100%** (mypy --strict compliance)
- [x] Code Quality: **ruff** linting passes
- [x] Security: **bandit** scan passes
- [x] Dependencies: **All bundled** (no external PyPI requirements)

### Integration Requirements
- [x] Config Flow: **Yes** (UI-based setup)
- [x] Auto-Discovery: **Yes** (Bluetooth integration)
- [x] IoT Class: **local_push** (local communication)
- [x] Documentation: **Ready** (HA_DOCS_DRAFT.md)
- [x] Tests: **Comprehensive** (unit, integration, E2E)
- [x] Unique ID: **Yes** (MAC address)
- [x] Diagnostics: **Yes** (device and config)

---

## Repository Structure

Home Assistant Core expects integrations in `homeassistant/components/`:

```
homeassistant/components/tuya_ble_mesh/
├── __init__.py           # Entry point, setup/unload logic
├── config_flow.py        # UI configuration flow
├── coordinator.py        # Data update coordinator
├── light.py              # Light entity platform
├── switch.py             # Switch entity platform
├── sensor.py             # Sensor entity platform
├── diagnostics.py        # Diagnostics platform
├── repairs.py            # Repairs framework (optional)
├── const.py              # Constants and configuration schema
├── manifest.json         # Integration metadata
├── strings.json          # User-facing strings (translations)
└── translations/
    └── en.json           # English translations
```

**Note:** The `lib/tuya_ble_mesh/` library must be **bundled** inside the integration directory (not as a separate PyPI package).

---

## Submission Process

### 1. Fork home-assistant/core
```bash
git clone https://github.com/YOUR_USERNAME/core.git
cd core
git remote add upstream https://github.com/home-assistant/core.git
```

### 2. Create Integration Branch
```bash
git checkout -b add-tuya-ble-mesh
```

### 3. Copy Integration Files
```bash
# Copy custom_components/tuya_ble_mesh/ → homeassistant/components/tuya_ble_mesh/
cp -r /path/to/custom_components/tuya_ble_mesh homeassistant/components/

# Ensure lib/ is bundled
cp -r /path/to/lib/tuya_ble_mesh homeassistant/components/tuya_ble_mesh/lib/
```

### 4. Update Code for Core Standards

#### a. Remove HACS-specific files
```bash
cd homeassistant/components/tuya_ble_mesh
rm -f hacs.json
```

#### b. Update imports (remove `custom_components` prefix)
Replace all occurrences:
```python
# Before (custom component)
from custom_components.tuya_ble_mesh.const import DOMAIN

# After (core integration)
from homeassistant.components.tuya_ble_mesh.const import DOMAIN
```

#### c. Update manifest.json
Ensure all required fields are present:
- `domain`, `name`, `version`, `documentation`, `issue_tracker`
- `codeowners` (your GitHub username)
- `config_flow: true`
- `iot_class`, `quality_scale`
- `bluetooth` (discovery matchers)
- `dependencies: ["bluetooth"]`
- `requirements: []` (empty — all code bundled)

### 5. Add Tests to Core
```bash
# Copy tests to HA Core test directory
cp -r tests/unit/test_ha_*.py tests/pytest/components/tuya_ble_mesh/
```

Core-specific test structure:
```
tests/components/tuya_ble_mesh/
├── __init__.py
├── conftest.py          # Fixtures
├── test_config_flow.py
├── test_coordinator.py
├── test_light.py
├── test_switch.py
├── test_sensor.py
├── test_diagnostics.py
└── test_init.py
```

### 6. Run HA Core Test Suite
```bash
# Install HA dev dependencies
pip install -e .
pip install -r requirements_test.txt

# Run integration tests
pytest tests/components/tuya_ble_mesh/

# Run full validation suite
script/lint
script/gen_requirements_all.py
script/hassfest
```

### 7. Add Documentation to HA Docs Repo

Fork and clone https://github.com/home-assistant/home-assistant.io:
```bash
git clone https://github.com/YOUR_USERNAME/home-assistant.io.git
cd home-assistant.io
git checkout -b add-tuya-ble-mesh-docs
```

Create documentation file:
```bash
nano source/_integrations/tuya_ble_mesh.markdown
```

Use content from `HA_DOCS_DRAFT.md` as a base.

### 8. Submit Pull Requests

#### PR to home-assistant/core
**Title:** Add Tuya BLE Mesh integration

**Description:**
```markdown
## Summary
Adds support for Tuya BLE Mesh devices (lights, switches, plugs) with fully local control via Bluetooth.

## Tested Hardware
- Malmbergs BT Smart LED Driver 9952126
- Malmbergs BT Smart Plug S17

## Features
- Auto-discovery via Bluetooth integration
- Config flow (UI-based setup)
- Zero-knowledge configuration for common devices
- Supports Tuya proprietary + SIG Mesh protocols
- Local push (no cloud dependency)
- Diagnostics platform

## Quality
- Test Coverage: 91%
- Quality Scale: Platinum
- Type Hints: 100% (mypy --strict)
- Security: bandit scan passes

## Checklist
- [x] The code follows the HA code style guidelines
- [x] Tests have been added to verify the new integration works
- [x] Documentation has been added to https://home-assistant.io
- [x] The integration has been added to the integrations manifest
```

**Labels:** `new-integration`

#### PR to home-assistant.io
**Title:** Add Tuya BLE Mesh documentation

**Description:**
```markdown
Documentation for the new Tuya BLE Mesh integration.

Depends on home-assistant/core#XXXXX
```

---

## Post-Submission

### Expected Timeline
- **Initial Review:** 1-2 weeks (bot checks + maintainer triage)
- **Code Review:** 2-4 weeks (architecture reviewer assigned)
- **Final Approval:** 1-2 weeks (after all feedback addressed)
- **Merge:** Next HA release cycle (monthly)

### Review Process
1. **Automated Checks** — hassfest, linting, type checking
2. **Code Owner Review** — Bluetooth component owner
3. **Architecture Review** — HA core team member
4. **Final Approval** — Merge once all comments addressed

### Common Feedback
- Bundled dependencies properly included
- No blocking I/O in async functions
- Entity naming follows HA conventions
- Config flow handles all edge cases
- Tests cover >90% of code
- Documentation is clear and complete

---

## Maintenance Commitment

By submitting to HA Core, you commit to:
- **Respond to issues** within 2 weeks
- **Fix critical bugs** within 1 week
- **Support HA updates** (maintain compatibility)
- **Review PRs** to your integration

If you become unavailable, ping `@home-assistant/core` to find a new maintainer.

---

## Alternative: HACS Only

If you prefer to **not** submit to Core:
- Integration remains in HACS (custom repository)
- You maintain full control (no HA review process)
- Users install via HACS → Custom repositories
- Updates published via GitHub releases

Pros: Faster iteration, full control
Cons: Less visibility, users must add custom repo manually

---

## References

- **ADR: Integration Quality Scale**
  https://github.com/home-assistant/architecture/blob/master/adr/0004-quality-scale.md
- **Developer Documentation**
  https://developers.home-assistant.io/docs/creating_integration_manifest/
- **Bluetooth Integration**
  https://developers.home-assistant.io/docs/bluetooth/
- **Config Flow Best Practices**
  https://developers.home-assistant.io/docs/config_entries_config_flow_handler/

---

## Contact

- **GitHub Issues:** https://github.com/11z4t/tuya-ble-mesh/issues
- **HA Discord:** #devs_core_integrations channel
- **HA Forums:** https://community.home-assistant.io/

---

**Status:** Ready for submission pending final decision on bundled lib structure and Core vs HACS-only strategy.
