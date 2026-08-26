# Motion Guide

**The one reference for how the platform moves the robot.** Everything
motion lives here: the primitives and their continuous twins, the
`has_motion_plan` grammar, the group grammar (what is one motion, where
the stops are), the builders (`pick_setting` / `place_setting`), planned
travel and the fold, blending, trajectory certification, speed classes,
the infinite wrist, screw motions, immerse/retract, and the agreed
design for cross-verb continuous motion.

Code truth lives in `workspace/workspace/recipes/recipe.py` (the
builders, `touch`, `_move_along_path`) and
`workspace/workspace/components/core/core.py` (`motion_plan`, IK,
blending, trajectory certification). When this doc and the code
disagree, the code wins — then fix this doc.

Related docs this one does NOT absorb:
`docs/recipe-guide.md` (recipe structure, DEFAULTS, calibration),
`docs/parameter-guidelines.md` (per-station tuning heuristics),
`docs/tmove-firmware-spec.md` (PVT trajectory firmware contract),
`docs/liquid-handling.md` (what the fluid verbs do around the motion).

## Contents

1. [The motion stack](#1-the-motion-stack)
2. [Primitives and the `has_motion_plan` grammar](#2-primitives-and-the-has_motion_plan-grammar)
3. [The group grammar — motions and stops](#3-the-group-grammar--motions-and-stops)
4. [The builders — pick_setting / place_setting](#4-the-builders--pick_setting--place_setting)
5. [Planned travel — motion_plan and the fold](#5-planned-travel--motion_plan-and-the-fold)
6. [Trajectory certification — the [traj] line](#6-trajectory-certification--the-traj-line)
7. [Speed — vaj, speed classes, speed_factor](#7-speed--vaj-speed-classes-speed_factor)
8. [The exit](#8-the-exit)
9. [The infinite wrist — j5 turn-carry](#9-the-infinite-wrist--j5-turn-carry)
10. [Special motions — screw, immerse/retract, soft approach](#10-special-motions--screw-immerseretract-soft-approach)
11. [Padding and the solver](#11-padding-and-the-solver)
12. [DESIGN: cross-verb continuous motion (the fusion buffer)](#12-design-cross-verb-continuous-motion-the-fusion-buffer)

---

## 1. The motion stack

```
pick / place / above / stand / immerse / retract   ← public recipe verbs
                    │
     pick_setting / place_setting                  ← build the param dict:
                    │                                approach groups, contact,
                    │                                exit groups, IO, attach
                    ▼
                touch(**prm)                       ← THE universal primitive:
                    │                                groups → motions, IO
                    │                                barriers, attach
                    ▼
            _move_along_path (per group)           ← IK-solve offsets, fold or
                    │                                chain, pin j5
                    ▼
   rt.jmove | lmove | cjmove | clmove | smove | tmove
                    │                                pause-aware execution
                    ▼
              core.robot_api                       ← real firmware or
                                                     SimulationAPI
```

Every robot motion in the platform goes through this stack. Nothing
issues raw moves beside it (the screw motion drives `rt.lmove/jmove`
directly, but through the same rt layer — §10).

## 2. Primitives and the `has_motion_plan` grammar

Two **motion classes** exist, declared per recipe as `motion_type`
(`"jmove"` or `"lmove"`, default `lmove`):

| class | discrete form | continuous twin | interpolates |
|---|---|---|---|
| jmove | `rt.jmove` | `rt.cjmove` | joint space |
| lmove | `rt.lmove` | `rt.clmove` | straight TCP line (tool-pose compensated) |

A continuous twin is a **chain**: sections queued `cont=1` with a
per-section corner radius, final section `cont=0` (decelerate to
stop). The firmware blends the corners; the robot does not stop
between sections. `smove` is one spline through all points; `tmove` is
a TOPP-RA-timed PVT trajectory (see `docs/tmove-firmware-spec.md`).

**`has_motion_plan` — one grammar owns both decisions** (whether the
travel hop is planned, and which primitive executes it):

| value | planned? | travel executes as |
|---|---|---|
| `true` / `[true, smove]` | yes | one spline through the fold |
| `[true, tmove]` | yes | PVT trajectory (TOPP-RA timing) |
| `[true, cjmove]` | yes | cont-jmove chain, bare knots, firmware corners |
| `[true, clmove]` | yes | cont-lmove chain, bare knots, straight TCP lines |
| `[true, jmove]` | yes | DISCRETE — full stop at every planner waypoint |
| `[true, lmove]` | yes | discrete lmoves per waypoint |
| `false` / `[false, jmove]` | no | one direct jmove |
| `[false, lmove]` | no | one direct lmove |
| `[false, cjmove]` | no | ≡ `[false, jmove]` (a one-target chain is discrete) |
| `[false, clmove]` | no | ≡ `[false, lmove]` |

Resolution order: per-call kwarg > recipe (`recipes.j2` kwargs) >
`core.has_motion_plan` (scene). The grammar governs the **travel
only** — approach corridors, contact legs and exits always run the
recipe's motion class (§3, §8).

## 3. The group grammar — motions and stops

`touch` takes `approach` and `exit` as **lists of GROUPS**; a group is
a list of `[x, y, z, a, b, c]` offsets in the target anchor's frame.
The grammar (docstring at `recipe.py::touch`):

* **A group is ONE continuous motion** — the robot never stops inside
  it. Multi-point groups run as the continuous twin of the motion
  class (cjmove/clmove); single points run discrete.
* **A boundary between groups is a full stop with a NAMED purpose**:
  the IO verify barrier, a speed-class change, or a process action.
  A stop that serves none of these is a bug — merge the groups.
* **The planned travel hop is implicit** — it is the entry into the
  FIRST approach group (`travel=False` keeps it direct/unplanned;
  the builders set that when `approach=False`).
* The last approach group runs the touch speed class; exit groups run
  their class the same way. **Exit groups never plan.**

Worked example — `approach = [[l0, l1, l2], [l3, l4], [l5]]` with
planned travel:

```
current ──(planned travel + l0 + l1 + l2, ONE chain)──▶ STOP at l2
        ──(l3 + l4, one chain)────────────────────────▶ STOP at l4
        ──(l5, discrete)──────────────────────────────▶ STOP at l5
```

Three motions; stops only at each group's last point. The planned
chain absorbs **only the first group** — every later group is its own
motion (`touch` passes `first_approach` for group 0 only).

Flow of a full touch: approach groups → `output_touch` IO, actions,
sleep, attach → exit groups → `output_exit` IO. The approach IO chain
(`output_approach`) overlaps the FIRST group and is joined +
pin-verified at its end; with a single group it completes before
motion starts — contact never begins on an unverified chain.

## 4. The builders — pick_setting / place_setting

The builders compute the groups. Heights involved:

* `height_load` — the payload: for **pick**, the stack sitting at the
  target anchor; for **place**, the stack attached to the tool
  (measured from its load anchor to the stack top).
* `height_container` — the target anchor to the component's `top`.
* `height_tool` — tool tcp→tip (shifted by `tool_tcp/tip_z_offset`).

Standard shapes:

```
a_pad   = max(height_load, height_container) + padding    (approach/exit clearance)
a_gap   = pick:  height_load + height_tool + gap          (soft-approach stop)
          place: height_container + gap
contact = the touch pose (place adds gravity_offset in z)
```

| knob | meaning |
|---|---|
| `approach=True` | corridor: planned travel → a_pad → (a_gap) → contact |
| `approach=False` | no corridor: the contact hop runs direct and unplanned (`travel=False`). The straight dive — bench-approved for vessel entry. `soft_approach` is inert here. |
| `soft_approach=True` | contact point gets its own final group → stop at a_gap, then a slow contact leg. `False`: contact folds into the previous group — one continuous motion to touch. Defaults: place verb True, pick verb False. |
| `exit=True/False/number` | exit leg at padding clearance / no exit leg / that clearance in mm (`exit=0` raises — use False) |
| `gravity_offset` (place) | z at touch-down: positive = release above and let it drop (default 1), negative = drive deeper (suction) |
| `padding` | resolution: per-call > recipe (`recipes.j2`) > method default (50) |

**The place frame rule:** for place, approach/exit tool frames are the
**LOAD's** anchor (`approach_tool = load_list[0]` at `load_anchor`),
so clearances are measured from the bottom of what you carry — the
carried stack always clears the container by ≥ padding. For pick, the
frame is the tool TCP (shiftable by `tool_tcp_z_offset`).

## 5. Planned travel — motion_plan and the fold

`core.motion_plan(joint)` produces the travel waypoints:

1. **Scene build** — `compute_collision_boxes(padding)` (default
   padding 10 mm), boxes → planner cubes, planner updated.
2. **Path cache** — `core/path.json` in the project folder, keyed on
   (start, goal, tool-box signature). Hit = replay, validated at
   creation only. Stale on scene change (stamped).
3. **Direct connect** — if the straight start→goal segment clears the
   padded envelope, it IS the path (collision-certified bare hop).
4. **OMPL** — else plan (AIT* @ 10 s default; RRTConnect fallback on
   PHS degeneracy), then decimate to essential corners (per-segment
   collision gate at `padding − margin`).
5. **Canonical j5** — the planner's joint space is canonical
   ((−180, 180]); a wound wrist is canonicalized before
   check/plan/cache and every returned waypoint re-carries the turns
   (§9). OMPL silently CLAMPS out-of-bounds joints — never feed it
   wound values.

**The fold** (`_move_along_path`, `first_approach=True`, blend > 0):
planner waypoints + the remaining offsets of the FIRST approach group
become one executed path. For `smove`/`tmove` the approach legs are
sampled every 5 mm and every sharp corner gets a G1 Bezier fillet
(`blend` radius, default 75 from the recipe; each fillet validated
against the slimmed envelope — an arc may not introduce a collision
the sharp corner didn't have). For `cjmove`/`clmove` the offsets are
BARE knots — the firmware blends corners; midpoints are never touched.

`[plan] START is inside the collision envelope` at plan time means the
PREVIOUS motion ended inside a padded box — usually an `exit=False`
place followed by a planned approach; pair those with
`approach=False` on the next verb, or restore the exit.

## 6. Trajectory certification — the [traj] line

Chains (`cjmove`/`clmove`) are certified before execution with the
firmware's exact math (`core.traj_points` / `_fw_verify_chain`): the
ported cont()/createProfile section profiles with carried velocity
over ported corner geometry. The search shrinks per-section vel/accel
until the measured peaks fit the caps (braking sections reduce the
UPSTREAM speed, not their own accel — cutting braking authority
diverges). Reading the line:

```
[traj] 5 pts -> 4 cjmove sections, vels [248,216,82,82], corners [0,48,13,0],
       legs [303,196,120,32], bind ['v','b','c','e'],
       certified: joint vel 241/260, acc 686/676, solved in 135 ms
```

* `vels` — per-section commanded velocity after certification
* `corners` — per-section blend radius (0 = sharp/stop)
* `legs` — section lengths (mm)
* `bind` — what bound each section: `v`elocity cap, `b`raking,
  `c`orner curvature, `e`nd condition
* `certified: measured/cap` — peak joint vel and accel vs caps; small
  overshoot within the search tolerance (~5%) is accepted
* `DEGRADED to stop-at-every-knot` — certification would not converge:
  the always-valid fallback (stop at every waypoint, no blends) runs
  instead, loud, with speeds recomputed for the stop-to-stop geometry.
  It means the model is wrong somewhere worth reporting.

## 7. Speed — vaj, speed classes, speed_factor

* Recipes carry `jmove_vaj` and `lmove_vaj` (`[vel, accel, jerk]`)
  as DEFAULTS, overridable per recipe in `recipes.j2`.
* `speed_factor` scales **physically**: vel × s, accel × s², jerk × s³
  (`scaled_vaj`). Every builder-driven motion is scaled.
* The **touch speed class**: the last approach group (the contact leg)
  runs lmove-class speeds — that is the point of the group boundary
  before it.
* **Screw motions are NOT speed_factor-scaled** — `_screw_motion`
  sends its `lmove_vaj`/`jmove_vaj` raw (`decap()`/`cap()` defaults;
  override at the call site). The screw's commanded lmove vel barely
  matters anyway: the Cartesian path is millimetres, so the firmware's
  per-joint caps on j5 govern the actual unscrew rate.

## 8. The exit

* Standard pick/place: **one single-point group** — a straight lift to
  `max(height_load, height_container) + exit_clearance`, one discrete
  move in the recipe's motion class, then a stop.
* Corridor exits (tool_rack): multi-point groups run as one continuous
  twin chain each; boundaries between groups are deliberate stops.
* Exits never plan and never get smove blend-fusion (approach-only).
* **The last exit point is the NEXT hop's start** — it must park
  OUTSIDE the plan-padded station box or the next planned travel
  refuses (`START is inside the collision envelope`). Recipe padding
  owns that clearance.

## 9. The infinite wrist — j5 turn-carry

Scene flag `j5_infinite: true` on the core (chassis template). The
firmware j5 counter is absolute and NEVER rewritten (`set_joint` is a
calibration command — forbidden in normal operation).

**The invariant: no commanded j5 ever differs from the live j5 by
more than one turn (360°).** Every executed joint target is unwrapped
against the live joints first — turn-carry, not shortest-path:
`unwrap_j5(target) = canonical(target) + 360 × turns(live)`. A limited
wrist's path semantics are preserved exactly (170→−170 travels −340
through 0, never the 20° seam shortcut), so bench-validated corridors
keep applying; the accumulated turns just ride along.

Where the carry is applied (each one measured, each one was once a
real unwind bug):

* `core.IK` — all returns, cache hits included; cache stays canonical
* `_solve_ik` `j5_override` (lock_j5 pins without unwinding)
* `_pin_j5` — the pin is unwrapped: `lock_j5: 0` names the SHAFT
  angle, not the firmware counter
* `Recipe.park`, `_screw_motion` (relative from live wrist, one-shot)
* `core.motion_plan` — plans canonical, re-carries every waypoint
* `core.check_collision` — canonicalizes state queries (planner limit
  table is ±179)

Tools: `lock_j5` (needle gripper's stripper rods) pins the roll on
every immerse/retract target; `approach_j5/exit_j5="keep"` pins at the
live angle (round payloads — no post-screw "correction"). Decapper
verbs default to "keep" on an infinite wrist; cap/decap run ONE-shot
(single helix lmove, no re-bites, no unwind).

**J5WindingGuard** (core) wraps the robot api on an infinite wrist: any
jmove/cjmove/smove/tmove whose j5 breaks the one-turn invariant prints
the command and the full call stack — non-blocking, names the leaking
layer in one reproduction.

## 10. Special motions — screw, immerse/retract, soft approach

**Screw** (`_screw_motion`): rotates j5 about the tool axis while z
advances by pitch — a helix. Limited wrist: chunked ±`max_rotation`
with gripper re-bites between chunks. Infinite wrist: ONE lmove for
the whole twist, relative from the live wrist, no staging rotation, no
rewind. Twist/pitch come from the cap component (`twist=None` default
in `decap` — never silently override the component's value).

**Immerse / retract**: the held tool's **tip** reaches `dist` below
the target (`tool_tip_z_offset = height_load − dist` — a 30 mm tip
and a 100 mm needle end at the same depth). `approach=False`
(default) is two-phase: hover at `padding` above the payload via
`above`, then ONE straight vertical dive (`pick(approach=False)`).
`approach=True` is single-phase through the corridor with the depth
offset — requires padding to comfortably exceed load height, and the
blended lateral entry was REJECTED on the bench for vessels: use the
straight dive. `lock_j5` pins the wrist through both phases.

**Soft approach** (`soft_approach=True`, corridor only): the contact
point becomes its own final group → full stop at the gap point
(`gap` mm, default 2), IO verified, then a slow lmove-class press to
contact. With `soft_approach=False` the S-curve decelerates to zero at
contact anyway — one continuous motion to touch.

## 11. Padding and the solver

`padding` buys clearance at `max(height_load, height_container) +
padding` — for place, measured from the **bottom of the carried
load** (§4), so "padding 20" means the load bottom passes ≥ 20 mm
above whatever occupies the target.

The solver (`solve.py`) reports, per station, the height at which the
approach beam clears — in the LOADED frame (`pad loaded` guesses the
payload from scene stock: this anchor → sibling → same-type component)
and `pad empty`. Planner boxes are inflated by the plan padding
(default 10) + margin. Tune per recipe in `recipes.j2`; defaults are
50.

## 12. Cross-verb continuous motion (the fusion buffer)

**Status: PHASES 1 + 2 IMPLEMENTED.** Phase 1: deferred tail on
`core`, recipe-layer deposit/merge, verb-level barriers. Phase 2: the
window stays open ACROSS action boundaries — a successful leaf leaves
the tail armed (it survives no-motion actions like a weigh), and the
seams that must break it do: any failure path flushes, a non-default
branch flushes before `ReplanRequested`, and a killed runtime DROPS
the tail (never move after a kill). Core-level safety invariant: a
flush whose deposit pose no longer matches the live robot (kill +
jog, any out-of-band motion) drops the tail loudly instead of
executing it. Streaming commit (overlap tail execution with the next
plan solve) is phase 3, design-only — with the single-tail design,
chains stay bounded, so it buys only planning overlap, not pause
latency.

### Phase 1 — what ships

* **Knob**: recipe kwarg `fuse: true` (recipes.j2, default false) or
  per-call `fuse_exit=True/False` on any verb. Off = today's motion,
  bit for bit.
* **Deposit**: a fusing verb's LAST exit group is IK-solved and held
  on `core` (`tail_deposit`) instead of executed — unless `output_exit`
  IO follows it (IO is a barrier by definition).
* **Merge**: the next verb's fold consumes the tail —
  `tail + planned travel + first approach group` run as ONE chain in
  the fold's planned primitive (`smove`/`tmove` sample the tail like
  approach legs; `cjmove`/`clmove` carry it as bare knots — the same
  coercion the fold already applies to approach offsets), certified
  and blended as one path.
* **Frontier**: while a tail is held, `core._live_joints()` answers
  with its endpoint — IK `cur`, `unwrap_j5` refs and `motion_plan`
  start all reason from where the robot WILL be. Recipes must read
  joints via `self._cur_joints()`, never `rt.joint()`.
* **Flush** (execute the tail to today's stop): any non-fold motion
  path (`_move_along_path` non-merge branches, `_execute_motion_planned`
  — park, `_screw_motion`), a second deposit, discrete planned
  primitives, and fold failure. At the ACTION level (phase 2): a
  successful leaf keeps the tail armed for the next robot action;
  every leaf failure path flushes it; a non-default branch flushes
  before the replan; a killed runtime drops it. A flush whose deposit
  pose no longer matches the live robot drops the tail loudly instead
  of executing it.

The original design (kept below — it is the contract phase 2 builds
on):

Today's continuity boundary is the group (§3): the planned chain
absorbs the first approach group, and every verb ends at a stop after
its last exit point. That terminal stop usually has NO named purpose —
by the grammar's own rule, it is the bug this design removes.

**Symmetry statement.** The first approach group is the chain's
**head**; the last exit group is its **open tail**. Heads already fuse
backward into the incoming travel. The fusion buffer lets tails fuse
forward into the next head:

```
…exit stops)  [last exit group] ──▶ (next verb's planned travel) ──▶ [first approach group] (…
              └──────────────── ONE continuous chain ────────────────┘
```

**Mechanism — hold back, don't look ahead.** The runtime holds the
tail motion unexecuted in a buffer instead of executing it at verb
end. The next rt command decides by its TYPE:

* another mergeable motion → fuse the junction (collision-checked
  fillet, chain profile recomputed over the merged path) and keep the
  chain growing — fusion CHAINS across any number of verbs/actions;
* a **barrier** → flush: execute the held tail to a normal stop.

Barriers (each must observe a stopped robot): gripper/tool IO, device
reads, `rt.checkpoint`/sleep/delay, pre/post_checks, operator pause,
plan end, errors, resource change. Branch decisions read state, so
branching flushes automatically — no action needs to know its
successor.

**The frontier rule (the one hard correctness requirement).** While a
tail is held, live joints are stale. Every IK reference, `unwrap_j5`
ref, motion_plan start and cache key must use the **planned frontier**
(the last queued target), not `rt.joint()`. The j5 turn carry rides
the frontier.

**Merge eligibility is DECLARED, never inferred** (explicit over
adaptive): approach/exit corridors and planned travel are fusible;
precision segments never are — the soft-approach press, screw helix,
the immerse dive below its hover, gap stops, anything ending at IO.
Verbs get a `fuse_exit`-style flag for stations that want their stop
regardless (scale, decapper).

**Determinism.** No timing in flush decisions — the buffer never
executes "because the next command didn't arrive fast enough". Same
plan in, same motion out.

**Pause safety.** A pause request is a barrier. With the streaming
window (segments committed to the firmware one junction at a time,
the tail kept open), pause reaction latency equals today's
one-segment behavior; firmware halt is untouched underneath. The
buffer records the owning action per segment for fault attribution
and recovery.

**Rollout.** Phase 1: fuse within one action's `execute()` (consecutive
verbs through the buffer). Phase 2: the BT engine holds the window
across an action boundary when the next robot action is unconditional
— the engine owns the schedule, so it, not the actions, knows.
Streaming window lands with phase 2 (it is what makes long chains
pause-responsive).

Prior art: this is industrial controller motion blending (ABB zones,
KUKA `C_DIS`, FANUC `CNT`, UR blend radius, CNC G64 lookahead) lifted
to the planner-selected action layer, with barriers derived from the
device/check semantics instead of hand-written programs.
