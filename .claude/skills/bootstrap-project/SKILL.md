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

Run it (the ``cd`` is part of the command — ``-m`` resolves from cwd):

    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.recipes.solve <project_dir>
    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.recipes.solve <project_dir> --skeleton skeleton.yaml

Report-only; values are applied deliberately after review. Geometry is
measured along each anchor's APPROACH RAY (tilted stations included)
with a hard 20 mm margin, and reports TWO numbers per station:
``min pad`` (what any hover padding must reach) and ``min end`` (how
far above the payload any motion must END there — retract distances
and exit heights; an arm stranded inside an inflated box poisons the
next plan's start). Boundary-exact endpoints pass in sim and fail on
real joints — never accept less than the margin.

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
Validate with the replay command — plan (PDDL) → precedence →
schedule (CP-SAT) → replay in scheduled order against real
`pre()`/`eff()` → 0 failures + goal reached. Seconds, no motion:

    cd ~/Downloads/workspace/workspace && sudo python3 -m workspace.bt.replay <project_dir> --batch 1 4

solve + replay are the ONLY standard software gates — path checking
is the operator's job on the bench, not the agent's. (The
`workspace.bt.dryrun` command exists for off-bench machinery
debugging when replay is green but the bench dies deep in engine
plumbing; it is not part of bootstrap.) Reading the solve report —
samples, column meanings, what ``(swept)`` and ``UNREACHABLE`` demand
of you — is project-guide §10.1.

## Step 5 — bench

The operator launches and WATCHES. Start/end states are pre-validated,
so what remains is judgment the tools cannot have: path shape,
clearance comfort, physical seating. Failures here are rare and
localized — the step log names the action; the [traj]/[plan] lines
name the motion.

## Who writes what, who proves what

| Artifact | Written by | Proven by |
|---|---|---|
| scene | operator (builder GUI) | step-3 endpoint report |
| skeleton (class/component/tool) | AI | operator's one-glance confirm |
| recipes.j2 parameters | solver (arithmetic + IK sweep) | evidence comments per value |
| actions.py (pre/eff/flow) | AI, from operator's INTENT in plain words | schedule replay: 0 precondition failures + goal reached, batch 1 AND multi-item |
| schedule | NOBODY — derived (pre/eff -> build_precedence -> CP-SAT) | correct by construction IF pre/eff/resource/capacity are truthful |
| motions | — | operator's eyes on the bench |

The author's only scheduling responsibility is telling the truth in
four places: ``pre``, ``eff``, ``resource``, and ``capacity=True`` on
shared single-slot facts. Untruthful capacity facts serialize batches
(the bd collapse); untruthful pre lets the planner act on items that
are not where it thinks (caught by the replay, e.g. bd's seeded
``printed`` letting Decap fire on an unpicked tube).

## How to invoke

Say ``/bootstrap-project`` (or "bootstrap the project", "the scene is
ready, build the project"). Then the conversation is exactly:

1. Operator: "scene is done" (+ any protocol intent in plain words).
2. AI presents the SKELETON table -> operator confirms tools/roles.
3. AI runs the solve -> operator decides on any reported scene fixes.
4. AI writes actions.py from the stated intent -> shows the replay
   result (0 failures + goal, batch 1 and N).
5. Operator launches and watches. Failures at this stage name their
   action in the step log and their motion in the [traj]/[plan] lines.

## Project layout convention

`launch.yaml` is a list of POINTERS, never inline blocks:

```yaml
scene:    [scene/core_500.j2, scene/layout.j2]
recipes:  recipes.j2
actions:  actions.py
checks:   checks.py
kwargs:   hmi/kwargs.j2      # the kwargs themselves (data)
setup:    hmi/setup.js       # screen to SET the kwargs, before the run
pendant:  hmi/pendant.html   # screen shown DURING the run
```

Operator-facing files live in **`hmi/`**. Inline `kwargs:` still works
for legacy/small projects. Field types are project-guide §3.

**Full contracts + traps: the `project-ui` skill.** In brief —
**one contract, two screens.** `kwargs:` always — it is the schema, read
headlessly by `bt.replay`, so it stays YAML and never describes how
anything looks. `setup:` only when run setup needs more than rows of
scalars (picking WHICH rack positions to run — that is a picture of your
hardware, so you draw it). `pendant:` only when the pendant should show
something during the run. The two screens are named for WHEN they
appear; both are project files (`.html` or `.js`).

**The pendant screen is the project's own file** — `hmi/pendant.html`
plus a sibling `pendant.css` (or `pendant.js` when it needs logic).
The platform hosts it in a shadow root, fills `data-bind` attributes
from `rt.op`, and passes down the design tokens; it holds no domain
widgets. Write it against `docs/design-system.md` tokens and never a
raw hex — that is what makes light/dark work for free. Contract and a
worked example: **hmi-guide §4b**; bd's screen is the reference.

A project that would rather write no markup can point `pendant:` at an
`hmi/hmi.j2` widget list instead (hmi-guide §4) — fine for bring-up,
not where domain features belong. No `pendant:` key at all = the default
pendant.

## Scene file ownership + caches

- The scene BUILDER owns ``layout.j2`` and regenerates it wholesale —
  hand-added blocks there get clobbered on the next export. Hand-
  maintained scene content (stock: caps in a feeder, consumables)
  lives in ``stock.j2``, listed AFTER ``layout.j2`` in launch.yaml's
  scene list so the merge applies it on top.
- ``core/ik.json`` and ``core/path.json`` are stamped with a scene
  fingerprint and AUTO-DISCARD when the scene changes — the old
  "delete path.json after geometry changes" ritual is obsolete.
  Unstamped legacy caches are treated as stale once.

## Canonical references

- `docs/recipe-guide.md` — recipe kwargs, motion primitives
- `docs/bt-framework-guide.md` — actions, planning, slicing
- `docs/project-guide.md` §8 — device reads, capacity facts
- Gold exemplars: `examples/` (CLAUDE.md table) — copy, change minimum
