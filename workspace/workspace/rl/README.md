# LAOS — Lab Automation Operating System

A YAML-driven framework for lab robotics with RL-based execution ordering.

## Overview

You define **what** exists (scene), **how** to operate it (recipes), **what states** items must reach (protocol), and **what rules** to follow (constraints). The RL agent learns the optimal execution order automatically.

## Project structure

```
projects/your_project/
├── 1_scene/           ← hardware layout (Jinja2 templates)
├── 2_params/
│   ├── params.yaml    ← positions, run parameters
│   └── recipes.yaml   ← component aliases → recipe classes
├── 3_protocol/
│   └── protocol.yaml  ← state dependency graph
├── 4_constraints/
│   └── constraints.yaml ← rewards, constraints, reward rules
├── 5_rl/
│   └── models/        ← trained model (policy.zip)
├── main.py            ← entry point
└── workflow.py        ← handler registrations (the glue)
```

## How to create a new project

### Step 1: Scene — `1_scene/`

Define the physical components on the robot rail. Copy `base.j2` and `layout.j2` from an existing project and modify.

These are real hardware names like `adapter_plate_amber_40ml_4x7_1`, `decapper_5`. Only this layer knows these names — everything else uses aliases.

### Step 2: Recipes — `2_params/recipes.yaml`

Give each component a human-readable alias and tell the system which recipe class to use:

```yaml
source_rack:
  class: workspace.recipes.rack.Rack
  kwargs:
    component: adapter_plate_amber_40ml_4x7_1
    base_distance: 75
```

Fields:
- `source_rack` — your alias. Used in params.yaml, protocol.yaml, constraints.yaml, and workflow.py.
- `class` — full Python import path to the recipe class. This tells the system HOW to interact with this component (pick, place, decap, etc.). No hardcoded import map needed — the system imports it dynamically.
- `kwargs.component` — the hardware name from `1_scene/`. This is the only place where the scene name appears outside of `1_scene/`.
- Other kwargs — recipe-specific parameters (approach distances, rail spans, etc.).

### Step 3: Params — `2_params/params.yaml`

Define positions and run parameters. Use the aliases from recipes.yaml:

```yaml
n_items: 4
speed_factor: 10
shake_duration: 120
source:
  - [source_rack, A1]    # item 0 starts here
  - [source_rack, A2]    # item 1 starts here
  - [source_rack, A3]
  - [source_rack, A4]
```

`source_rack` here refers to the alias in recipes.yaml. `A1`, `A2` are anchor names on that component.

Accessed in workflow.py as `cfg.source[i][0]` (alias) and `cfg.source[i][1]` (anchor).

### Step 4: Protocol — `3_protocol/protocol.yaml`

Define the states each item must reach and their dependencies:

```yaml
states:
  - name: source_rack.pick
    requires: []

  - name: scale.weight
    requires: [source_rack.pick]

  - name: source_rack.place
    requires: [scale.weight]

goal:
  - source_rack.place
```

**State fields:**
- `name` — a label (any string). Must match the name used in `workflow.py register()` and `constraints.yaml`. The `alias.method` convention is recommended for readability but not required.
- `requires` — list of state names that must be completed first (dependency graph). Empty `[]` means no dependencies — can execute anytime.
- `background: true` — no robot needed, runs in parallel (e.g. shaker).
- `optional: true` — skip gracefully if it fails (e.g. autosampler empty).
- `duration: shake_duration` — references a key from params.yaml (used for timing background states).

**Goal:**
- All items in `goal:` are AND — every item must reach ALL goal states for the protocol to be complete. Only then the RL gets the completion bonus.

**Naming convention:**
State names have no meaning to the system — they're just string labels. The recommended format is `alias.method` with `_2`, `_final` suffixes for repeated calls to the same method:
```yaml
- name: decapper.decap       # first decap
- name: decapper.decap_2     # second decap (same method, different point in protocol)
```

### Step 5: Constraints — `4_constraints/constraints.yaml`

This file controls RL training behavior: how the agent is rewarded, what it can and can't do, and when to give bonuses.

#### Rewards

Global reward values the RL receives:

```yaml
rewards:
  per_step:    -1.0      # every step — pushes RL to finish fast
  goal_state:   50.0     # each time an item reaches a goal state
  completion:  200.0     # all items reached all goals — protocol done
  background:    5.0     # completing a background state
```

