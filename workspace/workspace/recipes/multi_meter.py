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

    # ``sim_return`` (device-guide §17) — explicit sim injection. Pass a
    # ``Measurement`` to inject the sim reading; omit it (None) to use the
    # component's canned per-function default. Real mode ignores it.

    def is_connected(self):
        return self.component.is_connected()

    def read_capacitance(self, mode: str = "Cp", frequency: int = 1000, sim_return=None):
        kw = {} if sim_return is None else {"sim_return": sim_return}
        return self.component.read_capacitance(mode=mode, frequency=frequency, **kw)

    def read_inductance(self, mode: str = "Ls", frequency: int = 1000, sim_return=None):
        kw = {} if sim_return is None else {"sim_return": sim_return}
        return self.component.read_inductance(mode=mode, frequency=frequency, **kw)

    def read_resistance(self, frequency: int = 1000, sim_return=None):
        kw = {} if sim_return is None else {"sim_return": sim_return}
        return self.component.read_resistance(frequency=frequency, **kw)

    def read_impedance(self, frequency: int = 1000, sim_return=None):
        kw = {} if sim_return is None else {"sim_return": sim_return}
        return self.component.read_impedance(frequency=frequency, **kw)
