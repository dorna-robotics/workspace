# pace_bt

Behaviour-tree-based implementation of the pace protocol. Demonstrates
the framework's authoring style: **one `Action` subclass per atomic
step + a `setup(**kwargs)` function**, everything else derived
automatically.

## What it does

Processes a batch of tubes through `inspect → decap → dispense → recap
→ shelve`. The dispense step branches on the tube's weight (heavy or
light) — the planner picks the right branch per tube from each
action's preconditions, no `if` anywhere.

## Run

The orchestrator spawns it automatically. Or directly:

```bash
cd projects/pace_bt
sudo python3 main.py --port 5010
```

`main.py` is two lines; the framework reads `launch.yaml`, loads
`recipes.yaml`, imports `actions`, and starts the BT engine.

## Files

| File | Purpose | How often you edit it |
|---|---|---|
| `scene/base.j2` | scene | when hardware changes |
| `main.py` | **explicit** orchestrator entry — opens `launch.yaml`, calls `load_recipes`, calls `run_protocol`. ~50 lines, same shape as pace_or's | rarely |
| `launch.yaml` | scene paths + GUI kwargs schema | when adding a kwarg |
| `recipes.yaml` | recipe aliases → class + component bindings | when scene components change |
| `actions.py` | predicates + `setup()` + one `Action` subclass per atomic step | **every protocol change** |
| `README.md` | this file | docs |

`main.py` keeps the wiring visible — you can read it and see exactly
where `launch.yaml`, `recipes.yaml`, and `actions` get hooked together.
The framework provides reusable helpers (`load_recipes`, `run_protocol`)
but doesn't hide the assembly.

## Where to edit for…

| You want to… | Edit |
|---|---|
| Add / remove an atomic action | `actions.py` (one `Action` subclass) |
| Change branch logic | `pre()` method on the relevant action |
| Change scheduling (duration / resource) | class attrs on the relevant action |
| Change the goal | `actions.setup`'s `goal` callable |
| Change initial state from kwargs | `actions.setup` |
| Change the GUI form | `launch.yaml` kwargs |
| Change the scene | `scene/base.j2` |
| Change recipe bindings | `recipes.yaml` |
| Override the protocol runner | add a `workflow.py` with `run(workspace, core, **kwargs)` |

## Framework discipline

See [`docs/bt-framework-guide.md`](../../../docs/bt-framework-guide.md).
New BT projects start as a copy of this one.
