# Plan: Shared SIG Mesh Network

Status (2026-11-04): **Phases 1–5 implemented** (`a4a294d`, `0e750a8`, `476dfef`,
Phase 5 commit below). New SIG devices discovered over Bluetooth now join the shared
network; the network entry is created automatically with the first one.

Implementation notes / deviations from the plan below:
- Instead of splitting `SIGMeshDevice`, node views (`sig_mesh_node.py`) subclass
  `SIGMeshDevice`/`SIGMeshLight` and override only transport (SEQ, writes, link
  state). Each node keeps its own pending-response table and device key, which
  gives per-src reply matching without touching the white-box-tested internals.
- Deferred to Phase 6: explicit Proxy Configuration filter messages (the spec's
  automatic accept-list add of our source address is relied on), Secure Network
  Beacon / IV Update handling, SAR retransmission of unacked outgoing segments.
- Phase 4: configuration runs over the shared link and falls back to a direct
  link to the new node (`MeshNetwork.use_proxy`); Tuya timestamp requests are
  answered during configuration; a config failure leaves the node `pending` and
  it is retried every 60 s once reachable. Relay/GATT Proxy/Network Transmit Set
  were not added — Phase 0 showed both bulbs have relay + proxy on by default.
- Phase 5: repair issue `legacy_sig_network_<entry_id>` per legacy SIG entry →
  `migration.async_migrate_legacy_entry`. If the bulb neither acks the reset nor
  shows up unprovisioned, the legacy entry is reloaded unchanged; a bulb that is
  already unprovisioned (fresh 0x1827 advert) skips the reset step.
- Node `update` entities use the subentry id in their unique ID (legacy used the
  entry id); Phase 5 migration must rename that one unique ID when re-pointing.
Decided: D1 = config subentries, D2 = Node Reset + reprovision, D7 = keys in
HA `entry.data`, D8 = n/a (no other SIG devices in this install).
D3–D6, D9 accepted as proposed: allocate from 0x0002 (0x00B0 reserved until
migration), one proxy link, Node Reset on removal with address quarantine
until confirmed, one network, groups in Phase 6.

## Why

Today every SIG Mesh device is its own config entry with its **own random
NetKey/AppKey** and the **same unicast address (0x00B0)**, reached over its
**own direct GATT proxy connection**. Consequences seen in production:

- Bulbs can never relay for each other (different networks), even though they
  report `features=0x0007` (relay + proxy + friend).
- Each bulb permanently holds an ESPHome BLE proxy slot (3 by default, 6 after
  the ble-proxy-2 change; IDF max 9). Provisioning needs one more.
- A weak link to one bulb means that bulb is unreachable, rather than routed
  through a neighbour.

Goal: one network (shared NetKey/AppKey/IV index), unique unicast allocation,
**one GATT proxy connection** to the best-reachable node with failover, and every
device addressed by unicast through it.

## 0. What blocks multi-node today (in the lib)

1. `_pending_responses` match on opcode only (`sig_mesh_device_segments.py`):
   two nodes answering the same opcode would resolve each other's futures.
2. One device key per link: `MeshKeys.dev_key` is used for all Config
   send/decrypt — node B's Config Status would be decrypted with node A's key.
3. Status handlers ignore `src` (`SIGMeshLight._handle_model_status`, onoff and
   vendor callbacks).
4. No receive-side replay/duplicate protection, no Proxy Configuration (filter)
   messages, no Secure Network Beacon handling, IVI bit ignored. Relaying
   produces duplicate PDUs.
5. No retransmission of unacked outgoing segments (needed once relays drop
   segments, e.g. 2-segment AppKey Add).
6. `send_power` hand-rolls PDUs; it, `send_vendor_command`, `send_attention`
   should all go through `_send_access`.
7. Provisioner takes `unicast_addr` before element count is known (it only
   arrives in Capabilities, `sig_mesh_provisioner_exchange.py`).
8. No `async_migrate_entry`; `ConfigFlow.VERSION = 1`; min HA 2024.1.
9. SEQ is persisted per entry; with one source address (0x0001) on one network,
   SEQ must be per network.
10. `sig_mesh_bridge.py` is an HTTP-daemon client — only the "one link, many
    devices" duck-typing idea is reusable.

## 1. Data model

**Decided (D1): one "SIG Mesh Network" config entry; each node is a config
subentry (`subentry_type="node"`) of it.** Subentries have been stable since
HA 2025.3/2025.4 (devices and entities attach via `config_subentry_id`);
production runs 2026.10.4. Bump `hacs.json` minimum to 2025.6.

