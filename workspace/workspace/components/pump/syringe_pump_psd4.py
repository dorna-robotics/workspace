"""Hamilton MICROLAB PSD/4 precision syringe drive — device component.

Stationary bench instrument: a kinematic body PLUS a device-bus
attachment to the pump over RS-232 / RS-485. Same shape as
``MultiMeterBk879b`` and ``ScaleSpx222`` — the component owns the
device link (a :class:`PSD4Station`) and exposes a sim-agnostic pumping
API in µL; recipes, actions and the operator UI call the same methods
regardless of sim or real.

Scene yaml::

    syringe_pump_1:
      type: "syringe_pump_psd4"
      port: "/dev/serial/by-id/usb-..."   # "" → no bus claim
      address: 0                          # rotary switch position
      baud: 9600
      syringe_volume_ul: 1000.0           # the INSTALLED syringe
      simulation: false
      critical: true
      attach:
        parent_name: "fixture_plate_1"
        ...

Empty ``port`` means "no device claimed": the component still works
(sim ops keep volume bookkeeping), it just takes no bus id and renders
no Devices-panel row.

``syringe_volume_ul`` is the one setting with no safe default. The pump
reports steps, never volume, and cannot detect which syringe is fitted
— if this does not match the barrel on the bench, every aspirate and
dispense is wrong by the same ratio, silently.

Full operating guide — settings that are silently wrong if guessed
(``syringe_volume_ul``, ``variant``, ``output_right``), the valve DIP
table, plumbing nozzles to ports, and which recipe drives what:
``docs/liquid-handling.md``. See also ``docs/device-guide.md`` for the
device contract and ``docs/component-guide.md`` §7 (atomic ops on the
component, not the recipe) / §8 (operator actions).
"""

from __future__ import annotations

import logging
from copy import deepcopy

from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register
from workspace.components.pump.psd4_station import PSD4Station
from workspace.devices import AutoRecover, attach_device


log = logging.getLogger(__name__)


