# Project Guide

How to create and run a workspace project.

---

## 1. Project structure

```
projects/my_project/
├── main.py              # Entry point — ties everything together
├── launch.yaml          # Scene paths + run parameters (default/setup/pendant)
├── protocol.yaml        # States, dependencies, checks, goals
├── states.py            # State handlers (what the robot does)
├── checks.py            # Verification checks (pre/post)
├── recipes.j2           # Component aliases → recipe classes (or recipes.yaml)
├── hmi/                 # OPERATOR-FACING files (see §3)
│   ├── default.j2       # The kwargs' defaults (data — Python reads it)
│   ├── setup.js         # Screen to SET the kwargs (optional; .html or .js)
│   ├── pendant.html     # Screen shown DURING the run (+ pendant.css)
│   └── hmi.j2           # …or a platform widget list, if writing no markup
└── scene/
    ├── base.j2          # Hardware layout (Jinja2)
    └── layout.j2        # Spatial arrangement
```

**Convention for new projects: operator-facing declarations live in
`hmi/`.** `launch.yaml` stays a short list of pointers (scene,
recipes, actions, checks, kwargs, hmi) — one file per concern, none of
them inline. Run parameters and the pendant HMI are the same concern
(what the operator sees and sets), so they share the folder.

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
| `default` | The kwargs' defaults / schema — each key becomes a run parameter. **Either inline (a dict) or a file path** — new projects use `default: hmi/default.j2` (see §1); inline stays supported for small projects. The file's top level IS the schema, rendered as Jinja2 then parsed. Both shapes work everywhere (orchestrator form, `bt.replay`). |

```yaml
scene: [scene/base.j2, scene/layout.j2]

default:
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

### Two entry shapes — bare, or a spec

Each schema entry is either **bare** (the value IS the default) or a
**spec dict** — one deterministic rule tells them apart: *a dict
containing the key `"default"` is a spec; anything else is a bare
default.* (Corollary: a bare map default must not itself contain a key
named `default` — use the spec form then.)

```yaml
tubes: {"A1": 0.4, "A2": 0.4}     # bare — a map kwarg with its default
print_label: false                 # bare
batch_size: {type: int, default: 4, min: 1, max: 20}   # spec
```

**Bare is the shape for a project with its own `setup:` screen** — the
schema stays "the kwargs themselves", and labels/units/limits live in
the screen, which is where presentation belongs (bd is the exemplar).
The generic form still renders bare entries by inferring the widget
from the default's type (bool → toggle, number → number input,
list/map → JSON textarea).

**The spec form is for a project with NO screen** that wants typed
widgets, labels and server-enforced limits on the auto-generated form
(apc is the exemplar). Its properties:

### Kwarg field properties (spec form)

| Property | Required | Description |
|----------|----------|-------------|
| `default` | No | Pre-filled value. `null` = empty |
| `label` | No | Display name in the modal (defaults to the key name) |
| `hint` | No | Help text shown below the input |
| `placeholder` | No | Greyed-out text inside the input when empty |
| `optional` | No | `true` = field can be left empty, sent as `null` |
| `min` / `max` | No | Numeric bounds (for `int` and `float` types) |
| `type` | For the generic form | Widget type: <br>• `int` — number input (`min`, `max`, `step=1`) <br>• `float` — number input (`min`, `max`, `step=any`) <br>• `str` — text input <br>• `bool` — touch switch <br>• `choice` — dropdown (requires `options: [a, b, c]`) <br>• `textarea` — multi-line text (`rows` default 4, tries JSON parse) <br>• `file` — file upload (`accept: ".csv,.xlsx"`) |

**`type:` exists to pick the generic form's widget — nothing else.**
A collection kwarg (a list or map, like bd's `tubes`) has NO type: its
default's shape declares it, and replay batches on that shape. The
standing rule for this file: **every key must have a reader** (the
generic form, validation, replay, or your screen) — a key nothing
reads is deleted, not kept for documentation's sake. Taken to its
conclusion: a project whose screen owns all presentation declares
bare entries only.

### `setup` — the project's own run-setup screen

The generic form covers scalars. When an operator must pick *which*
positions to run — a rack, a tray, a carousel — that is a picture of
the project's own hardware, so the project draws it. Declare it in
`launch.yaml` next to the schema:

```yaml
default: hmi/default.j2     # the kwargs' defaults (data)
setup:   hmi/setup.js      # screen to SET them
```

The schema still declares every parameter, because it is read with no
browser anywhere (`bt.replay`, the CLI, launch). It just stops
describing how anything looks:

```yaml
tubes:
  type: map                 # or list
  label: Tubes to process
  value: {label: Dispense, unit: mL, default: 0.4, min: 0.1, max: 5.0, step: 0.1}
  default:                  # the project's own grid, in the project
    "A1": 0.4
    "A2": 0.4
