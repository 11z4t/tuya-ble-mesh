# Threat Model — Tuya BLE Mesh Integration

**Framework:** STRIDE (Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service, Elevation of Privilege)

**Status:** Phase 0 — Foundation
**Owner:** 
**Date:** 2026-03-13
**Last Review:** 2026-03-13

---

## Executive Summary

This threat model identifies 23 threats across 5 attack surfaces in the Tuya BLE Mesh integration. All threats are categorized by STRIDE type, scored by likelihood and impact, and mapped to current/recommended mitigations.

**Current Security Posture:**
- ✅ AES-CCM encryption (mesh network layer)
- ✅ ECDH provisioning (SIG Mesh)
- ✅ Credential redaction in diagnostics
- ⚠️ Bridge HTTP API: no authentication (LAN-only assumption)
- ⚠️ Config entry storage: encrypted by HA core but decrypted in memory
- ❌ No mutual TLS for bridge-to-HA communication

---

## Assets

| Asset | Confidentiality | Integrity | Availability | Storage Location |
|-------|----------------|-----------|--------------|------------------|
| **Mesh Network Key** | CRITICAL | CRITICAL | HIGH | HA config entry (encrypted at rest) |
| **Mesh Application Key** | CRITICAL | CRITICAL | HIGH | HA config entry (encrypted at rest) |
| **Device Provisioning Key (DevKey)** | CRITICAL | CRITICAL | HIGH | HA config entry (encrypted at rest) |
| **Mesh Name / Password** | HIGH | MEDIUM | LOW | HA config entry (encrypted at rest) |
| **Session Keys (ephemeral)** | CRITICAL | CRITICAL | HIGH | Memory only (runtime) |
| **Device State** | MEDIUM | HIGH | MEDIUM | HA state machine + coordinator |
| **Bridge API Endpoint** | LOW | MEDIUM | HIGH | Config entry (redacted in diagnostics) |
| **BLE Advertisements** | LOW | MEDIUM | MEDIUM | Broadcast over BLE (unauthenticated) |
| **Command Sequence Numbers** | LOW | HIGH | MEDIUM | HA Store (persistent) |

---

## Data Flow Diagram (DFD)

```
┌──────────────┐
│ User (HA UI) │
└──────┬───────┘
       │ HTTPS (HA auth)
       ▼
┌─────────────────────────────────────────┐
│ Home Assistant Core                     │
│  ├─ Config Entry (encrypted storage)    │
│  ├─ State Machine                       │
│  └─ Integration (tuya_ble_mesh)         │
│      ├─ Coordinator                     │
│      ├─ Config Flow (credentials)       │
│      ├─ Diagnostics (redacted)          │
│      └─ Transport Layer                 │
└─────────────┬───────────────────────────┘
              │ HTTP (no auth)
              ▼
┌─────────────────────────────────────────┐
│ Bridge Daemon (remote host)             │
│  ├─ HTTP API (/health, /command, /v1/*) │
│  ├─ BLE Adapter (BlueZ)                 │
│  ├─ Mesh Protocol Stack                 │
│  └─ Session State                       │
└─────────────┬───────────────────────────┘
              │ BLE (AES-CCM encrypted mesh)
              ▼
┌─────────────────────────────────────────┐
│ Mesh Network (BLE broadcast)            │
│  ├─ Network Layer (encrypted)           │
│  ├─ Transport Layer (segmented)         │
│  └─ Access Layer (app-encrypted)        │
└─────────────┬───────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────┐
│ Mesh Devices (lights, plugs)            │
│  ├─ Device State                        │
│  ├─ Firmware                            │
│  └─ Vendor-specific protocol            │
└─────────────────────────────────────────┘
```

**Trust Boundaries:**
1. **User ↔ HA Core:** Authenticated (HA login)
2. **HA Core ↔ Bridge:** Unauthenticated HTTP (LAN trust boundary)
3. **Bridge ↔ Mesh:** Encrypted BLE (mesh keys)
4. **Mesh ↔ Devices:** Encrypted mesh protocol (shared keys)

---

## STRIDE Threat Analysis

### 1. BLE Radio Attack Surface

#### T1.1: Spoofing — Fake BLE Advertisements
- **Type:** Spoofing
- **Description:** Attacker broadcasts fake `out_of_mesh*` or `tymesh*` advertisements to trick integration into provisioning rogue device
- **Likelihood:** MEDIUM (requires BLE proximity)
- **Impact:** HIGH (rogue device in mesh, potential DoS or state manipulation)
- **Current Mitigation:**
  - BLE MAC filtering (only known prefixes)
  - ECDH provisioning prevents unauthorized provisioning (SIG Mesh)
  - Mesh credential validation during provisioning
