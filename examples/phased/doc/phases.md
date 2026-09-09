# phased — the boundary table

One row per phase. A row says what the bench looks like the moment the
phase closes — that is the contract the next phase's actions start
from, and the reason each phase can be built and bench-checked alone.
Write the row BEFORE the phase's actions; change the row before
changing them.

| phase | fact (per tube, monotonic) | every tube is | gripper | tool mounted | opens when |
|---|---|---|---|---|---|
| `weighed_1` | `home_1` | back in its own rack slot, weighed once | empty | gripper | run started |
| `weighed_2` | `home_2` | back in its own rack slot, weighed twice | empty | gripper | every tube has `home_1` |

Rules the rows obey:

- **The fact is one nothing removes.** `home_1` stays true through pass 2;
  a shared `home` would be cleared by the next pick and the phase would
  re-open (bt-framework-guide §13, monotonicity).
- **Nothing is mid-carry.** Every tube is in a slot, the gripper is
  empty. A boundary that leaves a tube in the hand or on a station is
  illegal — fuse those actions into one phase instead.
- **The tool at exit is stated** so the swap cost at each seam is a
  decision, not a surprise.
- **Recorded fields** produced in the phase belong in the row's phase
  module, written where the value is read: `weight_1_g` in `Weigh1`,
  `weight_2_g` in `Weigh2`, `status` at Park from the facts.
