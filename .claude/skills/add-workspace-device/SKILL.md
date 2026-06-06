---
name: add-workspace-device
description: "Use when adding a USB / serial / TCP device whose hardware handle lives in the workspace process itself (robot, multimeter, in-process pump, syringe). For cameras served by a vision server or any other daemon-owned device, use add-daemon-device instead."
---

# Add a workspace-owned device

## When to use this skill

The user says any of:
- "Add a multimeter / pump / scale / new robot to the platform"
- "How do I plug in a USB serial device the workspace should drive?"
- "Wire up a device that the workspace process itself talks to"

If the device is reached through a **separate daemon process** (e.g. a vision server on the camera's Pi, a print server on the printer's Pi), stop and use [`add-daemon-device`](../add-daemon-device/SKILL.md) instead.

## Architecture in one paragraph

The workspace process owns the hardware handle (USB / TCP / serial). A
**Station** class wraps the raw driver and implements the Device protocol
(`id`, `state`, `msg`, `on_state_change`, `recover`, `release`). A
**Component** class holds the station and exposes the workspace-side API
(`device_ids`, `device_claim`, atomic ops, operator actions). Both gate
on the **same** explicit identifier (`port`, `ip`, …); empty = no claim,
no bus row, no `attach_device`.

## Quick rules

1. **Three-file shape**: `raw_driver.py` (vendor SDK / pyserial / sockets) → `station.py` (Device-protocol wrapper) → `component.py` (workspace integration). See multi_meter and core for canonical examples.
2. **One identifier field** (`port` / `ip` / `host`+`serial_number`). Gate **both** `attach_device()` and `device_ids` on it. Empty → no claim. Non-empty → row appears (sim or real per the simulation flag).
3. **Sim is orthogonal to connection state.** Always attempt the initial connect; the bus dot reflects hardware truth, not the operator's sim intent. See device-guide.md §16.
4. **Slash-free `<natural-id>`** — use `os.path.basename(self.port)` when deriving the id from a `/dev/serial/by-id/...` path. Slashes break MQTT single-level wildcards. See device-guide.md §9.
5. **Idempotent SCPI / driver setters** — track last-known state, skip the round-trip when nothing changed. The BK 879B beep-on-every-change taught us this.
6. **Component method `simulation(on)`** — parity rule (project-guide.md §16): every workspace-owned device exposes a runtime sim toggle. Flag flip + `station.set_simulation()` + `attachment.set_sim()`.

## Canonical doc references

| Section | What you'll find |
|---|---|
| `docs/device-guide.md` §2 | The Device protocol contract (six members) |
| `docs/device-guide.md` §3 | Minimal device skeleton |
| `docs/device-guide.md` §4 | `attach_device()` wiring, AutoRecover, publisher-conflict check |
| `docs/device-guide.md` §9 | ID convention + `<natural-id>` rules + slash-free rule + USB-serial discovery |
| `docs/device-guide.md` §10 (shape A) | Workspace-owned skeleton — copy this |
| `docs/device-guide.md` §16 | Sim model (orthogonal to connection) + runtime toggle parity rule |
| `docs/component-guide.md` §7 | Atomic ops live on the component, not the recipe |
| `docs/component-guide.md` §8 | Operator actions (recommended) |

## Canonical reference implementations

- **Multimeter** (a fresh full-stack example): `workspace/workspace/components/multi_meter/` — `bk879b_driver.py` (raw SCPI), `bk879b_station.py` (Device protocol + sim-agnostic API), `multi_meter_bk879b.py` (component)
- **Robot** (the original): `workspace/workspace/components/core/robot_station.py` and `core.py`

## Common pitfalls

- **Unconditional `attach_device`** — every workspace-owned device must gate on the explicit identifier. Otherwise empty `port=""` still shows a panel row. See device-guide.md §14 pitfalls.
- **Auto-fallback to sim on connect failure** — never. Authored `simulation` is the operator's intent. See device-guide.md §14.
- **Driver caches the meter / device state and `_set_state` no-ops on identical msg** — `recover()` then needs an explicit `state="recovering"` → `state="down"` transition to fire a listener, otherwise the UI sits on the 35 s frontend fallback timeout. See `BK879BStation.recover()` for the pattern.
- **Stale `serial.Serial.is_open == True`** after USB unplug — `recover()` must rebuild the driver fresh (`self._driver = None; self._driver = Driver(port=self.port)`) so the new symlink resolution happens.

## After this

- If you also need a **recipe** for the new device: see [`write-recipe`](../write-recipe/SKILL.md).
- If you're authoring a **scene yaml** entry for it: see [`write-scene-yaml`](../write-scene-yaml/SKILL.md).
- If you want to **simulate** it: see [`enable-sim-mode`](../enable-sim-mode/SKILL.md).
- To **debug** what's actually on the bus: see [`debug-device-bus`](../debug-device-bus/SKILL.md).
