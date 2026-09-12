# phased — the boundary table

One row per phase. A row is the contract the next phase's actions are
written against, and the reason each phase can be built and
bench-checked alone. Write the row BEFORE the phase's actions; change
the row before changing them.

A row names three things:

- **The closure fact** — one per item, asserted by EVERY way the phase
  can end for that item. It is what the phase waits for, and it is
  monotonic by construction: nothing ever takes "this item is through
  phase N" back.
- **The outcomes** — the ways the phase can end for an item, each an
  outcome fact plus the physical resting state it means. Every item
  ends with exactly one. A sorting phase has two ("in the heavy rack",
  "in the light rack"); a phase with a skip rule has the normal outcome
  and "skipped". The next phase's actions gate on these.
- **What is measured** — the audit fields the phase writes, and where.

| phase | closure fact | outcomes → resting state | measured | opens when |
|---|---|---|---|---|
| `weighed_1` | `home_1(t)` | `home_1`: back in its own rack slot, weighed once | `weight_1_g` in `Weigh1` | run started |
| `weighed_2` | `home_2(t)` | `home_2`: back in its own rack slot, shaken and weighed twice. Inside: seated on the shaker (`on_shaker`, seat `t mod 4`, a `seat_free` capacity fact), its bank shaken (`shaken`, one cycle for the four of a load), back in its slot (`unloaded` — pass 2's gate), then weighed as in pass 1. ONE phase, grouped by bank (`phases.banks`): bank 2 shakes while bank 1 is weighed | `weight_2_g` in `Weigh2`; `status` at Park, from the facts | every tube has `home_1` |

This example has one outcome per phase, so the closure fact and the
outcome fact are the same predicate. With two or more outcomes they
split: `sorted(t)` closes the phase, `in_heavy(t)` / `in_light(t)` say
where the tube is, and the deciding action returns one of two `eff`
branches (bt-framework-guide §7).

Rules the rows obey:

- **Nothing is mid-carry at a boundary.** Every item rests somewhere
  named; the gripper is empty. A boundary that would leave an item in
  the hand or on a station is illegal — fuse those actions into one
  phase instead.
- **The decision is an action, not a boundary.** "Above 12 g goes
  left" lives in the weighing action's outcomes, inside the phase.
- **Stages that overlap across groups share a phase.** A barrier is a
  wait for everyone; the shake and the second weighing are one row so
  the scheduler can weigh bank 1 while bank 2 shakes. The phase names
  the groups (`group`) and the planner stamps one bank's chain per
  bank — never a search (bt-framework-guide §13 "Groups").
- **Nothing about tools.** Which tool is mounted at the end is whatever
  the last scheduled action used; the next phase's schedule charges
  the swap wherever it falls. A tool parked on purpose is an explicit
  `tool = None` action.
