---
name: operator-recovery
description: "Use when the user is troubleshooting a paused workflow — recovering a downed device, clearing a robot alarm, mutating state to skip a stuck action, or understanding why the system paused. Covers the four pause triggers, recovery affordances, and explicit-mutation APIs."
---

# Operator recovery

## When to use this skill

The user says any of:
- "Why did the run pause?"
- "How do I recover the [device / robot / camera]?"
- "Can the operator skip this action?"
- "Add an operator action button to clear / reset / re-home X"

## Mental model — four pause triggers, one recovery surface

The runtime pauses for **four distinct reasons** (project-guide.md §9):

| # | Trigger | Resolution |
|---|---|---|
| 1 | **Operator click "Pause"** | Click "Resume" |
| 2 | **Critical device down on the bus** | Recover the device → state clears to ok → click Resume |
| 3 | **Robot motion returns alarm code** | Clear alarm on the robot → click Resume |
| 4 | **Code calls `rt.pause()`** (custom checks, action policy) | Project-specific — usually Resume after handling the situation |

All four set the same `paused` flag. The next pause-aware call (`rt.sleep`, `rt.delay`, `rt.<robot>`, `rt.checkpoint`) blocks until Resume.

## Recovery surfaces (what the operator has access to)

1. **Recover button** on each device row + in the modal — calls `device.recover()` via MQTT. AutoRecover retries in the background. The button shows "Recovering…" (amber pill) while in-flight.
2. **Operator action buttons** in the **Operator Controls** panel — declared by each component's `operator_actions()` method. Used for non-bus operations: gripper enable/disable, tool attach/detach, motor enable, custom resets. Gated by workflow state (disabled when RUNNING). component-guide.md §8.
3. **Runtime scene + state mutation** — for advanced recovery, the runtime exposes:
   - `workspace.add_component(name, cfg)` / `workspace.remove_component(name)` (scene mutation) — component-guide.md §9.
   - `workspace.add_fact(*tuple)` / `workspace.remove_fact(*tuple)` (state mutation) — bt-framework-guide.md §9.
   - **Explicit-mutation rule**: scene topology and PDDL state are separate concerns; the framework never auto-bridges them. The caller is responsible for both sides.

## Quick rules

1. **Auto-pause is automatic; auto-resume is not.** The framework pauses on critical-down but won't resume — operator decides. project-guide.md §9.
2. **Recover doesn't mean resume.** Recovering the device clears the bus state to ok but the runtime stays paused. The operator must click Resume after fixing.
3. **Robot alarms need physical clearance.** AutoRecover retries connections but won't clear alarms — `clear the alarm on the robot, then click Resume`. RobotStation distinguishes connection-lost from alarm to avoid spinning recovery on alarms.
4. **Operator actions are disabled mid-run.** `operator_actions()` buttons are gated by workflow state. They're for **between** runs or **during pause**, not for parallel-with-running operation. component-guide.md §8.
5. **State mutation is the escape hatch.** If an action is stuck and the operator needs to "fake done", use `workspace.add_fact(...)` to set the relevant predicate; the planner re-evaluates on the next slice. bt-framework-guide.md §9.

## Canonical doc references

| Section | What you'll find |
|---|---|
| `docs/project-guide.md` §8 | Pause gate — comprehensive pause-aware reference |
| `docs/project-guide.md` §9 | What triggers pause + atomicity + resume semantics |
| `docs/device-guide.md` §5 | AutoRecover — what it does, what triggers retry |
| `docs/component-guide.md` §8 | Operator actions contract |
| `docs/component-guide.md` §9 | Runtime scene mutation (add/remove components) |
| `docs/bt-framework-guide.md` §9 | Runtime fact mutation (add_fact / remove_fact / facts) |

## Canonical reference implementations

- **AutoRecover wiring**: `workspace/workspace/components/core/core.py` (look for `attach_device(...recover_factory=...)`) — robot's connection-lost edge triggers recovery
- **Operator actions on a device**: `workspace/workspace/components/multi_meter/multi_meter_bk879b.py` — `operator_actions()` returns Read C/L/R buttons + Reconnect + Release
- **Operator actions on a tool**: `workspace/workspace/components/gripper/gripper.py` — enable / disable

## Common pitfalls

- **Auto-resuming after device recovery** — never. Operator decides. The "Recovery clears state but not pause" rule is deliberate.
- **Operator action that doesn't gate on workflow state** — recipes and components must accept the "called while paused" entry condition; never assume runtime is RUNNING. The framework already gates buttons but defensive code matters.
- **`workspace.add_fact()` without matching scene mutation** — if a fact references an object that doesn't exist in scene, the planner stalls. Pair every fact mutation with a scene mutation when adding/removing entities. component-guide.md §9.
- **Catching exceptions in `execute()` to "skip"** — better to raise → BT marks failure → replanner finds alternative path. Silent catches lose audit trail. bt-framework-guide.md §3.

## Critical-device-down workflow (step by step)

1. Bus detects state=down for a critical, non-sim device.
2. `MQTTOrchestrator` calls `runtime.pause()`.
3. UI: device row goes red, runtime pill goes PAUSED.
4. Operator either:
   - Clicks **Recover** on the device row → triggers `device.recover()` → AutoRecover retries; on success, state goes ok.
   - Performs operator action (e.g. reseat USB) → state goes ok.
5. Operator clicks **Resume** → `runtime.resume()` → next pause-aware call returns → workflow continues.

## After this

- For **why a specific action paused**, examine the device modal + recent step entries. The `msg` field on the device shows the publisher's failure mode.
- For **adding new operator-action buttons** to a component, see component-guide.md §8.
- For **mutating workflow state** to skip / re-do an action, see bt-framework-guide.md §9.
- To **simulate** a device for testing recovery flows: [`enable-sim-mode`](../enable-sim-mode/SKILL.md).
