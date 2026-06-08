# feeder — cap feeder + suction → cap holder

Standalone BT mini-project that transfers caps from a rotating
autosampler feeder to a 5×10 cap-holder rack, using a suction tool
that the tool-changer auto-swaps on.

## What this teaches

| Pattern | Where it shows up |
|---|---|
| **Tool-changer auto-swap** | `FeedCap.tool = "cap_tool"`. The BT framework picks the suction tool before the first FeedCap, returns it after the last. No manual pickup in `execute()`. |
| **Feeder `above` + `pick(approach=False)`** | The recipe overrides `pick`'s padding to 25 mm (tighter than the default 50). You hover at `plate_center` first — depth-independent — then descend straight down at the picking depth. Two-phase = safer for a tight envelope. |
| **Rack place via resolver pattern** | `cap_holder` is `Rack`, but its `component` is the **adapter plate**. `Rack` walks the kinematic tree to the actual `capholder_...` rack underneath. Decouples the recipe from which specific rack model is loaded. |
| **`rotate_in_step` for grid-snap rotation** | After each cap is picked, `feeder.rotate_in_step(step=1)` advances to the next slot. No vision required (compare with `sample_prep`'s `present_cap`, which uses a camera). |
| **Per-cap planning** | The PDDL planner sees `cap_count` objects and schedules one `FeedCap(c)` per cap. Reordering, retries, and parallelism (if you ever add a second tool) all fall out of the plan. |

## Run it

```bash
cd workspace/projects/examples/feeder
sudo python3 main.py
```

Operator UI opens at `http://<ip>:5010/` (the platform-wide default
port). If you already have another project running on 5010, pass
``--port 5020`` (or any free port) to override. Pick a cap count
(1–10),
start the run. The 3D view shows caps moving from the feeder into
the holder slots in real time.

The scene runs in **simulation by default** (`core.simulation: true`
in `scene/base.j2`). No hardware needed — works on any machine.

## How to adapt this to your bench

1. **Different feeder model**: swap `capfeeder_autosampler_2ml` for
   another `Feeder` subclass (look in
   `workspace/workspace/components/feeder/`). The recipe alias stays
   `feeder` — only the `component` line in `recipes.yaml` changes.

2. **Different cap-holder grid**: swap `capholder_autosampler_2ml_5x10`
   for whichever holder you have. Update `CAP_HOLDER_SLOTS` in
   `actions.py` to match the new grid (e.g. `[f"A{c}" for c in
   range(1, 6+1)]` for a 6-slot row).

3. **Different tool**: change the `tool` attribute on `FeedCap` and
   the corresponding alias in `recipes.yaml`. For example, swap
   `gripper_suction_1` for `gripper_4_finger_1` in `scene/layout.j2`
   and rename `cap_tool` → `finger_tool`.

4. **More caps**: raise the `kwargs.cap_count.max` in `launch.yaml`
   and extend `CAP_HOLDER_SLOTS` in `actions.py`.

5. **Add vision**: scene/base.j2 has `has_camera: false`. Flip to
   `true`, add a vision component, and swap
   `feeder.pick(approach=False)` for
   `feeder.present_cap(rcp["inspector"])` — see
   `sample_prep/actions.py:CapFed` for the full vision-driven version.

## What's NOT in this example (kept simple on purpose)

- Vision-based cap presence verification (`checks.py` is empty)
- Pause-aware reaction to a "cap missing" condition
- Multi-tool workflows (only one tool on the rack)
- Parallel scheduling (one cap at a time, robot-bound)

If you need any of those, the `sample_prep` project shows them in
their full form. This example is the minimal skeleton you copy and
extend.

## Files

| File | Purpose |
|---|---|
| `main.py` | Standard BT entry point — identical across examples |
| `launch.yaml` | Project name, port, scene, recipes, kwargs form |
| `recipes.yaml` | 3 recipe aliases: `cap_tool`, `feeder`, `cap_holder` |
| `scene/base.j2` | Core + fixtures + tool rack + feeder + cap holder |
| `scene/layout.j2` | Suction tool parked + 10 caps loaded into feeder |
| `actions.py` | `Start` → `FeedCap(c) × cap_count` → `Park` |
| `checks.py` | Empty stub — no vision/sensor predicates needed |

## See also

- [`recipe-guide.md`](../../../../docs/recipe-guide.md) — §3 motion
  pipeline, §6 conventions, §8 catalog entry for `Feeder`
- [`bt-framework-guide.md`](../../../../docs/bt-framework-guide.md) —
  full BT model: actions, predicates, planning
- [`.claude/skills/add-bt-action/SKILL.md`](../../../../.claude/skills/add-bt-action/SKILL.md)
  — task playbook for adding new actions