Why subentries beat separate network + device entries:

- The nodes really do share one lifecycle — one link, one key set, one SEQ
  counter, one address space. One entry = one setup/unload; no cross-entry
  ordering (`ConfigEntryNotReady` until the network loads) and no dangling
  `network_id` references.
- Allocation and SEQ reservation happen inside one entry, so there is no race
  between entries setting up in parallel.
- Native UI: "Add node" button on the network entry, nodes listed under it,
  per-node delete → `async_remove_subentry` hook sends Config Node Reset.
- Cost: subentry flows can only start from `user`/`reconfigure`, not
  Bluetooth discovery. Bridge it: the discovery config flow confirms,
  provisions, calls `hass.config_entries.async_add_subentry(network_entry,
  …)`, then aborts with `added_to_network`. Reloading the network reloads all
  nodes — acceptable, they share one link anyway.

- **Network entry** (`entry_kind="sig_network"`, unique_id=`network_uuid`):
  `name`, `net_key`(+index 0), `app_key`(+index 0), `provisioner_addr`=0x0001
  — keys in `entry.data`, like today (D7); redacted in diagnostics, never
  logged. Options `max_proxy_links`, `preferred_proxies`, `excluded_proxies`.
- **Network runtime store** `Store("tuya_ble_mesh.network.<uuid>")` — changes
  often, so not in `entry.data` (updating that reloads the entry):
  `seq_reserved`, `iv_index`, `iv_update_active`, `reserved_ranges`,
  `last_proxy_mac`.
- **Node subentry** (unique_id = MAC): `mac_address`, `dev_key`,
  `unicast_target`, `element_count`, `features`, `model_elements`,
  `config_state` (`complete|pending`).
- **Address source of truth** = node subentries' (`unicast_target`,
  `element_count`) + `reserved_ranges`; the allocator derives free space each
  time, so nothing can drift.
- **Legacy entries** (top-level entries with `net_key`) keep working in direct
  mode until migrated; `__init__.py` branches on `entry_kind`.
- Entity unique IDs stay MAC-based. Because unique IDs are per integration,
  migration must **re-point** the existing device and entities to the new
  subentry (`device_registry.async_update_device(add_config_entry_id=,
  add_config_subentry_id=)`, `entity_registry.async_update_entity(
  config_entry_id=, config_subentry_id=)`) before removing the legacy entry —
  never recreate them.

## 2. Library (`lib/tuya_ble_mesh/`, no HA imports)

**Phase 1 — generalise internals, no behaviour change**

- `sig_mesh_protocol.py`: device key passed explicitly / resolved by src;
  `iv_index_for_ivi()`; proxy-config PDU encrypt/decrypt (proxy nonce);
  Secure Network Beacon parse + beacon/identity keys (crypto via
  `sig_mesh_crypto`).
- `sig_mesh_protocol_codec.py`: Proxy Config (Set Filter Type, Add/Remove
  Addresses, Filter Status); Config Node Reset, Relay Get/Set, GATT Proxy
  Get/Set, Network Transmit Set, Key Refresh opcodes, Model Subscription Add;
  `parse_proxy_advertisement()` (Network ID / Node Identity).
- `sig_mesh_device_segments.py`: pending responses keyed by
  `(src, opcode, corr)`; pass `src` to every callback; `ReplayCache`
  (per-src SEQ/IV high-water + PDU-hash LRU); route beacons / proxy-config PDUs.
- `sig_mesh_device_commands.py`: `_send_access(dst→key)`; rebuild
  `send_power`/`send_vendor_command(dst=)`/`send_attention` on it; paced write
  queue (~30 ms gap — ESPHome drops write-without-response floods); SAR
  retransmit on Segment Ack.
- Split `SIGMeshLight` into `SIGLightModelMixin`, Config client into
  `SIGConfigClientMixin`.

**Phase 2 — new modules**

- `sig_mesh_proxy_link.py` `GattProxyLink`: the GATT plumbing lifted out of
  `SIGMeshDevice.connect`.
- `sig_mesh_network.py` `MeshNetwork`: keys (no dev key), shared SEQ store, IV
  state, replay cache, reassembly, pending responses, node registry
  `{unicast range → (dev_key, handler)}`, active link(s).
  `send_access(dst, …)` picks the key by dst and matches the reply on src.
  On link-up: proxy filter = accept list [0x0001], confirm IV from the first
  beacon, notify all nodes.
