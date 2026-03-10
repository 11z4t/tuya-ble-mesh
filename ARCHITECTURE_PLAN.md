# Architecture Improvement Plan — tuya-ble-mesh v0.23+

**Author:** Deep code analysis (Claude Opus 4.6)
**Date:** 2026-03-10
**Base:** v0.22.0 codebase analysis (819-line coordinator, 435-line light, 714-line bridge, 432-line connection, 511-line device, 640-line protocol)
**Goal:** Move from "experimental beta" to "robust Home Assistant integration"

---

## Executive Summary

Three architectural changes will dramatically increase reliability:

1. **Event-driven BLE notification pipeline** — Replace thread-bridging with proper async event bus
2. **Robust command dispatcher** — Replace fire-and-forget with response-matched command/reply
3. **SIG Mesh reliability layer** — Add sequence persistence, SAR timeout handling, retransmit

Current state: The codebase is *functionally correct* but *structurally fragile*. Most failures are silent (no response matching), timing-dependent (no retransmit), or thread-unsafe (BLE callbacks on wrong thread patched with `call_soon_threadsafe`).

---

## Phase 1: Event-Driven Notification Pipeline

### Problem

BLE notifications arrive on Bleak's background thread. Current fix (`_dispatch_update` using `call_soon_threadsafe`) is correct but:
- No ordering guarantee between multiple rapid notifications
- No backpressure if HA event loop is slow
- State mutations in callbacks happen on the wrong thread *before* dispatch
- Multiple state fields updated non-atomically (race between `_on_status_update` setting 7 fields and entity reading them)

### Current Flow (Fragile)

```
Bleak thread:
  notification_handler(data)  ← WRONG THREAD
    → decrypt(data)
    → parse_status(data)
    → coordinator._on_status_update(status)  ← WRONG THREAD
        → self._state.brightness = status.brightness  ← MUTATION ON WRONG THREAD
        → self._state.color_temp = status.color_temp   ← NON-ATOMIC
        → self._dispatch_update()
            → hass.loop.call_soon_threadsafe(...)  ← CORRECT: schedules on event loop

Event loop (later):
  async_set_updated_data(None)
    → entities read self._state.* ← May see partially-updated state
```

### Target Flow (Robust)

```
Bleak thread:
  notification_handler(data)
    → raw_queue.put_nowait(data)  ← ONLY action on wrong thread (thread-safe queue)

Event loop (consumer task):
  while True:
    data = await raw_queue.get()
    status = decrypt_and_parse(data)  ← ON CORRECT THREAD
    old_state = copy(self._state)
    self._state = apply_update(old_state, status)  ← ATOMIC REPLACEMENT
    if old_state != self._state:
      self.async_set_updated_data(None)  ← DIRECT CALL (already on event loop)
```

### Implementation Plan

**File: `lib/tuya_ble_mesh/notification_bus.py` (NEW)**

```python
"""Thread-safe notification bus for BLE → event loop dispatch."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

_LOGGER = logging.getLogger(__name__)

class NotificationType(Enum):
    RAW_BLE = "raw_ble"
    STATUS = "status"
    ONOFF = "onoff"
    VENDOR = "vendor"
    DISCONNECT = "disconnect"

@dataclass(frozen=True, slots=True)
class Notification:
    type: NotificationType
    data: Any
    source_address: str

NotificationHandler = Callable[[Notification], None]

class NotificationBus:
    """Thread-safe async queue bridging BLE thread → event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int = 64) -> None:
        self._loop = loop
        self._queue: asyncio.Queue[Notification] = asyncio.Queue(maxsize=maxsize)
        self._handlers: dict[NotificationType, list[NotificationHandler]] = {}
        self._consumer_task: asyncio.Task[None] | None = None

    def publish(self, notification: Notification) -> None:
        """Thread-safe: enqueue from any thread."""
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, notification)
        except asyncio.QueueFull:
            _LOGGER.warning("Notification queue full, dropping: %s", notification.type)

    def subscribe(self, ntype: NotificationType, handler: NotificationHandler) -> None:
        self._handlers.setdefault(ntype, []).append(handler)

    def start(self) -> None:
        self._consumer_task = asyncio.ensure_future(self._consume())

    async def stop(self) -> None:
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

    async def _consume(self) -> None:
        while True:
            notification = await self._queue.get()
            for handler in self._handlers.get(notification.type, []):
                try:
                    handler(notification)
                except Exception:
                    _LOGGER.warning("Handler error for %s", notification.type, exc_info=True)
```