```

The screen is hosted exactly like the pendant screen (hmi-guide §4b) —
shadow root, design tokens, `.html` with `data-field="key"` or `.js`
with `{css, mount(root, api), value(), validate()}`. Its `api` carries
`{schema, values, frozen, theme, onTheme}`. Two rules make it safe:

* **The platform validates whatever the screen returns** against the
  schema — required, `min`, `max`. A project screen is not trusted to
  enforce its own contract, and `validate()` only ADDS a message.
* **Fields the screen does not draw keep their schema default**, so a
  screen can cover only the parameters it cares about (bd draws the
  rack; `print_label` keeps its default).

Filter excluded positions **in `setup` too** — the screen prevents
picking them, but a replay/API caller can pass anything:

```python
picked = kwargs.get("tubes") or {}          # {"A1": 0.4, ...}
tubes = sorted({SLOTS.index(s) for s in picked
                if s in SLOTS and s != SOURCE_SLOT})
```

`workspace.bt.replay --batch N` slices the first N entries of the
first collection-typed kwarg's default, so the schedule gate keeps
meaning "run N items" with no rack knowledge in the platform. Verify a
real selection with `--kw 'tubes={"A1": 0.4, "C2": 1.5}'`.

### `_layout` — arranging the run-setup form

The schema renders stacked in declaration order. To place fields side
by side, declare rows — a HINT, not a field:

```yaml
_layout:
  - row: [batch_size, print_label]    # side by side