- **Recommended Mitigation:**
  - ✅ CURRENT: ECDH + mesh credential check
  - ➕ ADD: User confirmation step during provisioning (show device identifier)
  - ➕ ADD: Log warning if unknown BLE device attempts provisioning
- **Status:** PARTIALLY MITIGATED
- **Owner:** Transport + Config Flow

#### T1.2: Tampering — BLE Packet Injection
- **Type:** Tampering
- **Description:** Attacker injects malformed or replay BLE packets to corrupt device state or trigger errors
- **Likelihood:** LOW (requires BLE proximity + protocol knowledge)
- **Impact:** MEDIUM (DoS, state corruption)
- **Current Mitigation:**
  - AES-CCM authentication (AEAD) prevents tampering of encrypted payloads
  - Sequence number tracking prevents basic replay attacks
  - Input validation on received packets
- **Recommended Mitigation:**
  - ✅ CURRENT: AES-CCM + sequence validation
  - ➕ ADD: Nonce freshness check (reject old nonces)
  - ➕ ADD: Rate limiting on packet processing (DoS prevention)
- **Status:** MITIGATED
- **Owner:** Protocol Layer

#### T1.3: Information Disclosure — BLE Sniffing
- **Type:** Information Disclosure
- **Description:** Attacker captures BLE advertisements to learn mesh topology, device presence, command frequency
- **Likelihood:** HIGH (BLE is broadcast, widely available sniffers)
- **Impact:** LOW (metadata only — encryption protects payload)
- **Current Mitigation:**
  - AES-CCM encryption prevents payload disclosure
  - Redaction in diagnostics prevents accidental MAC/IP leakage
- **Recommended Mitigation:**
  - ✅ CURRENT: Encryption + redaction
  - ℹ️ ACCEPT RISK: Metadata (device presence, command frequency) is inherent to BLE broadcast
- **Status:** ACCEPTED (low impact)
- **Owner:** Protocol + Diagnostics

#### T1.4: Denial of Service — BLE Jamming
- **Type:** Denial of Service
- **Description:** Attacker jams 2.4 GHz band to prevent BLE communication
- **Likelihood:** LOW (requires dedicated hardware, illegal in most jurisdictions)
- **Impact:** HIGH (complete service loss)
- **Current Mitigation:**
  - Automatic reconnect with exponential backoff
  - Connection state tracking (DEGRADED → RECOVERING)
  - Repair flows guide user to check RF environment
- **Recommended Mitigation:**
  - ✅ CURRENT: Reconnect + diagnostics
  - ℹ️ ACCEPT RISK: Physical-layer jamming is unpreventable in software
  - ➕ ADD: Health score based on RSSI trends (warn if RF environment degrades)
- **Status:** ACCEPTED (physical layer)
- **Owner:** Connection Layer

---

### 2. Bridge HTTP API Attack Surface

#### T2.1: Spoofing — Rogue Bridge
- **Type:** Spoofing
- **Description:** Attacker runs fake bridge on LAN, user configures integration to connect to it
- **Likelihood:** LOW (requires LAN access + user error)
- **Impact:** HIGH (credentials sent to attacker, full control of devices)
- **Current Mitigation:**
  - `/health` check during config flow (basic liveness test)
  - Bridge host entered manually (no auto-discovery)
- **Recommended Mitigation:**
  - ➕ ADD: Bridge version check (`/v1/version`) — reject unknown versions
  - ➕ ADD: Optional bearer token authentication (off by default for simplicity)
  - ➕ ADD: mTLS support (advanced users)
- **Status:** UNMITIGATED (low likelihood, high impact)
- **Owner:** Config Flow + Bridge Daemon

#### T2.2: Tampering — Command Injection
- **Type:** Tampering
- **Description:** Attacker on LAN sends malicious HTTP requests to bridge to trigger unintended commands
- **Likelihood:** MEDIUM (LAN access via compromised device)
- **Impact:** HIGH (arbitrary device control, potential DoS)
- **Current Mitigation:**
  - Bridge listens on LAN-only interface (not internet-exposed)
  - Command validation in bridge (opcode, parameter ranges)
- **Recommended Mitigation:**
  - ➕ ADD: Optional bearer token authentication
  - ➕ ADD: Request-ID tracking (prevent replay)
  - ➕ ADD: Rate limiting per source IP
- **Status:** PARTIALLY MITIGATED (LAN trust boundary)
- **Owner:** Bridge Daemon

#### T2.3: Information Disclosure — Unauthenticated /metrics Endpoint
- **Type:** Information Disclosure
- **Description:** Attacker on LAN scrapes `/v1/metrics` to learn mesh topology, command frequency, device identifiers
- **Likelihood:** MEDIUM (LAN access)
- **Impact:** LOW (metadata only, no credentials)
- **Current Mitigation:**
  - NONE (endpoint planned in Phase 5)
