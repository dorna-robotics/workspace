# Project Guide

How to create and run a workspace project.

---

## 1. Project structure

```
projects/my_project/
├── main.py              # Entry point — ties everything together
├── launch.yaml          # Scene paths + runtime parameters (kwargs)
├── protocol.yaml        # States, dependencies, checks, goals
├── states.py            # State handlers (what the robot does)
├── checks.py            # Verification checks (pre/post)
├── recipes.j2           # Component aliases → recipe classes (or recipes.yaml)
└── scene/
    ├── base.j2          # Hardware layout (Jinja2)
    └── layout.j2        # Spatial arrangement
```

To create a new project, copy `projects/pace_or/` as a template and edit each file. The sections below explain them in detail.

### `main.py` — entry point

This is how all the pieces connect:

```python
import os, argparse, yaml
from pathlib import Path
from workspace.workspace import Workspace
from workspace.ortools.workflow import BaseWorkflow
from workspace.runtime_server import RuntimeServer
from states import States
from checks import Checks

_BASE_DIR = Path(__file__).parent

def workflow_fn(*, workspace, core, **kwargs):
    BaseWorkflow(workspace, core, _BASE_DIR, States, Checks, **kwargs).run()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "5010")))
    args = p.parse_args()

    with open(_BASE_DIR / "launch.yaml") as f:
        launch = yaml.safe_load(f)

    ws = Workspace(config_path=launch["scene"], port=args.port)
    RuntimeServer(runtime=ws.rt, workflow_fn=workflow_fn, workspace=ws).run()

if __name__ == "__main__":
    main()
```

- `launch.yaml` defines the scene files and runtime parameters
- `Workspace` loads the scene and creates the runtime
- `RuntimeServer` exposes start/pause/end/kill commands and serves the 3D viewer
- `workflow_fn` is called on **Start** — it receives `**kwargs` from the Parameters modal and passes them to `BaseWorkflow`, which passes them to `States` and `Checks`
- The orchestrator launches this with `sudo python3 main.py --port 5010`

---

## 2. Scene — `scene/`

Defines the physical hardware: robots, racks, tools, peripherals. Built using the **Scene Builder** GUI. The Jinja2 templates (`.j2` files) describe every component and its position. This is the source of truth for component names used in recipes.

---

## 3. Launch config — `launch.yaml`

Two top-level keys:

| Key | Description |
|-----|-------------|
| `scene` | List of scene file paths (relative to project folder). Loaded in order to build the 3D scene and component registry. Typically `base.j2` for hardware, `layout.j2` for consumables. |
| `kwargs` | Parameter definitions shown in the GUI's Parameters modal. Each key becomes a field the user can set before starting. |

```yaml
scene: [scene/base.j2, scene/layout.j2]

kwargs:
  batch_size:
    type: int
    default: 4
    label: Number of tubes
    hint: How many tubes to include in the schedule
    min: 1
    max: 20

  horizon:
    type: int
    default: null
    label: Planning horizon
    hint: Empty = plan all at once
    placeholder: plan all at once
    optional: true
    min: 1
```

### Kwarg field properties

| Property | Required | Description |
|----------|----------|-------------|
| `default` | No | Pre-filled value. `null` = empty |
| `label` | No | Display name in the modal (defaults to the key name) |
| `hint` | No | Help text shown below the input |
| `placeholder` | No | Greyed-out text inside the input when empty |
| `optional` | No | `true` = field can be left empty, sent as `null` |
| `min` / `max` | No | Numeric bounds (for `int` and `float` types) |
| `type` | Yes | Widget type: <br>• `int` — number input (`min`, `max`, `step=1`) <br>• `float` — number input (`min`, `max`, `step=any`) <br>• `str` — text input <br>• `bool` — checkbox <br>• `choice` — dropdown (requires `options: [a, b, c]`) <br>• `textarea` — multi-line text (`rows` default 4, tries JSON parse) <br>• `file` — file upload (`accept: ".csv,.xlsx"`) |

