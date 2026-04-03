# Project Guide

How to create and run a workspace project.

---

## Project structure

```
projects/my_project/
├── launch.yaml          # Scene paths + runtime parameters (kwargs)
├── main.py              # Entry point
├── scene/
│   ├── base.j2          # Hardware layout (Jinja2)
│   └── layout.j2        # Spatial arrangement
├── recipes/
│   └── recipes.j2       # Component aliases → recipe classes
└── protocol/
    ├── __init__.py      # Required (empty file, makes it a Python package)
    ├── protocol.yaml    # States, dependencies, checks, goals
    ├── states.py        # State handlers (what the robot does)
    └── checks.py        # Verification checks (pre/post)
```

---

## 1. Scene — `scene/`

Defines the physical hardware: robots, racks, tools, peripherals. Built using the **Scene Builder** GUI. The Jinja2 templates (`.j2` files) describe every component and its position. This is the source of truth for component names used in recipes.

---

## 2. Recipes — `recipes/recipes.yaml` or `recipes.j2`

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

## 3. Protocol — `protocol/protocol.yaml` or `protocol.j2`

Defines the workflow as a list of states with dependencies, tool assignments, and checks. The OR-Tools scheduler reads this to figure out the optimal execution order. You don't write the scheduling logic — you just declare "dosed requires picked" and the solver handles the rest.

Also supports `.j2` format (same as recipes).

```yaml
states:
  - name: picked
    duration: 8
    requires: []
    tool: gripper
    post_check: tube_picked

  - name: dosed
    duration: 15
    requires: [picked]
    tool: pipette
    pre_check: tube_in_rack

  - name: placed
    duration: 6
    requires: [dosed]
    tool: gripper

  - name: shaken
    duration: 5
    requires: [placed]
    background: true

goal: [placed]
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Must match a key in `states.py` `make()` |
| `duration` | No | Estimated seconds (used by scheduler, default: 1) |
| `requires` | No | List of state names that must complete first |
| `tool` | No | Tool name from the tool changer (recipe alias, e.g. `gripper`). When two consecutive states need different tools, the robot auto-swaps via the tool rack. |
| `background` | No | `true` = runs in parallel, completes all items at once |
| `pre_check` | No | Check name or list — must match a key in `checks.py` `make()` |
| `post_check` | No | Check name or list — must match a key in `checks.py` `make()` |
### Goal

The `goal` list defines terminal states. The protocol succeeds when every goal state has been completed.

If your project processes multiple items (e.g. tubes, vials), pass `batch_size` via `launch.yaml` kwargs. Each state runs once per item (index 0 to n-1). The protocol finishes when all items reach every goal state. If `batch_size` is not set, it defaults to 1 — each state runs once, like a simple sequence.

---

## 4. Checks — `protocol/checks.py`

Verification functions that run before/after states. The keys in `make()` are the names you reference in `pre_check` and `post_check` in `protocol.yaml`.

```python
class Checks:
    def tube_in_rack(self, i):
        return True   # just return True or False

    def tube_picked(self, i):
        ok = gripper.has_object()
        if not ok:
            self.rt.step(f"Tube {i} — gripper empty, check manually", level="warning")
        return ok

    def make(self):
        return {
            "tube_in_rack": self.tube_in_rack,   # ← use in protocol.yaml as pre_check: tube_in_rack
            "tube_picked": self.tube_picked,      # ← use in protocol.yaml as post_check: tube_picked
        }
```

```yaml
# protocol.yaml — references the check names from make()
states:
  - name: picked
    pre_check: tube_in_rack
    post_check: tube_picked
```

- Each check receives `i` (item index) and returns `True` (passed) or `False` (failed)
- On `False`, the task is skipped and the runner moves to the next task
- Use `rt.step()` inside the check to show a message to the operator
- Use `rt.pause()` inside the check if you want to pause and wait for the operator before skipping
- Checks are optional — states without `pre_check`/`post_check` just run directly

```python
def tube_picked(self, i):
    ok = gripper.has_object()
    if not ok:
        self.rt.step(f"Tube {i} pick failed — fix and resume", level="warning")
        self.rt.pause()   # wait for operator, then skip
    return ok
