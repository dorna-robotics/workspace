---
name: bootstrap-project
description: "Use when standing up a NEW project from a finished scene — the step-by-step pipeline from scene to running protocol: recipe skeleton, parameter solving, flow, schedule validation, bench check. Also the recovery map when a project misbehaves and you need to know WHICH layer the error lives in."
---

# Bootstrap a project (scene → running protocol)

## When to use this skill

- "The scene is ready — build the project" (recipes + actions + launch)
- "Guide me through setting up a new bench project"
- A project is misbehaving and you need to localize which layer the
  error belongs to before touching anything.

## The pipeline — one owner, one artifact, one error surface per step

| # | Step | Owner | Artifact | Failure here means |
|---|---|---|---|---|
| 1 | Scene | operator (builder GUI) | `scene/*.j2` | geometry/attachment problem |
| 2 | Skeleton | AI, operator confirms | station → `{class, component, tool}` | wrong intent (class/tool/role) |
| 3 | Solve | tool / AI, cheap checks only | `recipes.j2` | SCENE problem — named station, named reason |
| 4 | Flow | AI | `actions.py` + schedule replay | protocol logic |
| 5 | Bench | operator | watched motions | rare residual: path shape |

**The discipline: never debug across layers.** A failure in step N's
check is caused in step N's inputs or earlier — the reports name the
station and reason so nobody guesses.

## The three parameter layers (do not mix them)

| Layer | Parameters | Depends on | How solved |
|---|---|---|---|
| Geometry | `padding` (hover clearance) | scene only — inflated box tops vs payload tops. Robot-agnostic. | arithmetic: `box_top + planner_pad − payload_top`, planner pad = 10/face |
| Kinematics | `base_distance`, `left_approach`, `rail_step`, `rail_span` | robot + rail + mounted tool TCP | closed-form IK sweep (no OMPL) |
| Intent | recipe class, role name, `tool` | the protocol | authored in the skeleton, confirmed by the operator |

## Step 2 — the skeleton

One entry per station the protocol touches:

```yaml
source_rack_1: {class: Rack,       component: adapter_plate_amber_40ml_4x7_in_1, tool: gripper_tube_large_1}
doser_40ml:    {class: DosingSite, component: adapter_plate_amber_40ml_1x6_1,    tool: gripper_needle_1}
decapper:      {class: Decapper,   component: decapper_1,                        tool: gripper_tube_large_1}
```

Class inference is mechanical: adapter+rack → `Rack` (+ `DosingSite`
if dosed into), tool_rack+gripper → `ToolRack`, decapper → `Decapper`,
scale + top → `Scale` + base-`Recipe` holder (the bd split), shaker →
`Shaker` with `target_solid_name: rotating`, feeder → `Feeder`,
inspection → `FixedInspector`.

Tool conventions (override only for unusual benches):
tube_large ↔ large tubes (40 ml amber, falcon) · 4-finger ↔ 2 ml vials
· suction ↔ caps/discs · needle ↔ dosing/pipetting.

The `tool` field is REQUIRED — endpoint checks are meaningless without
the mounted tool's TCP.

## Step 3 — solve (cheap checks ONLY, no OMPL, no motion)

Per station, with its declared tool virtually mounted:

1. **Reference IK sweep** — `left_approach × base_distance` at
   `rail_span: 1` (the platform rule for new projects; see the
   feedback memory) until the reference joints solve.
2. **Entry point** (`a_pad`, where planning ends and the owned corridor
   begins): IK-valid AND collision-free against boxes inflated by the
   planner's padding (10 mm per face — `workspace.compute_collision_boxes`).
   If inside a box, raise `padding` to the computed clearance.
3. **Touch point and exit point**: IK-valid the same way (the exit
   check catches "previous motion ended inside a box").

Every value written to `recipes.j2` carries its evidence in a comment.
Unreachable stations are reported with the geometric reason
("rail-frame x=941, rail ends at 801") — that is a scene fix, decided
by the operator, BEFORE any flow work starts.

Why no motion planning here: measured on real projects — every recipe
failure was an ENDPOINT failure (goal in an inflated box, start in a
box after exit, IK-unreachable). Valid-endpoints-but-no-path is rare
enough to leave for step 5's eyes.

## Step 4 — flow + schedule validation (pure logic, no robot sim)

Author `actions.py` from the gold exemplars (see CLAUDE.md table).
Validate with the precondition replay: plan (PDDL) → precedence →
schedule (CP-SAT) → replay in scheduled order against real
`pre()`/`eff()` → 0 failures + goal reached, at batch 1 AND a
multi-item batch. Seconds. Motion never runs.

## Step 5 — bench

The operator launches and WATCHES. Start/end states are pre-validated,
so what remains is judgment the tools cannot have: path shape,
clearance comfort, physical seating. Failures here are rare and
localized — the step log names the action; the [traj]/[plan] lines
name the motion.

## Canonical references

- `docs/recipe-guide.md` — recipe kwargs, motion primitives
- `docs/bt-framework-guide.md` — actions, planning, slicing
- `docs/project-guide.md` §8 — device reads, capacity facts
- Gold exemplars: `examples/` (CLAUDE.md table) — copy, change minimum