### How kwargs flow

All kwargs defined here are passed to `States.__init__(rcp, rt, **kwargs)` and `Checks.__init__(rcp, rt, **kwargs)`. Use `kwargs.get("my_param", default)` to access them.

Two reserved keys are also used by the scheduler if present:

| Key | Default | Description |
|-----|---------|-------------|
| `batch_size` | `1` | Number of items to process. Each state runs once per item (index 0 to n-1). |
| `horizon` | `60` | Rolling window size for replanning. `null` = plan all tasks at once. |

If `batch_size` or `horizon` are in your kwargs, they override the defaults for the scheduler. They are still passed to States and Checks like everything else.

---

## 4. Recipes — `recipes.yaml` or `recipes.j2`

Maps human-readable aliases (like `gripper`, `pipette`) to recipe classes with their configuration. A recipe knows how to pick, place, dose, etc. using a specific component from the scene. You write the alias once here and use it everywhere in your states.

Supports both `.yaml` and `.j2` (Jinja2 template). If `recipes.j2` exists, it's rendered first. Use `.j2` to define shared variables like `speed_factor` — change one value, every recipe gets it.

```yaml
{% set speed_factor = 1 %}
{% set base_distance = 200 %}

gripper:
  class: workspace.components.gripper.Gripper
  kwargs:
    component: gripper_1
    left_approach: true
    speed_factor: {{ speed_factor }}

pipette:
  class: workspace.components.pipetting_site.PipettingSite
  kwargs:
    component: pipette_1
    base_distance: {{ base_distance }}
    speed_factor: {{ speed_factor }}

tube_rack:
  class: workspace.components.rack.Rack
  kwargs:
    component: tube_rack_50ml_1
    base_distance: {{ base_distance }}
    speed_factor: {{ speed_factor }}
```

Each recipe entry has:
- `class` — full Python import path to the recipe class
- `kwargs.component` — required, matches a name from `scene/base.j2`
- Everything else in `kwargs` is recipe-specific and passed to the constructor

The loader creates each recipe as: `cls(workspace, core, component, **kwargs)`

Access in states: `self.rcp["gripper"].pick(i)`

---

## 5. Protocol — `protocol.yaml` or `protocol.j2`

Defines the workflow states with dependencies, tool assignments, and checks. The OR-Tools scheduler reads this to figure out the optimal execution order. You don't write the scheduling logic — you just declare "dosed requires picked" and the solver handles the rest.

Also supports `.j2` format (same as recipes).

```yaml
states:
  picked:
    duration: 8
    requires: []
    tool: gripper
    post_check: tube_picked

  dosed:
    duration: 15
    requires: [picked]
    tool: pipette
    pre_check: tube_in_rack

  placed:
    duration: 6
    requires: [dosed]
    tool: gripper

  shaken:
    duration: 5
    requires: [placed]
    background: true

  shutdown:
    trigger: park

goal: [placed]

tool_swap_duration: 10
```

