# Component Guide

How to add custom components to your project.

A component is a physical object in the scene — a rack, tool, holder, or any hardware. It needs a 3D model, anchor points, and a Python class.

---

## 1. What you need

| File | Location | Purpose |
|------|----------|---------|
| GLB model | `my_project/CAD/{type_name}.glb` | 3D visualization |
| Python class | `my_project/components/{name}.py` | Anchors, collision boxes, type registration |
| Scene entry | `my_project/scene/base.j2` | Places the component in the workspace |

---

## 2. GLB model

Export your CAD as a `.glb` file (binary glTF) and place it in your project:

```
my_project/CAD/custom_holder.glb
```

The filename must match the registered type name exactly — `@register("custom_holder")` expects `custom_holder.glb`. The system checks your project folder first, then falls back to built-in library models.

**Guidelines:**
- Origin `(0, 0, 0)` at the natural center/base of the component
- Units: millimeters
- Z-axis points up
- Keep the model lightweight for faster rendering

---

## 3. Python class

### Inheriting from a library base class

Most custom components are variants of existing types. Inherit from a base class to get shared logic and override only what's different:

```python
from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from workspace.components.rack.rack import Rack

@register("rack_custom_24")
class RackCustom24(Rack):
    DEFAULTS = dict(
        anchors={
            "body": {
                "center": [0, 0, 0, 0, 0, 0],
                "place":  [0, 0, 5, 0, 0, 0],
                "top":    [0, 0, 60, 0, 0, 0],
            }
        },
        collision_box={
            "body": [
                {"pose": [0, 0, 30, 0, 0, 0], "scale": [120, 80, 60]}
            ]
        },
        offset=[0, 0],
        pitch=[20, 20],
        rows=["A", "B", "C", "D"],
        cols=[1, 2, 3, 4, 5, 6],
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(Rack.DEFAULTS)
        merge(prm, self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))
        super().__init__(name=name, workspace=workspace, **prm)
```

**Available base classes:**

| Base class | Use for | Import |
|------------|---------|--------|
| `Rack` | Racks, plates, holders with grid positions | `from workspace.components.rack.rack import Rack` |
| `ToolRack` | Tool holder stands | `from workspace.components.tool_rack.tool_rack import ToolRack` |
| `Gripper` | Gripping tools | `from workspace.components.gripper.gripper import Gripper` |
| `Cap` | Caps and lids | `from workspace.components.cap.cap import Cap` |
| `Tube` | Tubes and vials | `from workspace.components.tube.tube import Tube` |
| `Adapter` | Adapter plates | `from workspace.components.adapter.adapter import Adapter` |

### From scratch (no base class)

If your component doesn't fit any existing type, build it directly from `Solid`:

```python
from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register

@register("custom_holder")
class CustomHolder:
    DEFAULTS = dict(
        anchors={
            "body": {
                "center": [0, 0, 0, 0, 0, 0],
                "place":  [0, 0, 10, 0, 0, 0],
                "top":    [0, 0, 25, 0, 0, 0],
                "slot_0": [20, 0, 10, 0, 0, 0],
                "slot_1": [-20, 0, 10, 0, 0, 0],
            }
        },
        collision_box={
            "body": [
                {"pose": [0, 0, 5, 0, 0, 0], "scale": [80, 40, 10]}
            ]
        },
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name = name
        self.workspace = workspace
        self.type = prm.get("type")

        self.assembly = {
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {})
            ) for k in prm["anchors"]
        }
```

### Where to put it

Place component files in `components/` and import in `main.py` before the workspace loads:

```
my_project/
├── components/
│   └── custom_holder.py
├── main.py
└── ...
```

```python
from components.custom_holder import CustomHolder  # triggers @register

from workspace.workspace import Workspace
# ... rest of main.py
```

---

## 4. Anchors

Anchors are named 6-DOF poses `[x, y, z, rx, ry, rz]`:

