"""cab AXON 1 label printer + applicator — device component.

Stationary bench instrument: a 3D/kinematic body PLUS a device-bus
attachment to the real printer over TCP. The component owns the device
link (a :class:`CabStation`) and exposes a sim-agnostic printing API;
recipes / actions / the operator UI call the same methods regardless of
sim or real.

Same shape as ``ScaleSpx222`` — the printer talks JScript over TCP, so
the bus identity is keyed on ``ip`` (like Core and the scale) rather
than a serial port.

Wire it up in a scene layout like any other component, plus the device
fields:

    printer_1:
      type: "printer_axon_1"
      ip: ""                  # printer address; "" → no bus claim
      port: 9100              # cab JScript raw-TCP port
      simulation: true
      critical: false
      attach:
        parent_name: "fixture_plate_6"
        ...

The type "printer_axon_1" maps to static/CAD/printer_axon_1.glb.

The tube is held by the gripper and pressed against the applicator pad
(the ``place`` anchor); ``_place_offset`` computes the lateral offset
from the tube radius so the label lands on the barrel. See
``docs/device-guide.md`` for the device contract and
``docs/component-guide.md`` §7 (atomic ops on the component, not the
recipe) / §8 (operator actions).
"""

from __future__ import annotations

import logging
from copy import deepcopy

import numpy as np
from mergedeep import merge
from dorna2 import pose as dorna_pose

from workspace.components.factory import register
from workspace.components.printer.printer import Printer
from workspace.components.printer.cab_station import CabStation
from workspace.devices import AutoRecover, attach_device


log = logging.getLogger(__name__)