```

`row` places fields side by side. That is the whole vocabulary: a
project that needs more than rows of scalars ships a `setup` screen
and arranges it however it likes (multiple racks, tabs, wizards — all
project markup, none of it platform vocabulary).

Unlisted fields stack below in declaration order; rows wrap to a
single column on narrow screens (pendant portrait). This is the only
layout vocabulary for the **run-setup form** — a project never ships
markup or CSS for its parameters, because a list of typed fields is a
genuine declaration. Its pendant SCREEN is the opposite case and is a
project-owned file (hmi-guide §2, §4b).

### Compatibility rule for declarative files

Projects are `.j2`/`.yaml` declarations that outlive the platform
version they were written against. The rule that keeps old projects
working:

1. **New features are opt-in keys. Absent = previous behaviour.**
   Everything added this cycle obeys it: no `setup:` → the generic
   form; no `pendant:` → the default pendant; no `_layout` → fields stack;
   `default:` as a dict still works exactly as before the file form
   existed.
2. **Never repurpose an existing key.** A changed meaning is a silent
   behaviour change on every project that already uses it. Add a new
   key instead and let the old one keep working.
3. **Deprecate loudly, never silently.** If a key must go, the loader
   warns with the project name and the replacement for at least one
   release; it does not quietly do something else.
4. **Readers degrade, they don't crash.** Unknown keys are ignored;
   an unresolvable reference (a `component:` that no longer exists)
   falls back to a plain field. A display concern must never block a
   run.
5. **`examples/` is the compatibility suite.** Before shipping a
   platform change that touches loaders, recipes or the schedule, run
   `workspace.bt.replay` (and `workspace.recipes.solve` for motion
   changes) over the example projects — they are the cheap regression
   net for "did I just break older projects".

**One rename, on the record.** The schema's launch key was `kwargs:`
until 2026-08; every in-tree example and bench project now declares
`default:`. The old key still loads with the same meaning but prints a
rename warning at load (rule 3) — new work never writes it.

**One removal, on the record.** `type: slots` — a rack-position picker
the platform drew — was deleted rather than deprecated, against rule 3.
It had exactly one user (bd), in-tree, migrated in the same commit, and
keeping it would have meant carrying a rack in the platform forever
(hmi-guide §2). A project still declaring it degrades to a plain text
field rather than crashing (rule 4); the replacement is a `params`
screen.

### How kwargs flow

All kwargs defined here are passed to `States.__init__(rcp, rt, **kwargs)` and `Checks.__init__(rcp, rt, **kwargs)`. Use `kwargs.get("my_param", default)` to access them.

Two reserved keys are also used by the scheduler if present:

| Key | Default | Description |
|-----|---------|-------------|
| `batch_size` | `1` | Number of items to process. Each state runs once per item (index 0 to n-1). |
| `horizon` | `60` | Rolling window size for replanning. `null` = plan all tasks at once. |

If `batch_size` or `horizon` are in your kwargs, they override the defaults for the scheduler. They are still passed to States and Checks like everything else.

---

### `rt.op(**values)` — operator-facing values

`rt.step` is the engineer timeline (append). `rt.op` is the operator
value channel (replace): actions publish what the project's pendant
screen binds to, and writing a key again overwrites it.

An action publishes plain values and stays ignorant of the UI; the
screen (`hmi/pendant.html`, hmi-guide §4b) decides how they look. A
map value is how a rack is driven — `rt.op(tubes={"A1": "done"})` sets
`data-state` on whatever element claims `data-slot="A1"`.

```python
rt.step(f"tube {t+1}: weighed")          # timeline entry
rt.op(state=f"Weighing tube {t+1}", weight=grams)   # current values
rt.op(last_image="/captures/bd/tube_06.jpg")        # assets BY REFERENCE
rt.op(weight=None)                                   # remove a key
```

Rules: values must be JSON-able and small (≤4 KB each, ≤200 keys,
≤64 KB total — over-limit values are dropped with one log line, never
a crash); large data is passed as a URL, not inlined; the call never
blocks and never raises into the workflow. Values are stored as
**detached copies** — keep and mutate your own dict freely, the store
never sees it. Publish plain data only (a barcode STRING, not the
driver's Scan object): an object sneaks a nice repr into ``rt.step``
but is not a value. Values are memory-only and
cleared at run start. Delivery is a coalesced WS push — see
`docs/hmi-guide.md` §3 for the channel spec.

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

### Device reads + declarative retry

A device read (`rcp["scale"].weight()`, `rcp["meter"].read_resistance()`,
`rcp["inspector"].detect()`) can fail mid-run — the instrument drops, the
TCP link stalls, the camera server hiccups. The platform handles this in
two cooperating-but-independent layers. Understand both; the second is the
one you write.

#### Layer 1 — who pauses the robot (bus-driven, automatic)

When a **`critical: true`, non-sim** device goes `down`, the **orchestrator**
pauses the runtime. The chain is entirely bus-driven and has *nothing to
do with your action*:

```
read fails → station._set_state("down") → adapter publishes device/<id>/state=down
          → MQTTOrchestrator sees critical+down → runtime.pause()
```

So the robot pauses whether or not your action notices the failed read.
The pause lands at the **next pause-aware call** (`rt.<robot>`, `rt.sleep`)
— the device read itself isn't a checkpoint (it bypasses the gate, per the
table above), so an in-flight read finishes, then the next motion holds.
This pause does **not** fire when the device is sim (publisher-sim or the
project claims sim) — there's no real failure to react to.

You don't write any of this. Set `critical: true` on the device and it
happens.

#### Layer 2 — re-doing the read after recovery (declarative, you write it)

Pausing stops the robot, but the *reading was never captured*. To redo
**just the read** — without repeating the motions around it — make the
read its own action and let the **planner** retry it. Don't write a retry
loop or reach for `with_retry`; encode it in pre/eff so retry falls out of
the plan:

**The three rules:**

1. **Make the read its own action**, separate from the motions. Don't
   bundle place + read + pick into one action — then a failed read forces
   you to redo the arm moves. Split them: `PlaceOnScale` / `Weigh` (read
   only) / `PickFromScale`.

2. **Assert the success fact only on success.** The read action's `eff`
   adds `weighed(item)`; its `execute` returns the eff branch **only when
   it got a value**, and returns **`False`** otherwise:

   ```python
   class Weigh(Action):           # pure read, no motion
       params = ["tube"]
       resource = "robot"
       def pre(self, tube):  return on_scale(tube) & ~weighed(tube)
       def eff(self, tube):  return {"weighed": (+weighed(tube),)}
       def execute(self, tube):
           grams = self.ctx.recipes["scale"].weight()
           if grams is None:
               return False        # FAIL — weighed(tube) NOT asserted
           return "weighed"        # success → weighed(tube) becomes true
   ```

   Returning `False` fails the leaf and **applies no effect** (BT contract:
   `execute` returns the eff-branch string on success, `False` on failure
   — never `None`).

3. **Let the existing replan do the retry.** The launcher already wraps the
   body in `replan_on_failure`, so a failed leaf rebuilds the plan from the
   **observed world**. There, `on_scale(item) & ~weighed(item)` is still
   true (the item never left the pan), so the planner **re-selects the read
   action**. The retry *is* the plan — no loop, no special case.

Putting the layers together, end to end:

```
Weigh runs → weight() returns None → return False  → leaf FAILS, weighed(t) not set
  (meanwhile, if critical+real: bus down → orchestrator paused the runtime)