**Changes to `coordinator.py`:**

1. Replace direct callback registration with bus subscription
2. State mutations happen in bus consumer (always on event loop)
3. Use frozen dataclass replacement instead of field-by-field mutation

```python
# BEFORE (current):
def _on_status_update(self, status: StatusResponse) -> None:
    self._state.brightness = status.white_brightness  # Non-atomic, wrong thread
    self._state.color_temp = status.color_temp
    # ... 5 more fields ...
    self._dispatch_update()

# AFTER (proposed):
def _on_status_notification(self, notification: Notification) -> None:
    """Called on event loop by NotificationBus consumer."""
    status = notification.data
    old = self._state
    self._state = TuyaBLEMeshDeviceState(
        is_on=status.is_on if status.is_on is not None else old.is_on,
        brightness=status.white_brightness if status.white_brightness else old.brightness,
        color_temp=status.color_temp if status.color_temp else old.color_temp,
        # ... atomic replacement ...
        available=True,
    )
    if self._state != old:
        self.async_set_updated_data(None)
```

**Changes to `device.py` / `connection.py`:**

Replace `self._notification_handler(data)` with `self._bus.publish(Notification(...))`.

### Migration Strategy

1. Add `NotificationBus` as optional (default: None, falls back to current behavior)
2. Coordinator creates bus if `hass` is available
3. Device accepts bus in constructor
4. Remove `_dispatch_update()` once all paths use bus
5. Test: verify ordering, backpressure, thread safety

### Files Changed

| File | Change | Risk |
|------|--------|------|
| `lib/tuya_ble_mesh/notification_bus.py` | NEW | Low (additive) |
| `lib/tuya_ble_mesh/device.py` | Add bus.publish() | Medium |
| `lib/tuya_ble_mesh/connection.py` | Pass bus to notification handler | Medium |
| `coordinator.py` | Replace callbacks with bus subscriptions | High |
| `tests/unit/test_notification_bus.py` | NEW | Low |

### Test Plan

- [ ] Thread safety: publish from 10 threads concurrently, verify ordering
- [ ] Backpressure: fill queue, verify drop + warning log
- [ ] Atomic state: rapid notifications, verify no partial reads
- [ ] Disconnect during consume: graceful shutdown
- [ ] Bus stop: pending notifications drained or dropped cleanly

---

## Phase 2: Command Dispatcher with Response Matching

### Problem

Commands are fire-and-forget. No way to know if device received and executed:

```python
# Current: Hope for the best
await device.send_brightness(80)
# Did device actually change brightness? Unknown until unsolicited status arrives.
# If BLE packet was lost, no retry. If device was busy, no backoff.
```

**Consequences:**
- Silent failures (user moves slider, nothing happens, no error)
- Stale state (coordinator shows brightness 80, device is still at 50)
- No timeout per command (only BLE write timeout)
- No correlation between command and response

### Current Flow (Fragile)

```
send_brightness(80)
  → encode_command_packet(opcode=0xD2, params=[dp_id=122, value=80])
  → conn.write_command(packet)  ← BLE ACK at link layer only
  → (nothing — no response tracking)

...later (maybe):
  device sends unsolicited status notification
  → coordinator updates state
  → (no correlation to our command)
```

### Target Flow (Robust)

