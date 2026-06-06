---
name: enable-sim-mode
description: "Use when configuring simulation mode for a device or component — for dev testing, pre-flight checks, or runtime toggle. Covers the two-signal model (info.sim + device_claim), the orthogonal-connection rule, and the simulation(on) parity method."
---

# Enable simulation mode

## When to use this skill

The user says any of:
- "Run this project in sim"
- "Set up the robot / multimeter / camera in simulation"
- "Toggle sim mode mid-run"
- "Why is the dot red in sim mode?"
- "Why is the SIM pill showing but the publisher_sim says false?"

## Mental model — two signals, deliberately separate

Sim mode is **NOT** a single flag. It's two signals from two actors:

| Signal | Set by | Visible as |
|---|---|---|
| `info.sim` (bus) | The **publisher** of the device topic (workspace process for the robot / meter; vision server for the camera) | "publisher sim" row in the device modal |
| `device_claim(id)` (workspace) | The **workspace component** (`device_claim()` method) | "project claim" row in the device modal |

The **SIM pill** lights when **either** is true (OR). Auto-pause is **skipped** when either is true (OR — strictest claim aggregation enforced server-side).

**The dot color (green / amber / red)** is hardware truth — `info.state` from the publisher. Sim does NOT change it. A sim-claimed device with a real publisher reporting down shows a red dot with the SIM pill — both layers visible, neither hides the other. device-guide.md §16.

## Quick rules

1. **Sim is orthogonal to connection state.** Always attempt the initial connect; the dot reflects hardware truth. Don't gate `recover()` or `_set_state()` on the sim flag. device-guide.md §16.
2. **Component constructor picks the API once.** `self.robot_api = SimulationAPI(...) if simulation else self.dorna`. Same idea for any other device-API split. The recipes / actions never branch on the sim flag again. device-guide.md §10.5.
3. **Every workspace-owned device exposes a `simulation(on)` method.** The parity rule (device-guide.md §16): flag flip + `station.set_simulation(on)` + `attachment.set_sim(on)`. Core and MultiMeter follow this verbatim. Recipes never call `simulation()` — only the operator does (manually or via the UI toggle).
4. **For daemon-owned devices, only `device_claim()` matters.** The workspace can't override the daemon's `info.sim`. The daemon publishes the bus truth; the workspace's `device_claim()` overlays project-level sim intent.
5. **Empty identifier (`port: ""`, `ip: ""`, etc.) means no claim** — the component declares nothing, `device_ids` returns `[]`, no bus row, no auto-pause. Independent of the sim flag.

## Canonical doc references

| Section | What you'll find |
|---|---|
| `docs/device-guide.md` §10.5 | "Where the sim/real branch lives" — component constructor, never recipe |
| `docs/device-guide.md` §16 | Sim model (two signals, orthogonal-connection rule, parity method) |
| `docs/device-guide.md` §1 rule 5 | Bus presence gated by explicit identifier |
| `docs/component-guide.md` §3 | Component skeleton showing `simulation: bool` config flow |
| `docs/project-guide.md` §9 | "What triggers Pause" — device-down auto-pause + sim claim opt-out |

## Canonical reference implementations

- **Robot**: `workspace/workspace/components/core/core.py` and `robot_station.py` — `Core.simulation(on)` swaps `robot_api`, calls `station.set_simulation`, `attachment.set_sim`
- **Multimeter**: `workspace/workspace/components/multi_meter/multi_meter_bk879b.py` — `MultiMeterBk879b.simulation(on)` mirrors Core's pattern
- **Camera**: `workspace/workspace/components/inspection/inspection.py` — daemon-owned; `device_claim()` returns `"sim"` if the project authored sim regardless of the daemon's `info.sim`

## Common pitfalls

- **Auto-fall-back to sim on connect failure** — never. Operator authored `simulation: false`; respect it. Connect failure → red dot + auto-pause (or skip if `simulation: true`). device-guide.md §14.
- **`recover()` no-ops in sim** — wrong. Always attempt the real reconnect; in sim, AutoRecover is suspended via `attachment.set_sim` so the retry loop doesn't spam, but `recover()` itself stays real.
- **`info.sim` on a daemon-owned device used as the only sim signal** — daemon doesn't know about the workspace's sim intent. Use `device_claim()` for project-side intent; rely on `info.sim` only when the workspace IS the publisher.
- **Component DEFAULTS missing `simulation: true`** — leaves the field undefined; ambiguous in scene yaml. Always set `simulation: true` as the safe default in the component's DEFAULTS dict.
- **Switching sim → real mid-run without `simulation(on)`** — flag changes don't propagate to the bus or the AutoRecover loop. Always call the component method.

## After this

- To **debug** sim signal cross-product on the bus: [`debug-device-bus`](../debug-device-bus/SKILL.md).
- To **add a new device** with sim support: [`add-workspace-device`](../add-workspace-device/SKILL.md) or [`add-daemon-device`](../add-daemon-device/SKILL.md).
- To **author scene yaml** with explicit sim flags: [`write-scene-yaml`](../write-scene-yaml/SKILL.md).