- `x, y, z` — position in millimeters, relative to the component origin
- `rx, ry, rz` — rotation vector in degrees (axis-angle representation, not Euler angles)

### Anchor names

| Anchor | Required | Description |
|--------|----------|-------------|
| `center` | Yes | Component origin — always `[0, 0, 0, 0, 0, 0]`. Where it connects to its parent. |
| `place` | Yes | Where a child component connects to this component |
| `top` | Yes | Highest point on the component — typically used for gripping |
| `hole_0`, `hole_1`, ... | No | Mounting holes for attaching to fixture plates |
| `clb_0`, `clb_1`, ... | No | Reserved prefix — calibration points used by the calibration system |
| Custom names | No | Any additional anchors for your component (e.g. `slot_0`, `nozzle`) |

### Example

```python
anchors={
    "body": {
        "center": [0, 0, 0, 0, 0, 0],
        "place":  [0, 0, 5, 0, 0, 0],
        "top":    [0, 0, 60, 0, 0, 0],
        "hole_0": [25, 25, 0, 0, 0, 0],
        "hole_1": [-25, 25, 0, 0, 0, 0],
    }
}
```

---

## 5. Collision boxes

Collision boxes define the physical boundaries used for safety checks. Each box is axis-aligned with a pose and a scale. The workspace uses them to detect self-collisions and collisions with the environment during motion planning.

```python
collision_box={
    "body": [
        {"pose": [0, 0, 30, 0, 0, 0], "scale": [120, 80, 60]},
    ]
}
```

| Property | Format | Description |
|----------|--------|-------------|
| `pose` | `[x, y, z, a, b, c]` | Position and orientation of the box center in the parent solid's local frame. The box is centered on `pose` — to sit a 60 mm tall box flat on the parent origin, its Z should be 30, not 0. `a, b, c` are rotations in degrees around the X, Y, and Z axes. Use rotation to tilt a box that doesn't align with the parent frame's axes. |
| `scale` | `[lx, ly, lz]` | Total dimensions of the box in millimeters: width (X), depth (Y), and height (Z). |

Prefer a single box, but combine several when the geometry needs it:

```python
collision_box={
    "body": [
        {"pose": [0, 0, 3, 0, 0, 0], "scale": [66, 66, 6]},       # base plate
        {"pose": [0, 0, 78, 0, 0, 0], "scale": [27, 27, 152]},     # vertical column
        {"pose": [0, 23, 145, 0, 0, 0], "scale": [54, 75, 20]},    # top bracket
    ]
}
```

---

## 6. Scene reference

Place the component in `scene/base.j2`. The top-level key (e.g. `my_holder`) is the component name — this is how you reference it in recipes and states:

```yaml
my_holder:
  type: custom_holder
  attach:
    parent_name: fixture_plate_0
    parent_solid: body
    parent_anchor: B5
    child_solid: body
    child_anchor: center
    offset: [0, 0, 0, 0, 0, 0]
```

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Registered type name — matches `@register("...")` and GLB filename |
| `attach` | No | How this component connects to a parent. Without it, placed at world origin. |
| `attach.parent_name` | Yes | Name of the parent component in the scene |
| `attach.parent_solid` | Yes | Solid name on the parent (usually `"body"`) |
| `attach.parent_anchor` | Yes | Anchor name on the parent solid |
| `attach.child_solid` | Yes | Solid name on this component (usually `"body"`) |
| `attach.child_anchor` | Yes | Anchor name on this component to align |
| `attach.offset` | No | `[x, y, z, rx, ry, rz]` fine-tuning offset |

Use Jinja2 loops to create multiple instances:

```yaml
{% for level in range(4) %}
plate_{{ level }}:
  type: rack_custom_24
  attach:
    parent_name: hotel_0
    parent_solid: body
    parent_anchor: place_{{ level }}
    child_solid: body
    child_anchor: center
{% endfor %}
```

---