Each key under `states` is the state name — must match a key in `states.py` `make()`.

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `duration` | No | Estimated seconds (used by scheduler, default: 1) |
| `requires` | No | List of state names that must complete first |
| `tool` | No | Which tool the robot holds. Auto-swaps via tool rack between states. <br>• **not set** — keep current tool, no swap <br>• **`tool: gripper`** — swap to named tool <br>• **`tool: null`** — return current tool, run bare |
| `tool_swap_duration` | No | Per-state override (seconds) of the global `tool_swap_duration`. Represents the gap inserted *before* this state when transitioning from a different tool. Falls back to the top-level value if unset |
| `background` | No | `true` = runs in parallel, completes all items at once (default: `false`) |
| `pre_check` | No | Check name or list — runs **before** the tool swap and state handler. If it fails, the state is skipped entirely (no tool swap happens). Must match a key in `checks.py` `make()` |
| `post_check` | No | Check name or list — runs **after** the state handler completes. Must match a key in `checks.py` `make()` |
| `trigger` | No | `"park"` — state is not scheduled, only runs on Park signal. <br>• A trigger state is **a normal state with a different invocation point** — it goes through the same execution path (`_execute_state` in `runner.py`) as scheduled states, so every state-level field above (`tool`, `pre_check`, `post_check`, etc., plus any future additions) applies naturally <br>• When Park is pressed, the current state finishes, then this trigger runs before the process exits <br>• `tool:` on the trigger is the **authoritative final tool state** — no auto-release runs after <br>• If `trigger: park` is **not** defined, Park simply exits after the current state finishes — tools are **not** auto-released. To release the held tool on Park, define a `trigger: park` state with `tool: null` <br>• Scheduling-only fields (`requires`, `duration`, `tool_swap_duration`, `background`) are ignored on triggers since they're not part of the OR-tools plan <br>• Use it for cleanup (return tools, home robot, safe position) |

### Goal

The `goal` list defines which states mark the protocol as done. Goal names must be states defined in the `states` section of the same YAML file.

- A state is "completed" when its handler returns without raising an exception — no return value is checked
- The protocol finishes when every goal state has run for every item in the batch
- If `batch_size` is not set, it defaults to 1 — each state runs once, like a simple sequence
- If your project processes multiple items (e.g. tubes, vials), pass `batch_size` via `launch.yaml` kwargs — each state runs once per item (index 0 to n-1)

### Tool swap duration

`tool_swap_duration` (top-level, optional) — estimated seconds to swap between tools (default: `0`). When set, the scheduler adds this as a penalty between consecutive tasks that use different tools, so it naturally batches same-tool work together to minimize total time.

Per-state overrides are also supported — add `tool_swap_duration: N` to any individual state to override the global value. The override represents the cost of swapping **into** that state's tool, so when transitioning A→B the gap is B's value, and B→A uses A's value.

---

## 6. States — `states.py`

Implements what the robot actually does for each state defined in `protocol.yaml`.

### Structure

- **`__init__(self, rcp, rt, **kwargs)`** — called once when the workflow starts
  - `rcp` — recipe dict from `recipes.yaml` (e.g. `rcp["gripper"]`)
  - `rt` — runtime object (for `rt.call()`, `rt.step()`, `rt.sleep()`)
  - `**kwargs` — all kwargs from `launch.yaml`, access via `kwargs.get("my_param", default)`
- **State handlers** — one method per state, each must accept `i`
  - `i` is the item index (`0` to `batch_size - 1`), passed by the runner
  - The runner calls your handler once per item — you don't loop yourself
  - You must accept `i` even if you don't use it
  - Background states always receive `i=0` and run once for all items
- **`register(self, runner)`** — framework-reserved hook. Called once by `BaseWorkflow` at workflow startup. Bind each handler to its protocol-state name with `runner.register_state(name, fn)`. Names must match the keys in `protocol.yaml`.

### Example

```python
class States:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt
        # Access any kwarg from launch.yaml — these are just examples:
        # self.dry_run = kwargs.get("dry_run", False)
        # self.speed = kwargs.get("speed", 100)

    def picked(self, i):
        """Pick tube i from the rack."""
        self.rcp["gripper"].pick(i)

    def dosed(self, i):
        """Dose 40ml into tube i."""
        self.rcp["pipette"].dose(volume=40)

    def placed(self, i):
        """Place tube i into output rack."""
        self.rcp["gripper"].place(i)

    def homed(self, i):
        """Home the robot — same action regardless of i."""
        self.rcp["robot"].home()

    def register(self, runner):
        runner.register_state("picked", self.picked)
        runner.register_state("dosed",  self.dosed)
        runner.register_state("placed", self.placed)
        runner.register_state("homed",  self.homed)
```

---