operator/AutoRecover reconnects → resume
engine replans from observed state → on_scale(t) & ~weighed(t) still holds
  → Weigh re-selected → weight() succeeds → weighed(t) set → flow continues
```

The tube stayed on the pan the whole time (`on_scale` held), so **only the
read was retried — no motion repeated.** This same shape works for *any*
device read: keep it its own action, assert the fact only on success,
return `False` otherwise. The reference implementation is
`examples/scale/actions.py` (`PlaceOnScale` / `Weigh` /
`PickFromScale`).

#### `resource` on a read-only action — it's the scheduling lock, not the device

Tempting question: a read-only `Weigh` touches the *scale*, so shouldn't
its `resource` be `"scale"`, not `"robot"`? **No — `resource` is the
mutual-exclusion lock the scheduler uses to decide what may run
concurrently, not "which device this action reads."** Pick it by asking:
**during this action, is the robot free to do other work, or committed?**

The scheduler interleaves actions on *different* resources and on
*different* items freely — and it has **no model of "the robot stays put
while the gripper is open at the pan."** So if `Weigh(tube N)` declared
`resource="scale"`, the scheduler would consider the robot free during the
read and could slot `PlaceOnScale(tube N+1)` into that window — sending the
arm off while tube N sits ungripped on the pan, which `PickFromScale(tube
N)` must still return to. Nothing validates that away; the schedule is
"valid" by the scheduler's rules and physically wrong.

In the weigh flow the robot is **committed** to the tube for the whole
`PlaceOnScale → Weigh → PickFromScale` sequence (released on the pan, must
re-grip the same tube), so `resource="robot"` is correct — it keeps that
per-tube sequence serial against all other robot work. The only cost is a
short idle block on the robot timeline during the read, which is honest:
the arm genuinely is occupied.

Use a **device resource** (e.g. `resource="shaker_1"`, cf. sample_prep's
`ShakerOne`/`ShakerTwo`) **only when the robot is genuinely free during the
operation** — load the item, leave, come back later (a shaker runs
autonomously for minutes). That unlocks real parallelism: weigh/shake one
item while the arm works another. A ~instant, hands-on read where the arm
never leaves is *not* that case. Rule of thumb: **device-as-resource when
the robot leaves; `"robot"` when the robot waits.**

> **Why not `with_retry`?** `with_retry` re-runs the *same leaf* blindly N
> times. The declarative approach replans from the real world, so it
> naturally composes with pause/recover (it waits for the device to come
> back), with windowed slicing, and with anything else the planner knows.
> Reach for `with_retry` only for a transient that needs an immediate
> in-place retry with no world change; for "redo this until its goal-fact
> holds," use pre/eff.

### Single-occupancy resources — the gripper holds one item

A subtle planning trap, and one almost every multi-item protocol hits.
The planner is free to reorder actions whose preconditions are
independently satisfiable. If each action only references **its own
item's** facts — `Pick`'s pre is `started() & ~picked(t)` — then `Pick(0)`,
`Pick(1)`, `Pick(2)` are all independently applicable, and the planner
will happily schedule **all the picks first**, then all the places. That's
physically impossible: there's one gripper, it holds one item.

The symptom is a schedule like
`Pick(3) Pick(0) Pick(1) Pick(2) PlaceOnScale(0) PlaceOnScale(2) …` —
batched by action across items instead of one item completed end-to-end.

The fix is to model the **capacity-1 physical resources** as facts the
planner must respect — most commonly the **gripper** (`hand_empty`), and
any fixture an item rests on exclusively (a scale pan `pan_empty`, a single
inspection nest, etc.). A capacity-1 resource is a **no-arg fact** that's
*consumed* when the slot fills and *restored* when it empties:

```python
hand_empty = predicate("hand_empty")     # gripper holds no item

