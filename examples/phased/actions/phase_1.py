"""Phase 1 — ``weighed_1``: every tube picked, weighed, returned.

Read top to bottom, this file IS the phase: the concrete actions in the
order a tube meets them — the same order ``phases.Weighed1.route``
declares. Each is a thin subclass of a base in ``base.py``; the only
thing this module decides is ``PASS``.
Boundary at exit: doc/phases.md row ``weighed_1``.
"""

from actions.base import (PickBase, PickFromScaleBase, PlaceOnScaleBase,
                          ReturnBase, WeighBase)


class Pick1(PickBase):
    PASS = 1


class PlaceOnScale1(PlaceOnScaleBase):
    PASS = 1


class Weigh1(WeighBase):
    PASS = 1


class PickFromScale1(PickFromScaleBase):
    PASS = 1


class Return1(ReturnBase):
    PASS = 1
