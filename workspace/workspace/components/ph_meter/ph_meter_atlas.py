"""Atlas Scientific pH probe — a robot-mounted tool that is ALSO a
workspace-owned serial device (the EZO-pH circuit).

Same shape as ``PipettorSP2840mm``: the kinematic side (tool changer,
anchors, collision box) comes from ``Gripper``, the device side follows
the standard workspace-owned stack (device-guide §10 shape A): raw
driver (``ezo_ph_driver.AtlasPH``) → Device-protocol station
(``ezo_ph_station.EzoPHStation``) → this component, which owns the bus
attachment and the sim-agnostic atomic ops that recipes and operator
buttons call.

Scene yaml:

    ph_meter_1:
      type: "ph_meter_atlas"
      has_tool_changer: true
      simulation: true
      port: ""                # "" → no bus claim; set a /dev/... to claim
      ...attach...

Empty ``port`` means "no device claimed": the component still works
(sim ops return canned readings), it just takes no bus id and renders
no Devices-panel row. Set a port to claim the circuit — the dot then
shows hardware truth (sim is orthogonal, device-guide §16). Use the
stable ``/dev/serial/by-id/...`` symlink, not ``/dev/ttyUSB0``
(device-guide §9).

Geometry, measured off ph_meter_atlas.glb (mm, z from the flange face):
    0.0 -  40.0   mount body        43.0 dia
   40.0 -  53.5   collar / flange   47.6 dia
   41.5 -  77.1   taper             16.3 dia at its widest
   77.1 - 192.1   probe shaft       12.2 dia
  182.1 - 190.1   glass bulb inside the guard sleeve
"""

from __future__ import annotations

import logging
from copy import deepcopy

from mergedeep import merge

from workspace.components.factory import register
from workspace.components.gripper.gripper import Gripper
from workspace.components.ph.ezo_ph_driver import Reading, Slope
from workspace.components.ph.ezo_ph_station import EzoPHStation
from workspace.devices import AutoRecover, attach_device


log = logging.getLogger(__name__)


