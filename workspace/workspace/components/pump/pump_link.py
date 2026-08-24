"""The fluid-path capability: "a nozzle at the end of a tube on pump
port N".

A syringe pump is ONE device with ONE bus row, but several things on a
bench can be plumbed to it — a needle the robot carries, a fixed
dispense arm, a doser over a bottle. Those objects are opposites
kinematically (a carried tool vs a bench fixture, ``Gripper`` vs
``Arm``), so they share no base class; what they share is this one
capability, which is why it is COMPOSED in rather than inherited.

Ownership stays honest (device-guide §4): the pump component is the
sole owner and sole publisher of the device id. A plumbed tool claims
no device and renders no panel row — it only knows *which* pump feeds
it and *which* valve port its tube lands on.

Scene yaml declares the plumbing, because the tube is physical::

    pump_1:
      type: "pump"
      port: "/dev/serial/by-id/usb-..."
      syringe_volume_ul: 100.0
      valve_ports:
        1: reservoir
        3: {name: needle, tube_volume_ul: 150}
      outlets: [3]

    needle_gripper_1:
      type: "needle_gripper"
      pump: "pump_1"            # which pump feeds this needle
      pump_port: 3                # which valve port its tube is on

Call sites never repeat the port — the link binds it, so the one
mistake that silently dispenses down the wrong line is not typeable::

    tool = core.current_tool()
    tool.aspirate(200)            # -> pump.aspirate(200, port=3)
    tool.dispense(200)

Method names match the pipettor's on purpose: a carried needle then
rides the existing ``PipettingSite`` recipe unchanged instead of
growing a parallel recipe family.

Full guide: ``docs/liquid-handling.md``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional


log = logging.getLogger(__name__)


class PumpLinkError(RuntimeError):
    """Raised when a plumbed component names a pump that isn't there."""


class PumpLink:
    """A port-bound handle on a pump component, resolved by NAME.

    Resolution is lazy — the pump may be constructed after the tool
    that references it (scene order is the operator's business, not a
    load-order contract), so the lookup happens on first use and is
    then cached. Same trick ``core`` and ``probe`` already use with
    ``workspace.components[...]``.

    ``port=None`` means "wherever the valve already is" — the pump's
    own default. Declare a port whenever the plumbing has one.
    """

    def __init__(self, workspace, pump_name: str = "", port: Any = None,
                 label: str = ""):
        self.workspace = workspace
        self.pump_name = pump_name or ""
        self.port = port
        self.label = label or "pump_link"
        self._pump = None

    # ── Resolution ────────────────────────────────────────────────────

    @property
    def linked(self) -> bool:
        """True when a pump name was declared at all."""
        return bool(self.pump_name)

    @property
    def pump(self):
        """The pump COMPONENT (not the station). Raises a plain-English
        error naming both sides when the scene doesn't have it — a
        typo in ``pump:`` should not surface as a KeyError three frames
        deep in a motion call."""
        if self._pump is None:
            if not self.pump_name:
                raise PumpLinkError(
                    f"{self.label}: no pump declared — add "
                    f'`pump: "<pump component name>"` to its scene entry'
                )
            comps = getattr(self.workspace, "components", {}) or {}
            pump = comps.get(self.pump_name)
            if pump is None:
                raise PumpLinkError(
                    f"{self.label}: pump {self.pump_name!r} is not in the scene "
                    f"(have: {', '.join(sorted(comps)) or 'nothing'})"
                )
            self._pump = pump
        return self._pump

    def _port(self, port=None):
        """Explicit call-site port wins; otherwise this link's own."""
        return self.port if port is None else port

    # ── Fluid ops (names match the pipettor's) ────────────────────────
    # Thin, port-bound pass-throughs. Every one is sim-agnostic: the
    # pump's station branches sim once, so a plumbed tool never sees
    # the flag. ``sim_return`` rides through unchanged (device-guide
    # §17) for callers that want to inject a specific sim outcome.

    # ``speed`` / ``blowout`` are the air-displacement pipettor's
    # per-move arguments — ``PipettingSite`` passes both by name to
    # whatever tool is mounted — and they are swallowed here. A syringe
    # drive has no blowout stroke, and its speed is a PERCENT of the
    # drive's fastest preset, not µL/s: forwarding a pipettor's
    # ``speed=500`` would silently mean "100%", the opposite of slow.
    # Swallowing is what lets a needle ride the stock pipetting recipe
    # unchanged.
    #
    # To set the pump's speed through a nozzle, say so in its own
    # units: ``pump_speed`` (0-100) is forwarded, and like every speed
    # on this drive it stays in effect after the move.

    def aspirate(self, volume_ul: float, port=None, pump_speed=None,
                 speed=None, blowout=None, **kw):
        """Draw ``volume_ul`` in through this link's port."""
        return self.pump.aspirate(volume_ul, port=self._port(port),
                                  speed=pump_speed, **kw)

    def dispense(self, volume_ul: float, port=None, pump_speed=None,
                 speed=None, blowout=None, **kw):
        """Push ``volume_ul`` out through this link's port."""
        return self.pump.dispense(volume_ul, port=self._port(port),
                                  speed=pump_speed, **kw)

    def move_to_volume(self, volume_ul: float, port=None, **kw):
        """Absolute barrel volume; the difference moves through the port."""
        return self.pump.move_to_volume(volume_ul, port=self._port(port), **kw)

    def empty(self, port=None, **kw):
        """Plunger home, contents out through this link's port."""
        return self.pump.empty(port=self._port(port), **kw)

    def prime(self, cycles=None, volume_ul=None, from_port=None, **kw):
        """Flush air out of the path, ending through THIS port.
        Defaults let the pump decide: ``from_port=None`` resolves the
        single declared source, ``cycles=None`` is computed from the
        declared tube volumes."""
        return self.pump.prime(cycles, volume_ul=volume_ul, from_port=from_port,
                               to_port=self._port(), **kw)

    def valve(self, port=None, **kw):
        """Point the valve at this link's port (or an explicit one)."""
        return self.pump.valve(self._port(port), **kw)

    def volume(self, **kw):
        """µL currently in the barrel (shared across every port)."""
        return self.pump.volume(**kw)

    def set_speed(self, percent: float = 100.0, **kw):
        return self.pump.set_speed(percent, **kw)

    def is_connected(self) -> bool:
        """False when no pump is declared — never raises, so a status
        readout can call it unguarded."""
        try:
            return bool(self.pump.is_connected())
        except PumpLinkError:
            return False


