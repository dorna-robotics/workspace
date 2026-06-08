"""feeder example protocol.

Three-action shape — the same minimal template as multimeter_test
but with motion + tool change:

    Start            — runs once. Picks the suction tool from the
                       tool rack so subsequent FeedCap actions can
                       run without an explicit pickup step.
    FeedCap(cap)     — for each cap, hover over the feeder, pick the
                       cap currently at the feeder's ``place`` anchor,
                       place it in the next cap-holder slot, advance
                       the feeder by one step.
    Park             — runs once at end. Drops the suction tool back
                       on the rack, parks the robot.

``cap_count`` (operator kwarg) controls how many FeedCap actions
the planner schedules. The first 10 cap-holder slots (A1..A10) are
used as destinations; if you raise ``cap_count`` above 10 you'll
need to extend ``CAP_HOLDER_SLOTS`` below.

Framework reference: ../../../../docs/bt-framework-guide.md
"""

from __future__ import annotations

from workspace.bt import Action, predicate


# ── 1. Predicates ──────────────────────────────────────────────────────────

started   = predicate("started")     # workspace initialised (global)
parked    = predicate("parked")      # robot parked at end of run (global)
cap_fed   = predicate("cap_fed")     # cap c has been transferred to the holder


# Destination slots in the cap holder, in the order FeedCap fills
# them. Extend this list (or generate it from a Jinja-style range) if
# you want runs with more than 10 caps.
CAP_HOLDER_SLOTS = [f"A{c}" for c in range(1, 10 + 1)]


# ── 2. setup — operator kwargs → planning inputs ───────────────────────────


def setup(**kwargs):
    cap_count = int(kwargs.get("cap_count", 1))
    caps = list(range(cap_count))

    facts: set = set()

    def item_done(state, cap):
        return (cap_fed.name, cap) in state

    def goal(state):
        return (
            (started.name,) in state
            and all(item_done(state, c) for c in caps)
            and (parked.name,) in state
        )

    goal_facts = frozenset(
        [(cap_fed.name, c) for c in caps]
        + [(started.name,), (parked.name,)]
    )

    return {
        "initial_facts": frozenset(facts),
        "goal":          goal,
        "item_done":     item_done,
        "goal_facts":    goal_facts,
        "objects":       {"cap": caps},
    }


# ── 3. Actions ─────────────────────────────────────────────────────────────


class Start(Action):
    """First action of every run — operator's initialisation hook.

    Turns motors on and moves the robot to a known safe joint pose
    via the planned ``park`` (collision-aware ``smove`` rather than a
    blind ``jmove``). Put your axis-init / homing / device-warmup
    calls in ``execute`` — this is the place for them.

    The BT framework's tool-swap machinery will pick the suction tool
    on the first ``FeedCap`` action automatically (driven by
    ``FeedCap.tool = "cap_tool"``), so Start doesn't need to do a
    manual pickup.
    """

    params   = []
    duration = 5
    resource = "robot"

    def pre(self):
        return ~started()

    def eff(self):
        return {"started": (+started(),)}

    def execute(self):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes
        rt.motor(1)
        # Any recipe inherits ``park`` from the base Recipe class — we
        # use ``cap_tool`` here because it's the only tool recipe in
        # this minimal example. Same call sample_prep makes via
        # ``gripper``.
        rcp["cap_tool"].park(joint=[0, 45, -90, 0, -45, 0, 100], has_motion_plan=True)
        return "started"


class FeedCap(Action):
    """Transfer one cap from the feeder to the cap holder.

    Flow per cap:
      1. ``feeder.above("plate_center")`` — hover over the feeder's
         rotating plate at a safe height. Pure positioning, no IO.
      2. ``feeder.pick(approach=False)`` — pick the cap currently at
         the feeder's ``place`` anchor. The suction tool's gripper
         IO fires automatically (built by ``pick_setting``).
         ``approach=False`` skips the corridor — we just hovered, so
         a second corridor would waste time.
      3. ``cap_holder.place(SLOT)`` — release into the next free
         slot. ``cap_holder`` is the Rack recipe (resolver pattern);
         it delegates to ``capholder_autosampler_2ml_5x10_1`` via
         the adapter plate.
      4. ``feeder.rotate_in_step(step=1)`` — advance the feeder one
         slot so the next FeedCap call picks a fresh cap.

    All four calls are pause-aware through ``rt.*`` — operator can
    Pause mid-transfer and the motion halts cleanly.
    """

    params    = ["cap"]
    duration  = 10
    resource  = "robot"
    tool      = "cap_tool"   # auto-pickup via framework tool-swap

    def pre(self, cap):
        return started() & ~cap_fed(cap)

    def eff(self, cap):
        return {"placed": (+cap_fed(cap),)}

    def execute(self, cap):
        rt  = self.ctx.runtime
        rcp = self.ctx.recipes

        slot = CAP_HOLDER_SLOTS[cap]
        rt.step(f"cap {cap + 1}: feeder → cap_holder[{slot}]")

        rcp["feeder"].above(anchor="plate_center")
        rcp["feeder"].pick(approach=False)
        rcp["cap_holder"].place(slot)
        rcp["feeder"].rotate_in_step(step=1)

        return f"cap {cap + 1} placed at {slot}"


class Park(Action):
    """Last action of every run. Motors off, parked flag set.

    The tool is auto-returned to the tool rack by the BT framework's
    tool-swap machinery when Park (which has no ``tool``) runs after
    the last FeedCap.
    """

    params   = []
    duration = 1
    resource = "robot"

    def pre(self):
        caps = self._ctx_all_objects().get("cap", [])
        expr = ~parked() & started()
        for c in caps:
            expr = expr & cap_fed(c)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rt = self.ctx.runtime
        rt.motor(0)
        rt.step("workspace parked", level="success")
        return "parked"
