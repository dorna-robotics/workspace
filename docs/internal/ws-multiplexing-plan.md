# WebSocket Multiplexing — Migration Plan

Status: **planned, not yet executed.** Captured here so future work
can pick it up cleanly when there's a focused window for it. The
current 5-WS design works fine on a Pi5 with one project; this
proposal is architectural cleanup, not a perf emergency.

## Today

The admin project page opens **five** independent WebSocket
connections:

| # | Endpoint | Owner | What it carries |
|---|---|---|---|
| 1 | `ws://<workspace>/ws/steps` | Workspace process | Live workflow step events (`{type:"step_state",...}`) |
| 2 | `ws://<workspace>/ws/status` | Workspace process | Runtime state ticks (RUNNING / PAUSED / IDLE / progress) |
| 3 | `ws://<workspace>/ws/devices` | Workspace process | `device_state` events + initial snapshot |
| 4 | `ws://<workspace>/ws/operator_actions` | Workspace process | Op-actions schema changes when components are added/removed |
| 5 | `ws://<orchestrator>/ws/logs/<name>` | Orchestrator | Live log streaming (high-volume) |

Each has its own backoff timer, retry counter, reconnect path, and
heartbeat (or absence thereof). The schedule sub-module opens its own
WS on top. All four workspace-side WSes connect to the **same**
process, so each is a duplicate TCP handshake + WebSocket upgrade
that could share one channel.

## Proposed shape

Consolidate the **workspace-side 4** into a single multiplexed
channel. Keep logs separate (different owner, high volume, distinct
concern).

```
ws://<workspace>/ws        ← single multiplexed channel
ws://<orchestrator>/ws/logs/<name>   ← logs stays separate
```

Both ends speak this envelope:

```json
{ "type": "step_state" | "device_state" | "runtime_status" | "op_actions_snapshot" | "scene_changed",
  "payload": { ... } }
```

Optionally accept client → server subscription messages so the
server only ships event types the client cares about:

```json
{ "subscribe": ["step_state","device_state","runtime_status"] }
{ "unsubscribe": ["op_actions_snapshot"] }
```

Default (no subscribe sent): server pushes everything. Subscriptions
let a future thin client (e.g. a dashboard that only renders steps)
opt out of bandwidth it doesn't render.

## Wins

- **Half the TCP / WS handshakes** at page load. Faster first paint
  on cold connections (Pi5 + slow link).
- **One shared backoff path** — the operator either has connection
  to the workspace or doesn't; tracking it 4 times is redundant.
- **One heartbeat** — currently none of the four has explicit
  keep-alive; adding it once is cheaper than adding it 4×.
- **One place to add new event types** — currently each new event
  category needs a new endpoint + handler + client connect path.
- **One place for filtering** — server can drop project-scoped
  events without spamming clients that don't care.

## What stays

- **Per-channel retain semantics** — devices still need their full
  snapshot on connect (today via the explicit "snapshot" message).
  The multiplexed server replays the same snapshots; nothing observable
  changes for the client beyond the envelope.
- **Logs WS stays separate** — different owner (orchestrator captures
  the workspace's stdout), high message volume, conceptually a
  different concern. Keeping it independent lets us tune its retry +
  backpressure without touching the rest.
- **Schedule WS stays separate** for now — it's project-managed and
  cleanly factored in `schedule.js`. Could fold in later.

## Backward compatibility

The four existing endpoints (`/ws/steps` / `/ws/status` /
`/ws/devices` / `/ws/operator_actions`) stay live during the
migration. The new `/ws` endpoint runs alongside. Clients migrate one
at a time:

1. Add the new server-side `/ws` handler that fans events out to
   subscribers with the unified envelope.
2. Add a client-side dispatcher: `_dispatchWsMessage(envelope)` that
   routes to existing handlers based on `type`.
3. Migrate handlers one at a time. The old endpoints stay for a
   release or two; remove only after we're confident.
4. Remove the four endpoints + their per-endpoint client code in a
   single cleanup commit.

This staged approach means each step is reversible — a regression in
the multiplexed path can fall back to the old endpoint while the bug
gets fixed.

## Implementation sketch

**Server side** (`workspace/runtime_server.py`):

- New Tornado WS handler `WsAllHandler` at `/ws`.
- Maintains a per-connection subscription set; defaults to all.
- Pushes the envelope `{type, payload}` from one event dispatcher
  fed by the existing event publishers (no new event sources).
- Holds a per-client send queue (already needed to avoid blocking
  the publishers); cap with a small drop-oldest ring for safety.

**Client side** (`workspace/gui/orchestrator/web/admin/workspace.js`):

- New `_connectWs()` function — the only WS connect call besides
  the logs one.
- A `_dispatchWsMessage(env)` function that routes by `env.type` to
  the existing `_handleStepState`, `_handleDeviceState`,
  `_handleRuntimeStatus`, `_handleOpActionsSnapshot`.
- Remove `_tryStepWS`, `_tryDevicesWS`, `_tryRuntimeStatusWS`,
  `_tryOpActionsWS` (and their disconnect counterparts) in the
  cleanup commit.

## Risk register

| Risk | Mitigation |
|---|---|
| Backpressure on the unified channel (a busy steps stream stalls device updates) | Per-event-type queue with bounded depth; drop-oldest |
| Order interleaving (devices update before the step that triggered it) | Each event type already ordered independently; client dispatchers don't cross-reference order |
| Heartbeat false positives during heavy GC | Standard ping/pong with multi-second tolerance |
| External tooling using the old `/ws/devices` etc. | Keep old endpoints during transition (above) |

## Estimated effort

- Server-side multiplexer: ~1 day
- Client migration + testing: ~1 day
- Cleanup commit (remove old endpoints): ~½ day
- **Total: 2-3 focused days**, single dedicated PR

## Triggers for actually doing this

- More than one project per orchestrator with significant device
  events → the per-WS overhead starts mattering
- Adding a 6th workspace-side WS → would push us over a "this many
  WS handshakes is silly" threshold
- Operator complaints about slow first-paint on the Pi5 + LAN
- A planned feature that needs server-side event filtering (e.g. a
  thin dashboard that only renders one project's steps)

Until one of those triggers fires, the current 5-WS design is fine.
