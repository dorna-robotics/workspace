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


class PhMeter(Recipe):
    """Stationary probe — no motion, no mounted tool.

    ``Recipe.__init__`` (IK / calibration / motion settings) is skipped
    on purpose, the same way ``MultiMeter`` skips it: a probe sitting in
    a stand has no robot motion. The recipe just holds refs so calls can
    be written ``rcp["ph"].read()``.

    Workflow-level methods (``read_and_log``, a full calibration
    sequence) belong here — they combine the component's atomic ops
    with other workspace state. Never inline a sim check.
    """

    DEFAULTS = dict()

    def __init__(self, workspace, core, component, **kwargs):
        self.workspace = workspace
        self.core = core
        self.component = component

    def _probe(self):
        return self.component

    # ``sim_return`` (device-guide §17) — inline default, shaped like the
    # real return. Pass your own to inject; real mode ignores it.

    def is_connected(self):
        return self._probe().is_connected()

    def read(self, sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Single reading (``Reading`` or None)."""
        return self._probe().read(sim_return=sim_return)

    def read_at_temperature(self, celsius: float,
                            sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Compensate for the sample temperature and read in one call."""
        return self._probe().read_at_temperature(celsius, sim_return=sim_return)

    def read_stable(self, n: int = 3, tolerance: float = 0.02, max_readings: int = 20,
                    sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Block until the electrode settles (``Reading`` or None)."""
        return self._probe().read_stable(n=n, tolerance=tolerance,
                                         max_readings=max_readings, sim_return=sim_return)

    def ph(self, stable: bool = True, sim_return: float = 7.000):
        """pH value (float or None). ``None`` means "no valid reading" —
        a BT action should ``return False`` on it and let the planner
        re-select after recover (declarative retry, project-guide §8)."""
        return self._probe().ph(stable=stable, sim_return=sim_return)

    def slope(self, sim_return=Slope(acid_percent=99.5, base_percent=99.2, offset_mv=0.0, raw="sim")):
        """Probe health vs an ideal electrode (``Slope`` or None)."""
        return self._probe().slope(sim_return=sim_return)

    def calibrate(self, value: float, sim_return: bool = True):
        """Calibrate against the buffer the probe is sitting in. Returns
        True/False, never raises. Mid (~pH 7) must be calibrated first —
        the chip enforces it."""
        return self._probe().calibrate(value, sim_return=sim_return)

    def calibration_points(self, sim_return: int = 3):
        """Stored calibration points, 0–3 (int or None)."""
        return self._probe().calibration_points(sim_return=sim_return)

    def calibrate_clear(self, sim_return: bool = True):
        """Wipe all stored calibration points."""
        return self._probe().calibrate_clear(sim_return=sim_return)

    def set_temperature_compensation(self, celsius: float, sim_return: bool = True):
        """Store the sample temperature on the chip (persists)."""
        return self._probe().set_temperature_compensation(celsius, sim_return=sim_return)


class PhMeterSite(Recipe):
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

    def immerse(self, anchor="top", dist=20, approach=False, padding=60, **kwargs):
        """Dip the probe ``dist`` mm below ``anchor`` on this site.

        Defaults are cup-shaped: ``anchor="top"`` (the container rim),
        two-phase motion (hover, then a straight dive) because the well
        is deep and narrow, and 60 mm of clearance above the rim. The
        glass bulb must end up submerged — a dry electrode reads
        garbage, so ``dist`` should clear the bulb (the last ~8 mm of
        the shaft), not just kiss the surface.
        """
        return super().immerse(
            dist=dist, anchor=anchor, approach=approach, padding=padding, **kwargs,
        )

    def retract(self, anchor="top", padding=60, **kwargs):
        """Lift the probe clear of the well — inverse of ``immerse``."""
        return super().retract(
            dist=0, anchor=anchor, padding=padding, **kwargs,
        )

    # ── Device pass-throughs ──────────────────────────────────────────
    # Resolved through the mounted tool, so the same recipe works with
    # the probe on any tool-changer slot.
    #
    # ``sim_return`` (device-guide §17) — explicit sim injection. Its
    # default IS the canned sim value, inline in the signature and shaped
    # like the real return (a ``Reading`` for read / read_stable, a
    # ``float`` for ph, a ``Slope`` for slope). Real mode ignores it.

    def _probe(self):
        probe = self.core.current_tool()
        if probe is None:
            raise RecipeError("no pH probe attached to the robot")
        return probe

    def is_connected(self):
        return self._probe().is_connected()

    def read(self, sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Single reading (``Reading`` or None)."""
        return self._probe().read(sim_return=sim_return)

    def read_at_temperature(self, celsius: float,
                            sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Compensate for the sample temperature and read in one call."""
        return self._probe().read_at_temperature(celsius, sim_return=sim_return)

    def read_stable(self, n: int = 3, tolerance: float = 0.02, max_readings: int = 20,
                    sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Block until the electrode settles (``Reading`` or None).

        Call this AFTER ``immerse`` — settling starts when the bulb hits
        liquid. The wait is the probe's, not the bus's; it is a plain
        blocking read, so a BT action wanting a checkpoint should sit
        behind ``rt.*`` like any other long step.
        """
        return self._probe().read_stable(n=n, tolerance=tolerance,
                                         max_readings=max_readings, sim_return=sim_return)

    def ph(self, stable: bool = True, sim_return: float = 7.000):
        """pH value (float or None). ``stable=True`` waits for a settled
        reading. ``None`` means "no valid reading" — a BT action should
        ``return False`` on it and let the planner re-select after
        recover (declarative retry, project-guide §8)."""
        return self._probe().ph(stable=stable, sim_return=sim_return)

    def slope(self, sim_return=Slope(acid_percent=99.5, base_percent=99.2, offset_mv=0.0, raw="sim")):
        """Probe health vs an ideal electrode (``Slope`` or None)."""
        return self._probe().slope(sim_return=sim_return)

    # ── Calibration ───────────────────────────────────────────────────
    # The chip's rule: mid (~pH 7) FIRST — it wipes any existing
    # calibration — then low (~4) / high (~10) in either order. A full
    # 3-point session is one immerse/read_stable/calibrate cycle per
    # buffer cup, with a rinse in between.

    def calibrate(self, value: float, sim_return: bool = True):
        """Calibrate against the buffer the probe is currently sitting in.

        Returns True/False rather than raising, so a BT action can
        ``return False`` and let the planner re-select. Let the reading
        settle first::

            rcp["buffer_7"].immerse()
            rcp["buffer_7"].read_stable()
            rcp["buffer_7"].calibrate(7.00)
            rcp["buffer_7"].retract()
        """
        return self._probe().calibrate(value, sim_return=sim_return)

    def calibration_points(self, sim_return: int = 3):
        """Stored calibration points, 0–3 (int or None)."""
        return self._probe().calibration_points(sim_return=sim_return)

    def calibrate_clear(self, sim_return: bool = True):
        """Wipe all stored calibration points."""
        return self._probe().calibrate_clear(sim_return=sim_return)

    def set_temperature_compensation(self, celsius: float, sim_return: bool = True):
        """Store the sample temperature on the chip (persists across
        power cycles). Use ``read_at_temperature`` instead for a one-off
        sample at a different temperature."""
        return self._probe().set_temperature_compensation(celsius, sim_return=sim_return)
