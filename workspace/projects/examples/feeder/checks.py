"""feeder example — vision/sensor checks.

This minimal example has no real predicates to verify (no camera in
the scene, no scale, no force sensor). The ``Checks`` class still
exists because the BT launcher expects it — it just has nothing to
register.

If you adapt this example to a real bench with a camera, this is
where the vision predicates live. See ``sample_prep/checks.py`` for
the full pattern (``camera.detect(...)`` / ``camera.is_empty(...)``
gated by anchor name).

Framework reference: ../../../../docs/bt-framework-guide.md §13
"""


class Checks:

    def __init__(self, rcp, rt, **kwargs):
        self.rcp = rcp
        self.rt  = rt

    def register(self, runner) -> None:
        # No predicates in this minimal example. Add registrations here
        # the moment you wire a real check.
        pass
