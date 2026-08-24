"""Syringe-pump recipe.

The pump component (``workspace/components/pump/``) already
exposes the atomic pumping API in µL and named valve ports — sim/real
branching happens once inside the ``PumpStation`` it holds, so
callers always get the same shape.

This recipe is the thin coordination layer for project workflows that
combine pumping with other steps ("draw 200 µL of surrogate, dispense
into the tube the robot just placed, log the volume"). For one-off
calls it is fine to reach the component directly; the recipe adds
composability, not behaviour.

``Recipe.__init__`` (IK / calibration / motion settings) is skipped on
purpose, the same way ``MultiMeter`` skips it: the pump is a bench box
with no robot motion of its own. If a project mounts a dispense head on
the arm, the MOTION belongs in that project's site recipe — this one
stays about the fluidics.

Recipes never branch on simulation. See ``docs/recipe-guide.md`` §1 and
``docs/component-guide.md`` §7. Full pump guide:
``docs/liquid-handling.md``.
"""

from __future__ import annotations

from workspace.recipes.recipe import Recipe


class Pump(Recipe):
    """Thin recipe wrapper — pass-throughs to the component's atomic
    ops, so call sites read ``rcp["pump"].aspirate(200, port="reservoir")``.

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
    # ``port`` is a number or a name from the scene's ``valve_ports``
    # map; omitted, it resolves the single declared outlet. Moves
    # return True/False — a BT action should ``return False`` on False
    # and let the planner re-select after recovery (declarative retry,
    # project-guide §8). ``sim_return`` (device-guide §17) injects a
    # specific sim outcome; real mode ignores it.

    def is_connected(self):
        return self.component.is_connected()

    def syringe_volume(self):
        """µL the full stroke sweeps — the declared barrel."""
        return self.component.syringe_volume()

    def initialize(self, syringe_volume_ul=None, half_force: bool = True,
                   sim_return: bool = True):
        """Configure and home the plunger and valve. Required after
        power-up and after any stall or stop; homing expels the barrel
        through the output port. Pass ``syringe_volume_ul`` for a
        barrel swap without touching the scene."""
        return self.component.initialize(syringe_volume_ul=syringe_volume_ul,
                                         half_force=half_force, sim_return=sim_return)

    def stop(self):
        """Immediate stop; the next op re-initializes automatically."""
        return self.component.stop()

    # ── Valve ──

    def valve(self, port=None, sim_return: bool = True):
        """Point the valve at ``port`` without moving the plunger."""
        return self.component.valve(port, sim_return=sim_return)

    # ── Plunger ──

    def aspirate(self, volume_ul: float, port=None, speed=None, sim_return: bool = True):
        """Draw ``volume_ul`` in through ``port``. Refuses (False) when
        the barrel would overflow or the pump is unreachable."""
        return self.component.aspirate(volume_ul, port=port, speed=speed,
                                       sim_return=sim_return)

    def dispense(self, volume_ul: float, port=None, speed=None, sim_return: bool = True):
        """Push ``volume_ul`` out through ``port``. Same contract as
        :meth:`aspirate`."""
        return self.component.dispense(volume_ul, port=port, speed=speed,
                                       sim_return=sim_return)

    def move_to_volume(self, volume_ul: float, port=None, speed=None,
                       sim_return: bool = True):
        """Absolute: leave exactly this much in the barrel. The
        difference moves through ``port``."""
        return self.component.move_to_volume(volume_ul, port=port, speed=speed,
                                             sim_return=sim_return)

    def empty(self, port=None, speed=None, sim_return: bool = True):
        """Plunger fully home, contents pushed out through ``port``."""
        return self.component.empty(port=port, speed=speed, sim_return=sim_return)

    def prime(self, cycles=None, volume_ul=None, from_port=None, to_port=None,
              aspiration_speed=None, dispensing_speed=None, sim_return: bool = True):
        """Flush air out of the fluid path: fill from ``from_port``
        (default: the single source), empty to ``to_port`` (default:
        the single outlet). ``cycles=None`` is computed from the
        declared tube volumes."""
        return self.component.prime(cycles, volume_ul=volume_ul,
                                    from_port=from_port, to_port=to_port,
                                    aspiration_speed=aspiration_speed,
                                    dispensing_speed=dispensing_speed,
                                    sim_return=sim_return)

    # ── Reads ──

    def volume(self):
        """µL currently held (float, or None when unreachable)."""
        return self.component.volume()

    def material_in_barrel(self):
        """Name of the last source drawn from; None after initialize."""
        return self.component.material_in_barrel()

    def material_at(self, outlet=None):
        """Last material pushed through ``outlet`` — what sits at that
        nozzle's tip."""
        return self.component.material_at(outlet)

    def set_speed(self, percent: float = 100.0, sim_return: bool = True):
        """Plunger speed, 0-100 (100 = fastest preset, 0 = slowest)."""
        return self.component.set_speed(percent, sim_return=sim_return)

    def status(self, sim_return: dict = {"ready": True, "error": 0,
                                         "error_text": "no error"}):
        return self.component.status(sim_return=sim_return)

    def summary(self):
        """Everything at once — connection, barrel, materials, ports."""
        return self.component.summary()