## 7. Checks — `checks.py`

Verification functions that run before/after states. Same signature as States — receives `rcp`, `rt`, and all `**kwargs`.

### Structure

- **`__init__(self, rcp, rt, **kwargs)`** — same as States, has access to recipes, runtime, and all kwargs
- **Check methods** — each accepts `i` (item index) and returns `True` (passed) or `False` (failed)
- **`register(self, runner)`** — framework-reserved hook. Bind each check to its name with `runner.register_check(name, fn)`. Names are referenced in `pre_check` / `post_check` in `protocol.yaml`.

### Example

```python
class Checks:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt

    def tube_in_rack(self, i):
        return True

    def tube_picked(self, i):
        ok = self.rcp["gripper"].has_object()
        if not ok:
            self.rt.step(f"Tube {i} pick failed — fix and resume", level="warning")
            self.rt.pause()   # wait for operator, then skip
        return ok

    def register(self, runner):
        runner.register_check("tube_in_rack", self.tube_in_rack)
        runner.register_check("tube_picked",  self.tube_picked)
```

```yaml
# protocol.yaml — references the check names registered above
states:
  picked:
    pre_check: tube_in_rack
    post_check: tube_picked
```

- On `False`, the task is skipped and the runner moves to the next task
- Use `self.rt.step()` inside the check to show a message to the operator
- Use `self.rt.pause()` if you want to pause and wait for the operator before skipping
- Checks are optional — states without `pre_check`/`post_check` just run directly

---

## 8. Runtime API

### Logging steps

```python
rt.step("Picking tube 3")                          # info — appears in timeline
rt.step("All tubes placed", level="success")        # success — green dot
rt.step("Tube misaligned", level="warning")          # warning — amber banner
rt.step("Robot alarm", level="error")                # error — red banner + beep
rt.step(45, level="progress")                        # progress bar (0-100)
```

| Level | Timeline | Banner | Sound | Progress bar |
|-------|----------|--------|-------|-------------|
| `info` | Blue dot | — | — | — |
| `success` | Green dot | — | — | — |
| `warning` | Amber dot | Amber banner | — | — |
| `error` | Red dot | Red pulsing banner | Beep + notification | — |
| `progress` | — | — | — | Updates bar |

### Robot commands

In States, call recipe methods directly — recipes handle robot commands and checkpoints internally:

```python
self.rcp["gripper"].pick(i)
```

If you need to call a raw robot command outside of a recipe, call it directly on `rt` — it proxies any method to `robot_api` and wraps it with checkpoint and alarm handling automatically:

```python
self.rt.jmove(j0=0, j1=0, j2=0, j3=0, j4=0, j5=0)
```

If the robot returns a negative value (alarm), `rt` will automatically log an error, pause, and wait for the operator to clear and resume.

### Sleep

```python
rt.sleep(5.0)  # Interruptible — responds to pause/kill
```

### Pause gate

```python
rt.checkpoint()  # Blocks if paused, raises KillRequested if killed
```

#### The one rule

> **Observability never blocks. Work always checkpoints.**

Anything you call on `rt.*` that *does work* observes pause. Anything that
just *records* or *reads* state doesn't. This single rule covers every
runtime method without exception.

#### What is and isn't pause-aware

| Category | Methods | Pause-aware? |
|---|---|---|
| **Waiting** | `rt.sleep(s)`, `rt.delay(s)` | ✅ |
| **Robot / tool work via runtime** | `rt.<robot_method>(...)` — `rt.motor(1)`, `rt.jmove(...)`, `rt.lmove(...)`, `rt.cmove(...)`, any tool/IO method exposed by the robot api | ✅ |
| **Explicit checkpoint** | `rt.checkpoint()`, `rt.call(fn)` | ✅ |
| **Observability** | `rt.step(label, level)` — for every level: `info`, `success`, `warning`, `error`, `progress` | ❌ |
| **Runtime state reads** | `rt.status()`, `rt.state`, `rt.step_info` | ❌ |
| **Runtime control** | `rt.pause()`, `rt.resume()`, `rt.kill()`, `rt.start()`, `rt.park()` | ❌ |
| **Direct recipe / component / driver calls** | `rcp["x"].foo()`, `core.dorna.x()`, `self.component.bar()`, `self.driver.cmd()` | ❌ |

