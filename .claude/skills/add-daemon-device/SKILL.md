---
name: add-daemon-device
description: "Use when adding a device whose hardware handle is owned by a separate daemon process — typically because the device is plugged into a different Pi than the workspace. The vision-server / camera pair is the canonical example. For devices the workspace process drives directly, use add-workspace-device."
---

# Add a daemon-owned device

## When to use this skill

The user says any of:
- "Add a camera (or another vision server)"
- "The printer / pipette daemon talks to its own Pi — wire it to a project"
- "How does the workspace claim a device a different process owns?"

If the workspace process holds the USB / serial / TCP handle itself, use [`add-workspace-device`](../add-workspace-device/SKILL.md) instead.

## Architecture in one paragraph

The **daemon** (e.g. a vision server running on the Pi where the USB
camera is plugged in) owns the hardware handle and is the bus
publisher — it calls `attach_device()` and reports state on the MQTT
bus. The **workspace component** is a thin client that declares the
dependency via `device_ids` and optionally surfaces sim intent via
`device_claim()`. The workspace component **does NOT** call
`attach_device()` — that would be a second publisher for the same id
and would be rejected as `DevicePublisherConflict`.

## Quick rules

1. **Workspace component declares; daemon publishes.** The workspace gates `device_ids` on the connection identifier. The daemon's own `attach_device()` lives in its codebase, gated on the same identifier from the daemon's side.
2. **No `attach_device()` on the workspace side** — would conflict with the daemon. The publisher-conflict check enforces this.
3. **A helper station** (e.g. `VisionStation` for the camera) holds the connection details and exposes a clean RPC surface to recipes. Same code pattern in sim and real — recipes never branch on sim.
4. **Two sim signals, both visible** — `info.sim` (set by the daemon) AND `device_claim()` (set by the workspace component). Either alone lights the SIM pill and skips auto-pause. See device-guide.md §16.
5. **No `critical:` field on this component's DEFAULTS.** The daemon owns critical-ness when it calls `attach_device(critical=…)`. Workspace can't override it.

## Canonical doc references

| Section | What you'll find |
|---|---|
| `docs/device-guide.md` §10 (shape B) | Daemon-owned skeleton — copy this |
| `docs/device-guide.md` §11 | Multi-device patterns |
| `docs/device-guide.md` §15 | Camera case study (full daemon-owned walkthrough) |
| `docs/device-guide.md` §16 | Sim model (two signals — bus + workspace claim) |
| `docs/component-guide.md` §3 | Component skeleton (same shape for workspace + daemon-owned) |
| `docs/component-guide.md` §8 | Operator actions (optional, recommended) |

## Canonical reference implementation

- **Camera** (`workspace/workspace/components/inspection/`):
  - `inspection.py` — the component, gates `device_ids` on serial number, implements `device_claim`
  - `vision_station.py` — connection helper, RPC façade
  - Daemon publisher lives in the separate vision server repo

## Common pitfalls

- **Calling `attach_device()` from the workspace** for a daemon-owned device — rejected by the conflict check. The workspace is a client of the bus topic, not its publisher. See device-guide.md §14.
- **Putting a sim stub in the workspace** that publishes for a device with a separate daemon publisher — two writers stomp the bus topic. Use `device_claim()` to express sim intent, let the daemon own the publish.
- **Adding `critical:` to the component's DEFAULTS** — the workspace doesn't publish `info.critical`; the daemon does. Override `critical` on the daemon side.

## After this

- For the **daemon-side service** itself, see device-guide.md §8 (where the adapter lives) — it lives in its own repo.
- If you also need a **recipe** for the new device: see [`write-recipe`](../write-recipe/SKILL.md).
- To **simulate** it: see [`enable-sim-mode`](../enable-sim-mode/SKILL.md).
- To **debug** what's on the bus: see [`debug-device-bus`](../debug-device-bus/SKILL.md).