@register("ph_meter_atlas")
class PhMeterAtlas(Gripper):
    DEFAULTS = dict(
        anchors={"body": {"center": [0, 0, 0, 0, 0, 0], "tcp": [0, 0, 192.1, 0, 0, 0], "tip": [0, 0, 192.1, 0, 0, 0]}},
        collision_box =
            {"body":[
                {"pose":[0.0, 0.0, (53.5+192.1)/2, 0.0, 0.0, 0.0], "scale":[16.3, 16.3, 192.1-53.5]},
                {"pose":[0.0, 0.0, (40.0+53.5)/2, 0.0, 0.0, 0.0], "scale":[47.6, 47.6, 53.5-40.0]},
                {"pose":[0.0, 0.0, 40.0/2, 0.0, 0.0, 0.0], "scale":[43.0, 43.0, 40.0]},

        ]},
        #cfg
        has_tool_changer = False,
        # Passive dip tool — no IO of its own, so enable/disable are no-ops.
        output_enable=[[None, None, 0], [None, None, 0]],
        output_disable=[[None, None, 0], [None, None, 0]],
        # ── device link ──────────────────────────────────────────────
        port="",           # "" → no bus claim; non-empty → claim + panel row
        baud=9600,         # EZO factory default
        timeout=2.0,       # s — per-command read deadline
        simulation=True,
        # ``critical`` controls whether a non-sim, unreachable transition
        # pauses the runtime. A probe we can't read mid-run is a real
        # fault worth pausing for; set ``critical: false`` in scene yaml
        # where the pH value is genuinely advisory.
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

        # Authored simulation intent — failures must NOT flip this (same
        # rule as Core / the meter / the pump). An unreachable real probe
        # is a fault we surface, not a reason to silently switch to sim.
        self._simulation_mode = bool(prm["simulation"])
        self._port = prm["port"] or ""
        self._critical = bool(prm["critical"])

        # ── The one sim/real branch — station handles the unified API;
        #    recipes and operator buttons never think about it. ──
        self.probe = EzoPHStation(
            port=self._port,
            baud=int(prm["baud"]),
            timeout=float(prm["timeout"]),
            simulation=self._simulation_mode,
            label=self.name,
        )

        # Always attempt the initial real connection, regardless of sim —
        # bus state reflects hardware truth (device-guide §16). In sim,
        # AutoRecover is suspended via ``attachment.set_sim``, so a failed
        # connect sits red without retry storms. Failure does NOT raise.
        if self._port:
            self.probe.recover()

        # Bus attachment — gated on ``port``, same rule as Core's ip and
        # the multimeter's / pipettor's port.
        self._attachment = None
        if self._port:
            def _make_recover() -> AutoRecover:
                return AutoRecover(
                    recover_fn=self.probe.recover,
                    set_status=self.probe._set_state,
                    log_label=self.probe.id,
                )

            try:
                self._attachment = attach_device(
                    self.probe,
                    kind=EzoPHStation.KIND,
                    sim=self._simulation_mode,
                    critical=self._critical,
                    meta={"port": self._port},
                    recover_factory=_make_recover,
                )
            except Exception:
                # Adapter wiring must NOT take down the component — the
                # probe is still usable for direct reads.
                log.exception("PhMeterAtlas[%s]: attach_device failed", self.name)

    # ── DeviceComponent contract ───────────────────────────────────────

    @property
    def device_ids(self) -> list[str]:
        """Empty when ``port`` is unset — no device claimed, no panel row
        (mirrors Core / MultiMeter / Pipettor)."""
        return [self.probe.id] if self._port else []

    def device_claim(self, device_id: str) -> str:
        if device_id == self.probe.id:
            return "sim" if self._simulation_mode else "real"
        return "real"

    # ── Atomic pH API (component-level — recipes call these) ───────────
    # Sim-agnostic by construction: the station branches internally.
    # Returns ``Reading`` / ``Slope`` / float on success, ``None`` when
    # disconnected and not in sim. Never raises on transient failures —
    # the station transitions state to ``down`` so AutoRecover takes over.
    #
    # ``sim_return`` (device-guide §17) — explicit sim injection, passed
    # straight to the station. Its default IS the canned sim value, inline
    # in the signature and shaped like the real return. Pass your own to
    # inject a different reading; real mode ignores it.

    def is_connected(self) -> bool:
        return self.probe.is_connected()

    def read(self, sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Single reading (``Reading`` or None)."""
        return self.probe.read(sim_return=sim_return)

    def read_at_temperature(self, celsius: float,
                            sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Compensate for the sample temperature and read in one call."""
        return self.probe.read_at_temperature(celsius, sim_return=sim_return)

    def read_stable(self, n: int = 3, tolerance: float = 0.02, max_readings: int = 20,
                    sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Block until the electrode settles (``Reading`` or None)."""
        return self.probe.read_stable(n=n, tolerance=tolerance,
                                      max_readings=max_readings, sim_return=sim_return)

    def ph(self, stable: bool = True, sim_return: float = 7.000):
        """pH value (float or None). ``stable=True`` waits for a settled
        reading; ``stable=False`` takes an instantaneous one."""
        return self.probe.ph(stable=stable, sim_return=sim_return)

    def slope(self, sim_return=Slope(acid_percent=99.5, base_percent=99.2, offset_mv=0.0, raw="sim")):
        """Probe health vs an ideal electrode (``Slope`` or None)."""
        return self.probe.slope(sim_return=sim_return)

    def calibration_points(self, sim_return: int = 3):
        """Stored calibration points, 0–3 (int or None)."""
        return self.probe.calibration_points(sim_return=sim_return)

    def calibrate(self, value: float, sim_return: bool = True):
        """Calibrate against the buffer on the probe — the point is picked
        from ``value`` (mid must come first; the chip enforces it).
        Returns True/False, never raises."""
        return self.probe.calibrate(value, sim_return=sim_return)

    def calibrate_clear(self, sim_return: bool = True):
        """Wipe all stored calibration points."""
        return self.probe.calibrate_clear(sim_return=sim_return)

    def set_temperature_compensation(self, celsius: float, sim_return: bool = True):
        """Store the sample temperature on the chip (persists)."""
        return self.probe.set_temperature_compensation(celsius, sim_return=sim_return)

    def get_temperature_compensation(self, sim_return: float = 25.0):
        """The temperature the chip is compensating for (°C, or None)."""
        return self.probe.get_temperature_compensation(sim_return=sim_return)

    # ── Operator actions (component-guide §8) ─────────────────────────
    # Buttons in the Operator Controls panel — every method here takes no
    # required args and returns something the runtime can stringify
    # (``Reading`` / ``Slope`` have __str__). ``calibrate`` needs a buffer
    # value, so it stays a recipe/action call, not a button.
    #
    # Groups are paired deliberately: consecutive entries sharing a
    # ``group`` render as ONE row of equal-width buttons, so two per
    # group keeps every label readable on a sidebar-width tablet.

    def reconnect(self):
        """Re-run the connection sequence (same path AutoRecover uses)."""
        return self.probe.recover()

    def release_probe(self):
        """Close the serial port and mark the probe down — lets the
        operator unplug it without a connection-lost alarm."""
        self.probe.release()

    def simulation(self, on: bool = True):
        """Live sim/real flip — device-guide §16 parity rule. Flips the
        authored intent, republishes ``info.sim`` for the SIM pill, and
        suspends/re-arms AutoRecover. The serial connection (if open)
        stays open; bus state keeps reflecting hardware truth."""
        new_sim = bool(on)
        if new_sim == self._simulation_mode:
            return
        self._simulation_mode = new_sim
        self.probe.set_simulation(new_sim)
        if self._attachment is not None:
            self._attachment.set_sim(new_sim)
        print(
            f"{'🔵' if new_sim else '🟡'} {self.name} simulation "
            f"{'enabled' if new_sim else 'disabled'}"
        )

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Read pH",    "method": "read",               "icon": "activity", "group": "read"},
            {"label": "Read Settled", "method": "read_stable",      "icon": "activity", "group": "read"},
            {"label": "Health",     "method": "slope",              "icon": "eye",      "group": "cal"},
            {"label": "Cal Points", "method": "calibration_points", "icon": "eye",      "group": "cal"},
            {"label": "Reconnect",  "method": "reconnect",          "icon": "rotate",   "group": "conn"},
            {"label": "Release",    "method": "release_probe",      "icon": "link-off", "group": "conn"},
        ]

    # ── Teardown ──────────────────────────────────────────────────────

    def close(self):
        """Release the bus attachment + close the serial port. Idempotent."""
        try:
            if self._attachment is not None:
                self._attachment.close()
        except Exception:
            log.exception("PhMeterAtlas[%s]: attachment close raised", self.name)
        finally:
            self._attachment = None
            self.probe.release()