A "step" = one action from the protocol (one state executed). With 54 states and 4 items, the minimum is 213 steps, so the step penalty is always at least -213.

#### Constraints

Dynamic trackers that affect masking (what the RL can do) and rewards. Three built-in types:

##### `bool` — binary flag (hard constraint)

Tracks a true/false condition. When true, blocks specific actions entirely — the RL can never choose them.

```yaml
- name: holding
  type: bool
  observe: true
  true_on:                      # flag becomes true after these states
    - source_rack.pick
    - decapper.decap
  false_on:                     # flag becomes false after these states
    - scale.place
    - cap_holder.place
  block:                        # these states are BLOCKED when flag is true
    - source_rack.pick
    - decapper.decap
```

Example: `holding` prevents double pick — you can't grab a tube while already holding a cap.

`observe: true` — the RL sees the flag value (0 or 1) as part of its input. This helps it learn patterns around the constraint.

##### `int` — counter (hard constraint)

Tracks an integer value with a maximum. Blocks actions when the counter hits the max.

```yaml
- name: rack_slots
  type: int
  observe: true
  max: 4
  add_on: [rack.place]          # +1 after these states
  sub_on: [rack.pick]           # -1 after these states
  block: [rack.place]           # blocked when counter == max
```

Example: `rack_slots` prevents placing more items than the rack can hold.

##### `enum` — categorical (soft constraint)

Tracks which value from a list is currently active. Applies a penalty when the value changes.

```yaml
- name: current_tool
  type: enum
  observe: true
  penalty: -25.0
  options: [gripper, needle, feeder_tool, gripper_2ml]
  map:
    source_rack.pick: gripper
    doser_40ml.immerse: needle
    autosampler.above: feeder_tool
    rack_2ml.pick: gripper_2ml
```

Fields:
- `values` — all possible values. These names MUST match the aliases in `recipes.yaml` because `_ensure_tool("gripper")` calls `rcp["gripper"].pick()`.
- `map` — maps each state (from protocol.yaml) to the value it requires. If the current value is different, a tool swap happens and the penalty is applied.
- `penalty: -25.0` — reward penalty every time the value changes. This is SOFT — the RL can still change tools, it just learns to batch same-tool states together to avoid penalties.
- `observe: true` — the RL sees the current value as a one-hot vector (e.g. `[0, 1, 0, 0, 0]` for gripper). This lets the RL know which tool is mounted and decide to batch.

No tools in your project? Omit the entire enum entry. No pick/place? Omit the bool. No capacity limits? Omit the int. Only add what your project needs.

#### Reward rules

Conditional bonuses for smart behavior:

```yaml
reward_rules:
  - name: fill_idle_time
    when: cap_feeder.place              # this state is executed
    while_incomplete: shaker.shake      # while this state hasn't been reached
    reward: 20.0                        # bonus
```

This teaches the RL to feed caps during shake idle time. If `cap_feeder.place` happens while `shaker.shake` is still incomplete, the RL gets +20. If it happens after shaking is done, no bonus.

#### How constraints work together

```
protocol.yaml requires     →  decides WHAT is allowed (dependency order)
constraints bool block     →  decides WHAT is physically possible (hard)
constraints enum penalty   →  shapes HOW the RL orders things (soft)
constraints int block      →  decides WHAT fits (capacity limits, hard)
reward_rules               →  teaches WHEN to do optional things (bonuses)
```

Hard constraints (bool, int) prevent illegal moves during training — the RL can never violate them.
Soft constraints (enum penalty, reward_rules) shape behavior — the RL learns to avoid penalties and seek bonuses over time.

### Step 6: Workflow — `workflow.py`

The glue between protocol state names and actual recipe calls. Each state from `protocol.yaml` must be registered to a handler:

```python
from pathlib import Path
from workspace.rl.workflow import BaseWorkflow

_BASE_DIR = Path(__file__).parent

class Workflow(BaseWorkflow):
    def __init__(self, workspace, core):
        super().__init__(workspace, core, _BASE_DIR, n_items=4)

    def _register_all(self):
        r   = self.runner.register
        rcp = self.rcp
        cfg = self.cfg

        r("source_rack.pick",  lambda i: rcp["source_rack"].pick(cfg.source[i][1]))
        r("scale.weight",      lambda i: rcp["scale"].weight())
        r("source_rack.place", lambda i: rcp["source_rack"].place(cfg.source[i][1]))

def workflow_fn(*, workspace, core):
    wf = Workflow(workspace, core)
    wf.run()
```

