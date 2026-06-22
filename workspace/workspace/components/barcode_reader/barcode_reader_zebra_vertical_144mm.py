"""Zebra/Symbol DS457 barcode reader — device component.

A fixed bench station the robot presents a tube to, like an inspection
station — but a workspace-owned **serial device** (USB CDC), not a
vision-daemon camera. So it has two faces:

* a **kinematic body** (3D/collision + a ``place`` anchor the robot
  presents the tube to), like ``Inspection``; and
* a **device-bus attachment** to the DS457 over serial, like the scale
  (``ScaleSpx222``) and multimeter — the component owns the device link
  (a :class:`DS457Station`) and exposes a sim-agnostic scanning API.

Scanning is **host-triggered (on demand)**: the scanner stays quiet until
``scan()`` is called over SSI — it does not stream decodes on its own.
The scanner's host interface must be set to "SSI over USB CDC" with
Host trigger mode (123Scan); the driver enables + triggers + auto-disables
per call.

Mirrors ``ScaleSpx222`` in device shape; the difference is the DS457 is
serial, so the bus identity is keyed on ``port`` (like the multimeter)
rather than ``ip``.

Wire it up in a scene layout like any other component, plus the device
fields:

    barcode_reader_1:
      type: "barcode_reader_zebra_vertical_144mm"
      port: "/dev/ttyACM0"    # scanner serial port; "" → no bus claim
      baud: 9600
      beep: false             # beep on a good read
      simulation: false
      critical: false
      attach:
        parent_name: "fixture_plate_1"
        ...

The type "barcode_reader_zebra_vertical_144mm" maps to
static/CAD/barcode_reader_zebra_vertical_144mm.glb.

See ``docs/device-guide.md`` for the device contract and
``docs/component-guide.md`` §7 (atomic ops on the component, not the
recipe) / §8 (operator actions).
"""

from __future__ import annotations

import logging
from copy import deepcopy

from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register
from workspace.components.barcode_reader.ds457_station import DS457Station
from workspace.components.barcode_reader.ds457_driver import Scan, ALL_SYMBOLOGIES
from workspace.devices import AutoRecover, attach_device


log = logging.getLogger(__name__)