## 7. Methods — what belongs on the component vs the recipe

A common trap is to put atomic device operations in the recipe ("`rotate_in_step` lives on the Feeder recipe, so does the math…"). That leads to:

- **Duplication** the moment you want the same operation called from a different recipe or directly from the UI
- **Operator buttons you can't expose** — `operator_actions` can only call methods on the **component**; anything in the recipe is locked out
- **Confusion when refactoring** ("should I call `self.method` or `self.component.method`?")

There's one rule that prevents all three:

> **Component owns the atomic device operation. Recipe owns the workflow that coordinates it.**

### The test question

When you write a new method, ask:

> *"Could the operator press one button to do this single thing in isolation?"*

| Answer | Where it goes |
|---|---|
| **Yes** | Method on the component class. Candidate for `operator_actions` (next section). |
| **No — it's a sequence / approach path / IK / sensor loop / multi-step choreography** | Method on the recipe. |

### Worked example — Feeder

Before refactoring, the Feeder recipe inlined the rotation math:

```python
# OLD — recipe/feeder.py (wrong place for the math)
class Feeder(Recipe):
    def rotate_in_step(self, step=1, **kwargs):
        current = self.rt.joint()
        axis = self.component.axis_cfg["axis"]
        current_steps = round((current[axis] - self.pick_offset) * (self.component.num_slots / 360))
        target = (step + current_steps) * (360 / self.component.num_slots) + self.pick_offset
        return self.rt.jmove(joint=..., vel=self.vaj_mix[0], ...)
```

This is one jmove — the operator could press an "Advance" button to do it in isolation → atomic → belongs on the component. After refactor:

```python
# NEW — components/feeder/feeder.py (atomic op lives here)
class Feeder:
    def rotate(self, step=1, vaj=None):
        rt = self.workspace.rt
        axis = self.axis_cfg["axis"]
        current = rt.joint()
        current_steps = round((current[axis] - self.pick_offset) * (self.num_slots / 360))
        new = current[:]
        new[axis] = (step + current_steps) * (360 / self.num_slots) + self.pick_offset
        rt.checkpoint()
        vaj = vaj or self.vaj
        return rt.jmove(joint=new, vel=vaj[0], accel=vaj[1], jerk=vaj[2])

    def advance(self): return self.rotate(+1)
    def reverse(self): return self.rotate(-1)
```

```python
# NEW — recipes/feeder.py (workflow keeps coordinating; delegates the motion)
class Feeder(Recipe):
    def rotate_in_step(self, step=1, **kwargs):
        # Override speed for slower mixing; grid snap lives on the component
        return self.component.rotate(step, vaj=self.vaj_mix)

    def mix(self):  ...   # tracks direction, calls rotate_in_step over multiple cycles
    def present_cap(self, inspector): ...   # sensor loop + recursion
```

`mix()` and `present_cap()` stay in the recipe — they're real coordination.

### What also moves with the operation

