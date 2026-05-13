"""Scheduling metadata for pace_bt — durations and resource claims.

The framework's scheduler reads this dict to lay out the PDDL plan
across time. One entry per action template defined in ``domain.py``.

Resources here are the things that two actions can't share at the
same moment. We model:

  * ``robot``      — the arm (one robot in this scene)
  * ``scale``      — the scale (used briefly during inspect)
  * ``dispenser``  — the 40 mL dispenser
"""

from __future__ import annotations

from workspace.planner import ActionMeta, make_schedule_builder


META = {
    # name              duration (sec)  resource
    "inspect":         ActionMeta(duration=10, resource="robot"),
    "decap":           ActionMeta(duration=10, resource="robot"),
    "dispense_light":  ActionMeta(duration=10, resource="dispenser"),
    "dispense_heavy":  ActionMeta(duration=15, resource="dispenser"),
    "recap":           ActionMeta(duration=10, resource="robot"),
    "shelve":          ActionMeta(duration=5,  resource="robot"),
}


# The framework's Replanner expects ``build_schedule(plan) -> schedule``.
build_schedule = make_schedule_builder(META)
