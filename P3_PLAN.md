# P3 Plan — Gold Tier + Shelly-Beater

**Date:** 2026-03-10
**Author:** Deep audit (Sonnet 4.6)
**Base:** v0.23.0 + P2 (MESH-10–13)
**Goal:** Reach Gold tier (match Shelly), then surpass it with BLE-mesh-specific advantages

---

## Bakgrund — Vad Shelly har som vi saknar

Shelly är ett referensexempel på Gold-tier HA-integration. Den djupa analysen av v0.23.0 identifierade **25 svagheter** rangordnade i 4 nivåer. Nedanstående plan tacklar dem i precis rätt ordning: kritiska blockers → Gold-kraven → differentierare.

### Gap-tabell (kortversion)

| Shelly har | Vi har | Delta |
|-----------|--------|-------|
| Reconfiguration flow | ❌ | Användare kan inte uppdatera credentials utan ta bort integration |
| Interactive repair forms | 🟡 Read-only | Våra repairs visar info men låter inte användaren fixa |
| Entry uniqueness check | ❌ | Duplikat-MAC-entries möjliga |
| Frozen coordinator state | ❌ | Mutable state = potentiell race vid läsning |
| Rate limiting på kommandon | ❌ | Rapid commands kan floda mesh |
| Bridge config via Options | 🟡 Partial | Poll-interval/timeout ej konfigurerbart |
| RSSI history / reconnect timeline | ❌ | Diagnostik saknar tids-dimension |
| Bridge health polling HA-test | ❌ | Koden finns, inga HA-level tester |
| UpdateEntity (firmware) | ❌ | Inget firmware-update UI i HA |
| Effect/scene support | ❌ | Protokollet stödjer scener, entiteten inte |

**Vad vi har som Shelly inte har:**
- Detaljerad BLE mesh-topologi (bridge vs. direct, Telink vs. SIG)
- Protocol-level diagnostics (sequence nummer, vendor ID, mesh authentication)
- Per-device ErrorClass klassificering med semantisk mapping
- Adaptiv RSSI-polling baserad på stabilitet
- Scoped repair issues per config entry (inte per integration)

---

## Commit-plan

### Ordning och beroenden

```
MESH-14 (reconfigure + uniqueness)   ← oberoende, gör ASAP
MESH-15 (frozen state + thread safety) ← oberoende av MESH-14
MESH-16 (interactive repairs + diagnostics) ← beroende av ingenting
MESH-17 (test coverage hardening)    ← beroende av MESH-14+15 (testar dem)
MESH-18 (UpdateEntity)               ← beroende av DeviceCapabilities (P2)
MESH-19 (effect/scene)               ← beroende av ingenting
MESH-20 (superior diagnostics)       ← beroende av MESH-15 (RSSI history kräver frozen state)
```

Rekommenderad ordning: **14 → 15 → 16 → 17 → 18 → 19 → 20**

---

## MESH-14 — Reconfiguration Flow + Entry Uniqueness

**Filer:** `config_flow.py`, `tests/unit/test_ha_config_flow.py`
**Netto:** ~+120 rader

### Problem
1. Ingen `async_step_reconfigure()` → användare måste ta bort + lägga till integration för att byta mesh-credentials
2. Ingen uniqueness-check → kan lägga till samma MAC-adress flera gånger
3. IPv6-validering accepterar `:::` och andra ogiltiga adresser

### Lösning

**A) Entry uniqueness** — lägg till i `async_step_user` (och bridge-steps) efter MAC-validering:

```python
# I async_step_user / async_step_ble_device när MAC är känd:
await self.async_set_unique_id(mac_address)
self._abort_if_unique_id_configured(updates={CONF_BRIDGE_HOST: bridge_host})
```

**B) Reconfiguration flow:**

```python
async def async_step_reconfigure(
    self, user_input: dict[str, Any] | None = None
) -> ConfigFlowResult:
    """Allow user to update mesh credentials without deleting the entry.

    Called when user selects "Reconfigure" on the config entry card.
    Re-uses the credential validation steps, then calls async_update_reload_and_abort()
    to apply changes without a full teardown/re-add.
    """
    entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
    if entry is None:
        return self.async_abort(reason="entry_not_found")

    if user_input is None:
        # Pre-fill form with current values from entry
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_MESH_NAME, default=entry.data.get(CONF_MESH_NAME, "")): str,
                vol.Required(CONF_MESH_PASSWORD, default=""): str,  # never pre-fill password
                vol.Optional(CONF_NET_KEY, default=entry.data.get(CONF_NET_KEY, "")): str,
            }),
            description_placeholders={"device_name": entry.title},
        )

    errors = {}
    try:
        _validate_mesh_credentials(user_input)
    except vol.Invalid as exc:
        errors["base"] = str(exc)
        return self.async_show_form(step_id="reconfigure", data_schema=..., errors=errors)

    new_data = {**entry.data, **user_input}
    return self.async_update_reload_and_abort(
        entry, data=new_data, reason="reconfigure_successful"
    )
```

