# pace_bt

Behaviour-tree-based implementation of the pace protocol. Demonstrates
the full framework stack: **PDDL planner → OR-style scheduler → py_trees
BT** with runtime pause / kill / replan support.

## What it does

Processes a batch of tubes through `inspect → decap → dispense → recap
→ shelve`. The dispense step branches on the tube's weight (heavy or
light) — the planner picks the right branch per tube.

## Run

```python
from workspace import Workspace
from projects.pace_bt import workflow

workspace = Workspace(config_path=["projects/pace_bt/config/base.j2"])
core = workspace.components["core"]
workflow.run(workspace, core, batch_size=4, heavy={1, 3})
```

In sim mode (which is the default unless you configure a real robot
IP) every action just sleeps for its declared duration and returns
success. Useful for validating the framework wiring without hardware.

## Where to edit for…

| You want to… | Edit |
|---|---|
| Add / remove an atomic action | `actions.py` (BT leaf) + `domain.py` (PDDL template) + `schedule.py` (duration/resource) |
| Add a new condition | `conditions.py` |
| Change the goal | `domain.make_goal` |
| Change branch logic | `domain.py` preconditions on the relevant actions |
| Change scheduling rules | `schedule.META` |
| Change tree shape (retry, recovery, parallelism) | `tree.build_tree` |
| Change the scene | `config/base.j2` |

## Framework discipline

This project follows the convention pinned in
`docs/bt-framework-guide.md`. New BT projects should be a copy of
this one with the seven files filled in for their protocol.