#### How robot calls inherit pause-awareness for free

The Runtime's `__getattr__` automatically wraps every `rt.<some_robot_method>(...)`
call through `self.call(...)`, which checkpoints before running the underlying
robot method. So you don't have to mark each robot method as pause-aware — it
inherits the property the moment you reach it via `rt.*`.

The corollary is equally important: **anything you call without going
through `rt.*` bypasses the gate.** Recipe calls (`rcp["x"].read()`), direct
component calls (`self.component.foo()`), raw driver SCPI commands — none of
those check the pause flag. That's deliberate: data and I/O calls don't
impose timing semantics.

#### When to call `rt.checkpoint()` explicitly

You almost never need to. The pause flag is observed naturally on the
next `rt.sleep` / `rt.delay` / `rt.<robot>` call your action or recipe
makes. Explicit `rt.checkpoint()` is only useful when:

- Your action runs a long pure-computation loop (uncommon — workflows
  are I/O-bound, not CPU-bound).
- You want a guaranteed pause point between two non-pause-aware calls
  without any incidental waiting.

#### What this means for action authors

When you write an action, ask: *"When the operator clicks Pause, where
will my code stop?"*

- If the action has `rt.sleep(...)` between sensor reads → pause is
  observed there. Common case.
- If the action only does `rt.step` + recipe reads (no sleep, no robot
  call) → pause is **not** observed inside this action; the next
  action's pause-aware call picks it up.
- If the action runs a tight loop without any pause-aware call →
  add `rt.checkpoint()` once per iteration.

Most actions get pause behavior automatically because they call
`rt.<robot>(...)` for motion or `rt.sleep(...)` for timing. Nothing
extra to wire.

