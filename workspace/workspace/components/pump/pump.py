"""Generic syringe pump component.

One component type for any syringe pump: the brand is a ``driver:``
key, everything else is hardware facts the pump cannot report plus the
port map. The component owns

* the device-bus attachment (one id, gated on ``port``),
* the atomic ops — the ONLY things that move the plunger or valve,
* the port map (``valve_ports`` + ``outlets``) and name → number
  resolution,
* the µL↔steps scale (``syringe_volume_ul`` — the INSTALLED barrel,
  which the drive cannot detect),
* bookkeeping the pump cannot do: what is in the barrel, what last
  went out of each outlet, how long each op took,
* sim / real parity, recovery, operator buttons.

It does NOT know liquids' rules — rinse, air gap, venting a septum
vial — those are a recipe's job.

Scene yaml (see ``docs/liquid-handling.md`` for every setting)::

    pump_1:
      type: "pump"
      driver: "psd4"
      port: "/dev/serial/by-id/usb-…"   # "" → no bus claim, sim bookkeeping only
      address: 0                        # rotary switch position, 0-9 / A-F
      baud: 9600                        # 9600 (DIP 3 off) or 38400 (DIP 3 on)
      simulation: true
      critical: true

      syringe_volume_ul: 100.0          # µL the full plunger stroke sweeps
      valve_type: 3                     # see the table in DEFAULTS
      variant: "standard"
      high_resolution: true
      output_right: true
      default_speed: 100                # percent, used when a call gives no speed

      valve_ports:                      # port → name, or port → {name, tube_volume_ul}
        1: {name: reservoir,  tube_volume_ul: 100}
        2: {name: needle,     tube_volume_ul: 150}
      outlets: [2]                      # these ports are nozzles; the rest are sources

      attach: {…}

``tube_volume_ul`` is the WHOLE dead volume of that port's line —
reservoir to valve for a source, valve to needle **tip** for an
outlet, so on an outlet it includes the fitted needle's internal bore
(~7 µL for a 22G x 2", ~44 µL for a 16G x 4"), not just the tubing.
The nozzle's ``needle_gauge``/``needle_length`` are declarative only
and do NOT feed this number — a needle swap means updating it here
too. ``prime`` uses it to compute how many barrel cycles actually
reach the tip (see :meth:`prime`); leave it 0 and prime falls back to
1 cycle.

Valve bodies vs. holes: ``valve_type`` declares the BODY the firmware
reports (a 6-port ceramic distribution valve is an 8-position type-3
body with two blind positions); initialize verifies it against the
pump's DIP switches. ``valve_ports`` lists the live holes; any port not
listed — blind or dry — is refused. Ports are the pump's NUMBERED
positions, so a distribution valve is assumed; a plain logical-letter
Y/T valve is driven from ``psd4_driver`` directly if one is ever
fitted.

One pump = one barrel = one component = one bus row. "Two syringes" is
two pump drives, so it is two entries (each on its own adapter).
Plumbed tools (needles, arms) declare ``pump:`` + ``pump_port:`` on
their own entry and drive this component through ``PumpLink``
(``pump_link.py``) — they own no device and take no bus row.
"""

from __future__ import annotations

import logging
import math
import time
from copy import deepcopy
from typing import Optional, Union

from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register
from workspace.components.pump.pump_station import PumpStation
from workspace.devices import AutoRecover, attach_device

log = logging.getLogger(__name__)

PortRef = Union[int, str, None]


class PortError(ValueError):
    """A port name or number that the map cannot resolve."""