@register("syringe_pump_psd4")
class SyringePumpPsd4:

    DEFAULTS = dict(
        # Body sized from the manual's specification table (§2-1):
        # 5.00" H x 1.75" W x 4.20" D -> 127.0 x 44.45 x 106.68 mm.
        # Measured off static/CAD/syringe_pump_psd4.glb (mm, z from the
        # base plane): x -22.5..22.4, y -67.5..101.2, z 0..135. The y
        # asymmetry is real — the body extends further forward of the
        # attachment origin than behind it.
        # hole_1..hole_4 are the mounting holes, set manually on the
        # bench — zeros are placeholders, not measurements.
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
        # ── device link ──────────────────────────────────────────────
        port="",                   # "" → no bus claim; non-empty → claim + panel row
        address=0,                 # rotary address switch position (0 → pump answers as "1")
        baud=9600,                 # 9600 with DIP 3 off (factory), 38400 with it on
        timeout=2.0,               # s — per-command read deadline
        syringe_volume_ul=1000.0,  # MUST match the installed syringe
        # Which pump this is — the step scale depends on it and the pump
        # cannot report it, so like the syringe volume it MUST match the
        # hardware or every move scales by the ratio, silently:
        #   "standard"    → PSD/4 and PSD/6 high-torque: 3000 steps per
        #                   stroke, 24000 in high resolution
        #   "smooth_flow" → PSD/4 SF / PSD/6 SF: 24000 / 192000
        variant="smooth_flow",
        high_resolution=False,     # True → high-res step mode (see variant for the scales)
        # Which physical side "output" means, assigned during
        # initialization: true = right (Z), false = left (Y), viewed
        # from the front of the pump. A property of the plumbing — get
        # it wrong and every valve("output") sends fluid the wrong way,
        # with no error.
        output_right=True,
        simulation=True,
        # ``critical`` controls whether a non-sim, unreachable transition
        # pauses the runtime. A pump we can't drive mid-run is a real
        # fault worth pausing for; set ``critical: false`` in scene yaml
        # where the pump is genuinely advisory.
        critical=True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        # ── Kinematic assembly (3D viewer + collision) ──
        self.assembly = {
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}),
            )
            for k in prm["anchors"]
        }

        # ── Device link ──
        # Authored sim intent — failures must NOT flip it (same rule as
        # Core / the meter / the probe). An unreachable real pump is a
        # fault we surface, not a reason to silently switch to sim.
        self._simulation_mode = bool(prm["simulation"])
        self._port = prm["port"] or ""
        self._critical = bool(prm["critical"])

        # The one sim/real branch — the station hides it from recipes.
        self.pump = PSD4Station(
            port=self._port,
            address=prm["address"],
            baud=int(prm["baud"]),
            timeout=float(prm["timeout"]),
            syringe_volume_ul=float(prm["syringe_volume_ul"]),
            variant=str(prm["variant"]),
            high_resolution=bool(prm["high_resolution"]),
            output_right=bool(prm["output_right"]),
            simulation=self._simulation_mode,
            label=self.name,
        )

        # Always attempt the initial real connect, regardless of sim
        # (device-guide §16). Failure does NOT raise.
        if self._port:
            self.pump.recover()

        # Bus attachment — gated on ``port``, same rule as Core's ip.
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
                    kind=PSD4Station.KIND,
                    sim=self._simulation_mode,
                    critical=self._critical,
                    meta={"port": self._port, "address": prm["address"],
                          "syringe_volume_ul": float(prm["syringe_volume_ul"])},
                    recover_factory=_make_recover,
                )
            except Exception:
                # Adapter wiring must NOT take down the component.
                log.exception("SyringePumpPsd4[%s]: attach_device failed", self.name)

    # ── DeviceComponent contract ──────────────────────────────────────

    @property
    def device_ids(self) -> list[str]:
        """Empty when ``port`` is unset — no device claimed, no panel
        row (mirrors Core / MultiMeter / Pipettor)."""
        return [self.pump.id] if self._port else []

    def device_claim(self, device_id: str) -> str:
        if device_id == self.pump.id:
            return "sim" if self._simulation_mode else "real"
        return "real"

    # ── Atomic pump API (component-level — recipes call these) ────────
    # Sim-agnostic by construction: the station branches internally.
    # Moves return True/False, reads return a value or ``None`` when
    # disconnected and not in sim. Never raises on transient failures —
    # the station classifies them, so a refused command leaves the
    # device green and a dropped link turns it red.
    #
    # ``sim_return`` (device-guide §17) — inline default, shaped like the
    # real return. Pass your own to inject; real mode ignores it.

    def is_connected(self) -> bool:
        return self.pump.is_connected()

    def syringe_volume(self, sim_return: float = 0.0):
        """The syringe size currently in effect, µL."""
        return self.pump.syringe_volume(sim_return=sim_return)

    def initialize(self, output_right=None, half_force: bool = False,
                   syringe_volume_ul=None, sim_return: bool = True):
        """Home the plunger and valve, and assign valve addressing.

        ``output_right`` picks which physical side "output" means (right
        = Z, left = Y); ``None`` uses the scene's ``output_right``,
        which is normally right since it describes the plumbing. Note
        ``reconnect`` also homes, so a recovered pump is already in a
        known state."""
        return self.pump.initialize(output_right=output_right,
                                    syringe_volume_ul=syringe_volume_ul,
                                    half_force=half_force, sim_return=sim_return)


    def valve(self, position="input", shortest: bool = False,
              direction: str = "shortest", sim_return: bool = True):
        """Move the valve, by any addressing mode the pump supports:

        * logical — input / output / bypass / extra / wash / return
        * numbered port — 1-8 on a distribution valve
        * absolute angle — ``"90deg"``, in 15° increments

        ``shortest`` routes logical moves the short way; ``direction``
        (shortest / cw / ccw) fixes the path for numbered and angular
        moves, which matters when carryover does."""
        return self.pump.valve(position, shortest=shortest,
                               direction=direction, sim_return=sim_return)

    def aspirate(self, volume_ul: float, port=None, sim_return: bool = True):
        """Draw ``volume_ul`` in from ``port`` — a logical name
        (``"input"``), a numbered port (``3``), or an angle
        (``"90deg"``). The valve moves there first. Omit ``port`` to
        draw through wherever the valve already is."""
        return self.pump.aspirate(volume_ul, port=port, sim_return=sim_return)

    def dispense(self, volume_ul: float, port=None, sim_return: bool = True):
        """Push ``volume_ul`` out through ``port``. Same addressing as
        :meth:`aspirate`."""
        return self.pump.dispense(volume_ul, port=port, sim_return=sim_return)

    def move_to_volume(self, volume_ul: float, port=None, sim_return: bool = True):
        """Absolute: leave exactly this much in the barrel. The
        difference moves through ``port`` — name it, or it goes through
        wherever the valve already is."""
        return self.pump.move_to_volume(volume_ul, port=port, sim_return=sim_return)

    def prime(self, cycles: int = 2, volume_ul=None, from_port="input",
              to_port="output", sim_return: bool = True):
        """Full-barrel fill/empty cycles to flush air out of the fluid
        path; ``volume_ul`` defaults to the declared barrel."""
        return self.pump.prime(cycles, volume_ul=volume_ul, from_port=from_port,
                               to_port=to_port, sim_return=sim_return)

    def empty(self, port=None, sim_return: bool = True):
        """Plunger fully home, contents pushed out through ``port``."""
        return self.pump.empty(port=port, sim_return=sim_return)

    def volume(self, sim_return: float = 0.0):
        """µL currently held."""
        return self.pump.volume(sim_return=sim_return)

    def set_speed(self, percent: float = 100.0, sim_return: bool = True):
        """Plunger speed, 0-100 (100 = fastest preset, 0 = slowest)."""
        return self.pump.set_speed(percent, sim_return=sim_return)

    def status(self, sim_return: dict = {"ready": True, "error": 0, "error_text": "no error"}):
        return self.pump.status(sim_return=sim_return)

    # ── Operator actions (component-guide §8) ─────────────────────────
    # Every method here takes no required args. Grouped in pairs —
    # consecutive entries sharing a ``group`` render as one row.

    def initialize_pump(self):
        """Operator button — home the plunger and valve."""
        return self.pump.initialize()

    def stop(self):
        """Emergency stop. Aborts the buffer and the move in progress.

        Mid-stroke termination can lose steps, so the pump needs
        re-initializing before its position means anything again.
        """
        return self.pump.stop()

    def report_volume(self):
        """Operator button — µL held, as a printable string."""
        v = self.pump.volume()
        return None if v is None else f"{v:.1f} µL"

    def report_status(self):
        """Operator button — ready/busy + last error, printable."""
        st = self.pump.status()
        if st is None:
            return None
        return f"{'ready' if st['ready'] else 'busy'} — {st['error_text']}"

    def release_pump(self):
        """Close the serial port and mark the pump down — lets the
        operator unplug it without a connection-lost alarm."""
        self.pump.release()

    def simulation(self, on: bool = True):
        """Live sim/real flip — device-guide §16 parity rule. Flips the
        authored intent, republishes ``info.sim`` for the SIM pill, and
        suspends/re-arms AutoRecover. The serial connection stays open;
        bus state keeps reflecting hardware truth."""
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
            {"label": "Initialize", "method": "initialize_pump", "icon": "rotate",   "group": "run"},
            {"label": "Volume",     "method": "report_volume",   "icon": "activity", "group": "read"},
            {"label": "Status",     "method": "report_status",   "icon": "eye",      "group": "read"},
            {"label": "Release",    "method": "release_pump",    "icon": "link-off", "group": "conn"},
        ]

    # ── Teardown ──────────────────────────────────────────────────────

    def close(self):
        """Release the bus attachment + close the serial port. Idempotent."""
        try:
            if self._attachment is not None:
                self._attachment.close()
        except Exception:
            log.exception("SyringePumpPsd4[%s]: attachment close raised", self.name)
        finally:
            self._attachment = None
            self.pump.release()