When you lift an atomic operation to the component, **any calibration data it needs goes with it**. In the Feeder case, `pick_offset` (where slot 0 sits in the robot's joint frame) moved from recipe config to component config — calibration belongs with the device, not with the workflow that uses it.

Recipe-level params stay in the recipe: `vaj_mix` (a slower workflow speed for agitation), `thr_dir` (mix-direction threshold), `shift_steps` (how many slots `mix()` advances per cycle).

### The signs you got it wrong

- The recipe has math that touches the device but doesn't reference any other recipe state
- You wanted to expose something as an operator button but couldn't because the implementation was in the recipe
- Two recipes use the same device differently and have nearly-identical helper methods

Any of those → lift the atomic op into the component.

---

## 8. Operator actions — exposing component methods as UI buttons

A component can declare methods that the **operator** should be able to
trigger from the UI — gripper enable/disable, decapper open, printer
cancel-job, fixture release-clamp, etc. The orchestrator scans every
component for an optional `operator_actions()` method and renders the
union as buttons in two surfaces, both labelled **"Operator Controls"**:

- **Sidebar "Operator Controls" section** — always visible; shows an
  empty-state message ("No operator actions declared") when no
  component contributes anything. Collapsible, grouped by component.
- **Pendant "Controls" button** in the secondary row → modal with the
  same component-grouped layout. Hidden when no component declares
  any actions (pendant row is too constrained to spend a tile on an
  empty surface).

Both surfaces gate the buttons by workflow state: **disabled while
RUNNING** (out-of-band ops mid-run would race the workflow), enabled
in IDLE / PAUSED / ERROR / NOT_LAUNCHED. The component never has to
enforce that — the orchestrator does.

This is a **component-level** concern, intentionally separate from the
device contract in [`device-guide.md`](device-guide.md). A fixture
with no device-bus dependency can still expose operator actions; a
device-backed component need not expose any.

### The contract

```python
class Gripper:
    def enable(self):
        self.workspace.rt.output(config=self.output_enable)

    def disable(self):
        self.workspace.rt.output(config=self.output_disable)

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Enable",  "method": "enable"},
            {"label": "Disable", "method": "disable"},
        ]
```

Each entry is a dict with two string fields:

- **`label`** — display name on the button
- **`method`** — name of a no-arg callable on the component
  (`getattr(component, method)()` must work)

The orchestrator silently drops entries whose `label` / `method` is
missing or whose `method` doesn't resolve to a callable — a malformed
entry can't crash the panel.

### Same code path as recipes use

The lifted `enable` / `disable` methods are also what recipes should
call internally for the same hardware operation — e.g. the decapper
recipe should call `self.component.open()` instead of inlining
`rt.output(config=tool.output_disable)`. **One named entry point, two
consumers**: recipe during the automated flow + operator button during
recovery / testing / setup.

### Two layers — component vs project

Some operator actions need workspace knowledge a single component
doesn't have ("move robot to *this project's* service position",
"park above the tool rack on *this* layout"). Those belong at the
project layer, not the component:

| Layer | Belongs here | Owner |
|---|---|---|
| Component | Device-local actions, no environment context (open, disable, eject, clear_jam) | The component class |
| Project | Actions that need workspace topology, collision boxes, currently-loaded tools | The project's `actions.py` or a sibling module |

(The project-level surface is reserved for future work — for now,
keep new actions on the component side and we'll wire the
project-level extension when a concrete use case lands.)

### Wire (for reference)

The runtime exposes the actions over a single WebSocket
(`/ws/operator_actions`) — both the list (server → client on
connect) and the invocation messages (client → server, with the
result pushed back). One pre-opened socket per workspace session, so
every button click is a single `ws.send()` with no HTTP handshake on
the hot path. See
[`workspace.components.operator_actions`](../workspace/workspace/components/operator_actions.py)
for the helper that reads the contract defensively.

---

## 9. Runtime scene mutation — adding and removing components live

The scene is normally loaded once at workspace launch from the
`scene/*.j2` yaml files. But sometimes you need to add or remove
components **at runtime** — for example, when a recovery routine
declares "the operator placed a new cap at slot A1", or when a
recipe creates a tube on the fly. The Workspace exposes two
explicit APIs for this:

```python
workspace.add_component(name, cfg)         # cfg = same dict shape as the yaml entry
workspace.remove_component(name)
```

### `add_component(name, cfg)` — paired with `add_fact`

`cfg` is the **same dict** a `scene/*.j2` yaml entry parses to —
must include `type`, may include `attach`, plus whatever per-type
config the component class accepts. Returns the new instance.

Almost every real add will also need a corresponding PDDL fact, so
the canonical pattern is two calls:

