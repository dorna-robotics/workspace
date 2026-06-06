---
name: debug-device-bus
description: "Use when troubleshooting the device bus — devices not appearing, publisher conflicts, state mismatches between UI and reality, recover not working, MQTT messages not flowing. Covers mosquitto inspection, topic patterns, conflict detection, and common state-mismatch causes."
---

# Debug the device bus

## When to use this skill

The user says any of:
- "Device not appearing in the panel"
- "It says offline but the device is plugged in"
- "DevicePublisherConflict — what does that mean?"
- "The Recover button does nothing"
- "Bus state doesn't match what I see on the hardware"
- "Why does the operator panel show stale state?"

## Bus topology in one paragraph

The device bus is **MQTT-only**. Topics under `device/<id>/...`:
- `device/<id>/info` (retained) — publisher metadata: kind, sim, critical, publisher_id
- `device/<id>/state` (retained) — current state + msg + timestamp
- `device/<id>/cmd/<action>` — commands to the device (recover, release)
- `device/<id>/cmd/<action>/reply` — replies

Every device has **exactly one publisher** (enforced by `DevicePublisherConflict`). The publisher process owns the hardware handle and is the sole writer to that id's retained topics.

## Quick rules

1. **First, watch the bus directly** — don't trust the UI when something's wrong. `sudo mosquitto_sub -t 'device/+/state' -v` shows real-time state for every device. If it's silent, the publisher isn't publishing.
2. **`DevicePublisherConflict` means two processes claim the same id.** Co-locate the adapter with the hardware (device-guide.md §8). If you're seeing this on workspace startup, a previous instance didn't shut down cleanly OR a daemon and workspace both call `attach_device()` for the same id.
3. **State stays at the last retained value** until a new publish. If you restart a publisher, its `info` and `state` resume from the broker's retained copy until the publisher writes fresh values.
4. **Last Will fires when a publisher disconnects ungracefully.** The broker auto-publishes `state.online=false` on dropped TCP. The dot goes red after a few seconds even without the publisher explicitly publishing.
5. **Slashes in device-id break topic depth.** Single-level wildcards (`+`) don't match across slashes. Ids must use `os.path.basename(...)` for path-derived natural-ids. device-guide.md §9.

## Canonical doc references

| Section | What you'll find |
|---|---|
| `docs/device-guide.md` §4 | `attach_device()` — what it publishes + conflict detection |
| `docs/device-guide.md` §5 | AutoRecover — when it fires + how to inspect retry attempts |
| `docs/device-guide.md` §8 | Where the adapter must live — and why the conflict guard exists |
| `docs/device-guide.md` §9 | ID convention + slash-free rule + `/dev/serial/by-id/*` discovery |
| `docs/device-guide.md` §13 | Watching the bus — `mosquitto_sub` patterns, debugging recipes |
| `docs/device-guide.md` §16 | Sim model — debugging "publisher sim says X but project claim says Y" |

## Diagnostic commands (cheat sheet)

```bash
# All device state events (live)
sudo mosquitto_sub -h localhost -t 'device/+/state' -v

# All device discovery + state (filtered to one device)
sudo mosquitto_sub -h localhost -t 'device/dorna:127.0.0.1/#' -v

# Recover command + reply (round-trip)
sudo mosquitto_sub -h localhost -t 'device/multimeter:ttyUSB0/cmd/+/reply' -v
sudo mosquitto_pub -h localhost -t 'device/multimeter:ttyUSB0/cmd/recover' -m '{"req_id":"manual-1"}'

# Workspace logs (filter to device bus)
sudo journalctl -u dorna-workspace -f | grep -i 'device\|mqtt\|attach\|recover'

# Find USB-serial paths
ls -d /dev/serial/by-id/*
```

## Common pitfalls (with diagnosis)

| Symptom | Probable cause | Fix |
|---|---|---|
| Device missing from panel | Component doesn't return id from `device_ids` (empty `port=""`) | Fill `port:` in scene yaml |
| Device row in panel, "offline" / "not on bus" | Component `device_ids` returns the id but no publisher exists | Check `attach_device()` was actually called; look at workspace boot logs for the right gate |
| Device shows red dot, msg says "publisher conflict" | Two processes claim same id; check daemon process + workspace component both publishing | Co-locate the adapter — only one publisher per id |
| Recover button does nothing visible | Publisher's `recover()` returns success but no state change | `_set_state()` no-ops on identical state+msg; ensure `recovering → result` transition fires for the bus to see an edge |
| Sim toggles UI flag but bus row stays "real" | Component method `simulation(on)` doesn't call `attachment.set_sim(...)` | Always propagate via the parity method — device-guide.md §16 |
| Topic has slashes → orchestrator can't subscribe | Device id has slashes (`/dev/...`) | Use `os.path.basename(self.port)` to derive id — device-guide.md §9 |
| State stuck stale after workspace restart | Retained message from previous run | Either wait for fresh publish, or clear retained: `mosquitto_pub -t 'device/.../state' -n -r` (publish empty retained) |

## After this

- For **understanding** the sim signal split when debugging "publisher sim says X but project claim says Y": [`enable-sim-mode`](../enable-sim-mode/SKILL.md).
- For **fixing a broken publisher**, see whichever applies: [`add-workspace-device`](../add-workspace-device/SKILL.md) or [`add-daemon-device`](../add-daemon-device/SKILL.md).
- For **operator-side recovery** when paused: [`operator-recovery`](../operator-recovery/SKILL.md).
