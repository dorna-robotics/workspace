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

`main.py` is **explicit, ~50 lines** — opens `launch.yaml`, loads
`recipes.yaml`, loads `checks.py`, imports `actions`, and hands
everything to `bt.launcher.run_protocol`. Same shape as pace_or's
`main.py`. You can read it top-to-bottom and see exactly where each
piece is hooked in.

## Files

| File | Purpose | How often you edit it |
|---|---|---|
| `scene/base.j2` | scene | when hardware changes |
| `main.py` | explicit orchestrator entry (opens launch.yaml, calls load_recipes/load_checks, calls run_protocol) | rarely |
| `launch.yaml` | scene paths + GUI kwargs schema | when adding a kwarg |
| `recipes.yaml` | recipe aliases → class + component bindings | when scene components change |
| `actions.py` | predicates + `setup()` + one `Action` subclass per atomic step | **every protocol change** |
| `checks.py` | `Checks` class — pre/post-check methods referenced by name from actions | when adding sensor / vision checks |
| `README.md` | this file | docs |

## Framework guide

The exhaustive reference — including the "where to edit for X" routing
table, the data model (Predicate / Fact / state), the sensing-action
pattern, dynamic-world handling, and the FAQ — lives at
[`docs/bt-framework-guide.md`](../../../docs/bt-framework-guide.md).

New BT projects start as a copy of this one.
