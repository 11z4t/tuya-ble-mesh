# Premium Quality Definition — Tuya BLE Mesh Integration

**Purpose:** Define what "premium quality" means for this integration in measurable, enforceable terms.

**Status:** Phase 0 — Foundation
**Owner:** PLAT-402
**Date:** 2026-03-13

---

## What is Premium?

Premium means:
1. **Predictable** — behavior is consistent and documented
2. **Observable** — failures are detectable and diagnosable
3. **Recoverable** — errors lead to clear recovery paths
4. **Supportable** — users can self-diagnose 70%+ of issues
5. **Proven** — every claim is backed by tests or metrics

---

## Premium KPIs

### 1. Reliability Metrics

| KPI | Definition | Baseline | Target | Measurement Source | Frequency |
|-----|-----------|----------|--------|-------------------|-----------|
| **provisioning_success_rate** | Successful zero-knowledge provisions ÷ total provision attempts | TBD (establish in Phase 1) | ≥ 95% | diagnostics: `provisioning.success_count` / `provisioning.total_attempts` | Per device, rolling 30 days |
| **command_success_rate** | Commands completed successfully ÷ total commands sent | TBD (establish in Phase 1) | ≥ 99% | diagnostics: `transport_health.commands_succeeded` / `transport_health.commands_total` | Per device, rolling 7 days |
| **median_command_latency_ms** | p50 latency for successful commands | TBD (establish in Phase 1) | ≤ 100ms | diagnostics: `latency_percentiles.p50` | Real-time per device |
| **reconnect_recovery_success** | Automatic reconnects that restore service ÷ disconnect events | TBD (establish in Phase 1) | ≥ 90% | diagnostics: `reconnect_timeline` — count transitions `DISCONNECTED→READY` without manual intervention | Per device, rolling 30 days |
| **false_state_rate** | State mismatches (desired ≠ confirmed) ÷ state changes | TBD (establish in Phase 1) | ≤ 1% | diagnostics: `state_tracking.discrepancies` / `state_tracking.total_changes` | Per device, rolling 7 days |

### 2. Supportability Metrics

| KPI | Definition | Baseline | Target | Measurement Source | Frequency |
|-----|-----------|----------|--------|-------------------|-----------|
| **issues_resolvable_via_diagnostics** | GitHub issues closed with "diagnostics showed X" ÷ total closed issues | TBD (manual audit) | ≥ 70% | Manual audit of closed GitHub issues | Quarterly |
| **issue_types_with_repair_flow** | Issue categories with interactive repair ÷ total issue categories | 8/10 (current repair flows) | ≥ 80% | Code: count `RepairFlow` subclasses vs known issue types | Per release |
| **devices_with_verified_feature_matrix** | Models with full compatibility matrix ÷ claimed supported models | 3/3 (9917072, 9917076, 9952126) | ≥ 5 models | `compatibility/devices.yaml`: count `tested_status: verified` | Per release |
| **regression_frequency_between_releases** | Breaking regressions per release | TBD (track from V3 onward) | ≤ 1 per release | GitHub issues labeled `regression` + `severity:high` | Per release |
| **unknown_failures_per_1000_commands** | Failures without classified error type | TBD (establish in Phase 1) | ≤ 5 per 1000 | diagnostics: `error_classifier.unknown_count` / (`commands_total` / 1000) | Per device, rolling 30 days |

---

## Measurement Methodology

### How Baselines Are Established
1. **Instrumentation Phase (Phase 1):** Add metrics collection to all critical paths
2. **Baseline Collection (Phase 1-2):** Run integration under normal conditions for 7 days with real hardware
3. **Baseline Review:** Review p50/p95/p99 values, set baseline = p50 observed value
4. **Target Setting:** Targets based on baseline + acceptable margin (e.g., latency target = baseline × 1.2)

### How KPIs Are Measured
- **Real-time metrics:** Exposed via `diagnostics.py` → `async_get_diagnostics()`
- **Historical metrics:** Stored in HA recorder (diagnostic sensor entities updated every 5 minutes)
- **Support metrics:** Manual quarterly audit of GitHub issues + automated CI checks

### When Metrics Are Collected
- **Per-device metrics:** Collected continuously, exposed in diagnostics
- **Integration-wide metrics:** Aggregated from all config entries
- **CI metrics:** Collected on every PR via benchmark tests

---

## Quality Gates

**GATE POLICY:** No pull requests may be merged without demonstrating impact on at least one KPI.

### PR Quality Gate Checklist
Every PR description must include:
```markdown
## KPI Impact
- [ ] **Affects KPI:** [KPI name]
- [ ] **Expected change:** [increase/decrease/neutral]
- [ ] **Evidence:** [test output / benchmark comparison / fixture coverage]
```

### Stable Release Gate
A release is promoted from `beta` → `stable` only if:
1. All Premium KPIs meet targets (or documented exception)
2. Zero open issues with label `blocker`
3. Mutation test score ≥ 80% for core packages
4. All fixture tests pass
5. E2E tests pass on real hardware (minimum 2 device models)
6. Documentation complete (no "TODO" or "TBD" in user-facing docs)

---

## KPI-to-Metric Mapping

| KPI | Diagnostics Field | Test Metric | CI Output |
|-----|------------------|-------------|-----------|
| provisioning_success_rate | `provisioning.success_rate` | `test_provision_success_rate` | GitHub Actions: "Provisioning success: 98.5%" |
| command_success_rate | `transport_health.success_rate` | `test_command_stress_1000` | pytest benchmark: `commands_ok / commands_total` |
| median_command_latency_ms | `latency_percentiles.p50` | `test_latency_p50_under_load` | pytest benchmark: `p50_latency_ms` |
| reconnect_recovery_success | `reconnect_timeline.auto_recovery_rate` | `test_reconnect_recovery_scenarios` | Integration test: "Auto-recovery: 95%" |
| false_state_rate | `state_tracking.discrepancy_rate` | `test_state_confirmation_accuracy` | pytest: "State mismatches: 0.5%" |

---

## Premium vs Non-Premium

| Aspect | Non-Premium (Before V3) | Premium (V3+) |
|--------|------------------------|---------------|
| **Success Rate** | Unknown (no metrics) | 99%+ measured |
| **Diagnostics** | Basic error logs | Full support export with classified errors |
| **Recovery** | Manual restart required | Automatic reconnect with metrics |
| **State Confidence** | Optimistic (always assume success) | Tracked (desired/confirmed/assumed) |
| **Support** | "Check logs" | Interactive repair flows + guided troubleshooting |
| **Regression Detection** | Manual testing | Automated fixture suite + mutation testing |
| **Documentation** | Implementation-focused | Symptom-based troubleshooting |

---

## How to Update This Document

1. **Add new KPI:** Requires approval in GitHub discussion
2. **Change target:** Document rationale in PR description
3. **Update baseline:** Only after 7-day measurement period with logged evidence
4. **Change measurement source:** Update both table + implementation in same PR

---

## References
- Diagnostics implementation: `custom_components/tuya_ble_mesh/diagnostics.py`
- Test metrics: `tests/benchmarks/ci_benchmarks.py`
- Compatibility matrix: `compatibility/devices.yaml`
- Quality gate enforcement: `.github/workflows/quality_gate.yml`

---

**Next Steps:**
- [ ] Establish baselines (Phase 1 implementation)
- [ ] Create diagnostic sensors for KPI tracking
- [ ] Implement CI quality gate enforcement
- [ ] Create Grafana dashboard for KPI visualization
