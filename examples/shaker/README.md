# shaker — load, shake, unload with a non-robot resource

Standalone BT mini-project demonstrating the shaker component and —
the real point — **resource management**: the shake occupies the
`shaker` resource, not the robot, so the two lanes separate cleanly
on the Gantt.

For each pair of 40 ml amber tubes: pick from the rack, set into a
shaker slot, run one mechanical shake cycle (both slots at once),
then return each tube to its rack slot.

## What this teaches

| Pattern | Where it shows up |
|---|---|
| **Non-robot resource** | `Shake.resource = "shaker"` — the shake occupies the shaker lane while the robot lane stays free for the scheduler. Every other action holds `"robot"`. |
| **Batched device action** | One `shake()` shakes BOTH slots, so `Shake` has no `tube` param. Its `pre` requires every slice tube loaded (`in_shaker`) and its state-aware `eff` marks exactly the loaded ones `shaken` — same shape as bna's `ShakerOne`/`ShakerTwo`. |
| **`plan_window` = device capacity** | The shaker holds 2 tubes → `plan_window: 2`. Each slice loads both slots, shakes once, unloads both; slot `t % 2` means slices reuse the slots without colliding. |
| **Start seeds per-tube facts** | `Start.eff` adds `in_rack(t)` for the FULL batch (`_ctx_all_objects`), gating every per-tube action behind Start without `& started` in each `pre`. |
| **Multi-solid component recipe** | The shaker's slots live on its `rotating` solid, so the recipe passes `target_solid_name: rotating` for the boot-time IK (the `body` solid has no `place` anchor). |

## Per-tube flow

**Load(t)** — robot + gripper:
```
tube_rack.pick(slot, soft_approach=True)   # grab capped tube t
shaker.place(A1 or A2, gravity_offset=4)   # set into slot t % 2
```

**Shake** — shaker resource, no tube param:
```
shaker.shake(duration=10)   # toggles until duration elapsed AND back at start
```

**Unload(t)** — robot + gripper:
```
shaker.pick(A1 or A2)                                 # grab shaken tube
tube_rack.place(slot, gravity_offset=4, soft_approach=True)  # back home
```

End state: every tube back in its original rack slot, shaken.

## Run it

```bash
cd examples/shaker
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Pick `batch_size` (1–8), start.
Watch the schedule: `Load Load → Shake → Unload Unload` per pair,
with the Shake bar on the shaker lane, not the robot lane.

Sim mode by default — works on any machine, no hardware needed.

## Convention: `Start` / `Park` / `OperatorPark` stay canonical

Same as every example — all tube-specific work lives in `Load`,
`Shake`, `Unload`. Only the per-item predicate (`done` here), the
object key (`"tube"`), and the tool (`gripper`) vary.

## How to adapt this to your bench

1. **Longer/shorter shake**: change `SHAKE_DURATION` in `actions.py`
   (also the action's `duration` hint for the scheduler).
2. **Bigger rack**: swap `adapter/rack_amber_40ml_2x4` for the `4x7`
   variants and raise `kwargs.batch_size.max` — the slot lookup
   walks `tube_rack.slot["body"]`, no table to maintain.
3. **Second shaker**: add `shaker_2slot_2` + a second recipe alias,
   split `Shake` into two subclasses with a shared abstract base
   (`register = False`) — the bna project's `ShakerOne`/`ShakerTwo`
   is the canonical reference. With two shakers the robot loads one
   while the other shakes — full pipeline overlap.

## What's NOT in this example

- Decapping before the shake (see `capping/` for the decapper)
- Weighing or dispensing between load and shake (bna has the full
  PACE-style protocol around its shakers)
- Vision checks (`checks.py` is empty)

## Files

| File | Purpose |
|---|---|
| `main.py` | Standard BT entry point (byte-identical to other examples) |
| `launch.yaml` | Port, scene, recipes, `plan_window: 2`, `batch_size` kwarg |
| `recipes.j2` | 4 recipe aliases: `robot`, `gripper`, `tube_rack`, `shaker` |
| `scene/core_500.j2` | Local copy of the bench chassis (core + rail + 6 plates + walls) |
| `scene/layout.j2` | Tool rack + amber 2×4 rack (8 capped tubes) + 2-slot shaker |
| `actions.py` | `Start` → `Load(t)` → `Shake` → `Unload(t)` → `Park` |
| `checks.py` | Empty stub |

## See also

- [`capping/`](../capping/) — decapper roundtrip with the same
  split-action planning idea
- [`pipetting/`](../pipetting/) — another per-item flow on the same
  chassis footprint
