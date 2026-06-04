# pace_bt

Behaviour-tree implementation of the **pace protocol** — a batch of
tubes processed through `inspect → decap → dispense → recap → shelve`.

The dispense step branches on the tube's weight: `Inspect`'s sensing
eff returns `"light"` or `"heavy"` after reading the scale, and the
planner routes each tube to `DispenseLight` or `DispenseHeavy`
accordingly. No `if` anywhere — branching is in the action
preconditions, the planner does the choosing.

## Run

```bash
cd projects/pace_bt
sudo python3 main.py --port 5010
```

Operator UI then at `http://<ip>:5010/`. The orchestrator also spawns
this automatically when a user starts pace_bt from the project list.

## Configuration

Everything project-specific lives in [`launch.yaml`](launch.yaml) —
project name, port, scene, recipes file, the protocol module
(`actions.py`), the checks module (`checks.py`), and the GUI form
schema (`batch_size`). `main.py` is pure wiring.

## See also

For everything framework-level — authoring rules, the data model,
sensing actions, dynamic-world handling, FAQ — read
[`docs/bt-framework-guide.md`](../../../docs/bt-framework-guide.md).
New BT projects start as a copy of this one.
