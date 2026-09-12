"""phased — a phased protocol as a PACKAGE: one module per phase.

    actions/
      predicates.py   every fact, grouped by phase, one line of meaning each
      base.py         abstract action bodies + shared helpers
      phase_1.py      the concrete actions of phase 1, in execution order
      phase_2.py      the concrete actions of phase 2 — the shake, then pass 2
      __init__.py     THIS FILE — the bookends and setup
    phases.py         one Phase class per phase, each with its ``route``, and the
                      run's ROUTE: ``[Start, Weighed1, Weighed2, Park]``
    doc/phases.md     the boundary table — what the bench looks like at each line

``launch.yaml`` names the package (``actions: actions/``) and the route
(``route: phases.py``). What runs is what the ROUTE lists — a phase's
``route`` is the order its steps are met. To add a phase: one row in
doc/phases.md, one ``phase_N.py``, one class in phases.py with its
``route``, its place in ROUTE — in that order.

Two weigh passes over one rack, a shake in banks of four between
them, is a deliberately small protocol; the shape is what this example
teaches (bt-framework-guide §13) — and, in phase 2, how a step whose
``pre`` spans a bank pipelines against the work after it.
"""

from workspace.bt import Action

from actions.predicates import PASSES, hand_empty, home, pan_empty, parked, seat_free, started
from actions.base import item_id, slot_of


def setup(**kwargs):
    tubes = list(range(int(kwargs.get("batch_size", 4))))
    last = home[PASSES[-1]]

    def item_done(state, tube):
        return (last.name, tube) in state

    def goal(state):
        return ((started.name,) in state
                and all(item_done(state, t) for t in tubes)
                and (parked.name,) in state)


    return {
        "initial_facts": frozenset(),
        "goal":          goal,
        "item_done":     item_done,
        "objects":       {"tube": tubes},
    }


def _status_of(facts, tube):
    """The audit status, DERIVED from the facts — never typed by hand."""
    reached = [p for p in PASSES if (home[p].name, tube) in facts]
    if len(reached) == len(PASSES):
        return "done"
    return f"weighed_{reached[-1]}" if reached else "not started"


class Start(Action):
    params       = []
    duration     = 5
    resource     = "robot"
    START_JOINTS = [0, 45, -90, 0, -45, 0, 100]

    def pre(self):
        return ~started()

    def eff(self):
        return {"started": (+started(), +hand_empty(), +pan_empty(),
                            *(+seat_free[i]() for i in seat_free))}

    def execute(self):
        rt, rcp, ws = self.ctx.runtime, self.ctx.recipes, self.ctx.workspace
        core = ws.components["core"]
        # Seed one audit row per tube before anything is measured, keyed
        # by identity, position as a field (project-guide §3 rt.record).
        for t in self._ctx_all_objects().get("tube", []):
            rt.record(item_id(t), slot=slot_of(self, t))
        rt.motor(1)
        if core.has_rail:
            rt.step("homing rail")
            if not rcp["robot"].set_axis_with_stop(core.rail_cfg):
                rt.step("homing failed")
                return "killed"
        rcp["robot"].park(joint=self.START_JOINTS)
        return "started"


class Park(Action):
    """Final park — planned after every tube is home from the last pass."""
    params      = []
    duration    = 5
    resource    = "robot"
    tool        = None
    PARK_JOINTS = [0, 90, 0, 0, 0, 0, 100]

    def pre(self):
        tubes = self._ctx_all_objects().get("tube", [])
        expr = ~parked() & started()
        for t in tubes:
            expr = expr & home[PASSES[-1]](t)
        return expr

    def eff(self):
        return {"parked": (+parked(),)}

    def execute(self):
        rt, rcp = self.ctx.runtime, self.ctx.recipes
        facts = (getattr(self.ctx, "state", None) or {}).get("facts") or set()
        for t in self._ctx_all_objects().get("tube", []):
            rt.record(item_id(t), status=_status_of(facts, t))
        rcp["robot"].park(joint=self.PARK_JOINTS)
        return "parked"


class OperatorPark(Park):
    """Operator-initiated park — fires on the Park button, outside the
    plan. Inherits Park's execute, so every row still gets its status
    from the facts as they stand."""
    trigger = "park"