**C) IPv6 fix** — byt ut regex-validering mot stdlib:

```python
# Nuvarande (config_flow.py ~rad 87):
_IPV6_RE = re.compile(r"(?:[0-9a-fA-F:]+)")  # TOO PERMISSIVE

# Ny hjälpfunktion:
def _is_valid_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False
```

### Tests att lägga till (8 st)

```python
class TestReconfigurationFlow:
    async def test_reconfigure_shows_prefilled_form()
    async def test_reconfigure_updates_credentials()
    async def test_reconfigure_validates_mesh_password()
    async def test_reconfigure_reloads_entry()

class TestEntryUniqueness:
    async def test_duplicate_mac_aborts_flow()
    async def test_same_mac_updates_existing_entry()
    async def test_ipv6_malformed_rejected()
    async def test_ipv6_valid_accepted()
```

---

## MESH-15 — Frozen State + Thread Safety + Rate Limiting

**Filer:** `coordinator.py`, `tests/unit/test_ha_coordinator.py`
**Netto:** ~+80 rader

### Problem
1. `TuyaBLEMeshDeviceState` är mutable → race om entitet läser state medan coordinator uppdaterar
2. `_listeners`/`_listener_error_counts` lazy-init i `add_listener()` → race vid concurrent calls
3. `send_command_with_retry()` har ingen rate limiting → kann floda mesh

### Lösning

**A) Frozen state** — byt till `frozen=True` + `replace()` i callbacks:

```python
@dataclass(frozen=True, slots=True)
class TuyaBLEMeshDeviceState:
    """Immutable snapshot of device state.

    Updated atomically via dataclasses.replace() — never mutated in-place.
    """
    is_on: bool = False
    brightness: int = 0
    # ... alla fält oförändrade ...
```

Uppdatera alla `self._state.X = value` → `self._state = dataclasses.replace(self._state, X=value)`:

```python
# _on_status_update:
self._state = dataclasses.replace(
    self._state,
    is_on=bool(status.mode_value),
    brightness=status.white_brightness,
    color_temp=status.white_temp,
    mode=status.mode,
    red=status.red,
    green=status.green,
    blue=status.blue,
    color_brightness=status.color_brightness,
)

# async_start (available=True):
self._state = dataclasses.replace(self._state, available=True, firmware_version=fw)
```

**B) Thread-safe listener init** — flytta init till `__init__()`:

```python
def __init__(self, ...):
    # ... befintlig init ...
    self._listeners: list[Callable[[], None]] = []        # init here, not lazy
    self._listener_error_counts: dict[int, int] = {}      # init here
```

Ta bort `if not hasattr(self, "_listeners"):` checkar.

**C) Rate limiting i `send_command_with_retry()`:**

```python
_COMMAND_RATE_LIMIT = 20  # max pending commands at any time

# I TuyaBLEMeshCoordinator.__init__:
self._pending_commands: int = 0

# I send_command_with_retry:
async def send_command_with_retry(self, ...):
    if self._pending_commands >= _COMMAND_RATE_LIMIT:
        _LOGGER.warning("Command queue full (%d pending), dropping", self._pending_commands)
        return
    self._pending_commands += 1
    try:
        # ... befintlig retry-logik ...
    finally:
        self._pending_commands -= 1
```

### Tests att lägga till (6 st)

```python
class TestFrozenState:
    def test_state_is_immutable()
    def test_state_updated_via_replace()
    def test_concurrent_reads_see_consistent_state()  # threading.Thread reads state while update

class TestRateLimiting:
    async def test_command_queue_full_drops_command()
    async def test_command_count_decremented_on_error()
    async def test_rate_limit_constant_exported()
```

---

## MESH-16 — Interactive Repairs + Reconnect Timeline

