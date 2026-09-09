"""Phase 1 — ``weighed_1``: every tube picked, weighed, returned.

Read top to bottom, this file IS the phase: the concrete actions in the
order a tube meets them. Each is a thin subclass of a base in
``base.py``; the only things this module decides are ``PASS`` and
``register = True`` (a ``register = False`` base passes its opt-out
down, so every concrete action opts back in explicitly).
Boundary at exit: doc/phases.md row ``weighed_1``.
"""

from actions.base import (PickBase, PickFromScaleBase, PlaceOnScaleBase,
                          ReturnBase, WeighBase)


class Pick1(PickBase):
    PASS = 1
    register = True


class PlaceOnScale1(PlaceOnScaleBase):
    PASS = 1
    register = True


class Weigh1(WeighBase):
    PASS = 1
    register = True


class PickFromScale1(PickFromScaleBase):
    PASS = 1
    register = True


class Return1(ReturnBase):
    PASS = 1
    register = True