The register call connects three things:
1. `"source_rack.pick"` — the state name from `protocol.yaml`
2. `rcp["source_rack"]` — the recipe alias from `recipes.yaml`
3. `.pick(cfg.source[i][1])` — the method + args from `params.yaml`

**With tool swaps:** use `_atomic()` to auto-manage tool pick/place:

```python
G = "gripper"    # must match values in constraints.yaml enum
N = "needle"

r("source_rack.pick", self._atomic(G, lambda i: rcp["source_rack"].pick(cfg.source[i][1])))
r("doser_40ml.immerse", self._atomic(N, lambda i: rcp["doser_40ml"].immerse(...)))
```

`_atomic(G, handler)` wraps the handler with `_ensure_tool("gripper")` — picks the tool if not already mounted, placing the current one first.

**Without tool swaps:** just register plain lambdas, no `_atomic()` needed.

### Step 7: Train

```bash
cd /home/dorna/Downloads/workspace/workspace
sudo python3 workspace/rl/train.py --project your_project --count 4 --steps 200000
```

Network size, learning rate, and batch size scale automatically based on the action space size.

Options:
- `--count N` — number of items to process (tubes, vials, etc.)
- `--steps N` — total training steps
- `--resume` — continue training from existing model
- `--out path` — custom output path for the model

**What to expect during training:**
- `avg_reward` should go UP over time — the RL is learning
- `avg_len` should stay at the minimum (n_states × n_items + n_background) — optimal from the start
- Early stop triggers when reward plateaus (no improvement for 25 intervals)
- Reward close to theoretical max = near-optimal tool batching

**Theoretical max reward** = `completion + (n_goal_states × n_items × goal_state) - (min_steps × per_step) - (min_tool_swaps × tool_swap_penalty) + bonuses`

### Train on Google Colab (faster)

For longer training runs, use Colab instead of the Pi:

```python
# Cell 1: setup
!git clone https://github.com/dorna-robotics/workspace.git
%cd workspace/workspace
!pip install sb3-contrib pyyaml

# Cell 2: train
!python -m workspace.rl.train --project pace_atomic --count 4 --steps 1000000

# Cell 3: download model
from google.colab import files
files.download('projects/pace_atomic/5_rl/models/policy.zip')
```

Download `policy.zip` and place it in your project's `5_rl/models/` folder on the Pi.

### Step 8: Run

Point the orchestrator to `main.py`. The trained model decides execution order at runtime. Inference is sub-millisecond — the robot motion is always the bottleneck.

## Name connection map

All names flow from one file to the next:

```
1_scene/layout.j2
    │
    │ component: adapter_plate_amber_40ml_4x7_1
    ↓
2_params/recipes.yaml
    │
    │ alias: source_rack  →  class: workspace.recipes.rack.Rack
    │                         kwargs.component: adapter_plate_amber_40ml_4x7_1
    ↓
2_params/params.yaml
    │
    │ source: [[source_rack, A1], [source_rack, A2]]
    ↓
3_protocol/protocol.yaml
    │
    │ state name: source_rack.pick  (any string, alias.method by convention)
    │ requires: []
    ↓
4_constraints/constraints.yaml
    │
    │ map: source_rack.pick → gripper  (state name → enum value)
    │ true_on: [source_rack.pick]            (state name → bool trigger)
    │ options: [gripper, ...]           (must match recipes.yaml alias)
    ↓
workflow.py register()
    │
    │ r("source_rack.pick", ...)   (state name → lambda → recipe call)
    │   rcp["source_rack"].pick()  (recipes.yaml alias → method)
    │   cfg.source[i][1]           (params.yaml position)
    ↓
trained model (5_rl/models/policy.zip)
    │
    │ picks actions by state index
    ↓
real robot
```

## Constraint type reference

| Type | Tracks | Hard/Soft | Blocks actions? | Key fields |
|------|--------|-----------|-----------------|------------|
| `bool` | true/false | hard | yes, via `block` | `true_on`, `false_on`, `block` |
| `int` | counter 0→max | hard | yes, when at max via `block` | `add_on`, `sub_on`, `block`, `max` |
| `enum` | category | soft | no, applies penalty | `values`, `map`, `penalty` |

`observe: true` on any constraint includes its value in the neural network input, helping the RL make better decisions.
