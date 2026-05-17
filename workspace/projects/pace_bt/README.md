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

Project-level knobs (name, port, file names) live at the top of
[`main.py`](main.py). Operator-facing inputs (batch size, tick rate)
are declared in [`launch.yaml`](launch.yaml).

## See also

For everything framework-level — authoring rules, the data model,
sensing actions, dynamic-world handling, FAQ — read
[`docs/bt-framework-guide.md`](../../../docs/bt-framework-guide.md).
New BT projects start as a copy of this one.