```
send_brightness(80)
  → cmd = PendingCommand(opcode=0xD2, params=..., timeout=5.0)
  → dispatcher.submit(cmd)
  → await cmd.future  ← Waits for matching response OR timeout

Notification arrives:
  → dispatcher.match(notification)
  → if matches pending command:
      cmd.future.set_result(notification)
  → else:
      forward to coordinator as unsolicited update
```

### Implementation Plan

**File: `lib/tuya_ble_mesh/dispatcher.py` (NEW)**

```python
"""Command dispatcher with response matching and retry."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

@dataclass
class PendingCommand:
    opcode: int
    params: bytes
    dest_id: int
    future: asyncio.Future[Any] = field(default_factory=lambda: asyncio.get_event_loop().create_future())
    created_at: float = field(default_factory=time.monotonic)
    timeout: float = 5.0
    max_retries: int = 2
    attempt: int = 0
    match_fn: Callable[[Any], bool] | None = None

class CommandDispatcher:
    """Serialized command dispatch with response matching."""

    def __init__(
        self,
        send_fn: Callable[[int, bytes, int], Any],  # async (opcode, params, dest) -> None
        *,
        max_concurrent: int = 1,  # BLE mesh = 1 command at a time
        default_timeout: float = 5.0,
    ) -> None:
        self._send_fn = send_fn
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._default_timeout = default_timeout
        self._pending: PendingCommand | None = None
        self._unmatched_handler: Callable[[Any], None] | None = None

    async def send(
        self,
        opcode: int,
        params: bytes,
        dest_id: int,
        *,
        timeout: float | None = None,
        max_retries: int = 2,
        match_fn: Callable[[Any], bool] | None = None,
    ) -> Any:
        """Send command and wait for matching response.

        Args:
            match_fn: Predicate to match incoming notification to this command.
                      If None, command is fire-and-forget (returns after BLE write).
        """
        cmd = PendingCommand(
            opcode=opcode,
            params=params,
            dest_id=dest_id,
            timeout=timeout or self._default_timeout,
            max_retries=max_retries,
            match_fn=match_fn,
        )

        async with self._semaphore:
            for attempt in range(cmd.max_retries + 1):
                cmd.attempt = attempt
                cmd.future = asyncio.get_event_loop().create_future()
                self._pending = cmd

                try:
                    await self._send_fn(opcode, params, dest_id)
                except Exception as exc:
                    self._pending = None
                    if attempt >= cmd.max_retries:
                        raise
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue

                if match_fn is None:
                    self._pending = None
                    return None  # Fire-and-forget

                try:
                    result = await asyncio.wait_for(cmd.future, timeout=cmd.timeout)
                    self._pending = None
                    return result
                except asyncio.TimeoutError:
                    self._pending = None
                    if attempt >= cmd.max_retries:
                        _LOGGER.warning(
                            "Command 0x%02X timed out after %d attempts",
                            opcode, attempt + 1,
                        )
                        raise
                    _LOGGER.debug("Retry %d for opcode 0x%02X", attempt + 1, opcode)

    def on_notification(self, notification: Any) -> bool:
        """Try to match notification to pending command.

        Returns True if matched (consumed), False if unmatched.
        """
        if self._pending and self._pending.match_fn:
            try:
                if self._pending.match_fn(notification):
                    if not self._pending.future.done():
                        self._pending.future.set_result(notification)
                    return True
            except Exception:
                _LOGGER.debug("Match function error", exc_info=True)
        return False

    def set_unmatched_handler(self, handler: Callable[[Any], None]) -> None:
        """Handler for notifications that don't match any pending command."""
        self._unmatched_handler = handler
```

**Changes to `device.py`:**