**Filer:** `repairs.py`, `diagnostics.py`, `coordinator.py`, test-filer
**Netto:** ~+150 rader

### Problem A: Repairs är read-only
Shelly låter användaren skriva in nya credentials i repair-flödet. Vi visar bara info + "OK"-knapp.

### Lösning: Interactive credentials form för `mesh_auth` repair

```python
class MeshAuthRepairFlow(RepairsFlow):
    """Interactive flow for mesh credential errors.

    Allows user to correct mesh_name + mesh_password directly from
    the Repairs UI without deleting the config entry.
    """

    async def async_step_init(self, user_input=None):
        if user_input is None:
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({
                    vol.Required(CONF_MESH_NAME): str,
                    vol.Required(CONF_MESH_PASSWORD): str,
                }),
                description_placeholders={"device_name": self._entry_title},
            )

        # Validate and update config entry
        errors = {}
        try:
            _validate_mesh_credentials(user_input)
        except vol.Invalid:
            errors["base"] = "invalid_credentials"
            return self.async_show_form(..., errors=errors)

        # Update entry data + trigger reload
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        new_data = {**entry.data, **user_input}
        self.hass.config_entries.async_update_entry(entry, data=new_data)
        await self.hass.config_entries.async_reload(self._entry_id)

        return self.async_create_entry(data={})
```

Lägg till routing i `async_create_fix_flow()` för `ISSUE_MESH_AUTH`:
```python
if issue_id.startswith(ISSUE_MESH_AUTH):
    return MeshAuthRepairFlow(hass, issue_id, entry_id=_extract_entry_id(issue_id))
```

### Problem B: Diagnostik saknar tid-dimension

Lägg till `reconnect_timeline` i `ConnectionStatistics`:

```python
@dataclass
class ReconnectEvent:
    timestamp: float
    duration_offline_s: float | None  # None if first connect
    error_class: str

@dataclass
class ConnectionStatistics:
    # ... befintliga fält ...
    reconnect_timeline: deque[ReconnectEvent] = field(
        default_factory=lambda: deque(maxlen=20)
    )
    rssi_history: deque[tuple[float, int]] = field(  # (timestamp, rssi)
        default_factory=lambda: deque(maxlen=50)
    )
```

Uppdatera diagnostics.py för att exponera dessa:
```python
diag["reconnect_timeline"] = [
    {
        "at": dt_util.utc_from_timestamp(ev.timestamp).isoformat(),
        "offline_seconds": ev.duration_offline_s,
        "error_class": ev.error_class,
    }
    for ev in stats.reconnect_timeline
]
diag["rssi_trend"] = [
    {"at": dt_util.utc_from_timestamp(t).isoformat(), "rssi_dbm": rssi}
    for t, rssi in stats.rssi_history
]
```

### Tests att lägga till (7 st)

```python
class TestInteractiveRepairs:
    async def test_mesh_auth_repair_shows_form()
    async def test_mesh_auth_repair_validates_credentials()
    async def test_mesh_auth_repair_updates_entry_and_reloads()
    async def test_bridge_down_repair_still_read_only()

class TestReconnectTimeline:
    def test_reconnect_event_appended_on_disconnect()
    def test_rssi_history_recorded_in_rssi_loop()
    def test_diagnostics_include_timeline()
```

---

## MESH-17 — Test Coverage Hardening

**Filer:** Nya test-filer
**Netto:** ~+200 rader nya tester

### Gap 1: Bridge health polling (HA-level)

**Ny fil:** `tests/unit/test_ha_bridge_health.py`

```python
class TestBridgeHealthPolling:
    async def test_health_endpoint_failure_marks_unavailable()
    async def test_health_endpoint_recovery_marks_available()
    async def test_bridge_down_creates_repair_issue()
    async def test_health_poll_interval_configurable()
    async def test_health_poll_cancelled_on_stop()
```

### Gap 2: Config entry reload med live coordinator

```python
class TestConfigEntryReload:  # i test_ha_coordinator.py
    async def test_coordinator_stops_cleanly_on_reload()
    async def test_coordinator_restarts_after_reload()
    async def test_state_preserved_after_reload()  # entities restore from cache
```

### Gap 3: Concurrent commands stress test

```python
class TestConcurrentCommands:  # i test_ha_coordinator.py
    async def test_rapid_turn_on_coalesced_by_debounce()
    async def test_rate_limit_drops_excess_commands()
    async def test_transition_cancelled_by_new_command()
```