```python
# Scene side — adds the solid to the kinematic tree.
workspace.add_component("cap_99", {
    "type": "cap_2ml",
    "attach": {
        "parent_name":   "rack_2ml_source",
        "parent_solid":  "body",
        "parent_anchor": "A1",
        "child_solid":   "body",
        "child_anchor":  "center",
        "offset":        [0, 0, 0, 0, 0, 0],
    },
})

# State side — tells the planner where it is so subsequent actions
# can reason about it. Must be called explicitly; the framework
# never infers facts from scene edits.
workspace.add_fact("at", "cap_99", "rack_2ml_source.A1")
```

After both return: the kinematic chain is wired, the 3D viewer
reflects the new solid, the Devices / Operator Controls panels
re-snapshot, and the next BT tick sees the new predicate.

### `remove_component(name)` — paired with `remove_fact`

Detaches every solid in the component's assembly from its parent,
drops the component from `workspace.components`, and broadcasts the
same "scene changed" event the add path does. As with add, you
also clean up any predicates that referenced the removed object:

```python
# Operator took cap_99 out of the workspace entirely.
workspace.remove_fact("at", "cap_99", "rack_2ml_source.A1")
workspace.remove_component("cap_99")
```

Order doesn't matter, but cleaning facts first (then scene) is the
defensive choice: a BT tick between the two calls sees a fact
about an object that still exists, never a fact about a phantom.

`remove_fact` is a silent no-op if the fact isn't present, so you
don't need to track exactly which predicates were set —
defensively clear what the action would have set:

```python
# Forget every cap-related predicate, no matter which ones were live.
workspace.remove_fact("at",     "cap_99", "rack_2ml_source.A1")
workspace.remove_fact("capped", "tube_5")
workspace.remove_component("cap_99")
```

### Refusal cases (both APIs)

The framework rejects mutations that would put the system in an
inconsistent state:

| Refused | Reason |
|---|---|
| Adding a name already in `workspace.components` | Would silently overwrite the existing component |
| Removing `core` | Runtime-critical; the Runtime holds a reference |
| Removing a tool currently mounted on the robot flange | Would yank the kinematic chain out from under live motion. Detach via Core's tool-changer first. |
| Adding **or** removing a device-backed component **during a run** | MQTT publisher lifecycle isn't safe to start/stop mid-run. Launch with it from the start, or pause the run first. |

Passive components (caps, racks, tubes, fixtures — anything whose
`device_ids` returns `[]`) can be added or removed during a run
freely. The framework holds a re-entrant scene lock during the
mutation so concurrent BT walks see a consistent state.

### The explicit-mutation rule

Scene topology and planner state (PDDL facts) are **separate
concerns**. The framework **never** infers one from the other. A
caller that mutates the scene is responsible for mutating any
corresponding facts, and vice versa — see the paired examples in
the `add_component` and `remove_component` sections above.

The state-side surface (`add_fact` / `remove_fact` / `facts`) is
documented fully in
[bt-framework-guide.md §9](bt-framework-guide.md#9-runtime-fact-mutation--add_fact--remove_fact--facts).

### What the caller is on the hook for

- **Updating PDDL facts** to reflect any predicate the change
  implies. The framework can't infer because predicates are
  project-specific.
- **Not removing something the current action is mid-touch on** —
  e.g. don't remove a tube the gripper is currently picking. The
  scene lock prevents data races, not logical conflicts.
- **Not adding a brand-new device-backed component during a run**
  — wait until the next launch.

---

## 10. Full example

```
my_project/
├── main.py
├── components/
│   └── custom_holder.py
├── CAD/
│   └── custom_holder.glb
└── scene/
    └── base.j2
```

**`components/custom_holder.py`** — see section 3 ("From scratch") for the full class.

**`main.py`**:
```python
from components.custom_holder import CustomHolder  # triggers @register

from workspace.workspace import Workspace
# ... rest of main.py
```

**`scene/base.j2`**:
```yaml
my_holder:
  type: custom_holder
  attach:
    parent_name: fixture_plate_0
    parent_solid: body
    parent_anchor: B5
    child_solid: body
    child_anchor: center
```