# set the sit -25 from the surface
@register("printer_axon_1")
class PrinterAxon1(Printer):

    DEFAULTS = dict(
        anchors={
            "body": {"center":[0, 0, 0, 0, 0, 0], "top": [0, 0, 190, 0, 0, 0], "place":[132.865, 34.16, 93.5-25, 0, 0, -45],
            "hole_0": [200, 50, 0, 0, 0, 0], "hole_1": [-200, 50, 0, 0, 0, 0], "hole_2": [-200, -50, 0, 0, 0, 0], "hole_3": [200, -50, 0, 0, 0, 0],
            "clb_0": [210.865, 41, 107, 0, 0, -90]},
        },
        collision_box =
            {"body":[
                {"pose":[-36.135, 0, 188.5/2, 0, 0, 0], "scale":[560,260,188.5]}#[xyzabc] , [lx,ly,lz]
        ]},
        # ── device link ──────────────────────────────────────────────
        ip="",            # printer IP; empty → no bus claim (component still works)
        port=9100,        # cab JScript raw-TCP port
        simulation=True,
        # ``critical`` controls whether a non-sim, unreachable transition
        # pauses the runtime. A label that never printed leaves an
        # unidentifiable tube, so default True.
        critical=True,
        # Label stock geometry — re-sent to the printer on every connect.
        label_cfg={
            "width_in": 1.5,
            "length_in": 1,
            "gap_in": 0.12,
            "ptype": "l1",
        },
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        # prm
        prm = deepcopy(Printer.DEFAULTS)  # base
        merge(prm, self.DEFAULTS)         # self
        merge(prm, cfg)                   # cfg
        merge(prm, kwargs)                # kwargs

        # type
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))

        # ── Kinematic assembly (3D viewer + collision) — the base builds
        # ``assembly`` and ``slot`` from the merged anchors.
        super().__init__(name=name, workspace=workspace, **prm)

        # ── Device link ──
        # Authored sim intent — failures must NOT flip it (same rule as
        # Core / the scale). An unreachable real printer is a fault we
        # surface, not a reason to silently switch to sim.
        self._simulation_mode = bool(prm["simulation"])
        self._ip = prm["ip"] or ""
        self._port = int(prm["port"])
        self._critical = bool(prm["critical"])
        self.label_cfg = prm["label_cfg"]

        # The one sim/real branch — the station hides it from recipes.
        self.printer = CabStation(
            ip=self._ip,
            port=self._port,
            simulation=self._simulation_mode,
            label=self.name,
            label_cfg=self.label_cfg,
        )

        # Always attempt the initial real connect, regardless of sim
        # (device-guide §16). Failure does NOT raise.
        if self._ip:
            self.printer.recover()

        # Bus attachment — gated on ``ip`` (mirrors Core / the scale).
        # Empty ip → no device claimed, no panel row; the component still
        # works (sim printing, 3D body, pick/place geometry).
        self._attachment = None
        if self._ip:
            def _make_recover() -> AutoRecover:
                return AutoRecover(
                    recover_fn=self.printer.recover,
                    set_status=self.printer._set_state,
                    log_label=self.printer.id,
                )

            try:
                self._attachment = attach_device(
                    self.printer,
                    kind=CabStation.KIND,
                    sim=self._simulation_mode,
                    critical=self._critical,
                    meta={"ip": self._ip, "port": self._port},
                    recover_factory=_make_recover,
                )
            except Exception:
                log.exception("PrinterAxon1[%s]: attach_device failed", self.name)

    # ── DeviceComponent contract ──────────────────────────────────────

    @property
    def device_ids(self) -> list[str]:
        """Device ids this component claims. Empty when ``ip`` is unset
        (no bus presence). Mirrors Core's ip behaviour."""
        return [self.printer.id] if self._ip else []

    def device_claim(self, device_id: str) -> str:
        """Project-level sim/real claim for ``device_id`` — drives the
        panel's SIM pill from one consistent source."""
        if device_id == self.printer.id:
            return "sim" if self._simulation_mode else "real"
        return "real"

    # ── Atomic printing API (component-level — recipes call these) ─────
    # Sim-agnostic by construction. Returns ``bool``. Never raises on
    # transient failures — the station transitions state to ``down`` so
    # AutoRecover takes over.
    #
    # ``sim_return`` (device-guide §17) — explicit sim injection, passed
    # straight to the station. Its default IS the canned sim value, in
    # the signature, shaped like the real return (a ``bool``). Real mode
    # ignores it.

    def is_connected(self) -> bool:
        return self.printer.is_connected()

    def print_label(self, data: str, code_type: str = "code128",
                    autorun: bool = True, verify: bool = True,
                    sim_return: bool = True) -> bool:
        """Print + apply one label carrying ``data`` (bool)."""
        return self.printer.print_label(
            data, code_type=code_type, autorun=autorun, verify=verify,
            sim_return=sim_return,
        )

    def dry_run_spin(self, count: int = 1, sim_return: bool = True) -> bool:
        """Cycle the applicator ``count`` times without printing (bool)."""
        return self.printer.dry_run_spin(count=count, sim_return=sim_return)

    def wait_ready(self, timeout_s: float = None, sim_return: bool = True) -> bool:
        """Block until the printer is online, inactive and idle (bool)."""
        return self.printer.wait_ready(timeout_s=timeout_s, sim_return=sim_return)

    # ── Geometry ──────────────────────────────────────────────────────

    def _place_offset(self, radius):
        """Lateral offset that puts a tube of ``radius`` mm against the
        applicator pad, expressed in the ``place`` anchor's frame."""
        # place_no_rotation
        place_no_rotation = self.assembly[next(iter(self.assembly))].pose("place")[0:3] + self.assembly[next(iter(self.assembly))].anchors["center"][3:6]

        # place
        place = self.assembly[next(iter(self.assembly))].pose("place")

        # offset in no rotation
        offset_in_no_rotation = [np.cos(np.deg2rad(30))*(np.sqrt((radius + 7.1)**2 - 100)),
                            np.sin(np.deg2rad(30))*(np.sqrt((radius + 7.1)**2 - 100)),
                            0] + place[3:6]

        # offset in place
        return dorna_pose.transform_pose(offset_in_no_rotation,
                                from_frame=place_no_rotation,
                                to_frame=place)

    # ── Operator actions (component-guide §8) ─────────────────────────

    def print_test(self):
        """Operator button — print one test label; returns a printable str."""
        ok = self.printer.print_label("TEST")
        r = self.printer.last_result()
        return f"{'printed' if ok else 'failed'}{f' — {r.message}' if r else ''}"

    def spin_once(self):
        """Operator button — feed one blank label (no printing)."""
        return "spun" if self.printer.dry_run_spin(count=1) else "failed"

    def reconnect(self):
        """Re-run the connection sequence (AutoRecover's path)."""
        return self.printer.recover()

    def release_printer(self):
        """Drop the driver + mark the printer down (clean unplug)."""
        self.printer.release()

    def simulation(self, on: bool = True):
        """Live sim/real flip — mirrors ``Core.simulation`` /
        ``ScaleSpx222.simulation`` (device-guide §16 parity rule).
        Flips the authored intent, republishes ``info.sim``, and
        suspends/re-arms AutoRecover."""
        new_sim = bool(on)
        if new_sim == self._simulation_mode:
            return
        self._simulation_mode = new_sim
        self.printer.set_simulation(new_sim)
        if self._attachment is not None:
            self._attachment.set_sim(new_sim)
        print(f"{'🔵' if new_sim else '🟡'} {self.name} simulation "
              f"{'enabled' if new_sim else 'disabled'}")

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Test label", "method": "print_test",      "icon": "activity"},
            {"label": "Feed",       "method": "spin_once",       "icon": "rotate"},
            {"label": "Reconnect",  "method": "reconnect",       "icon": "rotate",   "group": "conn"},
            {"label": "Release",    "method": "release_printer", "icon": "link-off", "group": "conn"},
        ]

    # ── Teardown ──────────────────────────────────────────────────────

    def close(self):
        """Release the bus attachment + drop the driver. Idempotent."""
        try:
            if self._attachment is not None:
                self._attachment.close()
        except Exception:
            log.exception("PrinterAxon1[%s]: attachment close raised", self.name)
        finally:
            self._attachment = None
            self.printer.release()