@register("barcode_reader_zebra_vertical_144mm")
class BarcodeReaderZebraVertical144mm:
    DEFAULTS = dict(
        anchors={
            "body": {
                "center": [0, 0, 0, 0, 0, 0],
                "camera": [75.115, 0, 166.969, -69.28203230275508, 69.28203230275508, -69.2820323027551],
                "place":  [75.115 + 75, 0, 166.969, 0, 0, 0],
                "top":    [0, 0, 185, 0, 0, 0],
                "hole_0": [ 25,  25, 0, 0, 0, 0],
                "hole_1": [-25,  25, 0, 0, 0, 0],
                "hole_2": [-25, -25, 0, 0, 0, 0],
                "hole_3": [ 25, -25, 0, 0, 0, 0],
            },
        },
        collision_box={
            "body": [
                # [x,y,z,a,b,c], [lx,ly,lz]
                {"pose": [4 + (81.115 / 4), 0, 185 / 2, 0, 0, 0], "scale": [113.615, 65, 185]},
            ],
        },
        # ── device link ──────────────────────────────────────────────
        port="",          # scanner serial port; empty → no bus claim
        baud=9600,
        beep=False,       # scanner beeps on a good read (set once at connect)
        simulation=True,
        # ``critical`` controls whether a non-sim, unreachable transition
        # pauses the runtime. A barcode read is usually advisory (you can
        # rescan), so default False; set ``critical: true`` where a scan
        # is mandatory to proceed.
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

        # slot — you present the tube to the reader at the "place" anchor
        self.slot = {"body": ["place"]}

        # ── Device link ──
        # Authored sim intent — failures must NOT flip it (same rule as
        # Core / the meter / the scale). An unreachable real scanner is a
        # fault we surface, not a reason to silently switch to sim.
        self._simulation_mode = bool(prm["simulation"])
        self._port = prm["port"] or ""
        self._baud = int(prm["baud"])
        self._beep = bool(prm["beep"])
        self._critical = bool(prm["critical"])

        # The one sim/real branch — the station hides it from recipes.
        self.reader = DS457Station(
            port=self._port,
            baud=self._baud,
            beep=self._beep,
            simulation=self._simulation_mode,
            label=self.name,
        )

        # Always attempt the initial real connect, regardless of sim
        # (device-guide §16). Failure does NOT raise.
        if self._port:
            self.reader.recover()

        # Bus attachment — gated on ``port`` (mirrors the meter). Empty
        # port → no device claimed, no panel row; the component still
        # works (kinematic body + sim scans).
        self._attachment = None
        if self._port:
            def _make_recover() -> AutoRecover:
                return AutoRecover(
                    recover_fn=self.reader.recover,
                    set_status=self.reader._set_state,
                    log_label=self.reader.id,
                )

            try:
                self._attachment = attach_device(
                    self.reader,
                    kind=DS457Station.KIND,
                    sim=self._simulation_mode,
                    critical=self._critical,
                    meta={"port": self._port, "baud": self._baud},
                    recover_factory=_make_recover,
                )
            except Exception:
                log.exception("BarcodeReader[%s]: attach_device failed", self.name)

    # ── DeviceComponent contract ──────────────────────────────────────

    @property
    def device_ids(self) -> list[str]:
        """Device ids this component claims. Empty when ``port`` is unset
        (no bus presence). Mirrors the meter's port behaviour."""
        return [self.reader.id] if self._port else []

    def device_claim(self, device_id: str) -> str:
        """Project-level sim/real claim for ``device_id`` — drives the
        panel's SIM pill from one consistent source."""
        if device_id == self.reader.id:
            return "sim" if self._simulation_mode else "real"
        return "real"

    # ── Atomic scanning API (component-level — recipes call these) ─────
    # Sim-agnostic by construction. Returns ``Scan`` on success, ``None``
    # when disconnected and not in sim. Never raises on transient
    # failures — the station transitions state to ``down`` so AutoRecover
    # takes over.

    def is_connected(self) -> bool:
        return self.reader.is_connected()

    # ``sim_return`` (device-guide §17) — explicit sim injection, passed
    # straight to the station. Its default IS the canned sim value, in the
    # signature, shaped like the real return (a ``Scan``). Real mode
    # ignores it.

    def detect(self, allowed=ALL_SYMBOLOGIES, timeout: float = 10.0,
               sim_return=Scan(status="ok", data="SIM-0000000000", symbology="code128")):
        """Trigger one on-demand detect and return it (``Scan`` or None).
        The scanner stays quiet until this is called. ``allowed`` defaults
        to every symbology; pass a subset (e.g. ``["code39", "qrcode"]``)
        to ignore other types. In sim, returns ``sim_return``."""
        return self.reader.detect(allowed=allowed, timeout=timeout, sim_return=sim_return)

    def code(self, allowed=ALL_SYMBOLOGIES, timeout: float = 10.0,
             sim_return: str = "SIM-0000000000"):
        """Convenience: trigger a detect and return just the decoded
        barcode string (or None on timeout/nak/disconnect). In sim,
        returns ``sim_return`` verbatim — matches this method's return
        type, so no ``Scan`` needed when you only want the text."""
        if self._simulation_mode:
            return sim_return
        r = self.detect(allowed=allowed, timeout=timeout)
        return r.data if (r is not None and r.ok) else None

    # ── Operator actions (component-guide §8) ─────────────────────────


    def simulation(self, on: bool = True):
        """Live sim/real flip — mirrors ``Core.simulation`` /
        ``ScaleSpx222.simulation`` (device-guide §16 parity rule). Flips
        the authored intent, republishes ``info.sim``, and suspends/
        re-arms AutoRecover. The serial connection stays open."""
        new_sim = bool(on)
        if new_sim == self._simulation_mode:
            return
        self._simulation_mode = new_sim
        self.reader.set_simulation(new_sim)
        if self._attachment is not None:
            self._attachment.set_sim(new_sim)
        print(f"{'🔵' if new_sim else '🟡'} {self.name} simulation "
              f"{'enabled' if new_sim else 'disabled'}")

    def operator_actions(self) -> list[dict]:
        # detect returns a Scan; the runtime stringifies the result
        # centrally (no per-method *_once str wrapper needed).
        return [
            {"label": "Detect", "method": "detect"},
        ]

    # ── Teardown ──────────────────────────────────────────────────────

    def close(self):
        """Release the bus attachment + close the port. Idempotent."""
        try:
            if self._attachment is not None:
                self._attachment.close()
        except Exception:
            log.exception("BarcodeReader[%s]: attachment close raised", self.name)
        finally:
            self._attachment = None
            self.reader.release()
