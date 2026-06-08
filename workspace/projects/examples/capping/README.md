# capping — full cap + decap roundtrip with the 4-finger gripper

Standalone BT mini-project demonstrating the decapper component
end-to-end: for each tube, pick from the rack, cap it at the
decapper, return it to the rack, then later decap it and return both
the tube and the cap to their original slots.

Same fixture footprint as `examples/feeder/` (plates 1–6 + tool rack
at plate 5 / B18), but the device set is different — tube rack +
cap holder + decapper, served by the 4-finger gripper.

## What this teaches

| Pattern | Where it shows up |
|---|---|
| **`decapper.cap(exit=False)` + `decapper.decap(approach=False)`** | The Decapper recipe owns the chunked screw motion (j5 spin while Z advances by pitch × chunk). Caller doesn't have to think about how many chunks or what speed — just calls `cap()` / `decap()`. |
| **Split-action planning** | `Cap` and `Decap` are separate Actions with linked predicates (`capped(t)` → `decapped(t)`). The planner schedules all caps first, then all decaps. Reordering, retries, parallelism all fall out of the plan. |
| **Two rack/holder via the same adapter type** | Both `tube_rack` and `cap_holder` use the `adapter_plate_autosampler_2ml_5x10` adapter, with different child racks attached (`rack_autosampler_2ml_5x10` vs `capholder_autosampler_2ml_5x10`). Recipe targets the adapter; resolver walks down to the actual rack. |
| **4-finger gripper** | `gripper_4_finger_1` — different tool than the feeder example's `gripper_suction_1`, same tool-changer auto-swap mechanism. |
| **`tool_tcp_z_offset=-2` on `decapper.pick`** | Mechanical detail: after capping, the cap is screwed on tight, so picking the tube needs the TCP shifted 2 mm deeper into the workpiece to keep the grip secure. Negative = along the tool's Z (which points into the workpiece). |

## Per-tube flow

**Cap(t)** — runs in the first pass over the tube list:
```
tube_rack.pick(slot)            # grab uncapped tube
decapper.place()                 # set it in the decapper
cap_holder.pick(slot)            # grab matching cap
decapper.cap(exit=False)         # screw the cap on (no exit motion)
decapper.pick(approach=False, tool_tcp_z_offset=-2)  # grab capped tube
tube_rack.place(slot, soft_approach=True)            # return to rack
```

**Decap(t)** — runs in the second pass after all caps are done:
```
tube_rack.pick(slot)             # grab capped tube
decapper.place(exit=False)       # set in decapper (no exit motion)
decapper.decap(approach=False)   # unscrew the cap
cap_holder.place(slot, gravity_offset=-15)  # return cap to its slot
decapper.pick(tool_tcp_z_offset=-2)         # grab the uncapped tube
tube_rack.place(slot, soft_approach=True)   # return to rack
```

End state: every tube + cap back in its original slot, decapped.

## Run it

```bash
cd workspace/projects/examples/capping
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Pick `tube_count` (1–10),
start. The progress bar tracks Cap phase 0–50% then Decap phase
50–100%.

Sim mode by default — works on any machine, no hardware needed.

## Convention: `Start` / `Park` / `OperatorPark` stay canonical

These three actions look the same in every project. **All
tube-specific work lives in `Cap` and `Decap`** — Start and Park
don't get tube-handling motion or per-tube predicate logic beyond
what's strictly needed for the planner.

Only the per-item predicate (`decapped` here, `cap_fed` in the
feeder example, `vial_2ml_capped` in sample_prep) and the object key
(`"tube"` here, `"cap"` in feeder, `"tube"` in sample_prep) change.

Full rule + canonical shapes: `.claude/skills/add-bt-action/SKILL.md`
rule 6.

## How to adapt this to your bench

1. **Different tube model**: swap `tube_autosampler_2ml` for
   `tube_falcon_15ml` / `tube_amber_40ml` / etc., and update the
   rack model + cap component to match.
2. **More tubes**: raise `kwargs.tube_count.max` in `launch.yaml`.
   The action uses `tube_rack.slot["body"][tube]` so it walks the
   whole 5×10 grid automatically — no slot list to maintain.
3. **Multiple decappers**: in real PACE-style protocols, parallel
   decappers exist (`decapper_1` … `decapper_4`). Add them to
   `scene/base.j2`, add a recipe alias per decapper, and have
   `Cap` / `Decap` route to a specific one based on the tube
   index (modulo). The planner can then run multiple Cap actions
   in parallel.

## What's NOT in this example

- Vision-based cap presence verification (`checks.py` is empty)
- Per-tube weighing or dispensing (sample_prep + syringe have this)
- Multiple decappers / parallel scheduling

If you need any of those, see `sample_prep`'s `Inspected` and
related actions for the full pattern.

## Files

| File | Purpose |
|---|---|
| `main.py` | Standard BT entry point (byte-identical to other examples) |
| `launch.yaml` | Port, scene, recipes, `tube_count` kwarg |
| `recipes.j2` | 4 recipe aliases: `gripper`, `tube_rack`, `cap_holder`, `decapper` |
| `scene/base.j2` | Plates 1–6 + tube rack + cap holder + decapper + tool rack |
| `scene/layout.j2` | 4-finger gripper parked + 50 tubes + 50 caps |
| `actions.py` | `Start` → `Cap(t) × N` → `Decap(t) × N` → `Park` |
| `checks.py` | Empty stub |

## See also

- [`feeder/`](../feeder/) — feeder + cap holder + suction tool (the
  other example in this folder, same fixture footprint)
- [`projects_old/printer/workflow.py`](../../../../workspace/projects_old/printer/workflow.py:110) —
  the source pattern this example is split from
- [`projects_old/syringe/main.ipynb`](../../../../workspace/projects_old/syringe/main.ipynb) —
  the dispense + cap variant
- [`recipe-guide.md`](../../../../docs/recipe-guide.md) — §8 Catalog
  entry for `Decapper`
