"""Syringe-pump recipe.

The pump component (today
``workspace/components/pump/pump_psd4.py``, future
``syringe_pump_<model>.py`` siblings) already exposes the atomic
pumping API in µL — sim/real branching happens once inside the
``PSD4Station`` it holds, so callers always get the same shape.

This recipe is the thin coordination layer for project workflows that
combine pumping with other steps ("draw 200 µL from port 3, dispense
into the tube the robot just placed, log the volume"). For one-off
calls it is fine to reach the component directly; the recipe adds
composability, not behaviour.

``Recipe.__init__`` (IK / calibration / motion settings) is skipped on
purpose, the same way ``MultiMeter`` skips it: the pump is a bench box
with no robot motion of its own. If a project mounts a dispense head on
the arm, the MOTION belongs in that project's site recipe — this one
stays about the fluidics.

Recipes never branch on simulation. See ``docs/recipe-guide.md`` §1 and
``docs/component-guide.md`` §7.
"""

from __future__ import annotations

from workspace.recipes.recipe import Recipe


class SyringePump(Recipe):
    """Thin recipe wrapper — pass-throughs to the component's atomic
    ops, so call sites read ``rcp["pump"].aspirate(200)``.

    Workflow-level methods (a wash cycle, a serial-dilution series, a
    prime-then-transfer) belong here: they compose the component's
    atomic ops with other workspace state. Never inline a sim check.
    """

    DEFAULTS = dict()

    def __init__(self, workspace, core, component, **kwargs):
        self.workspace = workspace
        self.core = core
        self.component = component

    # ── Pass-throughs to the component's atomic ops ───────────────────
    # ``sim_return`` (device-guide §17) — inline default, shaped like the
    # real return: bool for moves, float for volume, dict for status.
    # Pass your own to inject; real mode ignores it.

    def is_connected(self):
        return self.component.is_connected()

    def syringe_volume(self, sim_return: float = 0.0):
        """The syringe size currently in effect, µL."""
        return self.component.syringe_volume(sim_return=sim_return)

    def initialize(self, output_right=None, half_force: bool = False,
                   syringe_volume_ul=None, sim_return: bool = True):
        """Home the plunger and valve, and assign valve addressing —
        ``output_right`` picks which physical side "output" means.
        ``None`` uses the scene's value, which describes the plumbing.
        Required after power-up and after any stall or terminated move."""
        return self.component.initialize(output_right=output_right,
                                    syringe_volume_ul=syringe_volume_ul,
                                         half_force=half_force, sim_return=sim_return)

    # ── Valve ──

    def valve(self, position="input", shortest: bool = False,
              direction: str = "shortest", sim_return: bool = True):
        """Move the valve — logical name (input / output / bypass /
        extra / wash / return), numbered port 1-8, or an absolute angle
        like ``"90deg"``. ``shortest`` / ``direction`` control routing."""
        return self.component.valve(position, shortest=shortest,
                                    direction=direction, sim_return=sim_return)

    # ── Plunger ──

    def aspirate(self, volume_ul: float, port=None, speed=None, sim_return: bool = True):
        """Draw ``volume_ul`` in from ``port`` — a logical name
        (``"input"``), a numbered port (``3``), or an angle
        (``"90deg"``). The valve moves there first; omit ``port`` to use
        wherever it already is.

        Returns False when the pump refused (including "that would
        overfill the barrel") or is unreachable — the BT action should
        ``return False`` and let the planner re-select (declarative
        retry, project-guide §8)."""
        return self.component.aspirate(volume_ul, port=port, speed=speed,
                                      sim_return=sim_return)

    def dispense(self, volume_ul: float, port=None, speed=None, sim_return: bool = True):
        """Push ``volume_ul`` out through ``port``. Same addressing and
        contract as :meth:`aspirate`."""
        return self.component.dispense(volume_ul, port=port, speed=speed,
                                      sim_return=sim_return)

    def move_to_volume(self, volume_ul: float, port=None, sim_return: bool = True):
        """Absolute: leave exactly this much in the barrel. The
        difference moves through ``port``."""
        return self.component.move_to_volume(volume_ul, port=port, sim_return=sim_return)

    def empty(self, port=None, sim_return: bool = True):
        """Plunger fully home, contents pushed out through ``port``."""
        return self.component.empty(port=port, sim_return=sim_return)

    def volume(self, sim_return: float = 0.0):
        """µL currently held (float, or None when unreachable)."""
        return self.component.volume(sim_return=sim_return)

    # ── Speed / status ──

    def set_speed(self, percent: float = 100.0, sim_return: bool = True):
        """Plunger speed, 0-100 (100 = fastest preset, 0 = slowest)."""
        return self.component.set_speed(percent, sim_return=sim_return)

    def status(self, sim_return: dict = {"ready": True, "error": 0, "error_text": "no error"}):
        return self.component.status(sim_return=sim_return)

    def prime(self, cycles: int = 2, volume_ul=None, from_port="input",
              to_port="output", sim_return: bool = True):
        """Flush air out of the fluid path: fill from ``from_port``,
        empty to ``to_port``, ``cycles`` times. ``volume_ul`` defaults
        to the full declared barrel, and absolute moves mean it works
        from any starting volume."""
        return self.component.prime(cycles, volume_ul=volume_ul,
                                    from_port=from_port, to_port=to_port,
                                    sim_return=sim_return)