- **Recommended Mitigation:**
  - ➕ ADD: Redact device MACs in metrics (use anonymized IDs)
  - ➕ ADD: Optional authentication for `/v1/metrics`
- **Status:** PLANNED (Phase 5)
- **Owner:** Bridge Daemon

#### T2.4: Denial of Service — Bridge Flood
- **Type:** Denial of Service
- **Description:** Attacker floods bridge HTTP API with requests to exhaust resources
- **Likelihood:** MEDIUM (LAN access)
- **Impact:** MEDIUM (integration unavailable, but no data loss)
- **Current Mitigation:**
  - Queue depth limits (32 commands)
  - Timeout enforcement (5s default)
- **Recommended Mitigation:**
  - ➕ ADD: Per-source-IP rate limiting (e.g., 100 req/min)
  - ➕ ADD: Connection limits (max concurrent connections)
  - ➕ ADD: Graceful degradation (respond 503 when overloaded)
- **Status:** PARTIALLY MITIGATED
- **Owner:** Bridge Daemon

---

### 3. Mesh Provisioning Attack Surface

#### T3.1: Spoofing — Rogue Provisioner
- **Type:** Spoofing
- **Description:** Attacker provisions device into their own mesh before legitimate user
- **Likelihood:** MEDIUM (race condition during factory reset)
- **Impact:** HIGH (user cannot control device, must factory reset)
- **Current Mitigation:**
  - ECDH provisioning (SIG Mesh) requires attacker to know mesh credentials
  - Telink provisioning requires mesh name/password
  - User guide recommends provisioning immediately after factory reset
- **Recommended Mitigation:**
  - ✅ CURRENT: Credential-based provisioning
  - ➕ ADD: Warning in UI if device appears provisioned by another mesh
  - ➕ ADD: Factory reset guide with timing recommendations
- **Status:** MITIGATED
- **Owner:** Config Flow + Documentation

#### T3.2: Tampering — MITM During ECDH
- **Type:** Tampering
- **Description:** Attacker intercepts ECDH provisioning to learn DevKey
- **Likelihood:** LOW (requires BLE MITM + protocol knowledge)
- **Impact:** CRITICAL (full device compromise)
- **Current Mitigation:**
  - ECDH with P-256 curve (industry standard)
  - Out-of-band authentication (mesh credentials)
- **Recommended Mitigation:**
  - ✅ CURRENT: ECDH + OOB
  - ℹ️ ACCEPT RISK: BLE MITM is difficult without specialized hardware
- **Status:** MITIGATED
- **Owner:** SIG Mesh Protocol

#### T3.3: Information Disclosure — DevKey Leakage
- **Type:** Information Disclosure
- **Description:** DevKey exposed in logs, diagnostics, or memory dumps
- **Likelihood:** LOW (requires access to HA host)
- **Impact:** CRITICAL (full device compromise)
- **Current Mitigation:**
  - DevKey redacted in diagnostics (`REDACTED`)
  - No logging of DevKey (code review enforced)
  - HA config entry encryption at rest
- **Recommended Mitigation:**
  - ✅ CURRENT: Redaction + encrypted storage
  - ➕ ADD: Memory zeroing after use (Python gc limitations)
  - ➕ ADD: CI check: bandit scan for DevKey logging
- **Status:** MITIGATED
- **Owner:** Diagnostics + CI

---

### 4. Home Assistant Integration Attack Surface

#### T4.1: Elevation of Privilege — Config Flow Injection
- **Type:** Elevation of Privilege
- **Description:** Attacker with HA UI access but not admin privileges modifies integration config to exfiltrate credentials
- **Likelihood:** LOW (requires HA access)
- **Impact:** HIGH (credential theft)
- **Current Mitigation:**
  - HA config flow requires admin privileges (HA core enforcement)
  - Config entry encryption at rest
- **Recommended Mitigation:**
  - ✅ CURRENT: HA admin enforcement
  - ℹ️ ACCEPT RISK: Relies on HA core security model
- **Status:** MITIGATED (HA core responsibility)
- **Owner:** HA Core

#### T4.2: Information Disclosure — Diagnostics Over-Exposure
- **Type:** Information Disclosure
- **Description:** Diagnostics accidentally expose credentials, internal IPs, or sensitive topology
- **Likelihood:** MEDIUM (users paste diagnostics in public GitHub issues)
- **Impact:** MEDIUM (credential/topology leakage)
- **Current Mitigation:**
  - Automatic redaction of `_SENSITIVE_KEYS` (mesh credentials, bridge host)
  - Regex redaction of IP/MAC patterns
  - Manual review of diagnostics output
- **Recommended Mitigation:**
  - ✅ CURRENT: Auto-redaction
  - ➕ ADD: `get_redacted_support_export()` function (extra redaction for public sharing)
  - ➕ ADD: CI test: assert diagnostics output contains no plaintext credentials