### Gap 4: Coverage thresholds i pyproject.toml

```toml
[tool.pytest.ini_options]
addopts = "--cov=custom_components/tuya_ble_mesh --cov-fail-under=90"
```

---

## MESH-18 — Firmware Update Entity

**Ny fil:** `custom_components/tuya_ble_mesh/update.py`
**Ändrad fil:** `custom_components/tuya_ble_mesh/__init__.py` (lägg till Platform.UPDATE)
**Netto:** ~+120 rader

### Motivering
Shelly har `UpdateEntity` som låter användaren se och installera firmware-uppdateringar direkt i HA. Utan detta minskar "stickyness" drastiskt — användare föredrar integrationer som sköter hela livscykeln.

### Design

```python
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature

class TuyaBLEMeshUpdateEntity(TuyaBLEMeshEntity, UpdateEntity):
    """Firmware update entity for Tuya BLE Mesh devices.

    Reports current firmware version. Does NOT perform OTA updates
    (no BLE OTA protocol implemented yet) but provides:
    - installed_version: current firmware from coordinator.state
    - latest_version: fetched from manifest/HACS JSON (if available)
    - in_progress: False (OTA not supported yet)
    """

    _attr_supported_features = UpdateEntityFeature(0)  # No install support yet
    _attr_name = None
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def installed_version(self) -> str | None:
        return self.coordinator.state.firmware_version

    @property
    def latest_version(self) -> str | None:
        """Return latest known version from HACS/manifest, or None."""
        # Phase 1: return None (unknown)
        # Phase 2: fetch from release API
        return None

    @property
    def release_notes(self) -> str | None:
        return "OTA firmware update not yet supported for this device type."
```

Setup:
```python
# __init__.py — lägg till Platform.UPDATE i _PLATFORMS
```

---

## MESH-19 — Effect/Scene Support i Light Entity

**Fil:** `custom_components/tuya_ble_mesh/light.py`, `const.py`
**Netto:** ~+80 rader

### Motivering
Tuya BLE mesh-protokollet stödjer `0xF0` scene/effect-kommandon. Shelly exponerar effects via `SUPPORT_EFFECT`. Utan detta saknar vi en feature som är synlig och uppskattad i HA.

### Design

```python
# const.py — ny sektion
MESH_SCENES: dict[str, int] = {
    "flash": 0x01,
    "gradient": 0x02,
    "pulsing": 0x03,
    "color_loop": 0x04,
    "strobe": 0x05,
    "fade": 0x06,
    "smooth": 0x07,
}
```

I `TuyaBLEMeshLight`:
```python
_attr_supported_features = LightEntityFeature.TRANSITION | LightEntityFeature.EFFECT

@property
def supported_effects(self) -> list[str]:
    return list(MESH_SCENES.keys())

@property
def effect(self) -> str | None:
    """Return current effect name if in scene mode."""
    if self.coordinator.state.mode == 2:  # mode 2 = scene
        scene_id = self.coordinator.state.scene_id
        return next((k for k, v in MESH_SCENES.items() if v == scene_id), None)
    return None

async def async_turn_on(self, **kwargs):
    effect = kwargs.get(ATTR_EFFECT)
    if effect is not None and effect in MESH_SCENES:
        scene_id = MESH_SCENES[effect]
        await self.coordinator.send_command_with_retry(
            lambda: self.coordinator.device.send_scene(scene_id),
            description=f"send_scene({effect})",
        )
        return
    # ... befintlig logik ...
```

---

## MESH-20 — Superior Diagnostics (Shelly-beater)

**Filer:** `diagnostics.py`, `coordinator.py`
**Netto:** ~+100 rader

### Motivering
Det här är vår differentiator — Shelly kan inte ge BLE-mesh-specifik topologiinformation. Vi kan. Kombinerat med reconnect timeline och RSSI history (från MESH-16) ger det diagnostik som är **bättre än allt** i HA-ekosystemet för BLE-enheter.

### Tillägg i diagnostics.py

