"""Keyto SP28 40 mm pipettor — a robot-mounted tool that is ALSO a
workspace-owned serial device (the plunger pump).

The kinematic side (tool changer, anchors, collision box) comes from
``Gripper``; the device side follows the standard workspace-owned
device shape (device-guide §10 shape A): raw driver
(``keyto_driver.Keyto``) → Device-protocol station
(``keyto_station.KeytoStation``) → this component, which owns the bus
attachment and the sim-agnostic atomic ops that recipes and operator
buttons call.

Scene yaml:

    pipettor:
      type: "pipettor_sp28_40mm"
      has_tool_changer: true
      simulation: true
      port: ""                # "" → no bus claim; set a /dev/... to claim
      ...attach...

Empty ``port`` means "no device claimed": the component still works
(sim ops return canned data), it just takes no bus id and renders no
Devices-panel row. Set a port to claim the pump — the dot then shows
hardware truth (sim is orthogonal, device-guide §16).
"""

from __future__ import annotations

import logging
from copy import deepcopy

from mergedeep import merge

from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper
from workspace.components.pipettor.keyto_station import KeytoStation
from workspace.devices import AutoRecover, attach_device


log = logging.getLogger(__name__)


@register("pipettor_sp28_40mm")
class PipettorSP2840mm(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp":[0, 0, 174+1, 0, 0, 0], "tip": [0, 0, 185.375-1.25, 0, 0, 0]}},
        collision_box = {"body":[
                {"pose":[0,0,90+3.25,0,0,0], "scale":[9 ,9,180+6.45]},
                {"pose":[0,-3.863,146.4/2,0,0,0], "scale":[43,55,146.4]}
        ]},
        #cfg
        has_tool_changer = True,
        port="",           # "" → no bus claim; non-empty → claim + panel row
        simulation=True,
        # ``critical`` controls whether a non-sim, non-reachable
        # transition pauses the runtime. A pump we can't drive mid-run
        # is a real fault worth pausing for; set ``critical: false``
        # in scene yaml where the pipettor is genuinely advisory.
        critical=True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Gripper.DEFAULTS) # default
        merge(prm, self.DEFAULTS) # self
        merge(prm, cfg) # cfg
        merge(prm, kwargs) # kwargs

        # update type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        super().__init__(
            name=name,
            workspace=workspace,
            **prm
        )

        # Authored simulation intent — failures must NOT flip this
        # (same rule as Core / MultiMeter). An unreachable real pump is
        # a fault we surface, not a reason to silently switch to sim.
        self._simulation_mode = bool(prm["simulation"])
        self._port = prm["port"] or ""
        self._critical = bool(prm["critical"])

        # ── The one sim/real branch — station handles the unified
        #    API; recipes and operator buttons never think about it. ──
        self.pump = KeytoStation(
            port=self._port,
            simulation=self._simulation_mode,
            label=self.name,
        )

        # Always attempt the initial real connection, regardless of
        # sim — bus state reflects hardware truth (device-guide §16).
        # In sim, AutoRecover is suspended via ``attachment.set_sim``,
        # so a failed connect sits red without retry storms.
        if self._port:
            self.pump.recover()

        # Bus attachment — gated on ``port``, same rule as Core's ip
        # and the multimeter's port.
        self._attachment = None
        if self._port:
            def _make_recover() -> AutoRecover:
                return AutoRecover(
                    recover_fn=self.pump.recover,
                    set_status=self.pump._set_state,
                    log_label=self.pump.id,
                )

            try:
                self._attachment = attach_device(
                    self.pump,
                    kind=KeytoStation.KIND,
                    sim=self._simulation_mode,
                    critical=self._critical,
                    meta={"port": self._port},
                    recover_factory=_make_recover,
                )
            except Exception:
                # Adapter wiring must NOT take down the component.
                log.exception("PipettorSP2840mm[%s]: attach_device failed", self.name)

    # ── DeviceComponent contract ───────────────────────────────────────

    @property
    def device_ids(self) -> list[str]:
        """Empty when ``port`` is unset — no device claimed, no panel
        row (mirrors Core / MultiMeter)."""
        return [self.pump.id] if self._port else []

    def device_claim(self, device_id: str) -> str:
        if device_id == self.pump.id:
            return "sim" if self._simulation_mode else "real"
        return "real"

    # ── Atomic pump API (component-level — recipes call these) ────────
    # Sim-agnostic by construction: the station branches internally.
    # Return True/False from the pump, or ``None`` when disconnected
    # and not in sim (device-guide §17 contract). ``sim_return``
    # defaults are the inline literals the real ops would return on
    # success; pass your own to inject a different sim outcome.

    def is_connected(self) -> bool:
        return self.pump.is_connected()

    def initialize(self, speed: int = 16000, sim_return: bool = True):
        """Home the plunger (no tip eject)."""
        return self.pump.initialize(speed=speed, sim_return=sim_return)

    def aspirate(self, volume_ul: float, speed: int = 200, sim_return: bool = True):
        return self.pump.aspirate(volume_ul, speed=speed, sim_return=sim_return)

    def dispense(self, volume_ul: float, speed: int = 500, blowout: bool = False,
                 sim_return: bool = True):
        return self.pump.dispense(volume_ul, speed=speed, blowout=blowout,
                                  sim_return=sim_return)

    def eject_tip(self, speed: int = 64000, sim_return: bool = True):
        """Home + eject the mounted tip (pump action only — the waste
        bin MOTION lives in the DosingSite recipe)."""
        return self.pump.eject_tip(speed=speed, sim_return=sim_return)

    def has_tip(self, sim_return: bool = True):
        """Tip-presence sensor read."""
        return self.pump.has_tip(sim_return=sim_return)

    # ── Operator actions (component-guide §8) ─────────────────────────

    def reconnect(self):
        """Re-run the connection sequence (same path AutoRecover uses).
        NOTE: the driver homes the plunger on connect."""
        return self.pump.recover()

    def release_pump(self):
        """Close the serial port and mark the pump down — lets the
        operator unplug it without a connection-lost alarm."""
        self.pump.release()

    def simulation(self, on: bool = True):
        """Live sim/real flip — device-guide §16 parity rule. Flips the
        authored intent, republishes ``info.sim`` for the SIM pill, and
        suspends/re-arms AutoRecover. The serial connection (if open)
        stays open; bus state keeps reflecting hardware truth."""
        new_sim = bool(on)
        if new_sim == self._simulation_mode:
            return
        self._simulation_mode = new_sim
        self.pump.set_simulation(new_sim)
        if self._attachment is not None:
            self._attachment.set_sim(new_sim)
        print(
            f"{'🔵' if new_sim else '🟡'} {self.name} simulation "
            f"{'enabled' if new_sim else 'disabled'}"
        )

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Tip?",      "method": "has_tip",      "icon": "eye",      "group": "pump"},
            {"label": "Home Pump", "method": "initialize",   "icon": "activity", "group": "pump"},
            {"label": "Eject Tip", "method": "eject_tip",    "icon": "forward",  "group": "pump"},
            {"label": "Reconnect", "method": "reconnect",    "icon": "rotate",   "group": "conn"},
            {"label": "Release",   "method": "release_pump", "icon": "link-off", "group": "conn"},
        ]

    # ── Teardown ──────────────────────────────────────────────────────

    def close(self):
        """Release the bus attachment + close the serial port. Idempotent."""
        try:
            if self._attachment is not None:
                self._attachment.close()
        except Exception:
            log.exception("PipettorSP2840mm[%s]: attachment close raised", self.name)
        finally:
            self._attachment = None
            self.pump.release()