```

---

## 5. States — `protocol/states.py`

Each state is a function that executes one item.

```python
class States:
    def __init__(self, rcp, rt, batch_size, **kwargs):
        self.rcp = rcp          # Recipe dict from recipes.yaml
        self.rt  = rt            # Runtime (for step, call, sleep)
        self.batch_size = batch_size
        # Extra kwargs from launch.yaml are available here
        # e.g. self.dry_run = kwargs.get("dry_run", False)

    def picked(self, i):
        """Pick tube i from the rack."""
        self.rt.call(self.rcp["gripper"].pick, i)

    def dosed(self, i):
        """Dose 40ml into tube i."""
        self.rt.call(self.rcp["pipette"].dose, volume=40)

    def placed(self, i):
        """Place tube i into output rack."""
        self.rt.call(self.rcp["gripper"].place, i)

    def make(self):
        return {
            "picked": self.picked,
            "dosed": self.dosed,
            "placed": self.placed,
        }
```

- `i` is the item index (0 to batch_size-1).
- Use `rt.call()` for robot commands — it handles alarms automatically.
- Background states receive `i=0` and run once for all items.

---

## 6. Launch config — `launch.yaml`

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

### Supported kwarg types

| Type | Widget | Notes |
|------|--------|-------|
| `int` | Number input | `min`, `max`, `step=1` |
| `float` | Number input | `min`, `max`, `step=any` |
| `str` | Text input | |
| `bool` | Checkbox | |
| `choice` | Dropdown | Requires `options: [a, b, c]` |
| `textarea` | Multi-line text | `rows` (default 4), tries JSON parse |
| `file` | File upload | `accept: ".csv,.xlsx"` |

Optional fields: set `optional: true`. Empty = `null`.

---

## 7. Entry point — `main.py`

```python
import os, argparse, yaml
from pathlib import Path
from workspace.workspace import Workspace
from workspace.ortools.workflow import BaseWorkflow
from workspace.runtime_server import RuntimeServer
from protocol.states import States
from protocol.checks import Checks

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

The orchestrator launches this with `sudo python3 main.py --port 5010`.
`**kwargs` comes from the parameters modal in the GUI.

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

```python
rt.call(robot.jmove, target_joints)
```

Wraps any function. If the return value is negative (robot alarm), it:
1. Logs an error-level step
2. Pauses the runtime
3. Waits for the operator to clear the alarm and resume

### Sleep

```python
rt.sleep(5.0)  # Interruptible — responds to pause/kill
```

### Pause gate

```python
rt.checkpoint()  # Blocks if paused, raises KillRequested if killed
```

Called automatically after every `rt.step()` and `rt.call()`. Use manually in long loops.

---

## 9. Running a project

### From the orchestrator GUI

1. Open `http://<ip>:5000`
2. Click **+ Add Workspace**
3. Set name, port, path to `main.py`
4. Click the **gear icon** → set parameters → **Set**
5. Click **Launch** → **Start**

### From the command line

```bash
sudo python3 projects/my_project/main.py --port 5010
```

Then open `http://<ip>:5010` for the 3D viewer, or use the orchestrator to send start/pause/kill commands.

---

## 10. Creating a new project — checklist

1. Copy `projects/pace_or/` as a template
2. Edit `scene/` — define your hardware layout
3. Edit `recipes/recipes.yaml` — map component aliases
4. Edit `protocol/protocol.yaml` — define states, dependencies, goals
5. Write `states.py` — implement each state handler
6. Write `checks.py` — implement verification checks
7. Edit `launch.yaml` — set scene paths and kwargs
8. Update `main.py` imports if you renamed classes
9. Add workspace in orchestrator → set params → launch → start