```python
# Mesh topology section (already partial in v0.23.0)
diag["mesh_topology"] = {
    "mode": "bridge" if is_bridge else "direct_ble",
    "protocol": coordinator.capabilities.protocol,   # "SIG_Mesh" | "Tuya_BLE"
    "bridge_type": type(device).__name__ if is_bridge else None,
    "has_light_control": coordinator.capabilities.has_light_control,
    "has_sig_sequence": coordinator.capabilities.has_sig_sequence,
    "has_power_monitoring": coordinator.capabilities.has_power_monitoring,
    # NEW:
    "vendor_id": vendor_id,
    "vendor_name": _get_vendor_name(vendor_id),
    "sig_sequence_persisted": coordinator.capabilities.has_sig_sequence,
}

# Connection quality section (NEW — leverages MESH-16 data)
diag["connection_quality"] = {
    "rssi_trend": [  # last 10 readings with timestamps
        {"t": ts, "dbm": rssi}
        for ts, rssi in list(stats.rssi_history)[-10:]
    ],
    "rssi_avg_dbm": round(
        sum(r for _, r in stats.rssi_history) / max(len(stats.rssi_history), 1)
    ) if stats.rssi_history else None,
    "reconnect_count_24h": sum(
        1 for ev in stats.reconnect_timeline
        if time.time() - ev.timestamp < 86400
    ),
    "last_reconnect_reason": (
        stats.reconnect_timeline[-1].error_class
        if stats.reconnect_timeline else None
    ),
}

# Protocol health (NEW — unique to BLE mesh integrations)
if coordinator.capabilities.has_sig_sequence:
    diag["protocol_health"] = {
        "seq_persistence": "enabled",
        "seq_safety_margin": _SEQ_SAFETY_MARGIN,  # imported from coordinator
        "storm_threshold": coordinator._storm_threshold,
        "storm_detected": stats.storm_detected,
    }
```

---

## Sammanfattning — Vad vi uppnår

| Nivå | Feature | Status efter P3 |
|------|---------|----------------|
| **Gold** | Reconfiguration flow | ✅ MESH-14 |
| **Gold** | Entry uniqueness | ✅ MESH-14 |
| **Gold** | Frozen coordinator state | ✅ MESH-15 |
| **Gold** | Rate limiting | ✅ MESH-15 |
| **Gold** | Interactive repairs | ✅ MESH-16 |
| **Gold** | Coverage ≥ 90% med threshold | ✅ MESH-17 |
| **Gold** | Bridge health polling testat | ✅ MESH-17 |
| **Gold** | Firmware update entity | ✅ MESH-18 |
| **Gold** | Effect/scene support | ✅ MESH-19 |
| **Platinum** | Superior BLE diagnostics | ✅ MESH-20 (unik) |
| **Platinum** | Reconnect timeline | ✅ MESH-16+20 (unik) |
| **Platinum** | RSSI trend | ✅ MESH-16+20 (unik) |

**Realistisk nivå efter P3: Gold+ / tidig Platinum**

### Varför vi slår Shelly

Shelly är Gold-tier på bred front. Vi matchar dem på alla Gold-features (MESH-14–18) men har tre differentierar de saknar:
1. **Reconnect timeline med error class** — Shelly visar "Reconnected X times", vi visar "Disconnected 2026-03-10 14:23 — reason: mesh_auth; recovery: 47s"
2. **BLE RSSI trend** — Shelly (WiFi) har bara "connected/disconnected"; vi visar signal quality trend över tid
3. **Mesh protocol diagnostics** — sequence number, vendor ID, SIG vs. Telink — ingen annan HA BLE-integration ger detta

Dessa tre gör oss till **bästa BLE-diagnostiken i HA-ekosystemet** — ett smalt men verkligt övertag.

---

## Uppskattad storlek

| Commit | Netto rader | Nya tester |
|--------|-------------|-----------|
| MESH-14 (reconfigure + uniqueness) | +120 | 8 |
| MESH-15 (frozen state + rate limiting) | +80 | 6 |
| MESH-16 (interactive repairs + timeline) | +150 | 7 |
| MESH-17 (test coverage) | +200 | 20+ |
| MESH-18 (update entity) | +120 | 5 |
| MESH-19 (effects) | +80 | 6 |
| MESH-20 (superior diagnostics) | +100 | 5 |
| **Totalt P3** | **~+850** | **~57** |

---

## Nästa steg

Börja med **MESH-14** (reconfiguration flow + uniqueness) — det är det enskilt viktigaste gapet mot Shelly och blockerar ingen annan feature.

```bash
cd /home/claude/tuya-ble-mesh
python3 -m pytest tests/unit/ --timeout=60 -q  # baseline: 1217 passed
# Implementera MESH-14
python3 -m pytest tests/unit/ --timeout=60 -q  # target: 1225+ passed
```