class PumpedTool:
    """Mixin: gives any component the fluid API bound to its own port.

    Mix it in FIRST so its ``__init__`` runs before the kinematic base
    (``Gripper`` / ``Arm``) that owns the geometry::

        class NeedleGripper(PumpedTool, Gripper):
            ...
            super().__init__(name=name, workspace=workspace, **prm)

    Reads two scene keys, both optional so an unplumbed instance keeps
    working exactly as before:

        pump:       name of the pump component feeding this nozzle
        pump_port:  valve port its tube lands on (name, number, angle)

    The component gets ``aspirate`` / ``dispense`` / ``prime`` / … and
    ``.fluid`` (the link) / ``.pump`` (the pump component) for anything
    the narrow surface doesn't cover.
    """

    def _init_pump_link(self, workspace, prm: dict) -> None:
        """Call from the component's ``__init__`` with the merged prm."""
        self.fluid = PumpLink(
            workspace,
            pump_name=prm.get("pump", "") or "",
            port=prm.get("pump_port", None),
            label=getattr(self, "name", None) or self.__class__.__name__,
        )

    # ── Pass-throughs so call sites read tool.dispense(200) ───────────

    @property
    def pump(self):
        return self.fluid.pump

    def aspirate(self, volume_ul: float, port=None, pump_speed=None,
                 speed=None, blowout=None, **kw):
        return self.fluid.aspirate(volume_ul, port=port, pump_speed=pump_speed, **kw)

    def dispense(self, volume_ul: float, port=None, pump_speed=None,
                 speed=None, blowout=None, **kw):
        return self.fluid.dispense(volume_ul, port=port, pump_speed=pump_speed, **kw)

    def move_to_volume(self, volume_ul: float, port=None, **kw):
        return self.fluid.move_to_volume(volume_ul, port=port, **kw)

    def empty(self, port=None, **kw):
        return self.fluid.empty(port=port, **kw)

    def prime(self, cycles=None, volume_ul=None, **kw):
        return self.fluid.prime(cycles, volume_ul=volume_ul, **kw)

    def pump_volume(self, **kw):
        """Barrel contents. Named ``pump_volume`` rather than ``volume``
        so it can never collide with a geometric volume on the
        kinematic base."""
        return self.fluid.volume(**kw)

    def pump_connected(self) -> bool:
        return self.fluid.is_connected()
