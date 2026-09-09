"""Phase 2 — ``weighed_2``: the second pass over every tube.

Same stations, same motion, pass-indexed facts — the ``gate`` in
``PassAction`` makes each tube wait for its own ``home_1``, and
``phases.Weighed2.pre`` makes the whole batch wait for everyone's.
Boundary at exit: doc/phases.md row ``weighed_2``.
"""

from actions.base import (PickBase, PickFromScaleBase, PlaceOnScaleBase,
                          ReturnBase, WeighBase)


class Pick2(PickBase):
    PASS = 2
    register = True


class PlaceOnScale2(PlaceOnScaleBase):
    PASS = 2
    register = True


class Weigh2(WeighBase):
    PASS = 2
    register = True


class PickFromScale2(PickFromScaleBase):
    PASS = 2
    register = True


class Return2(ReturnBase):
    PASS = 2
    register = True
