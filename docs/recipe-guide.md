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
| `above(anchor, ...)` | Move above a position without touching |
| `rotate(rotation, ...)` | Rotate J5 by degrees |
| `vibrate(pattern, ...)` | Execute vibration pattern |
| `immerse(anchor, depth, ...)` | Lower into a container to a specific depth |
| `retract(anchor, ...)` | Lift out of a container |
| `calibrate()` | Run calibration for configured anchors |

---

## 4. Creating a custom recipe

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

## 5. The DEFAULTS pattern

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

## 6. Using `rt` in recipes

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

## 7. Full example

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

    def make(self):
        return {
            "dispensed": self.dispensed,
            "picked": self.picked,
        }
```