- `sig_mesh_node.py` `SIGMeshNode` / `SIGMeshLightNode`: per-device views with
  the same duck type coordinators and entities already use; `disconnect()`
  unregisters, never drops the shared link.
- `sig_mesh_proxy_selector.py`: pure ranking (RSSI, freshness, failure penalty
  with decay, stickiness, preferred/excluded).
- `sig_mesh_address_allocator.py`: lowest gap fitting `num_elements`, from
  0x0002; `AddressSpaceExhaustedError`.
- Provisioner accepts `unicast_allocator(num_elements)` called after
  Capabilities.
- `SIGMeshDevice` stays as legacy one-link-one-node (also needed for migration).

## 3. Home Assistant side

- New `mesh_network.py` `MeshNetworkManager` (network entry `runtime_data`): store + shared SEQ reservation (move
  `_ReservingSeqStore` out of `coordinator.py`); proxy candidates from HA
  bluetooth (0x1828 adverts matched to our Network ID = k3(NetKey)); single
  connect/failover loop (on drop, penalise and try the next candidate
  immediately; back off only after the whole list fails); repair issue when no
  proxy reachable.
- `__init__.py`: branch on `entry_kind`. The network entry builds one
  coordinator per node subentry and forwards platforms once; platforms call
  `async_add_entities(..., config_subentry_id=sub_id)` per node. Set up even
  if the link is down. Advert callbacks for network nodes go to the manager (no
  per-node reconnects). Subentry added/removed → entry update listener
  reloads.
- `coordinator.py` / `connection_manager.py`: an externally-managed mode — no
  own reconnect, advert RSSI instead of GATT RSSI, no per-entry SEQ, liveness =
  network up + recent message from this node's src.
- **Time sync**: immediate push to the proxy node on link-up (it is the one that
  drops the link), staggered pushes to the others; answer requests to the
  requesting src.
- Entities unchanged for lights/switches. Network gets a hub device
  (`via_device`), plus "Mesh connected", "Active proxy node", "Switch proxy".
- Diagnostics: proxy MAC, IV, SEQ high-water, node table, failover history;
  keys redacted.

## 4. Message routing

Out: entity → `node.send_x()` → `network.send_access(dst)` → key by dst →
shared SEQ → encrypt → paced write to active link.
In: link → network decrypt (NID, IVI) → replay check → reassemble (ack if
dst=0x0001) → upper-transport decrypt (AppKey, or dev key by src) → pending
response on (src, opcode), else route to the node owning src. Publications keep
targeting 0x0001.

## 5. Provisioning changes (`config_flow_sig.py` `run_provision`)

1. Ensure a network entry exists (auto-create a default one on first
   provision).
2. Use the network's keys (not `os.urandom`) and its allocator.
3. PB-GATT still needs one direct connection to the new device; optionally
   suspend the network link during it so only one slot is used.
4. Configure the node **through the shared link**: AppKey Add, Composition,
   binds, Pub Set → 0x0001, **plus Relay Set, GATT Proxy Set, Network Transmit
   Set**. Fall back to a direct connection if relaying fails.
5. After PB-GATT succeeds we own the node, so a config failure is recoverable:
   create the entry as `config_state=pending` with a repair flow that re-runs
   configuration — no factory reset.
6. Node subentry removal: Config Node Reset through the network, quarantine
   the address range until confirmed.
7. *(Optional, Phase 7)* PB-ADV via a proxy node needs the Remote Provisioning
   Server model (0x0004). The bulbs' element 0 models seen tonight are
   `0x0000, 0x0002, 0x0003, 0x1000, 0x1001, 0x1300–0x130B` — **no 0x0004**, so
   every new device will still need one direct PB-GATT connection.

## 6. Migrating existing entries

Both bulbs are 0x00B0 on separate networks. Options:

| Option | Verdict |
|---|---|
| NetKey Add (shared key at another index) | No — index 0 is taken, and two nodes at 0x00B0 on one subnet can't be told apart. |
| Key Refresh (keeps address + dev key) | Could move **one** bulb with no state loss; unverified on Tuya firmware. |
| Change unicast in place | Needs Mesh 1.1 Node Address Refresh (Remote Provisioning) — not available. |
| **Config Node Reset (sent with old keys) → reprovision** | **Recommended.** No physical reset/power cycling; one code path for all nodes. Loses Tuya-app scenes; new dev key + address. HA identity is MAC-based, so entities survive. |