- **Status:** MITIGATED
- **Owner:** Diagnostics

---

### 5. Credential Storage Attack Surface

#### T5.1: Information Disclosure — Plaintext in Config Entry
- **Type:** Information Disclosure
- **Description:** Credentials readable in plaintext from HA storage/config/.storage/core.config_entries
- **Likelihood:** MEDIUM (requires filesystem access to HA host)
- **Impact:** CRITICAL (full mesh compromise)
- **Current Mitigation:**
  - HA core encrypts config entries at rest (since HA 2024.x)
  - File permissions (owner read/write only)
- **Recommended Mitigation:**
  - ✅ CURRENT: HA encryption + filesystem permissions
  - ℹ️ ACCEPT RISK: Cannot improve beyond HA core encryption
  - ➕ ADD: Document threat in security docs (users should secure HA host)
- **Status:** MITIGATED (HA core responsibility)
- **Owner:** HA Core

#### T5.2: Tampering — Config Entry Modification
- **Type:** Tampering
- **Description:** Attacker with filesystem access modifies config entry to point integration to rogue bridge
- **Likelihood:** LOW (requires root access to HA host)
- **Impact:** HIGH (MITM all commands)
- **Current Mitigation:**
  - HA config entry encryption (tamper detection via HMAC)
  - Config flow validation on reload
- **Recommended Mitigation:**
  - ✅ CURRENT: HA encryption + validation
  - ℹ️ ACCEPT RISK: Root access to HA host = full compromise regardless
- **Status:** ACCEPTED
- **Owner:** HA Core

---

## Threat Summary by STRIDE Category

| STRIDE Category | Count | Mitigated | Partially Mitigated | Unmitigated | Accepted |
|-----------------|-------|-----------|---------------------|-------------|----------|
| **Spoofing** | 3 | 2 | 0 | 1 | 0 |
| **Tampering** | 4 | 3 | 0 | 0 | 1 |
| **Repudiation** | 0 | — | — | — | — |
| **Information Disclosure** | 6 | 4 | 0 | 0 | 2 |
| **Denial of Service** | 3 | 0 | 3 | 0 | 0 |
| **Elevation of Privilege** | 1 | 1 | 0 | 0 | 0 |
| **TOTAL** | **17** | **10** | **3** | **1** | **3** |

---

## High-Priority Mitigations (Roadmap)

| Threat ID | Mitigation | Priority | Phase | Owner |
|-----------|-----------|----------|-------|-------|
| T2.1 | Bridge version check + optional bearer token auth | HIGH | Phase 5 | Config Flow + Bridge |
| T2.2 | Request-ID tracking + rate limiting | MEDIUM | Phase 5 | Bridge Daemon |
| T2.3 | Metrics endpoint redaction | LOW | Phase 5 | Bridge Daemon |
| T2.4 | Per-IP rate limiting | MEDIUM | Phase 5 | Bridge Daemon |
| T1.1 | User confirmation during provisioning | LOW | Phase 3 | Config Flow |
| T4.2 | CI test for credential leakage in diagnostics | HIGH | Phase 2 | CI |

---

## Security Testing Checklist

- [ ] Fuzz testing of BLE packet parser (malformed advertisements, oversized payloads)
- [ ] Replay attack simulation (capture + replay BLE packets)
- [ ] Bridge API security audit (OWASP Top 10)
- [ ] Diagnostics credential leakage check (CI automated)
- [ ] Config entry encryption verification (HA core test)
- [ ] Memory dump analysis (DevKey zeroing verification)
- [ ] Timing attack resistance (constant-time crypto operations)

---

## Assumptions and Constraints

1. **LAN is trusted:** Bridge HTTP API assumes attacker is NOT on LAN
   - **Rationale:** Home network is user's responsibility
   - **Impact if wrong:** T2.1, T2.2, T2.3, T2.4 become HIGH likelihood

2. **HA host is secured:** Filesystem access = full compromise
   - **Rationale:** HA host security is out of scope for integration
   - **Impact if wrong:** T5.1, T5.2 guarantee full mesh compromise

3. **BLE broadcast is observable:** Metadata (presence, frequency) is public
   - **Rationale:** Inherent to BLE technology
   - **Impact if wrong:** N/A (cannot be wrong)

4. **Users follow factory reset guide:** Devices are reset before provisioning
   - **Rationale:** Documented best practice
   - **Impact if wrong:** T3.1 likelihood increases to HIGH

---

## References
- OWASP Threat Modeling: https://owasp.org/www-community/Threat_Modeling
- STRIDE Framework: https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats
- Bluetooth Mesh Security: https://www.bluetooth.com/blog/bluetooth-mesh-security-overview/
- HA Security Best Practices: https://www.home-assistant.io/docs/configuration/securing/

---

## Changelog
- 2026-03-13: Initial threat model (Phase 0)