@register("pump")
class Pump:
    DEFAULTS = dict(
        # Body sized from the PSD/4 manual's specification table (§2-1):
        # 5.00" H x 1.75" W x 4.20" D. Measured off
        # static/CAD/pump.glb (mm, z from the base plane):
        # x -22.5..22.4, y -67.5..101.2, z 0..135. The y asymmetry is
        # real — the body extends further forward of the attachment
        # origin than behind it. hole_1..hole_3 are the mounting holes.
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "top":    [0, 0, 135.0, 0, 0, 0],
            "hole_0": [12.5, 51.27, 0, 0, 0, 0],
            "hole_1": [-12.5, 51.27, 0, 0, 0, 0],
            "hole_2": [12.5, 1.27, 0, 0, 0, 0],
            "hole_3": [-12.5, 1.27, 0, 0, 0, 0],
        }},
        collision_box={"body": [
            {"pose": [-0.1, 16.8, 67.5, 0, 0, 0], "scale": [44.9, 168.6, 135.0], "padding_enabled": True},
        ]},

        # ── connection ────────────────────────────────────────────
        driver="psd4",       # key into pump_station.DRIVERS
        port="",             # /dev/serial/by-id/… symlink; "" → no device claim
        address=0,           # rotary switch position: 0-9 or "A"-"F"
        baud=9600,           # 9600 (DIP 3 off, factory) | 38400 (DIP 3 on)
        timeout=2.0,         # s — per-command read deadline
        simulation=True,
        critical=True,

        # ── hardware facts (the pump cannot report these) ─────────
        # µL the full 30 mm stroke sweeps. NOT always the barrel
        # label: PSD-series syringes sweep full nominal, 1700-series
        # gastights sweep about HALF of nominal (a 1725/250 µL sweeps
        # ~125). Wrong value = every dose off by the same ratio.
        syringe_volume_ul=100.0,

        # Valve BODY the firmware reports (?21000); initialize()
        # refuses on mismatch. None = skip the check.
        #   0 = 3-way 120° Y valve            (DIP 4-6: off/off/off, factory)
        #   1 = 4-way 90° T valve             (DIP 4-6: ON /off/off)
        #   2 = 3-port 90° distribution       (DIP 4-6: off/ON /off)
        #   3 = 8-port 45° distribution       (DIP 4-6: ON /ON /off)
        #       — also what a 6-port ceramic valve reports: same body,
        #         two blind positions; just leave them out of valve_ports
        #   4 = 4-port distribution / wash    (DIP 4-6: off/off/ON )
        # DIPs are read at power-up — power-cycle after changing them.
        valve_type=3,

        # Which drive this is — the serial protocol is identical, only
        # the steps-per-stroke differ, and the pump cannot say which:
        #   "standard"    = PSD/4, PSD/6 high-torque   (3000/24000 steps)
        #   "smooth_flow" = PSD/4 SF, PSD/6 SF, PN 97709-xx (24000/192000)
        # Declared wrong, every move is scaled 8x, silently — always
        # declare it in the scene.
        variant="standard",

        # Step mode over the same stroke:
        #   True  = high resolution — 8x finer, full stroke ~8x slower
        #           on SF drives
        #   False = standard
        high_resolution=True,

        # Which side the valve's logical OUTPUT is on after homing,
        # viewed from the front (PSD Z vs Y initialize):
        #   True  = output on the right  (Z, factory convention)
        #   False = output on the left   (Y)
        # Homing expels the barrel through that side, so it must match
        # the plumbing even though this component addresses ports by
        # number.
        output_right=True,

        # Plunger speed in percent used when a call passes no speed=.
        # 100 = the pump's fastest preset, 0 = slowest.
        default_speed=100.0,

        # ── plumbing: valve port → what hangs on it ───────────────
        # port → name, or port → {name, tube_volume_ul}.
        # tube_volume_ul (µL) = the WHOLE dead volume of that line:
        # reservoir→valve for sources, valve→needle TIP for outlets —
        # so an outlet's number includes the fitted needle's bore, and
        # a needle swap means updating it (the nozzle's declared
        # gauge/length do not feed it). prime() sizes its cycle count
        # from it. Unlisted ports are dry/blind and refused. Empty by
        # default — every scene declares its own plumbing
        # (explicit-values rule).
        valve_ports={},
        # Ports that are nozzles (dispense side). Every listed port
        # not in here is a source. Port numbers or names both work.
        outlets=[],
    )

    # ── construction ──────────────────────────────────────────────

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name, self.workspace, self.type = name, workspace, prm["type"]
        self.assembly = {
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name,
                     **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}))
            for k in prm["anchors"]
        }

        # Authored sim intent — failures must NOT flip it. An
        # unreachable real pump is a fault we surface, not a reason to
        # silently switch to sim.
        self._simulation_mode = bool(prm["simulation"])
        self._port = str(prm["port"] or "")
        self._critical = bool(prm["critical"])
        self._syringe_ul = float(prm["syringe_volume_ul"])

        self._ports = self._parse_ports(prm["valve_ports"], prm["outlets"])
        self._names: dict[str, int] = {v["name"]: k for k, v in self._ports.items()}

        # bookkeeping the pump cannot do
        self._material_in_barrel: Optional[str] = None
        self._material_at: dict[str, Optional[str]] = {n: None for n in self.outlets()}
        self._last_op: dict = {}
        self._op_log: list[dict] = []

        # The one sim/real branch of the stack — the station hides it
        # from everything above.
        self.pump = PumpStation(
            port=self._port, driver=str(prm["driver"]),
            driver_kwargs=dict(address=prm["address"], baud=int(prm["baud"]),
                               timeout=float(prm["timeout"]),
                               high_resolution=bool(prm["high_resolution"]),
                               output_right=bool(prm["output_right"]),
                               variant=str(prm["variant"])),
            valve_type=prm["valve_type"], speed=float(prm["default_speed"]),
            simulation=self._simulation_mode, label=self.name,
        )

        # Always attempt the initial real connect, regardless of sim
        # (device-guide §16). A pump is unusable until configured and
        # homed, so initialization is part of construction rather than
        # something a protocol must remember: recover() does the whole
        # configure-and-home for a claimed pump, the unclaimed / sim
        # path gets the explicit call.
        if self._port:
            self.pump.recover()
        else:
            self.pump.initialize()

        # Bus attachment — gated on ``port``, same rule as Core's ip.
        self._attachment = None
        if self._port:
            try:
                self._attachment = attach_device(
                    self.pump, kind=PumpStation.KIND, sim=self._simulation_mode,
                    critical=self._critical,
                    meta={"port": self._port, "driver": prm["driver"],
                          "address": prm["address"],
                          "syringe_volume_ul": self._syringe_ul, "ports": self.ports()},
                    recover_factory=lambda: AutoRecover(
                        recover_fn=self.pump.recover, set_status=self.pump._set_state,
                        log_label=self.pump.id),
                )
            except Exception:
                # Adapter wiring must NOT take down the component.
                log.exception("Pump[%s]: attach_device failed", self.name)

    @staticmethod
    def _parse_ports(raw: dict, outlets) -> dict[int, dict]:
        """Normalize to {port: {name, tube_volume_ul, outlet}}."""
        out: dict[int, dict] = {}
        for k, v in (raw or {}).items():
            port = int(k)
            if isinstance(v, dict):
                name = str(v.get("name", port))
                tube = float(v.get("tube_volume_ul", 0.0))
            else:
                name, tube = str(v), 0.0
            out[port] = {"name": name, "tube_volume_ul": tube, "outlet": False}
        marks = set()
        for o in (outlets or []):
            if isinstance(o, str) and not o.isdigit():
                hit = [p for p, v in out.items() if v["name"] == o]
                if not hit:
                    raise PortError(f"outlets: no port named {o!r}")
                marks.add(hit[0])
            else:
                marks.add(int(o))
        for p in marks:
            if p not in out:
                raise PortError(f"outlets: port {p} is not in valve_ports")
            out[p]["outlet"] = True
        return out

    # ── DeviceComponent contract ──────────────────────────────────

    @property
    def device_ids(self) -> list[str]:
        """Empty when ``port`` is unset — no device claimed, no panel
        row (same condition as the attach gate above)."""
        return [self.pump.id] if self._port else []

    def device_claim(self, device_id: str) -> str:
        if device_id == self.pump.id:
            return "sim" if self._simulation_mode else "real"
        return "real"

    def is_connected(self) -> bool:
        return self.pump.is_connected()

    # ── port map ──────────────────────────────────────────────────

    def ports(self) -> dict:
        """The whole map: {port: {name, tube_volume_ul, outlet}}."""
        return deepcopy(self._ports)

    def outlets(self) -> list[str]:
        return [v["name"] for v in self._ports.values() if v["outlet"]]

    def sources(self) -> list[str]:
        return [v["name"] for v in self._ports.values() if not v["outlet"]]

    def port_of(self, name: str) -> int:
        try:
            return self._names[str(name)]
        except KeyError:
            raise PortError(f"{self.name}: no port named {name!r}; have {sorted(self._names)}")

    def name_of(self, port: int) -> str:
        return self._ports.get(int(port), {}).get("name", f"port_{int(port)}")

    def tube_volume(self, port: PortRef) -> float:
        return self._ports.get(self._resolve(port), {}).get("tube_volume_ul", 0.0)

    def _resolve(self, port: PortRef) -> int:
        """int → itself, str → name lookup, None → the single outlet
        (refused when there are zero or several)."""
        if not self._ports:
            raise PortError(f"{self.name}: no valve_ports declared in the scene — "
                            f"add the port map before moving liquid")
        if port is None:
            outs = [p for p, v in self._ports.items() if v["outlet"]]
            if len(outs) != 1:
                raise PortError(f"{self.name}: port required — {len(outs)} outlets "
                                f"declared ({[self.name_of(p) for p in outs]})")
            return outs[0]
        if isinstance(port, str) and not port.isdigit():
            return self.port_of(port)
        port = int(port)
        if port not in self._ports:
            raise PortError(f"{self.name}: port {port} is not declared in valve_ports "
                            f"(live ports: {sorted(self._ports)})")
        return port

    def _resolve_source(self, port: PortRef) -> int:
        """Like :meth:`_resolve` but ``None`` means the single SOURCE —
        the natural default for where prime draws from."""
        if port is not None:
            return self._resolve(port)
        srcs = [p for p, v in self._ports.items() if not v["outlet"]]
        if len(srcs) != 1:
            raise PortError(f"{self.name}: from_port required — {len(srcs)} sources "
                            f"declared ({[self.name_of(p) for p in srcs]})")
        return srcs[0]

    # ── unit conversion ───────────────────────────────────────────

    def syringe_volume(self) -> float:
        """µL the full stroke sweeps — what we were TOLD is fitted.
        Change it with ``initialize(syringe_volume_ul=…)``."""
        return self._syringe_ul

    def _ul_to_steps(self, ul: float) -> int:
        return int(round(float(ul) / self._syringe_ul * self.pump.max_steps()))

    def _steps_to_ul(self, steps: float) -> float:
        return float(steps) / self.pump.max_steps() * self._syringe_ul

    # ── the one motion primitive everything else uses ─────────────

    def _stroke(self, op: str, target_ul: float, port: int, speed,
                sim_return: bool = True) -> bool:
        """Valve to ``port``, optional speed, plunger to ``target_ul``.
        Refuses overflow/underflow before touching hardware."""
        t0 = time.monotonic()
        ok = False
        try:
            if target_ul < -1e-9 or target_ul > self._syringe_ul + 1e-9:
                self.pump.last_error = (3, f"{op}: target {target_ul:.1f} µL outside "
                                           f"0-{self._syringe_ul:.0f} µL barrel")
                return False
            if not self.pump.initialized and not self.pump.initialize(sim_return=sim_return):
                return False
            if speed is not None and float(speed) != self.pump.speed():
                if not self.pump.set_speed(float(speed), sim_return=sim_return):
                    return False
            if self.pump.valve_port() != port and not self.pump.valve_to(port, sim_return=sim_return):
                return False
            ok = self.pump.move_to_steps(self._ul_to_steps(target_ul), sim_return=sim_return)
            return ok
        finally:
            rec = {"op": op, "port": port, "name": self.name_of(port),
                   "target_ul": round(target_ul, 3), "seconds": round(time.monotonic() - t0, 3),
                   "ok": ok, "error": None if ok else self.pump.last_error[1]}
            self._last_op = rec
            self._op_log.append(rec)
            del self._op_log[:-500]

    # ── atomic ops (PumpLink-compatible signatures) ───────────────

    def initialize(self, syringe_volume_ul=None, half_force: bool = True,
                   sim_return: bool = True) -> bool:
        """Configure the pump and home the plunger and valve. Homing
        expels the barrel through the output port — clear the deck
        first.

        Pass ``syringe_volume_ul`` for a barrel swap without touching
        the scene: it rescales every volumetric call from here on.
        """
        if syringe_volume_ul is not None:
            try:
                v = float(syringe_volume_ul)
            except (TypeError, ValueError):
                v = 0.0
            if v <= 0:
                self.pump.last_error = (3, f"syringe volume must be > 0, got {syringe_volume_ul!r}")
                return False
            self._syringe_ul = v
        ok = self.pump.initialize(half_force=half_force, sim_return=sim_return)
        if ok:
            self._material_in_barrel = None
        return ok

    def stop(self) -> bool:
        """Immediate stop. The pump must be re-initialized afterwards;
        the next op does that automatically."""
        return self.pump.terminate()

    def valve(self, port: PortRef = None, sim_return: bool = True, **_) -> bool:
        return self.pump.valve_to(self._resolve(port), sim_return=sim_return)

    def set_speed(self, percent: float = 100.0, sim_return: bool = True, **_) -> bool:
        """Plunger speed, 0-100 (100 = fastest preset, 0 = slowest).
        The drive has ONE speed register — a value set here or per-move
        stays in effect afterwards."""
        return self.pump.set_speed(float(percent), sim_return=sim_return)

    def aspirate(self, volume_ul: float, port: PortRef = None, speed=None,
                 sim_return: bool = True, **_) -> bool:
        """Draw ``volume_ul`` in through ``port`` (default: the single
        outlet). Refuses if the barrel would overflow."""
        p = self._resolve(port)
        held = self.volume()
        if held is None:
            self.pump.last_error = (-1, "aspirate: pump unreachable")
            return False
        if held + volume_ul > self._syringe_ul + 1e-6:
            self.pump.last_error = (3, f"aspirate {volume_ul:.1f} µL would overflow: holding "
                                       f"{held:.1f} of {self._syringe_ul:.0f} µL")
            return False
        if not self._stroke("aspirate", held + float(volume_ul), p, speed, sim_return):
            return False
        if not self._ports[p]["outlet"]:
            self._material_in_barrel = self.name_of(p)
        return True

    def dispense(self, volume_ul: float, port: PortRef = None, speed=None,
                 sim_return: bool = True, **_) -> bool:
        """Push ``volume_ul`` out through ``port`` (default: the single
        outlet). Refuses if the barrel holds less."""
        p = self._resolve(port)
        held = self.volume()
        if held is None:
            self.pump.last_error = (-1, "dispense: pump unreachable")
            return False
        if volume_ul > held + 1e-6:
            self.pump.last_error = (3, f"dispense {volume_ul:.1f} µL but barrel holds {held:.1f}")
            return False
        if not self._stroke("dispense", held - float(volume_ul), p, speed, sim_return):
            return False
        self._note_outlet(p)
        return True

    def move_to_volume(self, volume_ul: float, port: PortRef = None, speed=None,
                       sim_return: bool = True, **_) -> bool:
        """Absolute barrel volume; the difference moves through
        ``port``. Use it when the starting volume is unknown — e.g. the
        first move of a protocol."""
        p = self._resolve(port)
        before = self.volume()
        if not self._stroke("move_to_volume", float(volume_ul), p, speed, sim_return):
            return False
        if before is not None and volume_ul > before and not self._ports[p]["outlet"]:
            self._material_in_barrel = self.name_of(p)
        elif before is not None and volume_ul < before:
            self._note_outlet(p)
        return True

    def empty(self, port: PortRef = None, speed=None, sim_return: bool = True, **_) -> bool:
        """Plunger to 0 through ``port`` (default: the single outlet).
        Put the needle over waste first — the component can't know."""
        p = self._resolve(port)
        if not self._stroke("empty", 0.0, p, speed, sim_return):
            return False
        self._note_outlet(p)
        return True

    def prime_cycles(self, from_port: PortRef, to_port: PortRef = None) -> int:
        """Cycles needed so the pushed volume exceeds the line's dead
        volume: ceil((tube_in + tube_out) / barrel) + 1 margin cycle.
        The only inputs are the declared tube_volume_ul values — an
        outlet's must therefore include the fitted needle's bore (its
        definition is valve→tip). Ports with tube_volume_ul 0
        contribute nothing; with no tube volumes declared this is 1."""
        dead = self.tube_volume(from_port) + self.tube_volume(self._resolve(to_port))
        return max(1, math.ceil(dead / self._syringe_ul - 1e-9) + (1 if dead > 0 else 0))

    def prime(self, cycles: Optional[int] = None, volume_ul=None, from_port: PortRef = None,
              to_port: PortRef = None, aspiration_speed=None, dispensing_speed=None,
              sim_return: bool = True, **_) -> bool:
        """Fill from ``from_port`` (default: the single source), empty
        through ``to_port`` (default: the single outlet), ``cycles``
        times.

        cycles=None → computed from the declared tube volumes via
        :meth:`prime_cycles`, so a 25 µL barrel behind 150 µL of
        tubing gets 7 cycles, not 1. volume_ul=None → full barrel.
        Under-priming returns success with no error signature — size
        the tube volumes from the real plumbing.
        """
        src, dst = self._resolve_source(from_port), self._resolve(to_port)
        if cycles is None:
            cycles = self.prime_cycles(src, dst)
        full = self._syringe_ul if volume_ul is None else min(float(volume_ul), self._syringe_ul)
        for _ in range(int(cycles)):
            if not self.move_to_volume(full, port=src, speed=aspiration_speed,
                                       sim_return=sim_return):
                return False
            if not self.move_to_volume(0.0, port=dst, speed=dispensing_speed,
                                       sim_return=sim_return):
                return False
        return True

    def _note_outlet(self, port: int) -> None:
        if self._ports[port]["outlet"]:
            self._material_at[self.name_of(port)] = self._material_in_barrel

    # ── reads ─────────────────────────────────────────────────────

    def status(self, sim_return: dict = {"ready": True, "error": 0,
                                         "error_text": "no error"}) -> Optional[dict]:
        return self.pump.status(sim_return=sim_return)

    def volume(self) -> Optional[float]:
        """µL currently in the barrel (``None`` when unreachable)."""
        steps = self.pump.position_steps()
        return None if steps is None else round(self._steps_to_ul(steps), 3)

    def open_port(self) -> Optional[int]:
        return self.pump.valve_port()

    def open_port_name(self) -> Optional[str]:
        p = self.open_port()
        return None if p is None else self.name_of(p)

    def material_in_barrel(self) -> Optional[str]:
        """Name of the last source drawn from; None after initialize."""
        return self._material_in_barrel

    def material_at(self, outlet: PortRef = None) -> Optional[str]:
        """Last material pushed through ``outlet`` (default: the single
        outlet) — what sits at that nozzle's tip."""
        return self._material_at.get(self.name_of(self._resolve(outlet)))

    def speed(self) -> float:
        return self.pump.speed()

    def last_op(self) -> dict:
        return dict(self._last_op)

    def op_log(self, n: int = 50) -> list[dict]:
        return [dict(r) for r in self._op_log[-n:]]

    def summary(self) -> dict:
        return {
            "connected": self.is_connected(), "sim": self._simulation_mode,
            "initialized": self.pump.initialized, "barrel_ul": self.volume(),
            "syringe_ul": self._syringe_ul, "material_in_barrel": self._material_in_barrel,
            "material_at": dict(self._material_at), "open_port": self.open_port_name(),
            "speed": self.speed(), "valve_type": self.pump.valve_type(),
            "ports": self.ports(), "status": self.status(), "last_op": self.last_op(),
        }

    # ── operator actions (component-guide §8) ─────────────────────

    def initialize_pump(self):
        return self.initialize()

    def stop_pump(self):
        return self.stop()

    def empty_pump(self):
        # Operator button — a pump with several outlets needs the port
        # named, which a no-arg button can't do; say so instead of
        # raising into the panel.
        try:
            return self.empty()
        except PortError as ex:
            return str(ex)

    def report(self):
        return self.summary()

    def reconnect(self):
        return self.pump.recover()

    def release_pump(self):
        """Close the serial port and mark the pump down — lets the
        operator unplug it without a connection-lost alarm."""
        self.pump.release()

    def simulation(self, on: bool = True):
        """Live sim/real flip — device-guide §16 parity rule. Flips the
        authored intent, republishes ``info.sim`` for the SIM pill, and
        suspends/re-arms AutoRecover. The serial connection stays open;
        bus state keeps reflecting hardware truth."""
        new = bool(on)
        if new == self._simulation_mode:
            return
        self._simulation_mode = new
        self.pump.set_simulation(new)
        if self._attachment is not None:
            self._attachment.set_sim(new)
        print(f"{'🔵' if new else '🟡'} {self.name} simulation {'enabled' if new else 'disabled'}")

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Initialize", "method": "initialize_pump", "icon": "power",     "group": "run"},
            {"label": "Stop",       "method": "stop_pump",       "icon": "power-off", "group": "run"},
            {"label": "Empty",      "method": "empty_pump",      "icon": "forward",   "group": "fluid"},
            {"label": "Report",     "method": "report",          "icon": "eye",       "group": "fluid"},
            {"label": "Reconnect",  "method": "reconnect",       "icon": "rotate",    "group": "conn"},
            {"label": "Release",    "method": "release_pump",    "icon": "link-off",  "group": "conn"},
        ]

    # ── teardown ──────────────────────────────────────────────────

    def close(self):
        """Release the bus attachment + close the serial port. Idempotent."""
        try:
            if self._attachment is not None:
                self._attachment.close()
        except Exception:
            log.exception("Pump[%s]: attachment close raised", self.name)
        finally:
            self._attachment = None
            self.pump.release()