Flow: repair issue `legacy_sig_network` per legacy entry → confirm → ensure
network entry → unload legacy entry (frees its slot) → connect directly with
legacy keys → Config Node Reset → wait for 0x1827 (unprovisioned) advert →
provision into network → configure → add node subentry → re-point device and
entities to it → remove legacy entry → reload network. One node at a time,
~1 min each; entity IDs, names, areas and automations are kept.

Fallback if a bulb ignores Node Reset: physical factory reset (Tuya bulbs:
power-cycle off/on ~3× until fast blinking), then the same flow continues from
"wait for 0x1827 advert".

## 7. SEQ and IV index

- One SEQ counter per network (src 0x0001), reservation blocks in the network
  store. New network starts at 0 (new keys ⇒ fresh replay lists on nodes).
- Backup restore can rewind SEQ ⇒ nodes silently drop everything. Detect
  (link up, all acked messages to all nodes time out) → jump SEQ by 0x10000 +
  repair issue.
- Accept IV index from authenticated Secure Network Beacons per spec rules;
  use IVI for receive nonces; persist. Initiating IV Update (SEQ ≈ 0xF00000) is
  years away at HA traffic levels — Phase 6.

## 8. Phases (each shippable)

- **Phase 0 — hardware spikes (gate everything):** (a) dump composition +
  features from both bulbs; *(read-only relay/proxy/friend/beacon/TTL/transmit
  probe logged once per HA run — `probe_node_features`)*; (b) Node Reset + reprovision one bulb
  at a new address; (c) two nodes on one network — connect to A, control and
  read B, confirm relayed status arrives; is relay on by default?; (d) proxy
  filter behaviour on Tuya firmware; (e) does a non-proxy bulb still need
  timestamp answers?; (f) optional Key Refresh support.
- **Phase 1:** lib generalisation (§2 Phase 1). No user-visible change.
- **Phase 2:** `MeshNetwork`, `GattProxyLink`, node views, selector, allocator,
  provisioner allocator callback.
- **Phase 3:** network entry + node subentries, discovery → subentry bridge,
  `MeshNetworkManager`, coordinator external mode, network entities,
  diagnostics, repairs.
- **Phase 4:** provisioning into the shared network (§5).
- **Phase 5:** legacy migration repair flow (§6).
- **Phase 6:** hardening — IV Update, SEQ-rewind detection, optional hot-standby
  second link, group addresses.
- **Phase 7 (optional):** Remote Provisioning — not possible on current bulbs.

## 9. Testing

- Lib unit tests: allocator, selector, src routing across element ranges,
  simultaneous replies matched on (src, opcode), dev-key resolution,
  ReplayCache, IVI/IV rules, Proxy Config + beacon codecs against Mesh Profile
  spec sample data, new Config opcodes, SAR retransmit, write pacing,
  provisioner allocator callback, shared SEQ under concurrency.
- `tests/unit/fake_mesh.py`: `FakeProxyLink` + `FakeNode`s using the lib's own
  crypto, injecting relayed duplicates and link drops → end-to-end: two lights
  over one link, failover, status to the right coordinator, legacy + networked
  coexistence.
- HA tests: network entry + subentry lifecycle, discovery → subentry bridge,
  registry re-pointing on migration, coordinator external mode, migration and pending-config repair
  flows, diagnostics redaction.
- Hardware (`docs/MANUAL_TESTING.md`): Phase 0 spikes; failover by power-cutting
  the active proxy bulb (Shelly) and timing recovery; proxy slot use stays at 1;
  24 h soak across HA restarts; migrating the two real bulbs.

## 10. Risks

- Tuya firmware quirks (relay off by default, Node Reset ignored, no Key
  Refresh, odd proxy filtering) — Phase 0 exists to find these.
- Single proxy link is a single point of failure: failover ≈ 5–15 s via ESPHome;
  hot standby costs a slot.
- Throughput on one ESPHome link with many bulbs — pacing, later group
  addressing.
- SEQ rewind after backup restore.
- One leaked NetKey/AppKey now exposes every node (dev keys stay per node).
- Large refactor of heavily white-box-tested classes.

## 11. Decisions needed

- ~~D1~~ Config subentries. ~~D2~~ Node Reset + reprovision.
- **D3** Allocation start 0x0002 vs 0x0100; keep 0x00B0 reserved?
- **D4** `max_proxy_links` default 1, or 2 (hot standby).
- **D5** Entry removal sends Node Reset? When are quarantined addresses reused?
- **D6** One default network now, or user-defined networks in the UX.
- ~~D7~~ `entry.data`. ~~D8~~ n/a.
- **D9** Group addresses ("all lights") now or Phase 6.