Note: `checkpoint()` does **not** raise `ParkRequested` — Park is observed
between states, not mid-state. See [§9 Pause / Park / Kill](#pause--park--kill--runtime-control-semantics).

---

## 9. Running a project

### From the orchestrator GUI

1. Open `http://<ip>:5000/orchestrator/`
2. Click **+ Add Workspace**
3. Set name, port, path to `main.py`
4. Click the **gear icon** → set parameters → **Set**
5. Click **Launch** → **Start**
6. To park gracefully: **Park** (finishes current action → runs shutdown → exits)
7. To emergency halt: **Kill** (instant stop, may leave robot in dirty state)

### From the command line

```bash
sudo python3 projects/my_project/main.py --port 5010
```

Then open `http://<ip>:5010` for the 3D viewer, or use the orchestrator to send start/pause/kill commands.

### Pause / Park / Kill — runtime control semantics

The runtime exposes three control signals. Each interacts differently with the currently executing state:

| Signal | When it takes effect | What runs after | Use when |
|---|---|---|---|
| **Pause** | At the next pause-aware call (`rt.sleep` / `rt.delay` / `rt.<robot>` / `rt.checkpoint`) — see [§8 Pause gate](#pause-gate) for the full list | Blocks until you Resume — state continues from where it stopped | You want to inspect, intervene, or wait |
| **Park** | **Between states** — current state runs to completion first | If `trigger: park` is defined → that trigger runs and is the authoritative final cleanup. Otherwise → exit immediately, tools stay where they are | Graceful shutdown — the safe default |
| **Kill** | At the next pause-aware call (same set as Pause) | Nothing — process exits immediately, no cleanup | Emergency halt only — may leave robot/tools in a dirty state |

**Why Park is "between states", not mid-state:** many states perform multi-step atomic operations (most importantly tool swaps: `place(old)` then `pick(new)`). Interrupting between those steps would leave the robot in an inconsistent state — e.g. tool placed back but the next pick never happened, while the runtime still thinks a tool is held. Park therefore lets the current state finish, then exits cleanly between states.

If you need to stop *immediately* and accept the consequences, use Kill.

#### What triggers Pause

Four distinct sources can transition the runtime into PAUSED. The
runtime treats them identically — once `paused == True`, the next
pause-aware call blocks regardless of source. The differences are
purely in **who set the flag** and **how the operator should respond**.

| # | Trigger | Set by | Operator action to resume | Code path |
|---|---|---|---|---|
| 1 | **Operator clicks Pause** in the dashboard or pendant | The user, via UI | Click Resume when ready | WS cmd `pause` → `runtime_server.py:184` → `rt.pause()` |
| 2 | **Critical device goes down on the bus** (USB unplug, TCP drop, daemon crash, etc.) | Auto — by `MQTTOrchestrator` watching `device/+/state` topics | Fix the hardware → click Recover on the device row → wait for state=ok → click Resume | `devices/orchestrator.py:351` |
| 3 | **Robot motion command returns an alarm code** (negative int from a `rt.<robot>` call — limit hit, IK failed, E-stop pressed) | Auto — by `rt.call` itself when a wrapped robot method returns < 0 | Clear the alarm on the robot itself → click Resume | `runtime.py:532` (inside `rt.call`) |
| 4 | **Project code calls `rt.pause()`** directly (custom checks, action policy, "I want to wait for the operator here") | Your code | Whatever the project documents — usually Resume after handling the situation | Anywhere a `Check` / action / recipe calls `rt.pause()` |

Trigger 2 (device-down auto-pause) has additional gates: it fires only
when the device is **critical**, **not sim**, and either transitioning
from ok→down or first-observed-down. Sim devices and project-claimed-sim
devices never auto-pause. See [device-guide.md §1 rule 4](device-guide.md)
and [§16 simulation model](device-guide.md) for the full claim
aggregation logic.

#### Pause atomicity — entry, not middle

A pause is **observed at the entry to a pause-aware call**, not in the
middle of one. The atomic rule is:

> When the runtime is paused, the **next** pause-aware call your code
> reaches blocks until Resume. Work already in progress when the pause
> flag was set runs to completion, and the next call is where the
> operator sees the freeze.

Why this matters: a robot mid-motion can't be interrupted safely.
Pause therefore happens at the **boundary** before the next call, never
mid-execution. If you need an instant freeze accepting the
consequences, use **Kill** instead.

#### Resume semantics — work runs, nothing is skipped

After Resume, the call that was blocked **runs its work** — it isn't
skipped or aborted. The internal shape of every pause-aware call is:

```
1. Enter the call → internal checkpoint()
2. Checkpoint blocks while paused
3. Operator clicks Resume → checkpoint returns
4. The actual work runs (robot move / IO write / sleep tick / …)
5. Call returns
```

So `rt.jmove(...)` that was paused at entry will execute the move once
Resume fires. No silent drops.

**One subtle case: `rt.sleep(seconds)`.** Sleep uses wall-clock
end-time, not "N seconds of unpaused time." If you pause during a
`rt.sleep(10)` and resume after the original 10 s window has elapsed,
sleep **returns immediately** on Resume (the conceptual wait already
passed). Useful for "wait until ~T seconds from now" semantics; if you
need "exactly 10 unpaused seconds," compose with `rt.checkpoint()` and
a custom loop. For typical workflows the wall-clock behavior is what
you want — by the time the operator resumes, the wait is done.

#### Single mental model

> **Pause sets a flag. The next pause-aware call observes it and
> blocks. Resume releases the block; the work runs after Resume. Mid-
> call work is never interrupted — pauses happen at boundaries.**

That one paragraph covers the contract for all four trigger sources.

