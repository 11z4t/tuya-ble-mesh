# Roadmap

**Last updated:** 2026-03-10

---

## Delivered

### P0 — Options Flow & Error Pipeline
- Options flow — editable config after setup (`entry.options`, backward-compatible defaults)
- Scoped repair issues — per-entry `ErrorClass` classification, actionable issue cards
- Error class pipeline — `bridge_down`, `device_offline`, `mesh_auth`, `protocol`, `permanent`, `transient`
- CI smoke tests — `pytest --timeout=60`, 1206+ passing, strict-mode mypy gate
- `QUALITY_STATUS.md` — honest self-assessment replacing "Platinum" claim

### P1 — Protocol & Reconnect Hardening
- Exponential backoff with bridge-specific parameters
- Reconnect storm detection (configurable threshold/window)
- RSSI adaptive polling (30s–300s, stability-based)
- Sequence number persistence (HA Store, safety margin on restore)
- Bridge health polling (`/health` endpoint, 30 s interval)

### P2 — Polish & Formalisation (MESH-10, MESH-11, MESH-12)
- `DeviceCapabilities` dataclass — single authoritative place for device probing
  - Replaces 10+ scattered `hasattr(device, ...)` calls in coordinator, sensor, diagnostics
- `_build_turn_on_command()` — extracted from `light.py` transition branch
  - Eliminates duplicated brightness-scale mode-detection logic
- This ROADMAP

---

## P3 Candidates (ranked by value/effort)

### 1. SIG Mesh light control
- **Value:** Unlocks brightness + color for SIG Mesh lights (currently on/off only)
- **Effort:** High — requires lib-level work: vendor model commands in `SIGMeshDevice`
- **Blocker:** `SIGMeshDevice.send_brightness` does not exist yet in lib

### 2. Power monitoring sensor
- **Value:** Watt + kWh sensors for metering plugs
- **Effort:** Low (HA side ready) — requires a device where `supports_power_monitoring=True`
- **Blocker:** No test hardware with power metering confirmed; mock-only coverage

### 3. Config flow auto-detect improvement
- **Value:** Reduces manual setup steps for common device types
- **Effort:** Medium — heuristic based on Telink/SIG Mesh vendor ID patterns
- **Dependency:** Stable device inventory from field deployments

### 4. RSSI history in diagnostics
- **Value:** Better visibility into connection stability trends
- **Effort:** Low — extend `ConnectionStatistics.response_times` pattern to RSSI
- **No lib changes needed**

---

## Explicit Non-Goals

- **Dynamic entity registration** — speculative, no confirmed use-case
- **Cloud API fallback** — mesh devices are local-only by design
- **Z-Wave bridge support** — out of scope, separate integration
- **Web UI for mesh configuration** — HA config flow is sufficient

---

## Known Limitations

| Limitation | Notes |
|------------|-------|
| SIG Mesh: on/off only | No dimming or colour until lib adds vendor model commands |
| Bridge health poll interval | Not user-configurable (hardcoded 30 s) |
| No real-device integration tests | All integration tests use mocks |
| mypy strict mode | HA stubs unavailable in CI; strict mode skipped for `custom_components/` |
| Effect/scene support | Tuya mesh protocol supports scenes; HA entity does not expose them yet |
