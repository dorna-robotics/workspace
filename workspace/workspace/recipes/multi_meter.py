"""Multimeter recipe.

The multimeter component (today
``workspace/components/multi_meter/multi_meter_bk879b.py``, future
``multi_meter_<model>.py`` siblings) already exposes the atomic
measurement API (``is_connected``,
``read_capacitance`` / ``inductance`` / ``resistance`` /
``impedance``) — sim/real branching happens once inside the
``BK879BStation`` it holds, so callers always get the same shape.

This recipe is the thin coordination layer for project workflows
that want to combine a measurement with other steps (e.g. "after
the gripper places the component, read the capacitance and log it
against the tube id"). For one-off reads it's fine to call
``core.workspace.components["multi_meter_1"].read_capacitance()``
directly — the recipe doesn't add behaviour, it adds composability.

Recipes never branch on simulation. See
``docs/recipe-guide.md`` §1 and ``docs/component-guide.md`` §7.
"""

from __future__ import annotations

from workspace.components.multi_meter.bk879b_driver import Measurement
from workspace.recipes.recipe import Recipe


class MultiMeter(Recipe):
    """Thin recipe wrapper. ``Recipe.__init__`` (IK / calibration /
    motion settings) is skipped on purpose — multimeter operations
    have no robot motion. The recipe just holds refs so calls can be
    written as ``rcp["multi_meter"].read_capacitance(...)``.

    To add workflow-level methods (e.g. ``read_and_log``,
    ``read_with_average``), put them here — they'll combine the
    component's atomic ops with other workspace state. Never
    inline a sim check: the component handles it.
    """

    DEFAULTS = dict()

    def __init__(self, workspace, core, component, **kwargs):
        self.workspace = workspace
        self.core = core
        self.component = component

    # ── Pass-throughs to the component's atomic ops ───────────────────
    # Keeps the call sites symmetrical with other recipes
    # (``rcp["x"].method(...)``) without re-implementing anything.

    # ``sim_return`` (device-guide §17) — explicit sim injection. Its
    # default IS the canned per-function ``Measurement``, written inline in
    # the signature (the real read_* return a ``Measurement``, so
    # sim_return is one too). Pass your own to inject a different reading;
    # real mode ignores it.

    def is_connected(self):
        return self.component.is_connected()

    def read_capacitance(self, mode: str = "Cp", frequency: int = 1000,
                         sim_return=Measurement(primary=1.5e-9, primary_unit="F", secondary=0.001, secondary_unit="", function="C", frequency="1000", raw="sim,1.5e-9,0.001")):  # 1.5 nF
        return self.component.read_capacitance(mode=mode, frequency=frequency, sim_return=sim_return)

    def read_inductance(self, mode: str = "Ls", frequency: int = 1000,
                        sim_return=Measurement(primary=1.0e-3, primary_unit="H", secondary=0.001, secondary_unit="", function="L", frequency="1000", raw="sim,1e-3,0.001")):  # 1 mH
        return self.component.read_inductance(mode=mode, frequency=frequency, sim_return=sim_return)

    def read_resistance(self, frequency: int = 1000,
                        sim_return=Measurement(primary=1.0e3, primary_unit="Ω", secondary=0.0, secondary_unit="", function="R", frequency="1000", raw="sim,1e3,0.0")):  # 1 kΩ
        return self.component.read_resistance(frequency=frequency, sim_return=sim_return)

    def read_impedance(self, frequency: int = 1000,
                       sim_return=Measurement(primary=1.0e3, primary_unit="Ω", secondary=0.0, secondary_unit="", function="Z", frequency="1000", raw="sim,1e3,0.0")):  # 1 kΩ
        return self.component.read_impedance(frequency=frequency, sim_return=sim_return)
