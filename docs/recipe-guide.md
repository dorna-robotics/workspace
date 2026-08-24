# Recipe Guide

How to create, customize, and call recipes for your project.

A recipe is the **workflow coordination layer** between BT actions /
operator buttons / projects and the underlying hardware. It wraps a
component with high-level operations — pick, place, dose, cap, inspect,
shake, print — and knows how to drive the robot to the right poses,
fire the right IO at the right moment, and stay sim-agnostic.

> **Look first**: if you're writing a brand-new recipe class, jump to
> §7 — Creating your own recipe. If you're calling an existing recipe
> method from an action / state / operator button, read its docstring
> in [recipe.py](../workspace/workspace/recipes/recipe.py) — `help()`
> or your IDE's tooltip works too.

## Contents

1. [What a recipe is (vs a component)](#1-what-a-recipe-is-vs-a-component)
   - [Component vs recipe — the ownership rule](#component-vs-recipe--the-ownership-rule)
   - [Recipes are sim-agnostic](#recipes-are-sim-agnostic)
2. [The Recipe base class](#2-the-recipe-base-class)
   - [`DEFAULTS` reference](#defaults-reference)
   - [How `__init__` works](#how-__init__-works)
   - [Attributes set on `self`](#attributes-set-on-self)
   - [What the base class promises](#what-the-base-class-promises)
3. [The `touch` primitive — how every motion is built](#3-the-touch-primitive--how-every-motion-is-built)
   - [`pose_offset` — the anchor-local frame](#pose_offset--the-anchor-local-frame)
   - [Approach path vs target offset](#approach-path-vs-target-offset)
   - [Path planning — `has_motion_plan` and `first_approach`](#path-planning--has_motion_plan-and-first_approach)
   - [Parameter tuning](#parameter-tuning)
4. [Calibration flow](#4-calibration-flow)
5. [`recipes.yaml` — configuration format](#5-recipesyaml--configuration-format)
6. [Conventions](#6-conventions)
   - [6.1 Sim-agnostic](#61-sim-agnostic)
   - [6.2 DEFAULTS merge pattern](#62-defaults-merge-pattern)
   - [6.3 Pause-awareness — always via `rt.*`](#63-pause-awareness--always-via-rt)
   - [6.4 Component-vs-recipe ownership](#64-component-vs-recipe-ownership)
   - [6.5 The `**kwargs` override behavior — read this carefully](#65-the-kwargs-override-behavior--read-this-carefully)
7. [Creating your own recipe](#7-creating-your-own-recipe)
   - [Pattern A: subclass `Recipe`](#pattern-a-subclass-recipe)
   - [Pattern B: from scratch (no base class)](#pattern-b-from-scratch-no-base-class)
   - [Where to put it](#where-to-put-it)
   - [Full end-to-end example](#full-end-to-end-example)
8. [Catalog](#8-catalog)

> **Method API reference**: per-method signatures, parameters,
> required state, corner cases, and gotchas live in the docstrings
> in [recipe.py](../workspace/workspace/recipes/recipe.py). Use
> `help(rcp["..."].<method>)` at the REPL, your IDE's tooltip, or
> read the source. The docstrings are the canonical API — this guide
> covers the conceptual + design side.

---

## 1. What a recipe is (vs a component)

A recipe connects three things:

| What | Description |
|---|---|
| **Component** | The physical hardware from the scene (rack, gripper, decapper, etc.) — provides atomic device ops. |
| **Runtime (`rt`)** | Pause/stop-aware robot API — handles checkpoints, alarms, and motion. |
| **Core** | Inverse kinematics, calibration, and motion planning. |

Recipes are registered by alias in `recipes.yaml` (or `recipes.j2`).
BT actions / states call them through that alias:
`self.rcp["gripper"].pick("A3")`.

### Component vs recipe — the ownership rule

The single most important rule for deciding "does this code go on a
component or a recipe?":

> **Component owns the atomic device operation. Recipe owns the
> workflow.** Test: *"Could the operator press one button to trigger
> this?"* → component. *"Does this combine multiple atomic ops into a
> sequence?"* → recipe.

So: `gripper.enable()` lives on the Gripper component (one button, one
IO call). `gripper.pick(anchor)` lives on the Recipe (multi-step:
approach + IO + attach + exit). Full rule + examples in
[component-guide.md §7](component-guide.md).

### Recipes are sim-agnostic

A recipe **never** branches on `simulation:` and never calls anything
like `if component._simulation_mode: ...`. The component constructor
picks the right underlying API once (real driver vs. sim stub) and
exposes a unified interface — the same recipe code runs against real
hardware or sim with no edits. Full rule in
[device-guide.md §10.5](device-guide.md).

---

## 2. The Recipe base class

`workspace.recipes.recipe.Recipe` is the common base. Most custom
recipes inherit from it to get pick/place/motion + utility queries +
calibration. A handful (IO-only, no robot motion) skip the base
entirely — see §7 Pattern B.

### `DEFAULTS` reference

`Recipe.DEFAULTS` declares every parameter the base class supports.
Subclasses extend it (their DEFAULTS merge over base); operators
override per-instance via `recipes.yaml` kwargs (see §6.2).

Grouped by what each key influences:

#### Reference IK — used once at boot to validate the scene

These declare the anchor + offset the constructor probes with
`core.IK(...)`. Failure raises `RecipeError` immediately.

| Key | Default | What |
|---|---|---|
| `target_solid_name` | `"body"` | Which sub-solid of the component owns the reference anchor. |
| `target_anchor` | `"center"` | Anchor name on `target_solid_name` to validate IK against. |
| `target_offset` | `[0,0,50,0,180,0]` | XYZ-ABC offset from the anchor. XYZ in mm; ABC is a rotation vector in **degrees** (not radians). Default = 50 mm above with a 180° flip about Y so the tool faces down. |
| `initial_joints` | `[0,0,0,0,0,0,0,0]` | Starting joint guess for the reference IK solve. Set to a known-good pose for difficult workspaces. |

#### IK shape — applied to every motion the recipe issues

| Key | Default | What |
|---|---|---|
| `left_approach` | `True` | Robot's left vs right elbow configuration. `True` = left-elbow-up. |
| `base_distance` | `350` | Rail distance from robot base used for IK (mm). Smaller = robot closer to the component. |
| `rail_step` | `0` | Step size (mm) for rail search around `base_distance`. `0` = no search, single try at `base_distance`. |
| `rail_span` | `0` | Number of step-attempts on each side. `rail_step=10, rail_span=3` searches ±30 mm in 10 mm steps. |

#### Motion

| Key | Default | What |
|---|---|---|
| `motion_type` | `"lmove"` | Default move type for subsequent waypoints in `touch`. `"lmove"` = Cartesian straight line; `"jmove"` = joint space; any `rt.*` method name also works. |
| `speed_factor` | `0.5` | True playback-rate knob: `sf` asks for the same path in `1/sf` of the time, so it scales `vel×sf`, `accel×sf²`, `jerk×sf³` (the time-scale law — each derivative pulls down another factor of `sf`). The chain certifier clamps wherever geometry can't deliver and reports requested/achieved on every `[traj]` line. Use a smaller value per recipe instance for sensitive sites. |
| `jmove_vaj` | `[200, 500, 3000]` | `[velocity, acceleration, jerk]` for joint-space moves *before* `speed_factor` is applied. |
| `lmove_vaj` | `[600, 1400, 6000]` | Same shape, for linear moves. |

#### Calibration

| Key | Default | What |
|---|---|---|
| `calibration` | `True` | If `True`, every IK solve passes through `_calibrate_offset` to apply the stored correction. Set `False` for sim or uncalibrated stations. |
| `calibration_name` | `None` | Storage key for the calibration. If `None`, auto-generated as `{component.name}_{left_approach}_{base_distance}_{rail_step}_{rail_span}` — uniqueness depends on the IK params that produced the corrections. |
| `calibrate_abc` | `False` | If `True`, calibration corrects the ABC rotation vector (degrees) as well as XYZ. `False` = position-only correction (the common case). |
| `calibration_targets` | `None` | `{solid_name: [anchor_names]}` mapping for `calibrate()` to walk. If `None`, auto-discovered from every assembly solid by collecting anchors prefixed `clb_`. |
| `calibration_target_offset` | `[0,0,8,0,0,0]` | Offset applied during calibration touch — typically 8 mm above the calibration mark so the tool seats correctly. |
| `calibration_tool_solid_name` | `"body"` | Sub-solid of the calibration tool that holds the probe anchor. |
| `calibration_tool_anchor` | `"tcp"` | Anchor on the calibration tool used as the calibration probe. |
| `calibration_tool_offset` | `[0,0,0,0,0,0]` | Final offset applied to the tool's calibration probe pose. |

### How `__init__` works

```python
def __init__(self, workspace, core, component, **kwargs):
    ...
```

Seven steps, in order:

1. **Merge DEFAULTS + kwargs.**
   ```python
   prm = deepcopy(self.DEFAULTS)
   merge(prm, kwargs)
   ```
   Caller wins. When a subclass extends the chain
   (`Recipe.DEFAULTS → SubClass.DEFAULTS → kwargs`), the subclass
   is responsible for the extra merge — see §6.2.

2. **Wire references** — `self.workspace`, `self.core`,
   `self.component`. The `self.rt` property derives from
   `workspace.rt`; not set as an attribute so it always reflects the
   live workspace runtime.

3. **Stash IK shape params** — `self.left_approach`,
   `self.base_distance`, `self.rail_step`, `self.rail_span`. Used
   by every subsequent IK call as the per-recipe IK config.

4. **Stash motion params** — `self.motion_type`, `self.speed_factor`,
   `self.jmove_vaj`, `self.lmove_vaj`. Used by `_do_motion` and
   `_execute_motion_planned` for every move.

5. **Stash calibration params**. Three of them auto-fill:
   - `self.calibration_name` defaults to a unique-per-IK-shape key
     generated from the component name + IK params.
   - `self.calibration_targets` defaults to auto-discovered `clb_*`
     anchors across every assembly solid.
   - The remaining four (`calibrate_abc`, `calibration_target_offset`,
     `calibration_tool_*`) come straight from DEFAULTS / kwargs.

6. **IK validation at boot** —
   ```python
   J, C = self.core.IK(
       target_solid=self.component.assembly[prm["target_solid_name"]],
       target_anchor=prm["target_anchor"],
       target_offset=prm["target_offset"],
       base_distance=self.base_distance,
       rail_step=self.rail_step,
       rail_span=self.rail_span,
       ref_joints=prm["initial_joints"],
       left_approach=self.left_approach,
   )
   if C != 2:
       raise RecipeError(...)
   ```
   The probe runs against the reference IK keys (`target_*`,
   `initial_joints`). On failure, the operator sees a clear error
   at workspace launch — not silently halfway through a workflow.

7. **Store `self.ref_joints = J`** — the validated reference pose
   every subsequent IK call uses as `ref_joints=`. Wrong ref →
   wrong solutions; the boot validation guarantees you start from a
   sane one.

After `__init__` returns, the recipe is ready to call.

### Attributes set on `self`

Summary view of what's on the instance after `__init__`:

| Group | Attributes |
|---|---|
| References | `workspace`, `core`, `component`, `rt` (property) |
| IK | `left_approach`, `base_distance`, `rail_step`, `rail_span`, `ref_joints` |
| Motion | `motion_type`, `speed_factor`, `jmove_vaj`, `lmove_vaj` |
| Calibration | `calibration`, `calibrate_abc`, `calibration_name`, `calibration_targets`, `calibration_target_offset`, `calibration_tool_solid_name`, `calibration_tool_anchor`, `calibration_tool_offset` |

### What the base class promises

| Category | Methods |
|---|---|
| **High-level motion** | `pick`, `place`, `above`, `stand`, `immerse`, `retract`, `rotate`, `vibrate`, `park` |
| **Core motion primitive** | `touch` (universal — every pick/place/above/stand goes through it) |
| **Settings builders** | `pick_setting`, `place_setting` (compute the param dict `touch` consumes) |
| **Calibration** | `calibrate`, `calibrate_anchor` |
| **Utility queries** | `solid_attached_to_tool`, `solid_attached_to_anchor`, `solid_hierarchy` |
| **Axis init** | `set_axis_with_stop`, `set_axis_with_encoder` (rail / feeder homing) |

For exact signatures, required state, corner cases, and per-param
meanings, read the docstrings in
[recipe.py](../workspace/workspace/recipes/recipe.py) — they're the
canonical API reference.

---

## 3. The `touch` primitive — how every motion is built

**`touch`** is the universal motion primitive. Every high-level
method on `Recipe` (`pick`, `place`, `above`, `stand`, `immerse`,
`retract`) ultimately calls it. The helpers `pick_setting` and
`place_setting` exist solely to compute the param dict that `touch`
consumes — they don't move the robot themselves.

```
pick / place / above / stand / immerse / retract        ← public method
                       │
                       ▼
        pick_setting / place_setting                    ← compute the param dict
                       │
                       ▼
                   touch(**prm)                         ← universal motion primitive
                       │
                       ▼
               _move_along_path                         ← step through waypoints
                       │
                       ▼
        rt.smove | rt.jmove | rt.lmove                  ← pause-aware execution
```

Three concepts unlock most customization.

### `pose_offset` — the anchor-local frame

Returned by `pick_setting` in its output dict. It's a `Pose`
representing "the natural reference point at this anchor" — typically
the **center of whatever is already stacked there** (e.g. a tube
sitting in a rack slot), or the anchor itself if nothing is stacked.

When you call `pose_offset.pose(offset=[x, y, z, a, b, c])`, the
framework transforms that offset into the anchor's frame and returns
a world-ready pose. This is how `above(padding=50)` works
transparently whether the slot is empty or has a tall stack —
`pose_offset` absorbs the height math.

`stand(anchor, offset=...)` exposes this directly: the `offset` you
pass is in the same frame `pick_setting` uses internally.

### Approach path vs target offset

- **`approach_path`** — list of waypoints to pre-position the tool
  above/near the target. Built by `pick_setting` from `padding` and
  `gap` (and optionally `soft_approach`).
- **`exit`** — `True` retracts to `padding` clearance after touch-down
  (the default); `False` skips the exit leg entirely; a **number** is
  the exit's own clearance in mm — `pick(..., exit=10)` pairs the
  normal careful approach with a short 10 mm pull-off. The clearance
  is measured above `max(height_load, height_container)`, so a small
  number still clears a tall stack; `exit=0` is rejected (use `False`).
- **`target_offset`** — the exact final touch-down pose. For
  `pick`/`place` it's the anchor itself (transformed via `pose_offset`).
  For `above`/`stand` it's `None` so the motion stops at the single
  waypoint without final descent.

Full path consumed by `touch` is `approach_path + [target_offset]`
(unless `target_offset` is `None`).

### Path planning — `has_motion_plan` and `first_approach`

> **Term: "hop".** A hop is a single robot motion from one pose to
> the next waypoint. The approach path is a list of waypoints; the
> transition from the current pose to waypoint 1 is the **first
> hop**, current pose → waypoint 1 → waypoint 2 → target_offset is
> three hops total. Only hops do work — IO toggles, sleeps, and
> attach updates happen between hops, not during them.

Planning runs **only on the first hop of an approach path**. The
gate inside `_move_along_path`:

```python
if i == 0 and first_approach:
    _execute_motion_planned(...)   # smove along planned waypoints OR jmove
else:
    _do_motion(...)                # jmove or lmove based on self.motion_type
```

Two flags drive it:

- **`has_motion_plan`** — enabled on `core` by default. Override per
  call via `has_motion_plan=False`.
- **`first_approach`** — internal boolean, True only when `touch` is
  running an approach path with at least one waypoint. Exit paths
  and direct `approach=False` calls bypass planning entirely.

| Scenario | Planning? |
|---|---|
| `pick(...)`, `place(...)`, `above(...)`, `stand(...)` with core planning on | Yes — first hop |
| `pick(approach=False, ...)` | No — no approach waypoint exists |
| Exit from a pick/place | No — exit path always uses direct moves |
| `retract(...)` | No — `has_motion_plan=False` by default |
| `rotate(...)`, `vibrate(...)` | No — direct jmoves |
| `park(...)` | Yes if `has_motion_plan=True` (or `core.has_motion_plan`), else single jmove |

`lmove` (Cartesian straight line) is used only for subsequent
waypoints if `self.motion_type == "lmove"`, never on the first
planned hop.

When you do need to override:

```python
rcp["rack"].pick("A1", has_motion_plan=False)
rcp["rack"].pick("A1", motion_plan_kwargs={"padding": 30, "gravity_vec": [0, 0, -1]})
```

### Parameter tuning

For numeric parameters like `padding`, `gravity_offset`,
`soft_approach`, `tool_tcp_z_offset` — see
[parameter-guidelines.md](parameter-guidelines.md) for rules of thumb
and gripper-specific recommendations.

---

## 4. Calibration flow

Recipes that need per-bench calibration (most do) declare anchors
prefixed `clb_` on the component. Two methods drive the workflow:

- **`calibrate_anchor(...)`** — interactive single-point calibration.
  Moves the robot to the IK-solved pose, prompts the operator to
  nudge the robot onto the real calibration point, records the
  corrected joints, and stores the offset under
  `self.calibration_name`. Normally called by `calibrate()`.
- **`calibrate(calibration_targets=...)`** — runs `calibrate_anchor`
  over every anchor in the targets dict. Defaults to
  `self.calibration_targets` (auto-discovered from `clb_*` anchors on
  the component).

Calibration runs **interactively** — uses `input()` prompts. It's a
developer-time / bench-setup tool, not a workflow step. Skip it in
sim mode by leaving `calibration=False` in DEFAULTS.

Calibration corrections are applied transparently inside `_solve_ik`
when `self.calibration` is true:

```python
if self.calibration:
    offset = self._calibrate_offset(target_solid, target_anchor, offset)
```

So once you've calibrated a station, every subsequent `pick` /
`place` on that station benefits automatically.

---

## 5. `recipes.yaml` — configuration format

```yaml
gripper:
  class: workspace.recipes.tool_rack.ToolRack
  kwargs:
    component: tool_rack_1
    left_approach: true

decapper:
  class: workspace.recipes.decapper.Decapper
  kwargs:
    component: decapper
    base_distance: 200

# Custom recipe from project-local file
my_dispenser:
  class: recipes.dispenser.Dispenser
  kwargs:
    component: my_dispenser_1
    dispense_volume: 500
```

| Field | Description |
|---|---|
| Top-level key | Alias — used in actions as `self.rcp["alias"]` or `rcp["alias"]` |
| `class` | Fully qualified import path to the recipe class |
| `kwargs.component` | Required — component name from `scene/base.j2` |
| Other `kwargs` | Passed to the recipe constructor; override DEFAULTS |

For library recipes, the path starts with `workspace.recipes.`. For
project-local recipes, use a local import path like
`recipes.dispenser.Dispenser`.

Supports `.j2` for Jinja2 templating — useful for shared variables:

```yaml
{% set speed_factor = 0.8 %}

gripper:
  class: workspace.recipes.tool_rack.ToolRack
  kwargs:
    component: tool_rack_1
    speed_factor: {{ speed_factor }}
```

---

## 6. Conventions

Every recipe is expected to follow these. The framework enforces some
mechanically (sim-agnostic via the architecture); others are pure
convention authors must respect.

### 6.1 Sim-agnostic

Recipes never branch on simulation:

```python
# WRONG
if self.component._simulation_mode:
    return None
return self.component.driver.read()

# RIGHT
return self.component.read()    # component's sim/real branch lives here
```

The component constructor picks the API once. Recipes call the
component's public API and stay agnostic. Same recipe code runs
against real or sim. See [device-guide.md §10.5](device-guide.md).

### 6.2 DEFAULTS merge pattern

Recipes use a three-level merge chain:

```
Recipe.DEFAULTS → YourClass.DEFAULTS → kwargs from recipes.yaml
```

Later values override earlier ones:

```python
class MyRecipe(Recipe):
    DEFAULTS = dict(
        base_distance=200,      # override base default
        speed_factor=1.0,       # default for this recipe
    )

    def __init__(self, workspace, core, component, **kwargs):
        prm = deepcopy(Recipe.DEFAULTS)
        merge(prm, self.DEFAULTS)   # your defaults override base
        merge(prm, kwargs)           # yaml overrides yours
        super().__init__(workspace=workspace, core=core, component=component, **prm)
```

```yaml
# recipes.yaml — override per instance
my_recipe:
  class: recipes.my_recipe.MyRecipe
  kwargs:
    component: some_component
    speed_factor: 0.5              # yaml beats class beats base
```

### 6.3 Pause-awareness — always via `rt.*`

Every robot/IO/timing call goes through `rt.*` so the operator's
Pause / Resume is honoured automatically.

| Call | Pause-aware? |
|---|---|
| `rt.checkpoint()` | ✓ |
| `rt.sleep(s)` / `rt.delay(s)` | ✓ |
| `rt.output(config=...)` | ✓ |
| `rt.jmove(...)`, `rt.lmove(...)`, `rt.smove(...)` | ✓ |
| `rt.<robot_method>(...)` (any method on `core.robot_api`) | ✓ (auto-wrapped) |
| `rt.step("msg")` | ✗ — observability only |

**Rule**: *Observability never blocks. Work always checkpoints.*
Full pause-aware map and the architectural rule in
[project-guide.md §8 Pause gate](project-guide.md#pause-gate).

If you find yourself reaching for `time.sleep(...)` in a recipe,
stop — use `rt.delay(...)` instead. The base axis-init helpers
(`set_axis_with_stop`, `set_axis_with_encoder`) followed this
exact migration recently.

### 6.4 Component-vs-recipe ownership

If you find yourself writing a method that:
- Touches one device, one IO call, no motion — it belongs on the
  **component**.
- Combines multiple atomic ops, motion, sensing — it belongs on the
  **recipe**.

Full rule + Feeder example in
[component-guide.md §7](component-guide.md).

### 6.5 The `**kwargs` override behavior — read this carefully

`pick_setting` and `place_setting` accept `**kwargs` and apply any
kwarg whose name matches an attribute on `self`:

```python
def pick_setting(self, anchor, ..., **kwargs):
    for k, v in kwargs.items():
        if hasattr(self, k):
            setattr(self, k, v)   # ← mutates self
    ...
```

This lets you do one-line per-call overrides:

```python
rcp["rack"].pick(anchor="A1", speed_factor=0.1)
```

**Side effect to know about**: that override **persists**. The
`speed_factor` attribute is now 0.1 until something else changes it.
The NEXT call also runs at 0.1:

```python
rcp["rack"].pick(anchor="A1", speed_factor=0.1)  # speed_factor = 0.1
rcp["rack"].pick(anchor="A2")                      # still 0.1, not back to default
```

This is not a per-call override — it's a permanent mutation of the
recipe instance via kwargs. Typos are silently ignored (no error if
the kwarg doesn't match an attribute).

**If you want a true per-call override**: set the attribute back
afterwards, or scope the change yourself. Future versions may change
this behaviour; treat it as legacy.

---

## 7. Creating your own recipe

Two patterns.

### Pattern A: subclass `Recipe`

The common case — you want pick/place/motion plus a few
domain-specific methods. Inherit from `Recipe`, declare DEFAULTS,
write your methods using `rt.*` for any I/O.

```python
from copy import deepcopy
from mergedeep import merge
from workspace.recipes.recipe import Recipe

class Dispenser(Recipe):
    DEFAULTS = dict(
        base_distance=150,
        dispense_volume=500,
        speed_factor=1.0,
    )

    def __init__(self, workspace, core, component, **kwargs):
        prm = deepcopy(Recipe.DEFAULTS)
        merge(prm, self.DEFAULTS)
        merge(prm, kwargs)
        super().__init__(workspace=workspace, core=core, component=component, **prm)

        self.volume = prm.get("dispense_volume", 500)

    def dispense(self, volume=None):
        """Dispense liquid — pause-aware."""
        rt = self.rt
        rt.checkpoint()
        vol = volume or self.volume
        rt.output(config=self.component.output_enable)
        rt.delay(vol / 100)  # flow rate
        rt.output(config=self.component.output_disable)
```

**When to override `pick` / `place`** — only when your component
needs a custom approach corridor that `pick_setting` can't express
via params (`padding`, `gap`, `soft_approach`, etc.). Real-world
examples:

| Recipe | Why it overrides |
|---|---|
| `Adapter` | Side-loaded container — biased approach path to avoid wall collision |
| `Hotel` | Multi-level shelf with lateral slide-in approach |
| `Decapper` | Custom screw motion intercalated with pick/place |
| `Feeder` | Tighter `padding` (25 mm) for the smaller load envelope |
| `Printer` | Print head positioned via component radius offset |

Look at any of those for the override pattern.

### Pattern B: from scratch (no base class)

For IO-only devices that don't need robot motion (multimeters,
scales, conveyors, label printers driven by RS-232 only). Skip
`Recipe` entirely — you don't need IK validation at boot, and the
base class would only add ceremony.

```python
class Conveyor:
    def __init__(self, workspace, core, component, **kwargs):
        self.workspace = workspace
        self.core = core
        self.component = component

    @property
    def rt(self):
        return self.workspace.rt

    def run(self, duration=5.0):
        """Run conveyor belt — pause-aware."""
        rt = self.rt
        rt.checkpoint()
        rt.output(config=self.component.output_enable)
        rt.delay(duration)
        rt.output(config=self.component.output_disable)

    def stop(self):
        rt = self.rt
        rt.output(config=self.component.output_disable)
```

Real-world example: `workspace.recipes.multi_meter.MultiMeter` —
a thin pass-through to the component's atomic measurement API. It
inherits from `Recipe` for shape but bypasses `Recipe.__init__` (no
IK setup needed for a stationary instrument).

What you give up by skipping the base class:
- `pick` / `place` / `above` / `stand` / `immerse` / `retract` /
  `rotate` / `vibrate` / `park` / `calibrate` — none of these are
  available.
- `solid_attached_to_anchor` / `solid_hierarchy` — utility queries
  you'd have to re-implement.
- The IK-validation-at-boot guarantee — fine because there's nothing
  to validate.

What you keep:
- The recipes.yaml registration shape (still `class:` + `kwargs:`).
- `self.rt` for pause-aware IO and timing.
- The component reference.

### Where to put it

Project-local recipes live under `recipes/` in the project:

```
my_project/
├── recipes/
│   ├── __init__.py         # empty — required for Python imports
│   └── dispenser.py
├── recipes.yaml
├── main.py
└── ...
```

Reference it in `recipes.yaml` with a local import path:

```yaml
my_dispenser:
  class: recipes.dispenser.Dispenser
  kwargs:
    component: my_dispenser_1
    dispense_volume: 750
```

No changes to `main.py` needed — the recipe loader uses `importlib`,
which resolves local imports automatically since `main.py` runs from
the project directory.

For platform-wide recipes (used across multiple projects), add to
`workspace/workspace/recipes/` and use the full
`workspace.recipes.<name>.<Class>` import path.

### Full end-to-end example

```
my_project/
├── recipes/
│   ├── __init__.py
│   └── dispenser.py
├── recipes.yaml
├── states.py        (or actions.py for BT projects)
└── main.py
```

**`recipes/dispenser.py`** — see the Pattern A example above.

**`recipes.yaml`**:
```yaml
gripper:
  class: workspace.recipes.tool_rack.ToolRack
  kwargs:
    component: tool_rack_1

my_dispenser:
  class: recipes.dispenser.Dispenser
  kwargs:
    component: dispenser_1
    dispense_volume: 750
```

**`states.py`** — calling both library and custom recipes:

```python
class States:
    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt

    def dispensed(self, i):
        self.rcp["my_dispenser"].dispense(volume=500)

    def picked(self, i):
        self.rcp["gripper"].pick(i)

    def register(self, runner):
        runner.register_state("dispensed", self.dispensed)
        runner.register_state("picked",    self.picked)
```

For BT projects, the same recipes work — just call them from
`Action.execute(self)` via `self.ctx.recipes["..."]`.

---

## 8. Catalog

Every recipe currently shipped with the platform. Each entry
summarises what's special about that recipe; for the full per-method
API, read the source file's docstrings.

### Base
- **`Recipe`** ([recipe.py](../workspace/workspace/recipes/recipe.py))
  — the universal base class. Pick / place / above / stand / immerse
  / retract / rotate / vibrate / park / touch. Almost everything
  inherits from this.

### Storage & Tools
- **`Rack`** ([rack.py](../workspace/workspace/recipes/rack.py)) —
  rack-on-adapter resolver. Thin wrapper that delegates to the
  attached rack component (grid positions like `"A1"` … `"H12"`).
- **`ToolRack`** ([tool_rack.py](../workspace/workspace/recipes/tool_rack.py))
  — tool changer interface. Pick/place tools via pneumatic latch; both
  methods build their `motion_prm` dict by hand because the swap path
  is unique to that mechanism (one of the few recipes that calls
  `touch` directly rather than via `pick_setting`).
- **`Adapter`** ([adapter.py](../workspace/workspace/recipes/adapter.py))
  — side-loaded container with biased approach (10 mm X-offset) to
  avoid wall collision.
- **`Hotel`** ([hotel.py](../workspace/workspace/recipes/hotel.py)) —
  multi-level shelf with lateral slide-in approach. Uses `level`
  parameter to select shelf anchor (`place_0`, `place_1`, …).

### Liquid handling

Which one to use where — the decision table and a composed protocol
example live in `docs/liquid-handling.md` §4. In one line: robot
travels to the liquid → `PipettingSite` (racks and fixed dosing spots
alike); mounted nozzle → `DispenseArm`; nothing moves → `Pump`.
(`DosingSite` was folded into `PipettingSite` — migrate with a
one-word class swap in `recipes.j2`.)

- **`Pump`** ([pump.py](../workspace/workspace/recipes/pump.py)) —
  thin pass-throughs to the syringe-pump component's atomic ops
  (`aspirate` / `dispense` / `prime` / `empty` / `valve`, named ports,
  material reads). No robot motion of its own.
- **`PipettingSite`** ([pipetting.py](../workspace/workspace/recipes/pipetting.py))
  — the robot dips the carried tool into vessels, slot by slot (or at
  a single-place dosing site).
  `pick_tip`, `eject_tip` (with shake + tip-presence verification),
  `immerse`, `aspirate`, `dispense`. Speaks the air-displacement
  pipettor's vocabulary (`speed` in µL/s, `blowout`); a plumbed needle
  rides it unchanged because `PumpLink` matches the method names (and
  swallows the pipettor-unit args). **Note**: currently has sim-flag
  checks inline that belong in the component constructor — pending
  cleanup.
- **`DispenseArm`** ([dispense_arm.py](../workspace/workspace/recipes/dispense_arm.py))
  — bench-mounted nozzle head. `down` / `up` (pneumatic IO);
  `dispense(volume_ul=…)` pushes through the arm's pump port when the
  scene plumbs one, else falls back to the historical timed hold.

A tool that declares `lock_j5` (the needle gripper's stripper rods)
gets its wrist roll pinned automatically by `immerse` / `retract` —
see `docs/liquid-handling.md` §3.

### Cap handling
- **`Decapper`** ([decapper.py](../workspace/workspace/recipes/decapper.py))
  — cap twist/untwist with chunked screw motion and gripper re-bite.
  `decap` (unscrew in chunks), `cap` (screw on chunks). Targets the
  tube's `cap_seat` anchor for cap-on motion.

### Feeders & shakers
- **`Feeder`** ([feeder.py](../workspace/workspace/recipes/feeder.py))
  — rotary feeder with index-based grid-snap rotation and mixing.
  `mix` (oscillates, changes direction past threshold),
  `rotate_in_step` (grid-snap delegate to component), `present_cap`
  (recursive cap-finding with feeder rotation — the only multi-recipe
  orchestration in the codebase).
- **`Shaker`** ([shaker.py](../workspace/workspace/recipes/shaker.py))
  — orbital shaker with threading-safe stop event. `shake` (toggle
  until duration), `stop_shaking` (thread-safe interrupt).

### Inspection
- **`FixedInspector`** ([inspector.py](../workspace/workspace/recipes/inspector.py))
  — vision-based detection with server-side RPC. Registers a
  detection preset at construction; `capture`, `detect`, `rotate`
  (j5 override for camera flip).
- **`MobileInspector`** ([inspector.py](../workspace/workspace/recipes/inspector.py))
  — robot-mounted camera variant. Same `capture` / `detect` surface
  but no robot motion (bypasses `Recipe.__init__`).

### Other
- **`Printer`** ([printer.py](../workspace/workspace/recipes/printer.py))
  — label printer with print-head positioning via component radius
  offset. `dry_run_spin`, `print_label`. Also has pending sim-flag
  cleanup.
- **`Scale`** ([scale.py](../workspace/workspace/recipes/scale.py))
  — placeholder weighing station. `weight` returns 0 after 1 s sleep.
- **`MultiMeter`** ([multi_meter.py](../workspace/workspace/recipes/multi_meter.py))
  — thin pass-through to a BK 879B LCR meter component. Slim
  override of `Recipe.__init__` (no motion setup needed for a
  stationary instrument).

---

For deep-dive design notes on complex recipes (Decapper's chunked
screw motion, Pipetting's tip exchange logic, Feeder's
`present_cap` orchestration), see (planned) `docs/recipes/`
subdirectory. Created per-recipe only when the design rationale
outgrows what fits in the source's docstrings + this catalog entry.