```python
# BEFORE:
async def send_brightness(self, level: int) -> None:
    params = encode_compact_dp(DP_ID_BRIGHTNESS, DP_TYPE_VALUE, level)
    await self.send_command(OPCODE_COMPACT_DP, params)

# AFTER:
async def send_brightness(self, level: int) -> None:
    params = encode_compact_dp(DP_ID_BRIGHTNESS, DP_TYPE_VALUE, level)
    await self._dispatcher.send(
        OPCODE_COMPACT_DP,
        params,
        self._mesh_id,
        match_fn=lambda status: (
            isinstance(status, StatusResponse)
            and status.white_brightness == level
        ),
        timeout=3.0,
    )
```

### Migration Strategy

1. Add `CommandDispatcher` as optional wrapper
2. `MeshDevice` creates dispatcher if available, falls back to direct send
3. Bridge devices use fire-and-forget (HTTP bridge has own response matching)
4. Gradually add `match_fn` to each command
5. Commands without `match_fn` work exactly as before

### Response Matching Heuristics

| Command | Match Predicate | Notes |
|---------|----------------|-------|
| `send_power(on)` | `status.is_on == on` | Simple boolean |
| `send_brightness(level)` | `status.white_brightness == level` | ±1 tolerance? |
| `send_color_temp(temp)` | `status.color_temp == temp` | Device may round |
| `send_color(r,g,b)` | `status.r == r and ...` | Exact match |
| `send_mesh_address(addr)` | None (fire-and-forget) | No status expected |
| `send_mesh_reset()` | None (fire-and-forget) | Device disappears |

### Files Changed

| File | Change | Risk |
|------|--------|------|
| `lib/tuya_ble_mesh/dispatcher.py` | NEW | Low (additive) |
| `lib/tuya_ble_mesh/device.py` | Use dispatcher for sends | Medium |
| `coordinator.py` | Wire notification → dispatcher.on_notification | Medium |
| `tests/unit/test_dispatcher.py` | NEW | Low |

### Test Plan

- [ ] Basic send + match: command completes when matching notification arrives
- [ ] Timeout: command raises TimeoutError after timeout
- [ ] Retry: command retried on timeout, succeeds on 2nd attempt
- [ ] Fire-and-forget: command returns immediately when match_fn=None
- [ ] Serialization: 2 commands execute sequentially (semaphore)
- [ ] Unmatched notification: forwarded to coordinator
- [ ] Concurrent access: multiple callers await dispatcher safely

---

## Phase 3: SIG Mesh Reliability Layer

### Problem

SIG Mesh has more protocol complexity but the implementation lacks:
1. **Sequence persistence** — resets to 2000 on reconnect (IV index mismatch risk)
2. **SAR timeout** — incomplete segments hang forever
3. **No retransmit** — if device misses a segment, reassembly stalls
4. **Config model response timeout** — 15s hardcoded, no retry

### 3A: Sequence Number Persistence

**Current (Risky):**
```python
class SIGMeshDevice:
    _seq = _INITIAL_SEQ = 2000  # Hardcoded start
```

**Proposed:**
```python
class SIGMeshDevice:
    def __init__(self, ..., seq_store: SeqStore | None = None):
        self._seq_store = seq_store
        self._seq = 2000  # Default, overridden by stored value

    async def _load_seq(self) -> None:
        if self._seq_store:
            stored = await self._seq_store.load()
            if stored is not None:
                self._seq = stored + 100  # Safety margin for crash recovery

    async def _persist_seq(self) -> None:
        if self._seq_store:
            await self._seq_store.save(self._seq)
```

**Note:** Coordinator already has `_load_seq`/`_save_seq` but only for the coordinator wrapper. The SIGMeshDevice itself doesn't persist. Move persistence INTO the device class.

### 3B: SAR Timeout Handling

**Current:** `_segment_buffers` dict grows unboundedly on incomplete messages.