class Start(Action):
    def eff(self): return {"started": (+started(), +hand_empty())}   # seed it

class Pick(Action):
    def pre(self, t):  return started() & hand_empty() & ~picked(t)
    def eff(self, t):  return {"picked": (+picked(t), -hand_empty())} # hand now full

class Place(Action):
    def pre(self, t):  return off_scale(t) & ~placed(t)
    def eff(self, t):  return {"placed": (+placed(t), +hand_empty())} # hand frees
```

Now `Pick(1)` can't be scheduled until whatever filled the hand has
emptied it (a `Place` or a hand-off), so the planner is forced to finish
one item's hand-occupancy before starting the next. Use `+fact` to
restore, `-fact` to consume (the `eff` branch is a tuple of these). Seed
the resource's initial state in `Start.eff`.

Rule: **for every physical slot that holds at most one item — the gripper,
a pan, a nest, a single-tube fixture — add a no-arg `_empty` fact,
consume it on fill, restore it on empty.** The reference implementation is
`examples/scale/actions.py` (`hand_empty` + `pan_empty`). Without
it, single-item protocols look fine in small batches by luck and produce
impossible schedules as soon as the planner finds the reordering.

#### Always flag them `capacity=True`

```python
hand_empty = predicate("hand_empty", capacity=True)
pan_empty  = predicate("pan_empty",  capacity=True)
```

Declaring the fact is only half the job. A capacity fact is **shared**
— the same `hand_empty` toggles as *every* item passes through the
gripper — whereas an ordinary fact like `weighed(t)` belongs to one
item. The scheduler derives its ordering constraints from "which
earlier action last set this fact", and for a shared fact that answer
is whichever item the plan's own linearization happened to touch last.
The result is a chain welding every item's actions into one serial
sequence.

You only *see* the damage when an item **revisits a tool** — puts the
gripper down for another tool and comes back to it later (bd's re-cap
chain: gripper → pipettor → gripper). Batching then requires
interleaving items, which the weld forbids, and a batch that used to
run "all the decaps, then all the doses" collapses into strict
one-item-at-a-time with a tool change each way. On a 4-item bd batch
that was 10 tool swaps instead of 4.

`capacity=True` keeps the mutual exclusion (two items still can never
share the slot — it becomes a scheduler mutex constraint instead of an
ordering edge) while letting the solver interleave items to cluster by
tool. It is never *wrong* to omit — the schedule stays correct, just
needlessly serial — and the framework only pays the extra solve cost
on protocols where an item actually revisits a tool, so there is no
reason not to flag every predicate of this shape. Full rationale in
`workspace/bt/dsl.py`'s module docstring ("Capacity facts").

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


## 10. Validating a project — the toolchain

Three commands take a project from "scene finished" to "bench-ready"
without guessing, each answering one question and naming its failures.
The step-by-step pipeline that strings them together (who owns which
step, where an error class lives) is the `bootstrap-project` skill;
this section is the reference for the tools themselves. All are `-m`
module runs, so the `cd` is part of the command — from anywhere else
they fail with `ImportError: cannot import name 'Workspace'`. Always
in sim — they force
`simulation: true` on every device, so real hardware is never touched
(or fought over) by validation.

### 10.1 `workspace.recipes.solve` — recipe parameters + geometry

    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.recipes.solve <project_dir>
    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.recipes.solve <project_dir> --skeleton skeleton.yaml

Per station: boots the recipe with its declared `left_approach` /
`base_distance` (reference IK), sweeps both approaches × distances at
`rail_span: 1` when the declared values fail, and diagnoses total
failures geometrically (`UNREACHABLE — rail-frame x=941, rail ends at
801` is a bench-design error caught before any flow work).

Geometry is measured along each anchor's **approach ray** — its local
+z signed away from the bench, so tilted stations (a −48° feeder, a
hanging tool rack) are measured on their true axis, not world-vertical.
Boxes owned by the payload stack itself (the tube being entered, its
cap, their own collision boxes) are excluded by `componentName`. Two
numbers per station, both including a **hard 20 mm margin**:

| number | meaning |
|---|---|
| `min pad` | what any pick/place/immerse **hover padding** must reach (pick/place default 50, immerse default 10 — raise per call where the minimum is higher) |
| `min end` | how far above the payload any motion must **end** there — retract distances, exit heights. An arm stranded inside an inflated box poisons the *next* plan's start ("invalid start state") |

The margin is not negotiable: endpoints exactly on an inflated box
surface pass in sim and fail on real joints (measured — the retract
knife edge). Report-only; values are applied to `recipes.j2`
deliberately, with the evidence.

Sample output (bna) and how to read it:

    source_rack_1   la=False bd= 200 (declared)   min pad 31 / min end 31 above load ([305x186x123](collision_box_7) holds the ray to 112 @ A1; incl 20 margin)
    decapper        la=True  bd= 200 (declared)   min pad 122 / min end 132 above load ([88x88x166](collision_box_5) holds the ray to 112 @ place; incl 20 margin)
    scale           la=True  bd=  50 (swept)      ray clear (incl 20 margin)
    needle          UNREACHABLE — rail-frame x=941 y=-337, rail [-199, 801]   ** FIX SCENE **

Column by column:

- `la=... bd=... (declared)` — the recipe's own `left_approach` /
  `base_distance` verified as-is: your recipes.j2 needs no change.
- `(swept)` — the declared values FAILED reference IK; the shown pair
  is what the sweep found. Copy it into recipes.j2.
- `min pad 122` — every pick/place/immerse at this station must use
  hover `padding >= 122` (defaults: pick/place 50, immerse 10 — if
  the minimum exceeds the default, pass it per call).
- `min end 132 above load` — every motion must END at least 132 above
  the payload here: retract distances, exit heights. Lower and the
  arm is stranded inside a box → the NEXT plan dies with "invalid
  start state".
- `([88x88x166](collision_box_5) holds the ray to 112 @ place)` — the
  evidence: WHICH box constrains the approach ray, how far along the
  ray it reaches, at which probe anchor. If a number looks absurd,
  this names the box to inspect.
- `ray clear` — no box constrains this station; defaults suffice.
- `UNREACHABLE ... ** FIX SCENE **` — no (la, bd) solves the
  reference IK; the rail-frame x/y vs rail range says why. This is a
  bench-design decision (move the rack / station), not a parameter
  to tune — nothing downstream can fix it.

Numbers already include the 20 mm margin — use them as-is; do not add
your own on top.

### 10.2 `workspace.bt.replay` — the logic gate

    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.bt.replay <project_dir> --batch 1 4

PDDL plan → precedence → capacity spans → CP-SAT schedule → replay in
**scheduled** order against the real `pre()`/`eff()`. Zero
precondition failures + goal reached, or the exact action and time
that broke. Pure logic — seconds, no workspace, no motion. Run after
any `actions.py` change, at batch 1 AND a multi-item batch: batch 1
catches wrongly-seeded facts, multi-item catches capacity and
interleaving mistakes. Schedules are derived, never authored — this is
what proves the derivation's inputs truthful.

### 10.3 `workspace.bt.dryrun` — optional machinery debug, NOT a gate

    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.bt.dryrun <project_dir> --batch 2

The standard software gates are solve (10.1) and replay (10.2) —
path judgment belongs to the operator on the bench (step 5), never
to a sim check. Reach for dryrun only when replay is green but the
bench dies deep in recipe/engine plumbing and the failure needs
reproducing off-bench: it runs the real protocol through the real
engine in sim (planning, scheduling, checks, BT leaves) with playback
stubbed, so a batch runs in minutes.

Both bt commands resolve operator kwargs from `launch.yaml` (`--batch`
lands on the first int kwarg; `--kw name=value` overrides any) and are
exit-coded for scripting.

### 10.4 Caches and scene-file ownership

- `core/ik.json` and `core/path.json` are stamped with a **scene
  fingerprint** and auto-discard on mismatch (`[cache] ik.json
  discarded — scene changed since it was built`). The old "delete
  path.json after geometry changes" ritual is obsolete; legacy
  unstamped files count as stale once.
- The scene **builder owns `layout.j2`** and regenerates it wholesale.
  Hand-maintained scene content — consumable stock like caps in a
  feeder — lives in **`stock.j2`**, listed after `layout.j2` in
  `launch.yaml`'s scene list so the merge applies it on top and
  regeneration can never eat it.
