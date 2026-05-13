# pace_bt

Behaviour-tree-based implementation of the pace protocol. Demonstrates
the framework's authoring style: **one `Action` subclass per atomic
step**, everything else derived automatically.

## What it does

Processes a batch of tubes through `inspect → decap → dispense → recap
→ shelve`. The dispense step branches on the tube's weight (heavy or
light) — the planner picks the right branch per tube from each
action's preconditions, no `if` anywhere.

## Run

```python
from workspace import Workspace
from projects.pace_bt import workflow

workspace = Workspace(config_path=["projects/pace_bt/scene/base.j2"])
core = workspace.components["core"]
workflow.run(workspace, core, batch_size=4, heavy={1, 3})
```

In sim mode (default unless you configure a real robot IP) every
action sleeps for its declared `duration` and returns success.
Validates the plan-schedule-tree-execute loop without hardware.

## Files

| File | Purpose |
|---|---|
| `scene/base.j2` | scene |
| `main.py` | orchestrator launcher — boots `Workspace` + `RuntimeServer` |
| `launch.yaml` | scene paths + GUI kwargs schema (batch size, heavy tubes, tick rate) |
| `actions.py` | predicates, initial state, goal, one `Action` subclass per atomic step |
| `workflow.py` | called on every Start — wires actions registry into the BT engine |
| `README.md` | this file |

## Where to edit for…

| You want to… | Edit |
|---|---|
| Add / remove an atomic action | `actions.py` (single `Action` subclass) |
| Change branch logic | `pre()` method on the relevant action |
| Change scheduling (duration / resource) | class attrs on the relevant action |
| Change the goal | `actions.make_goal` |
| Change the scene | `scene/base.j2` |
| Custom tree shape (extra retry, parallel) | create an optional `tree.py` |

That's the whole authoring surface. The framework derives the PDDL
domain, scheduler meta, BT leaf factory, and `apply_effects` mirror
automatically.

## Framework discipline

This project follows the convention pinned in
[`docs/bt-framework-guide.md`](../../../docs/bt-framework-guide.md).
New BT projects start as a copy of this one.