**Proposed:**
```python
# In sig_mesh_device.py, add cleanup task:
async def _sar_cleanup_loop(self) -> None:
    """Remove stale segment buffers every 30s."""
    while True:
        await asyncio.sleep(30.0)
        now = time.monotonic()
        stale = [
            key for key, buf in self._segment_buffers.items()
            if now - buf.created_at > _SAR_TIMEOUT  # 10s
        ]
        for key in stale:
            _LOGGER.debug("Dropping stale SAR buffer: %s", key)
            del self._segment_buffers[key]
```

### 3C: Config Model Response Retry

**Current:**
```python
await asyncio.wait_for(future, timeout=15.0)  # One shot, no retry
```

**Proposed:**
```python
async def _send_config_with_retry(
    self,
    access_payload: bytes,
    response_opcode: int,
    max_retries: int = 2,
    timeout: float = 10.0,
) -> bytes:
    for attempt in range(max_retries + 1):
        future = asyncio.get_event_loop().create_future()
        self._pending_responses[response_opcode] = future
        await self._send_access(access_payload)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            if attempt >= max_retries:
                raise
            _LOGGER.debug("Config retry %d for opcode 0x%04X", attempt + 1, response_opcode)
            await asyncio.sleep(1.0)
```

### Files Changed

| File | Change | Risk |
|------|--------|------|
| `lib/tuya_ble_mesh/sig_mesh_device.py` | Seq persistence, SAR cleanup, config retry | High |
| `coordinator.py` | Remove seq persistence (moved to device) | Medium |
| `tests/unit/test_sig_mesh_device.py` | SAR timeout, config retry tests | Low |

---

## Phase 4: Hardening & Quality (Quick Wins)

These are low-risk, high-value fixes that can be done independently:

### 4A: Task Cleanup on async_stop

**Problem:** Coordinator's `async_stop()` cancels tasks but doesn't await them.

```python
# BEFORE:
async def async_stop(self) -> None:
    self._running = False
    if self._reconnect_task:
        self._reconnect_task.cancel()
    if self._rssi_task:
        self._rssi_task.cancel()

# AFTER:
async def async_stop(self) -> None:
    self._running = False
    tasks = [t for t in (self._reconnect_task, self._rssi_task, self._seq_persist_task) if t]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
```

### 4B: Frozen State Dataclass

**Problem:** `TuyaBLEMeshDeviceState` is mutable, updated field-by-field.

```python
# BEFORE:
@dataclass
class TuyaBLEMeshDeviceState:
    is_on: bool = False
    brightness: int = 0
    ...

# AFTER:
@dataclass(frozen=True, slots=True)
class TuyaBLEMeshDeviceState:
    is_on: bool = False
    brightness: int = 0
    ...

# Update via replace():
from dataclasses import replace
self._state = replace(self._state, brightness=80, available=True)
```

### 4C: Monotonic Time for TTLs

**Problem:** `time.time()` in coordinator stats is used for uptime calculation. If system clock adjusts (NTP), uptime can go negative.

```python
# Replace for TTL/timeout calculations only:
connect_time: float = field(default_factory=time.monotonic)
# Keep time.time() for human-readable timestamps in diagnostics
```

### 4D: Logging Context Module Tests

**Problem:** `logging_context.py` (154 lines) has 0 tests.

```python
# tests/unit/test_logging_context.py
def test_correlation_id_isolation():
    """Two concurrent operations should have independent correlation IDs."""
    ...

def test_mesh_log_adapter_format():
    """MeshLogAdapter should prepend device MAC and operation name."""
    ...
```

### 4E: Bridge HTTP Deduplication

**Problem:** `SIGMeshBridgeDevice._http_get` and `TelinkBridgeDevice._http_get` are identical except for base URL construction.

```python
# Extract to shared mixin:
class BridgeHTTPMixin:
    _session: aiohttp.ClientSession | None
    _bridge_host: str
    _bridge_port: int

    def _get_session(self) -> aiohttp.ClientSession: ...
    async def _close_session(self) -> None: ...
    async def _http_get(self, path: str, timeout: float = 5.0) -> dict[str, Any]: ...
    async def _http_post(self, path: str, data: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]: ...

class SIGMeshBridgeDevice(BridgeHTTPMixin):
    ...
class TelinkBridgeDevice(BridgeHTTPMixin):
    ...
```

