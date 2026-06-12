# runtime_disc — dynamic components, the documented way

Reference example for **creating (and removing) components at runtime**.
14 discs are spawned into the scene *while the workflow runs*
(`workspace.add_component`) — not declared in the scene yaml — and the
robot transfers each one with the suction tool.

The point is to show the **explicit-mutation rule** done correctly:
scene topology and planner state are separate concerns, and the caller
pairs every scene edit with the matching fact
(docs/component-guide.md §9, docs/bt-framework-guide.md §9).

## The pattern (inside a BT action)

```
execute()  →  scene side    — workspace.add_component(name, cfg)
eff()      →  planner side   — the location fact the new object gets
```

`SpawnDiscs.execute` calls `add_component` per disc; `SpawnDiscs.eff`
declares `at_in(d)` for every disc. The framework applies the eff once
`execute` succeeds, so the disc becomes a **located planning object** —
no separate `add_fact` call is needed *from inside an action*, because
the eff already is the explicit fact mutation. (Use
`workspace.add_fact` / `remove_fact` only when mutating **outside** an
action — e.g. an operator-recovery hook — where there's no eff to carry
the fact.)

Because the eff is a *declared* effect, the planner foresees it: even
though no disc exists at plan time, it knows `SpawnDiscs` will produce
`at_in(d)` and schedules the 14 transfers accordingly.

## Flow

```
Start          motor on + park (canonical)
SpawnDiscs     add 14 discs at runtime; eff: at_in(d)        (CREATE)
Transfer(d)    pick in slot → place paired out slot;
               eff: -at_in(d) +at_out(d)                     (MOVE, kinematic)
Park           motor off (canonical)
```

```
in_1[A_k] → out_1[A_k]   discs 0..6
in_2[A_k] → out_2[A_k]   discs 7..13
```

14 picks. The suction tool drives deeper on pick (`tool_tcp_z_offset=-10`)
and presses on release (`gravity_offset=-5`). `out_3` is spare.

The transfer move is purely **kinematic** — `pick` attaches the disc to
the tool, `place` re-attaches it under the out holder — so only the
location fact changes; no add/remove is needed for the move itself.

## Removing a component (the symmetric op)

To *consume* a disc instead of placing it — the removal half of the
rule — pair `remove_component` with the eff dropping the fact:

```python
# in an action's execute:
self.ctx.workspace.remove_component(f"disc_{d}")
# and in its eff:  {"consumed": (-at_out(d), +consumed(d))}
```

Outside an action (operator recovery, vision correction), do it
explicitly and clean the fact first, then the scene:

```python
ws.remove_fact("at_out", d)
ws.remove_component(f"disc_{d}")
```

`remove_fact` is a silent no-op if the fact isn't set, so you can
defensively clear whatever the action would have set.

## What's different from the other examples

| | Others | runtime_disc |
|---|---|---|
| **The items** | declared in `scene/layout.j2` | spawned at runtime via `workspace.add_component`, planner-tracked via `at_in`/`at_out` |

The chassis is a local `scene/core_500.j2` (copied from
`scenes/core/core_500.j2`) so the project is self-contained. The in/out
holders are `adapter_disc_holder` + `stack_holder_disc_in/out` (flat
`A1..A7` slots); the Rack recipes target the adapters and the resolver
walks down to the stack holder underneath.

## Run it

```bash
cd workspace/projects/examples/runtime_disc
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Sim mode by default. The holders
are **empty at launch** — the discs appear when you press Start (that's
`SpawnDiscs` running). Progress bar tracks completed transfers.

## Files

| File | Purpose |
|---|---|
| `main.py` | Canonical BT entry point (byte-identical to other examples) |
| `launch.yaml` | Port 5010; scene composes the local `core_500.j2` |
| `scene/core_500.j2` | Local copy of the bench chassis (self-contained) |
| `scene/layout.j2` | Tool rack + suction gripper + the in/out disc holders |
| `recipes.j2` | `gripper` (ToolRack) + `disc_in_1/2`, `disc_out_1/2` (Rack) |
| `actions.py` | `Start` → `SpawnDiscs` → `Transfer(d)` ×14 → `Park` |
| `checks.py` | Empty stub |
