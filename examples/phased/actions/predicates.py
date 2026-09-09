"""Every fact this protocol uses, grouped by phase, one line of meaning
each. The single place to look up what a fact means.

Per-pass facts are PASS-INDEXED (``picked_1``, ``picked_2``), never a
``pass`` parameter: an action takes one param, and a phase boundary must
be a fact nothing removes — ``home_1`` stays true forever, where a
shared ``home`` would be cleared by pass 2's pick and re-open phase 1.
"""

from workspace.bt import predicate

PASSES = (1, 2)


def per_pass(name):
    """``{1: predicate("name_1"), 2: predicate("name_2")}``."""
    return {p: predicate(f"{name}_{p}") for p in PASSES}


# ── Bookends ───────────────────────────────────────────────────────
started = predicate("started")
parked  = predicate("parked")

# ── Single-occupancy resources (capacity facts) ────────────────────
# One gripper, one balance pan. capacity=True makes the scheduler
# derive an exclusive span between the action that clears the fact and
# the one that restores it (project-guide §8).
hand_empty = predicate("hand_empty", capacity=True)   # gripper holds no tube
pan_empty  = predicate("pan_empty", capacity=True)    # balance pan is free

# ── Per pass: phase_1.py (pass 1), phase_2.py (pass 2) ─────────────
picked    = per_pass("picked")      # tube in the gripper, off its slot
on_scale  = per_pass("on_scale")    # tube released on the balance pan
weighed   = per_pass("weighed")     # a valid mass was read
off_scale = per_pass("off_scale")   # tube re-gripped off the pan
home      = per_pass("home")        # back in its own slot — THE PHASE FACT