---

## Implementation Priority & Ordering

| Phase | Effort | Impact | Risk | Dependencies |
|-------|--------|--------|------|-------------|
| **4A: Task cleanup** | 1h | Medium | Very Low | None |
| **4B: Frozen state** | 2h | Medium | Low | None |
| **4C: Monotonic time** | 30min | Low | Very Low | None |
| **4D: Logging tests** | 2h | Low | Very Low | None |
| **4E: Bridge dedup** | 1h | Low | Low | None |
| **Phase 2: Dispatcher** | 8h | **High** | Medium | None |
| **Phase 1: Event bus** | 12h | **High** | High | Phase 2 helpful |
| **Phase 3A: Seq persist** | 2h | Medium | Medium | None |
| **Phase 3B: SAR timeout** | 1h | Medium | Low | None |
| **Phase 3C: Config retry** | 2h | Medium | Low | None |

### Recommended Order

```
Sprint 1 (v0.23.0): Quick wins — 4A, 4B, 4C, 4D, 4E
Sprint 2 (v0.24.0): Command dispatcher — Phase 2
Sprint 3 (v0.25.0): Event bus — Phase 1
Sprint 4 (v0.26.0): SIG Mesh reliability — Phase 3
```

### Version Milestones

| Version | Quality Level | Key Feature |
|---------|-------------|-------------|
| 0.22.0 | Beta (current) | Thread-safe callbacks, session reuse |
| 0.23.0 | Stable Beta | Task cleanup, frozen state, test coverage |
| 0.24.0 | Release Candidate | Command response matching, retry |
| 0.25.0 | Production | Event-driven pipeline, atomic state |
| 0.26.0 | Production+ | SIG Mesh reliability, seq persistence |

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Event bus breaks existing callback flow | Medium | High | Feature flag, fallback to current behavior |
| Dispatcher timeout too aggressive | Medium | Medium | Configurable per-command timeout |
| Frozen dataclass breaks coordinator state | Low | High | Run full test suite after change |
| Seq persistence file corruption | Low | Medium | Safety margin (current +100) |
| SAR cleanup drops valid segments | Low | Low | 10s timeout is generous |

---

## Test Coverage Targets

| Module | Current | Target | Key New Tests |
|--------|---------|--------|---------------|
| notification_bus.py | 0% | 95% | Thread safety, ordering, backpressure |
| dispatcher.py | 0% | 95% | Send/match, timeout, retry, serialization |
| coordinator.py | 90% | 95% | Frozen state, task cleanup, bus integration |
| logging_context.py | 0% | 80% | Correlation ID isolation, adapter format |
| sig_mesh_device.py | 85% | 95% | SAR timeout, config retry, seq persist |
| device.py | 90% | 95% | Dispatcher integration, concurrent commands |

---

## Appendix: Current Architecture Diagram

