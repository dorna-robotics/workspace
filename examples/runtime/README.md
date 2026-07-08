# runtime — create / transfer / remove a disc per cycle

Reference example for **creating and removing components at runtime**,
the documented way (docs/component-guide.md §9,
docs/bt-framework-guide.md §9).

Each **cycle** is one disc's whole lifecycle:

```
1. CREATE    workspace.add_component — a disc at a RANDOM in-holder slot,
             lifted by a random z1 ∈ [0, 57.15] mm
2. TRANSFER  pick it, place it at a RANDOM out-holder slot, lifted by a
             random z2 ∈ [0, 57.15] mm
3. REMOVE    workspace.remove_component — the disc is consumed
```

`batch_size` (operator kwarg) is how many cycles to run. Only **one
disc exists at a time** — it's created and removed inside the same
`Cycle` action — so the disc is a *transient* scene object, not a
persistent planning object. The single planning fact is `cycled(i)`.

## Why it's bulletproof

| Concern | How it's handled |
|---|---|
| **Plan ↔ runtime agreement** | Randomness lives only in `execute`. `pre`/`eff` are deterministic (`cycled(i)`), so the plan is always `Cycle(0..n-1)` no matter the dice. |
| **No orphan facts** | `eff` only ever ADDS `cycled(i)`. A failure can leave a stray *scene* disc but never a dangling *fact*. |
| **Balanced scene edits** | Every `add_component` is matched by a `remove_component` in the same action; the disc never outlives its cycle. |
| **Idempotent retries** | Each cycle defensively removes a leftover `disc_<i>` before creating, so a retried cycle starts clean (a second `add_component` of a live name would raise). |

## The pattern (inside a BT action)

```
execute()  →  scene side    — workspace.add_component(...) / remove_component(...)
eff()      →  planner side   — the fact the cycle leaves behind (cycled(i))
```

Inside an action the `eff` IS the explicit fact mutation — no separate
`add_fact`/`remove_fact` is needed (those are for mutating **outside**
an action, e.g. an operator-recovery hook). Here the disc carries no
persistent fact because it never outlives the action; if you instead
kept a disc around across actions, you'd give it a location fact (e.g.
`at_in(d)`) declared in the creating action's `eff` and dropped in the
removing action's `eff` — same rule, just spanning actions.

## Flow

```
Start          motor on + park (canonical)
Cycle(i)       create → transfer → remove   (×batch_size)
Park           motor off (canonical)
```

The transfer is kinematic (`pick` attaches the disc to the tool,
`place` re-attaches it under the out holder). The suction tool drives
deeper on pick (`tool_tcp_z_offset=-10`) and presses on release
(`gravity_offset=-5`). The spawn's `z1` lift is honoured automatically —
`pick` finds the disc by walking the kinematic tree to wherever it
actually sits.

Targets: in ∈ {in_1, in_2}, out ∈ {out_1, out_2, out_3}, slot ∈ A1..A7.

## What's different from the other examples

| | Others | runtime |
|---|---|---|
| **The item** | declared in `scene/layout.j2` | created **and removed** at runtime via `workspace.add_component` / `remove_component` |

The chassis is a local `scene/core_500.j2` (self-contained). The in/out
holders are `adapter_disc_holder` + `stack_holder_disc_in/out` (flat
`A1..A7` slots); the Rack recipes target the adapters and the resolver
walks down to the stack holder underneath.

## Run it

```bash
cd examples/runtime
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Sim mode by default. Pick
`batch_size`, press Start — the holders are empty at launch; a disc
appears, gets transferred, and vanishes each cycle. Progress bar tracks
completed cycles.

## Files

| File | Purpose |
|---|---|
| `main.py` | Canonical BT entry point (byte-identical to other examples) |
| `launch.yaml` | Port 5010; `batch_size` kwarg; scene composes the local `core_500.j2` |
| `scene/core_500.j2` | Local copy of the bench chassis (self-contained) |
| `scene/layout.j2` | Tool rack + suction gripper + the in/out disc holders |
| `recipes.j2` | `gripper` (ToolRack) + `disc_in_1/2`, `disc_out_1/2/3` (Rack) |
| `actions.py` | `Start` → `Cycle(i)` ×batch_size → `Park` |
| `checks.py` | Empty stub |
