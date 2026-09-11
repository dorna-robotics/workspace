"""pH-meter recipes — two, for the two ways a probe lives on a bench.

``PhMeter`` — the probe is **stationary** (in a stand, a flow cell, a
holder) and the sample comes to it. Thin wrapper over the
``ph_meter_atlas`` component itself, exactly like ``MultiMeter`` over
the meter: no motion, no tool resolution, just pass-throughs.

``PhMeterSite`` — the probe is a **tool the robot carries** and this
recipe's component is what it dips into: the storage-solution cup
(``storage_liquid_container``), a buffer cup during calibration, or a
rack of sample tubes. Motion (``immerse`` / ``retract``) targets this
recipe's component; the device ops go to the probe the robot is
currently holding (``core.current_tool()``). Same shape as
``DosingSite``.

Either way the device ops land on the ``ph_meter_atlas`` component,
which already exposes the sim-agnostic atomic API — sim/real branching
happens once inside the ``EzoPHStation`` it holds. Recipes never branch
on simulation. See ``docs/recipe-guide.md`` §1 and
``docs/component-guide.md`` §7.
"""

from copy import deepcopy
from mergedeep import merge

from workspace.components.ph_meter.ezo_ph_driver import Reading, Slope
from workspace.recipes.recipe import Recipe, RecipeError


class _ProbeOps:
    """The probe's atomic ops, on whatever ``_probe()`` resolves to — the
    same five calls for a stationary probe and a robot-mounted one. The
    surface is the component's (``PhMeter``): ``read`` is THE reading
    call and settles by default per the probe's scene-yaml tuning;
    ``ph`` is its value. ``sim_return`` (device-guide §17): inline
    default, shaped like the real return; real mode ignores it."""

    def _probe(self):
        raise NotImplementedError

    def is_connected(self):
        return self._probe().is_connected()

    def read(self, settle: bool = None, n: int = None, tolerance: float = None,
             max_readings: int = None,
             sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """A reading (``Reading`` or None). ``settle`` unset → the probe's
        ``settle`` setting: block until ``n`` consecutive readings agree
        within ``tolerance``, giving up after ``max_readings`` (each unset
        → scene yaml ``settle_n`` / ``settle_tolerance`` /
        ``settle_max_readings``); ``False`` → one instantaneous reading.
        Call it AFTER ``immerse``: settling starts when the bulb hits
        liquid. A plain blocking call — a BT action wanting a checkpoint
        sits behind ``rt.*`` like any other long step."""
        return self._probe().read(settle=settle, n=n, tolerance=tolerance,
                                  max_readings=max_readings, sim_return=sim_return)

    def ph(self, settle: bool = None, sim_return: float = 7.000):
        """The pH value (float or None) — ``read`` with the same settle
        rule. ``None`` means "no valid reading": a BT action should
        ``return False`` on it and let the planner re-select after
        recover (declarative retry, project-guide §8)."""
        r = self.read(settle=settle, sim_return=Reading(status="ok", ph=sim_return, raw="sim"))
        return None if r is None or not r.ok else r.ph

    def slope(self, sim_return=Slope(acid_percent=99.5, base_percent=99.2, offset_mv=0.0, raw="sim")):
        """Probe health vs an ideal electrode (``Slope`` or None)."""
        return self._probe().slope(sim_return=sim_return)

    def calibrate(self, value: float, sim_return: bool = True):
        """Calibrate against the buffer the probe is sitting in; the point
        (low / mid / high) is picked from ``value``. The chip's rule: mid
        (~pH 7) FIRST — it wipes the other points — then low (~4) and
        high (~10) in either order. Returns True/False, never raises, so
        a BT action can ``return False``. Let the reading settle first::

            rcp["buffer_7"].immerse()
            rcp["buffer_7"].read()
            rcp["buffer_7"].calibrate(7.00)
            rcp["buffer_7"].retract()
        """
        return self._probe().calibrate(value, sim_return=sim_return)


class PhMeter(_ProbeOps, Recipe):
    """Stationary probe — no motion, no mounted tool.

    ``Recipe.__init__`` (IK / calibration / motion settings) is skipped
    on purpose, the same way ``MultiMeter`` skips it: a probe sitting in
    a stand has no robot motion. The recipe just holds refs so calls can
    be written ``rcp["ph"].read()``.

    Workflow-level methods (``read_and_log``, a full calibration
    sequence) belong here — they combine the component's atomic ops
    with other workspace state. Never inline a sim check. The atomic
    ops themselves come from ``_ProbeOps``.
    """

    DEFAULTS = dict()

    def __init__(self, workspace, core, component, **kwargs):
        self.workspace = workspace
        self.core = core
        self.component = self.gated(component)   # every op settles + checkpoints (recipes/gated.py)

    def _probe(self):
        return self.component


class PhMeterSite(_ProbeOps, Recipe):
    """Probe carried by the robot; this recipe's component is the vessel
    it dips into."""

    DEFAULTS = dict(
        # ref joints
        target_offset=[0, 0, 150, 0, 180, 0],
        # IK
        rail_step=20,
        rail_span=1,
    )

    def __init__(self, workspace, core, component, **kwargs):
        # prm
        prm = deepcopy(Recipe.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, kwargs) # kwargs

        super().__init__(
            workspace=workspace,
            core=core,
            component=component,
            **prm
        )

    # ── Motion ────────────────────────────────────────────────────────
    # The probe is the TOOL, not a held load, so ``height_load`` is 0 and
    # the tool's own ``tip`` anchor (192.1 mm below the flange) is what
    # reaches ``dist`` below the target anchor.

    def above(self, anchor="top", padding=60, **kwargs):
        """Hover ``padding`` mm above ``anchor`` on this site — the
        approach half of a dip (planned travel, fused). Default 60 mm
        of clearance above the container rim."""
        self._wire_verb("above", anchor)
        return super().above(anchor=anchor, padding=padding, **kwargs)

    def immerse(self, dist=20, anchor="top", **kwargs):
        """Dip the probe tip ``dist`` mm below ``anchor`` — one straight
        dive at the dip speed, from wherever :meth:`above` left it.

        The glass bulb must end up submerged — a dry electrode reads
        garbage, so ``dist`` should clear the bulb (the last ~8 mm of
        the shaft), not just kiss the surface.
        """
        return super().immerse(dist=dist, anchor=anchor, **kwargs)

    def retract(self, dist=60, anchor="top", **kwargs):
        """Lift the probe tip to ``dist`` mm above ``anchor`` — the
        mirror of :meth:`immerse` (default: back to the approach
        clearance)."""
        return super().retract(dist=dist, anchor=anchor, **kwargs)

    # ── Device pass-throughs ──────────────────────────────────────────
    # Resolved through the mounted tool, so the same recipe works with
    # the probe on any tool-changer slot.
    #
    def _probe(self):
        probe = self.core.current_tool()
        if probe is None:
            raise RecipeError("no pH probe attached to the robot")
        return self.gated(probe)