```
┌─────────────────────────────────────────────────────┐
│                  Home Assistant                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ light.py │ │sensor.py │ │switch.py │  Entities   │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘            │
│       │             │            │                    │
│       └──────┬──────┘────────────┘                   │
│              │ CoordinatorEntity (auto-update)        │
│       ┌──────┴──────┐                                │
│       │coordinator.py│  TuyaBLEMeshCoordinator       │
│       │             │  - State management             │
│       │             │  - Reconnection                 │
│       │             │  - Error classification         │
│       │             │  - RSSI polling                 │
│       └──────┬──────┘                                │
│              │ device.connect/send/register_callback  │
└──────────────┼───────────────────────────────────────┘
               │
┌──────────────┼───────────────────────────────────────┐
│   Library    │  lib/tuya_ble_mesh/                    │
│              │                                        │
│  ┌───────────┴──────┐     ┌──────────────────┐      │
│  │  device.py       │     │ sig_mesh_device.py│      │
│  │  MeshDevice      │     │ SIGMeshDevice     │      │
│  │  - Command queue │     │ - Full mesh stack │      │
│  │  - DP encoding   │     │ - SAR             │      │
│  │  - Status decode │     │ - Config model    │      │
│  └───────┬──────────┘     └────────┬─────────┘      │
│          │                          │                 │
│  ┌───────┴──────────┐     ┌────────┴─────────┐      │
│  │ connection.py    │     │sig_mesh_protocol.py│     │
│  │ BLEConnection    │     │ Network/Transport  │     │
│  │ - Keep-alive     │     │ - Encryption       │     │
│  │ - Provisioning   │     │ - Segmentation     │     │
│  │ - Seq counter    │     │ - Reassembly       │     │
│  └───────┬──────────┘     └────────┬─────────┘      │
│          │                          │                 │
│  ┌───────┴──────────┐     ┌────────┴─────────┐      │
│  │  crypto.py       │     │ sig_mesh_crypto.py│      │
│  │  AES-CCM (Telink)│     │ AES-CCM (SIG)    │      │
│  └──────────────────┘     └──────────────────┘      │
│                                                       │
│  ┌──────────────────────────────────────────┐        │
│  │ sig_mesh_bridge.py                        │        │
│  │  SIGMeshBridgeDevice / TelinkBridgeDevice │        │
│  │  - HTTP → RPi daemon → BLE               │        │
│  └──────────────────────────────────────────┘        │
│                                                       │
│  ┌──────────────┐  ┌──────────┐  ┌──────────┐       │
│  │provisioner.py│  │scanner.py│  │ power.py  │       │
│  │ BLE pairing  │  │ BLE scan │  │ Shelly    │       │
│  └──────────────┘  └──────────┘  └──────────┘       │
└───────────────────────────────────────────────────────┘
```

### Post-Implementation Architecture (Target)

```
┌─────────────────────────────────────────────────────┐
│                  Home Assistant                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ light.py │ │sensor.py │ │switch.py │  Entities   │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘            │
│       └──────┬──────┘────────────┘                   │
│       ┌──────┴──────┐                                │
│       │coordinator.py│  TuyaBLEMeshCoordinator       │
│       │  - Frozen state (atomic replacement)         │
│       │  - Bus consumer (event loop only)            │
│       │  - Task lifecycle (clean stop)               │
│       └──────┬──────┘                                │
│              │                                        │
│       ┌──────┴──────────┐   NEW                      │
│       │notification_bus │   Thread-safe queue         │
│       │  BLE thread →   │   → event loop consumer    │
│       └──────┬──────────┘                            │
│              │                                        │
│       ┌──────┴──────────┐   NEW                      │
│       │  dispatcher.py  │   Response matching         │
│       │  - Send + wait  │   - Timeout + retry         │
│       │  - Serialization│   - Match predicates        │
│       └──────┬──────────┘                            │
└──────────────┼───────────────────────────────────────┘
               │
┌──────────────┼───────────────────────────────────────┐
│   Library    │                                        │
│  ┌───────────┴──────┐     ┌──────────────────┐      │
│  │  device.py       │     │ sig_mesh_device.py│      │
│  │  + Dispatcher    │     │ + Seq persistence │      │
│  │  + Bus publish   │     │ + SAR cleanup     │      │
│  │                  │     │ + Config retry    │      │
│  └───────┬──────────┘     └────────┬─────────┘      │
│          │                          │                 │
│  ┌───────┴──────────┐     ┌────────┴─────────┐      │
│  │ connection.py    │     │ bridge_http.py    │ NEW  │
│  │ (unchanged)      │     │ BridgeHTTPMixin   │      │
│  └──────────────────┘     └──────────────────┘      │
└───────────────────────────────────────────────────────┘
```
