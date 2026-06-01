# Recipe Guide

How to create custom recipes for your project.

A recipe wraps a component with high-level actions — pick, place, dose, cap, inspect, etc. It knows how to move the robot to the right positions and handle tool attachments. Your states call recipe methods to get work done.

---

## 1. How recipes work

A recipe connects three things:

| What | Description |
|------|-------------|
| **Component** | The physical hardware from the scene (rack, gripper, decapper, etc.) |
| **Runtime (`rt`)** | Pause/stop-aware robot API — handles checkpoints, alarms, and motion |
| **Core** | Inverse kinematics, calibration, and motion planning |

You define recipes in `recipes.yaml` (or `recipes.j2`), mapping aliases to recipe classes. States use the alias to access the recipe: `self.rcp["gripper"].pick(i)`.

> **Recipes are sim-agnostic.** A recipe never branches on
> `simulation:` and never calls something like
> `if core._simulation_mode: ...`. The component constructor picks
> the right underlying API once (real driver vs. sim stub) and
> exposes a unified interface — recipe methods like `pick`,
> `place`, `dose` call `core.jmove(...)`, `core.vision.snapshot()`,
> `printer.print(...)` etc., and the same recipe code runs against
> real hardware or a sim with no edits. See
> [device-guide.md §10.5](device-guide.md#105-where-the-sim-ifelse-lives--component-not-recipe)
> for the full rule and the pattern new devices must follow.

---

## 2. `recipes.yaml` format

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
    volume: 500
```

| Field | Description |
|-------|-------------|
| Top-level key | Alias — used in states as `self.rcp["alias"]` |
| `class` | Fully qualified import path to the recipe class |
| `kwargs.component` | Required — component name from `scene/base.j2` |
| Other `kwargs` | Passed to the recipe constructor, overrides DEFAULTS |

For library recipes, the path starts with `workspace.recipes.`. For project-local recipes, use a local import path like `recipes.dispenser.Dispenser`.

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

## 3. Built-in recipes

| Recipe | Import | Use for |
|--------|--------|---------|
| `Recipe` | `workspace.recipes.recipe.Recipe` | Base class — pick, place, above, rotate, vibrate, immerse, retract |
| `Rack` | `workspace.recipes.rack.Rack` | Racks with grid positions (pick/place by anchor like `"A3"`) |
| `ToolRack` | `workspace.recipes.tool_rack.ToolRack` | Tool holder stands (pick/place tools) |
| `Decapper` | `workspace.recipes.decapper.Decapper` | Cap/decap with rotation |
| `PipettingSite` | `workspace.recipes.pipetting.PipettingSite` | Tip pick/eject, immerse/aspirate/dispense |
| `FixedInspector` | `workspace.recipes.inspector.FixedInspector` | Camera inspection with rotate/detect |
| `Printer` | `workspace.recipes.printer.Printer` | Label printing with print/dry-run |
| `Shaker` | `workspace.recipes.shaker.Shaker` | Background shaking with timer |
| `DispenseArm` | `workspace.recipes.dispense_arm.DispenseArm` | IO-controlled dispensing arm |
| `Hotel` | `workspace.recipes.hotel.Hotel` | Multi-level storage pick/place |
| `Scale` | `workspace.recipes.scale.Scale` | Weighing station |
| `Doser` | `workspace.recipes.doser.Doser` | Needle-based dosing |

### Key methods on `Recipe` (base class)

| Method | Description |
|--------|-------------|
| `pick(anchor, ...)` | Approach, grip, lift — attaches solid to tool |
| `place(anchor, ...)` | Approach, release, exit — detaches solid |
| `above(anchor, padding=...)` | Hover `padding` mm above anchor, no touch |
| `stand(anchor, offset=[x,y,z,a,b,c])` | Move to an arbitrary offset in the anchor frame |
| `rotate(rotation, joint, ...)` | Rotate a single joint by degrees |
| `vibrate(pattern, ...)` | Oscillate the flange through small Cartesian offsets |
| `immerse(anchor, dist, ...)` | Dip the held load `dist` mm into a container |
| `retract(anchor, dist, ...)` | Lift the held load out of a container |
| `calibrate()` | Run guided calibration on configured anchors |

For exact signatures and per-parameter details, call `help(Recipe.<method>)` or read the docstrings in [recipe.py](../workspace/workspace/recipes/recipe.py).

---

## 4. Motion primitives — when to use what

Several recipe methods all result in robot motion, but they differ in *what they assume, what they do at the target, and how much path planning they invoke.* Pick the one that matches your intent — don't force a harder primitive to do a softer job.

| Primitive | Use when… | Touches target? | Attach/IO? | Planning? |
|---|---|---|---|---|
| **`pick(anchor)`** | You want to grip the item at an anchor and carry it away | Yes | Yes — attach load → tool, trigger gripper IO | Yes, on first approach hop |
| **`place(anchor)`** | You're holding an item and want to release it at an anchor | Yes | Yes — detach load → destination, trigger IO | Yes, on first approach hop |
| **`above(anchor, padding=N)`** | You want to hover `N` mm above the anchor — e.g. before manual work or camera inspection | No — target_offset is None | No | Yes, on the single hop |
| **`stand(anchor, offset=[x,y,z,a,b,c])`** | You want to go to a specific pose relative to the anchor — not a standard "above the stack" position | No | No | Yes, on the single hop |
| **`immerse(anchor, dist=N)`** | You're holding a load (tip, needle) and want to dip it `N` mm into a container | Yes (at computed depth) | No | Yes on the above-leg, no on the dive-leg |
| **`retract(anchor, dist=N)`** | Inverse of `immerse` — lift the held load out | No | No | Off by default |
| **`rotate(rotation, joint)`** | Spin one joint without changing Cartesian pose — e.g. rotate j5 to flip a camera view | — | — | No |
| **`vibrate(pattern)`** | Small back-and-forth Cartesian oscillation — shaking a tip free, mixing | — | — | No |

**Rule of thumb:**
- Need the gripper to act? → `pick` / `place`.
- Need a safe pre-positioning point? → `above` (simple) or `stand` (arbitrary offset).
- Need to interact with liquid? → `immerse` / `retract`.
- Need joint-level motion, not pose-level? → `rotate` / `vibrate`.

If none of these fit, write a subclass that overrides `pick`/`place` with a custom `approach_path` or `exit_path` (see `adapter.py`, `hotel.py`, `decapper.py`).

---

## 5. How motion is built — `pose_offset`, approach path, and planning

Every pick/place in the framework goes through the same pipeline:

```
pick_setting/place_setting   →   touch   →   _move_along_path → smove | jmove | lmove
      ↑ compute waypoints          ↑ execute       ↑ step by step
```

Understanding three concepts unlocks most customization.

### `pose_offset` — the anchor-local frame

Returned by `pick_setting` in its output dict. It's a `Pose` that represents "the natural reference point at this anchor" — typically the **center of whatever is already stacked there** (e.g. a tube sitting in a rack slot), or the anchor itself if nothing is stacked.

When you call `pose_offset.pose(offset=[x, y, z, a, b, c])`, the framework transforms that offset into the anchor's frame and returns a world-ready pose. This is how `above(padding=50)` works transparently whether the slot is empty or has a tall stack — `pose_offset` absorbs the height math.

`stand(anchor, offset=...)` exposes this directly: the `offset` you pass is in the same frame that `pick_setting` uses internally.

### Approach path vs target offset

- **`approach_path`** — list of waypoints to pre-position the tool above/near the target. Built by `pick_setting` from `padding` and `gap` (and optionally `soft_approach`).
- **`target_offset`** — the exact final touch-down pose. For `pick`/`place` it's the anchor itself (transformed via `pose_offset`). For `above`/`stand` it's set to `None` so the motion stops at the single waypoint.

Full path consumed by `touch` is `approach_path + [target_offset]` (unless `target_offset` is None).

### Path planning — `has_motion_plan` and `first_approach`

Planning runs **only on the first hop of an approach path**. The gate inside `_move_along_path`:

```python
if i == 0 and first_approach:
    _execute_motion_planned(...)   # smove along planned waypoints OR jmove
else:
    _do_motion(...)                # jmove or lmove based on self.motion_type
```

Two flags control this:

- **`has_motion_plan`** — enabled on `core` by default. Overridable per call via `has_motion_plan=False`.
- **`first_approach`** — internal boolean, True only when `touch` is running an approach path with at least one waypoint. Exit paths and direct `approach=False` calls bypass planning entirely.

So:

| Scenario | Planning runs? |
|---|---|
| `pick(...)`, `place(...)`, `above(...)`, `stand(...)` (with core planning on) | Yes — on the first hop |
| `pick(approach=False, ...)` | No — no approach waypoint exists |
| Exit from a pick/place | No — exit path always uses direct moves |
| `retract(...)` | No — `has_motion_plan=False` by default |
| `rotate(...)`, `vibrate(...)` | No — they issue jmoves directly |

`lmove` (Cartesian straight line) is used only for subsequent waypoints if `self.motion_type == "lmove"`, never on the first planned hop.

For most recipes you never touch these flags. When you do need them:
- Pass `has_motion_plan=True/False` as a kwarg to override the core default.
- Pass `motion_plan_kwargs={...}` to forward extra args (padding, gravity_vec) to `core.motion_plan`.

### Parameter guidelines

For numeric parameters like `padding`, `gravity_offset`, `soft_approach`, `tool_tcp_z_offset` — see [parameter-guidelines.md](parameter-guidelines.md) for rules of thumb and gripper-specific recommendations.

---

## 6. Creating a custom recipe

### Inheriting from Recipe

Most custom recipes inherit from `Recipe` to get pick/place/motion and add domain-specific methods:

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

### From scratch (no base class)

For simple IO-only devices that don't need robot motion:

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

### Where to put it

Place custom recipes in a `recipes/` folder in your project:

```
my_project/
├── recipes/
│   ├── __init__.py           # empty file — required for Python imports
│   └── dispenser.py
├── recipes.yaml
├── main.py
└── ...
```

Reference in `recipes.yaml` with a local import path:

```yaml
my_dispenser:
  class: recipes.dispenser.Dispenser
  kwargs:
    component: my_dispenser_1
    dispense_volume: 750
```

No changes to `main.py` needed — the recipe loader uses `importlib` which resolves local imports automatically since `main.py` runs from the project directory.

---

## 7. The DEFAULTS pattern

Recipes use a merge chain to resolve parameters:

```
Recipe.DEFAULTS → YourClass.DEFAULTS → kwargs from recipes.yaml
```

Later values override earlier ones. This lets you:

- Set sensible defaults in your class
- Override per-instance in `recipes.yaml`

```python
class MyRecipe(Recipe):
    DEFAULTS = dict(
        base_distance=200,        # default approach distance
        speed_factor=1.0,         # default speed
    )

    def __init__(self, workspace, core, component, **kwargs):
        prm = deepcopy(Recipe.DEFAULTS)
        merge(prm, self.DEFAULTS)   # your defaults override base
        merge(prm, kwargs)           # yaml overrides yours
        super().__init__(workspace=workspace, core=core, component=component, **prm)
```

```yaml
# recipes.yaml — override speed_factor for this specific instance
my_recipe:
  class: recipes.my_recipe.MyRecipe
  kwargs:
    component: some_component
    speed_factor: 0.5             # overrides the default 1.0
```

---

## 8. Using `rt` in recipes

Always use `self.rt` for robot commands and timing — it handles pause, stop, and alarms automatically:

| Method | Description |
|--------|-------------|
| `rt.checkpoint()` | Blocks if paused, raises if killed — call before any action |
| `rt.delay(seconds)` | Interruptible sleep |
| `rt.output(config=...)` | Set IO outputs (gripper, valve, etc.) |
| `rt.step("message")` | Log a step to the GUI timeline |
| `rt.jmove(...)` | Joint move — auto-wrapped with checkpoint and alarm handling |
| `rt.lmove(...)` | Linear move — same auto-wrapping |

---

## 9. Full example

```
my_project/
├── recipes/
│   ├── __init__.py
│   └── dispenser.py
├── recipes.yaml
├── states.py
└── main.py
```

**`recipes/dispenser.py`** — see section 4 for the full class.

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

**`states.py`** — use both library and custom recipes:
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
