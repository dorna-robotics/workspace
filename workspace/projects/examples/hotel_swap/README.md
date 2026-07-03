# hotel_swap — swap plates pairwise between two hotels

Standalone BT mini-project that swaps plates between two SBS-format
hotels using two SBS plate holders as temporary stash. Demonstrates
the `Hotel` recipe's lateral slide-in approach and the `Adapter`
recipe's side-loaded pick/place pattern.

Same plates 1–6 footprint as the other examples.

## What this teaches

| Pattern | Where it shows up |
|---|---|
| **`Hotel` recipe — lateral slide-in** | `hotel_a.pick(level=N)` builds the `place_N` anchor and runs the side-load approach: the gripper enters from the side, slides in, descends, picks. `place(level=N)` mirrors it. |
| **`Adapter` recipe — biased approach** | `holder_a.place()` and `holder_a.pick()` run a 3-waypoint approach with a +10 mm X bias to clear the SBS adapter's wall. |
| **`gripper_sbs_width`** | SBS-width plate gripper. The tool changer auto-swaps it on the first `Swap` action via `Swap.tool = "gripper"`. |
| **Per-level PDDL planning** | One `Swap(level)` action per level; the planner schedules them based on `~swapped(level)` precondition. Each level is independent so they could be reordered/parallelised. |

## Per-level flow (8 motions)

```
hotel_a.pick(level)        # grab plate from hotel A's shelf
holder_a.place()           # park it on holder A
hotel_b.pick(level)        # grab plate from hotel B's shelf
holder_b.place()           # park it on holder B
holder_a.pick()            # holder A had A's plate; pick it up
hotel_b.place(level)       # place into hotel B's now-empty shelf
holder_b.pick()            # holder B had B's plate; pick it up
hotel_a.place(level)       # place into hotel A's now-empty shelf
```

End state: every plate is in the OTHER hotel from where it started.

## Run it

```bash
cd workspace/projects/examples/hotel_swap
sudo python3 main.py
```

Operator UI at `http://<ip>:5010/`. Pick `level_count` (1–4),
start. Progress bar tracks completed swaps.

Sim mode by default — works on any machine, no hardware needed.

## Convention: `Start` / `Park` / `OperatorPark` stay canonical

All level-swap work lives in `Swap`. Start and Park don't get
hotel-handling motion or per-level predicate logic beyond what's
strictly needed for the planner.

Full rule + canonical shapes: `.claude/skills/add-bt-action/SKILL.md`
rule 6.

## How to adapt this to your bench

1. **Different hotel model**: swap `hotel_sbs_76h_4lvl` for
   `hotel_sbs_52h_4lvl` or any other `Hotel` subclass; the recipe
   stays the same. Update `level_count.max` in `launch.yaml` to
   match the hotel's level count.
2. **Different plate type**: swap `rack_autosampler_2ml` (used here
   as the moved plate) for whatever SBS-footprint rack you have.
3. **Vision-based plate detection**: scene/core_500.j2 has
   `has_camera: false`. Flip to true, add a MobileInspector
   recipe, and call `inspector.detect(...)` before each pick if
   you want runtime "is the plate actually there?" checks.

## Files

| File | Purpose |
|---|---|
| `main.py` | Standard BT entry point (byte-identical to other examples) |
| `launch.yaml` | Port 5010, `level_count` operator kwarg (1–4) |
| `recipes.j2` | 5 recipe aliases: `gripper`, `hotel_a`, `hotel_b`, `holder_a`, `holder_b` |
| `scene/core_500.j2` | Local copy of the bench chassis (core + rail + 6 plates + boundary collision boxes) |
| `scene/layout.j2` | Devices (adapters/racks/holders/tool rack) + populated items |
| `actions.py` | `Start` → `Swap(level) × level_count` → `Park` |
| `checks.py` | Empty stub |

## See also

- [`feeder/`](../feeder/) — cap feeder + suction tool → cap holder
- [`capping/`](../capping/) — full cap + decap roundtrip
- [`recipe-guide.md`](../../../../docs/recipe-guide.md) — §8 catalog entries for `Hotel` and `Adapter`
