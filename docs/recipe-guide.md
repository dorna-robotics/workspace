# Recipe Guide

How to create, customize, and call recipes for your project.

A recipe is the **workflow coordination layer** between BT actions /
operator buttons / projects and the underlying hardware. It wraps a
component with high-level operations — pick, place, dose, cap, inspect,
shake, print — and knows how to drive the robot to the right poses,
fire the right IO at the right moment, and stay sim-agnostic.

> **Look first**: if you're writing a brand-new recipe class, jump to
> §8 — Creating your own recipe. If you're calling existing recipe
> methods from an action / state / operator button, §4 — Calling the
> methods is your home.

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
4. [Calling the methods (the API)](#4-calling-the-methods-the-api)
   - [Method comparison](#method-comparison)
   - [`pick`](#pick)
   - [`place`](#place)
   - [`above`](#above)
   - [`stand`](#stand)
   - [`immerse`](#immerse)
   - [`retract`](#retract)
   - [`rotate`](#rotate)
   - [`vibrate`](#vibrate)
   - [`park`](#park)
   - [`touch` (direct use)](#touch-direct-use)
5. [Calibration flow](#5-calibration-flow)
6. [`recipes.yaml` — configuration format](#6-recipesyaml--configuration-format)
7. [Conventions](#7-conventions)
   - [7.1 Sim-agnostic](#71-sim-agnostic)
   - [7.2 DEFAULTS merge pattern](#72-defaults-merge-pattern)
   - [7.3 Pause-awareness — always via `rt.*`](#73-pause-awareness--always-via-rt)
   - [7.4 Component-vs-recipe ownership](#74-component-vs-recipe-ownership)
   - [7.5 The `**kwargs` override behavior — read this carefully](#75-the-kwargs-override-behavior--read-this-carefully)
8. [Creating your own recipe](#8-creating-your-own-recipe)
   - [Pattern A: subclass `Recipe`](#pattern-a-subclass-recipe)
   - [Pattern B: from scratch (no base class)](#pattern-b-from-scratch-no-base-class)
   - [Where to put it](#where-to-put-it)
   - [Full end-to-end example](#full-end-to-end-example)
9. [Catalog](#9-catalog)

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
entirely — see §8 Pattern B.

### `DEFAULTS` reference

`Recipe.DEFAULTS` declares every parameter the base class supports.
Subclasses extend it (their DEFAULTS merge over base); operators
override per-instance via `recipes.yaml` kwargs (see §7.2).

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
| `speed_factor` | `0.5` | Multiplier applied to every move's `vel` / `accel` / `jerk`. Use a smaller value per recipe instance for sensitive sites. |
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
   is responsible for the extra merge — see §7.2.

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

For exact signatures, read the docstrings in
[recipe.py](../workspace/workspace/recipes/recipe.py). Per-method
behaviour summary is in §4.

---

## 3. The `touch` primitive — how every motion is built

**`touch`** is the universal motion primitive. Every high-level
method in §4 (`pick`, `place`, `above`, `stand`, `immerse`,
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
| `rotate(...)`, `vibrate(...)`, `park(...)` | Depends — see §4 |

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

## 4. Calling the methods (the API)

All robot work is **pause-aware** because it routes through `rt.*`.
You don't have to think about pause — the framework handles it.

### Method comparison

| Method | Use when… | Touches target? | Attach/IO? | Planning? |
|---|---|---|---|---|
| **`pick(anchor)`** | Grip the item at an anchor and carry it away | Yes | Yes — attach load → tool, trigger gripper IO | Yes, first hop |
| **`place(anchor)`** | Holding an item, release it at an anchor | Yes | Yes — detach load → destination, trigger IO | Yes, first hop |
| **`above(anchor, padding=N)`** | Hover `N` mm above the anchor (before manual / camera work) | No | No | Yes, single hop |
| **`stand(anchor, offset=[…])`** | Go to a specific pose relative to anchor (not "above the stack") | No | No | Yes, single hop |
| **`immerse(anchor, dist=N)`** | Holding a load (tip/needle), dip `N` mm into a container | Yes (at depth) | No | First above-leg yes, dive-leg no |
| **`retract(anchor, dist=N)`** | Inverse of `immerse` — lift the held load out | No | No | Off by default |
| **`rotate(rotation, joint)`** | Spin one joint without changing Cartesian pose | — | — | No |
| **`vibrate(pattern)`** | Small back-and-forth Cartesian oscillation (shake tip free, mix) | — | — | No |
| **`park(joint)`** | Move to a known safe pose (typically from `trigger="park"` action) | — | — | If `has_motion_plan=True` |

**Rule of thumb**:
- Gripper needs to act? → `pick` / `place`.
- Pre-positioning point? → `above` (simple) or `stand` (arbitrary offset).
- Liquid interaction? → `immerse` / `retract`.
- Joint-level motion only? → `rotate` / `vibrate`.
- End-of-run cleanup? → `park`.

If none fit, write a subclass that overrides `pick`/`place` with a
custom `approach_path` or `exit_path` — see Adapter, Hotel, Decapper
for canonical patterns (§8).

### `pick`

**What it does**: drives the robot to an anchor, closes the gripper
on whatever's there, attaches the picked item to the tool in the
kinematic tree, and exits straight up. One method, complete
pick cycle.

**Use when**: any time you need the robot to carry a physical item
from a station to anywhere else. Pick is the FROM end of every
transport.

**Required**: a tool must be attached to the robot (raises
`RecipeError` otherwise). The item to pick is found automatically —
the recipe walks the kinematic tree from `component.assembly[solid_name]`
at the named anchor and picks up whatever stack is there.

**Key parameters**:

| Param | What |
|---|---|
| `anchor` (required) | Target anchor name on the component (`"A1"`, `"place"`, `"slot_3"`, etc.) |
| `solid_name` | Which sub-solid owns the anchor (default `"body"`) |
| `component` | Override the recipe's default component — for racks-on-adapters and similar indirection |
| `padding` | Safe-height above the target stack (mm) for both the approach hop and the exit |
| `gap` | Clearance above the load used as the soft-approach waypoint (mm) |
| `soft_approach` | If `True`, insert a second approach waypoint just above the load for a vertical final descent. **Recommended for racks** — the straight-down move avoids hitting neighbouring slots |
| `tool_tcp_z_offset` | Shift the TCP along the **tool's Z axis** (mm). Tool Z typically points into the workpiece, so **negative = drive deeper** — `-5` for suction cups that need to seat, `-2` for decappers that engage cap threads |
| `tool_tip_z_offset` | Shift the tool tip along the **tool's Z axis** (mm). Same sign convention — negative = deeper. Affects load-height math without changing TCP |
| `trigger_io` | If `True`, build the gripper enable / component disable IO sequences automatically (default `True`). Set `False` when the recipe handles IO itself |
| `attachment` | If `True`, attach the picked solid to the tool on touch-down (default `True`). Set `False` for "pretend pick" workflows like inspection |
| `actions` | List of `(callable, args, kwargs)` to run during the touch phase — used for sensor reads, custom IO, etc. |

**Examples**:

```python
rcp["tube_rack"].pick(anchor="A1")
# Simplest case: 50mm padding, no soft approach, tool's natural TCP

rcp["tube_rack"].pick(anchor="A1", soft_approach=True)
# Recommended for any dense rack — vertical final descent

rcp["tube_rack"].pick(anchor="A1", tool_tcp_z_offset=-5)
# Suction cup that needs 5mm extra depth to seat

rcp["tube_rack"].pick(anchor="A1", speed_factor=0.2)
# Slow this pick (but see §7.5 — speed_factor sticks for next calls too)

rcp["adapter_plate"].pick(anchor="A3", component=workspace.components["rack_falcon_15ml_1"])
# Adapter holding a rack — target the rack's A3, not the adapter's
```

**Gotchas**:
- No tool attached → `RecipeError("no tool attached to the robot")`.
- Anchor doesn't exist on the named solid → `RecipeError("could not find a valid pose")` from the IK solver.
- The picked item is whatever's at the anchor's stack — if the operator forgot to load it, you'll grip empty air silently. Defend with a `pre_check` in your BT action.

---

### `place`

**What it does**: mirror of `pick`. Drives the robot to a destination
anchor with the held item, releases the gripper, detaches the item
to the destination in the kinematic tree, and exits straight up.

**Use when**: any time the robot needs to deposit a held item at a
new location. Place is the TO end of every transport.

**Required**: the robot must already be holding an item (raises
`RecipeError("no item in the gripper")` otherwise). The held item is
found via `solid_attached_to_tool` — wherever the kinematic tree
says the load is.

**Key parameters** (in addition to pick's common ones):

| Param | What |
|---|---|
| `offset` | XYZ-ABC offset applied to the target pose (frame transformation) — useful for placing slightly off-anchor without redefining the anchor |
| `gravity_offset` | Z-offset at touch-down (mm). **Positive = release slightly above the target** (typical for 2/4-finger grippers — let gravity finish the placement). **Negative = drive deeper** (suction cups with mechanical leveler). Default `1` mm |
| `load_anchor` | Which anchor on the held item is used as its reference point (default `"center"`) |

**Examples**:

```python
rcp["tube_rack"].place(anchor="B2")
# Simplest case: release 1mm above target, no soft approach

rcp["tube_rack"].place(anchor="B2", soft_approach=True)
# Rack placement — vertical final descent

rcp["tube_rack"].place(anchor="B2", gravity_offset=-10)
# Suction with elbow — needs to push 10mm down to release cleanly

rcp["holder"].place(anchor="slot", offset=[0, 0, 5, 0, 0, 0])
# Place 5mm above the slot center (the slot's natural target)
```

**Gotchas**:
- Robot not holding anything → `RecipeError("no item in the gripper")`.
- Destination anchor already occupied → no automatic check; the new item gets attached on top of the existing stack. If you need exclusivity, check via `solid_attached_to_anchor` first.
- `gravity_offset` is in the **target frame** (after rotation). A 180° rotation flips Z, so positive `gravity_offset` may drive deeper in the world frame. Validate on a real bench.

---

### `above`

**What it does**: hover `padding` mm above the anchor (or above the
stack at the anchor, if something's already there). No touch-down,
no IO, no attach. Pure positioning.

**Use when**: a pre-positioning step before manual operator work,
camera inspection, or before a `pick`/`place` that needs the
operator to confirm visually first.

**How it differs from `stand`**: `above` uses the same height math
as `pick_setting` (the `pose_offset` accounts for what's stacked at
the anchor). It correctly hovers above the actual top of the stack,
not the bare anchor.

**Key parameters**:

| Param | What |
|---|---|
| `anchor` (required) | Anchor on the component |
| `padding` | Height above the stack-top (mm). Default `50` |
| `solid_name`, `component` | Same as `pick` |
| `tool_tcp_z_offset`, `tool_tip_z_offset` | Tool Z shifts — propagated through |

**Examples**:

```python
rcp["inspector_1"].above("place", padding=80)
# 80mm above whatever's sitting at the inspector — useful for camera
# capture with adjustable working distance.

rcp["tube_rack"].above("A1")
# Standard 50mm above the tube at A1 — pre-positioning for a
# subsequent operator manual check.
```

**Gotcha**: planning DOES run (single hop, planned). If you want a
direct jmove without planning, pass `has_motion_plan=False`.

---

### `stand`

**What it does**: move to an arbitrary offset in the anchor's local
frame. Pure positioning — no touch, no IO, no attach.

**Use when**: you need a specific pose that's NOT "above the stack"
— e.g. 10 mm to the right of an anchor, or rotated 45° about C, or
at the anchor itself with a custom angle. `above` covers the
"hover" case; `stand` covers everything else.

**Key parameters**:

| Param | What |
|---|---|
| `anchor` (required) | Anchor on the component |
| `offset` | `[x, y, z, a, b, c]` in the anchor's local frame. XYZ in mm; ABC is a rotation vector in **degrees**. Default `[0,0,0,0,0,0]` (stand exactly at the anchor) |
| `solid_name`, `component` | Same as `pick` |

**Examples**:

```python
rcp["inspector_1"].stand("place", offset=[0, 0, 30, 0, 0, 0])
# 30mm directly above the anchor, same orientation

rcp["inspector_1"].stand("place", offset=[10, 0, 50, 0, 0, 45])
# +10mm X, +50mm Z, rotated 45° about C-axis

rcp["robot"].stand("home", offset=[0, 0, 0, 0, 0, 0])
# Sit exactly at the home anchor
```

**Frame of reference for `offset`** — read this carefully:

`offset` is interpreted in the frame returned by `pick_setting` as
`pose_offset`. That frame is:

- **The anchor's own frame** when nothing is stacked at the
  anchor. XYZ from the anchor itself, ABC rotates relative to it.
  This is the common case.
- **The load's `center` frame** when something IS stacked at the
  anchor. If the load was placed without rotation (typical — a tube
  sitting straight up in a slot), the load's frame and the anchor's
  frame align, so the offset behaves identically. **But** if the
  load was placed with a rotation (e.g. a tube held at a 30° tilt),
  the offset rides the load's tilted axes, NOT the anchor's. Same
  call, different world-frame result depending on what's there.

99% of layouts place loads center-aligned with their anchor, so
treating `offset` as "the anchor's local frame" is a safe mental
model. The 1% case (rotated mount) is worth knowing.

---

### `immerse`

**What it does**: lower the held load (pipette tip, needle, dispense
nozzle) `dist` mm below the surface at the anchor. The held item's
**tip** is what reaches `-dist` — the math accounts for the load's
height so a 30 mm long tip and a 100 mm long needle both dip the
same `dist` mm beneath the surface.

**Use when**: aspirating liquid, dispensing into a tube, dipping a
probe for measurement, anything that ends with the tool's tip below
the anchor's surface.

**Required**: the robot must be holding a load (raises
`RecipeError("no tool attached")` if no tool, OR auto-discovers no
load and uses tool-only depths).

**Two patterns** (selected via `approach=` parameter):

| Pattern | Use when | How it moves |
|---|---|---|
| `approach=False` (default) | Deep dips, fragile containers | **Two-phase**: first `above()` at container top (depth-independent — safe), then `pick(approach=False)` straight down at the target depth. Reduces sideways approach risk. |
| `approach=True` | Shallow dips, fast workflows | **Single-phase**: `pick(approach=True)` with the depth offset baked into the corridor. Faster — one continuous motion. |

**Key parameters**:

| Param | What |
|---|---|
| `dist` | Depth below the anchor surface (mm). `0` = tip touches surface. Positive values go deeper |
| `anchor` | Target anchor (default `"place"`) |
| `approach` | Pattern selector (see above). Default `False` |
| `padding` | Safe height above target (mm). Default `10` |
| `exit`, `attachment`, `trigger_io` | All default `False` — `immerse` is a "deposit but don't release" operation |

**Examples**:

```python
rcp["doser"].immerse(dist=10)
# Two-phase: hover at top, then dive 10mm below surface

rcp["pipetting_site"].immerse(dist=5, approach=True)
# Single-phase: more efficient for shallow

rcp["doser"].immerse(dist=20, padding=30)
# Deeper dip with extra hover clearance
```

**Gotcha**: `approach=True` requires `padding` to comfortably exceed
the load height; otherwise the corridor descent fails IK. Two-phase
is safer when you're unsure.

---

### `retract`

**What it does**: inverse of `immerse`. Lifts the held load `dist`
mm above the anchor's surface (load's tip ends up `dist` mm above
the surface, not the load's center).

**Use when**: pull out after aspirating, clear out before moving to
the next station.

**Key parameters**:

| Param | What |
|---|---|
| `dist` | Extra lift above the natural load-height clearance (mm) |
| `anchor` | Reference anchor (default `"place"`) |
| `padding` | Extra padding applied by `above` (mm). Default `0` |
| `has_motion_plan` | Default `False` — direct jmove, no planning |

**Examples**:

```python
rcp["doser"].retract(dist=20)
# Lift tip to 20mm above the surface

rcp["pipetting_site"].retract(dist=10, padding=20)
# Lift 10mm + extra 20mm padding — useful when next motion needs clearance
```

**Gotcha**: planning is **off by default** (the lift is straight up
and rarely needs planning). If you have obstacles above, pass
`has_motion_plan=True` explicitly.

---

### `rotate`

**What it does**: spin one joint by a relative number of degrees.
Other joints stay where they are. No Cartesian motion planning —
direct joint-space jmove.

**Use when**: flipping the wrist (`j5`) to reorient a camera, mild
re-orientation between operations, joint-level shake.

**Key parameters**:

| Param | What |
|---|---|
| `rotation` | Degrees to add to the current joint value (can be negative). Default `90` |
| `joint` | Identifier: `"j5"` / `"J5"` / `5` (string or int). Default `"j5"` |
| `limit` | `[min, max]` joint range used for wrap-around. Default `[-175, 175]` |
| `vaj` | `[velocity, accel, jerk]` for the jmove. Default `[500, 3000, 15000]` |

The result wraps within `limit` — if `current + rotation` would
exceed the range, it wraps to the other side of the limit window
rather than refusing.

**Examples**:

```python
rcp["robot"].rotate(rotation=180, joint="j5")
# Flip the wrist 180°

rcp["robot"].rotate(rotation=45, joint=5)
# Same joint, integer form

rcp["robot"].rotate(rotation=-30, joint="j4", limit=[-90, 90])
# Joint 4 by -30°, with a tighter wrap range
```

**Gotchas**:
- Invalid joint string (`"jx"`, `"5"`, etc.) → `RecipeError` with a
  clear "invalid joint" message.
- Joint index beyond the robot's joint count → `RecipeError`.
- `rotate` does NOT plan — collisions are caller's responsibility.

---

### `vibrate`

**What it does**: oscillate the robot flange through a small list of
Cartesian offsets, repeated `cnt` times, then return to the starting
joint configuration.

**Use when**: shake a stuck tip free, loosen a friction seal, mix
liquid in a tube, settle a powder.

**Key parameters**:

| Param | What |
|---|---|
| `pattern` | List of `[x, y, z]` offsets in the flange's output frame (mm). The robot sweeps through them in order. Default `[[2.5,0,0], [-2.5,0,0]]` (5 mm peak-to-peak shake along X) |
| `cnt` | Repeat count. Default `5` |
| `vaj` | `[velocity, accel, jerk]` for each step. Default `[300, 10000, 20000]` (high jerk for the snappy feel) |

**Examples**:

```python
rcp["robot"].vibrate(pattern=[[3,0,0],[-3,0,0]], cnt=10)
# 6mm peak-to-peak shake along X, 10 times

rcp["pipetting_site"].vibrate(pattern=[[0,0,1],[0,0,-1]], cnt=20)
# Vertical micro-shake — useful for tip release
```

**Gotchas**:
- `pattern` is in the **flange output frame** — not the world frame
  or the tool frame. If the wrist is at 90° to vertical, an `[x, 0, 0]`
  pattern shakes the tool sideways, not forward.
- IK failure on any waypoint → `RecipeError`.

---

### `park`

**What it does**: move the robot to a known joint configuration —
typically a safe parking pose at end of run. Goes through pause
checkpoint + supports motion planning for collision avoidance on the
way home.

**Use when**: `trigger="park"` action / end-of-workflow cleanup.

**Key parameters**:

| Param | What |
|---|---|
| `joint` (required) | Target joint vector (degrees). **May be shorter than the robot's full joint vector** — the missing trailing entries get filled from `rt.joint()` so auxiliary axes (rail, second rail) stay put |
| `has_motion_plan` | `True` plans a collision-free path; `False` is a single jmove. Default = `core.has_motion_plan` |
| `motion_plan_kwargs` | Forwarded to `core.motion_plan` (padding, gravity_vec, etc.) when planning is on |

The partial-vector behaviour is important: if your robot has 6 joints
+ a rail (axis 6) + a second rail (axis 7), and you pass
`park(joint=[0, 0, 90, 0, 90, 0])` (6 entries), the rails are not
touched — they stay where the workflow left them.

**Examples**:

```python
rcp["robot"].park(joint=[0, 0, 90, 0, 90, 0])
# 6 joints — rail/aux axes unchanged

rcp["robot"].park(joint=PARK_JOINTS, has_motion_plan=True,
                   motion_plan_kwargs={"padding": 30})
# Plan around obstacles with 30mm padding
```

**Gotcha**: pause-aware — operator can Pause mid-park, then Resume
to continue the trip home. Don't use park during a destructive
sequence (cap unscrewing mid-motion) — finish first, park after.

---

### `touch` (direct use)

**What it does**: the universal motion primitive everything else
calls. Walks the 8-step flow: approach IO → approach path → touch
IO → sleep + actions → attach → exit path → exit IO.

**Use when**: almost never directly. Only when your recipe needs a
motion shape that `pick_setting` / `place_setting` can't express
— e.g. tool-changer mechanisms with non-standard approach
corridors.

**The canonical example** is `ToolRack.pick` / `ToolRack.place`:
the swap path is unique to the pneumatic tool-changer mechanism, so
both methods hand-build the `motion_prm` dict and call `touch`
directly rather than going through `pick_setting`.

**Signature** (abbreviated — full doc in `touch`'s docstring):

```python
self.touch(
    target_solid=...,           # solid that owns the target anchor
    target_anchor=...,          # anchor name
    target_offset=[...],        # final touch-down offset (None to skip)
    output_approach=[...],      # IO list applied before approach
    approach_tool={...},        # {solid, anchor, offset} for the approach pose
    approach_path=[...],        # list of waypoints before touch
    approach_j5=None,           # j5 override for the approach
    output_touch=[...],         # IO list applied at touch-down
    actions=[(fn, args, kwargs), ...],  # callable list at touch
    sleep=0,                    # sleep at touch (pause-aware via rt.delay)
    attach=[child, {parent, ...}],  # solid attachment spec
    exit_tool={...},            # {solid, anchor, offset} for the exit pose
    exit_path=[...],            # list of waypoints after touch
    exit_j5=None,               # j5 override for the exit
    output_exit=[...],          # IO list applied after exit
    has_motion_plan=None,       # default = core.has_motion_plan
    motion_plan_kwargs={},      # forwarded to core.motion_plan
)
```

**Returns** `True` on success.

**When to read `touch`'s source vs use the helpers**: if you find
yourself building waypoint lists or output configs manually, you're
either doing something exotic (ToolRack-style) or you've missed a
parameter on `pick_setting`/`place_setting`. Re-read those first.

---

## 5. Calibration flow

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

## 6. `recipes.yaml` — configuration format

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

## 7. Conventions

Every recipe is expected to follow these. The framework enforces some
mechanically (sim-agnostic via the architecture); others are pure
convention authors must respect.

### 7.1 Sim-agnostic

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

### 7.2 DEFAULTS merge pattern

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

### 7.3 Pause-awareness — always via `rt.*`

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

### 7.4 Component-vs-recipe ownership

If you find yourself writing a method that:
- Touches one device, one IO call, no motion — it belongs on the
  **component**.
- Combines multiple atomic ops, motion, sensing — it belongs on the
  **recipe**.

Full rule + Feeder example in
[component-guide.md §7](component-guide.md).

### 7.5 The `**kwargs` override behavior — read this carefully

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

## 8. Creating your own recipe

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

## 9. Catalog

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
- **`DosingSite`** ([doser.py](../workspace/workspace/recipes/doser.py))
  — dosing-site holder. Resolves the attached plate, provides
  `immerse` / `retract` wrappers. Placeholder `aspirate` / `dispense`
  methods (real device not yet wired).
- **`PipettingSite`** ([pipetting.py](../workspace/workspace/recipes/pipetting.py))
  — pipette tip exchange + liquid handling. `pick_tip`, `eject_tip`
  (with shake + tip-presence verification), `immerse`, `aspirate`,
  `dispense`. **Note**: currently has sim-flag checks inline that
  belong in the component constructor — pending cleanup.
- **`DispenseArm`** ([dispense_arm.py](../workspace/workspace/recipes/dispense_arm.py))
  — pneumatic dispense arm. `down` / `up` / `dispense` (sleep-based).

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
