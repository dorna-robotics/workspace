"""pH-probe family base — the EZO device link every probe variant shares.

Same shape as ``Inspection`` for the camera family: this class owns
everything the variants have in common, and each registered variant
(``ph_meter_atlas``, ``ph_meter_apera753``) contributes nothing but its
own geometry.

A pH probe here is a robot-mounted dip **tool** that is ALSO a
workspace-owned serial device. The kinematic side (tool changer,
anchors, collision box) comes from ``Gripper``; the device side follows
the standard workspace-owned stack (device-guide §10 shape A):

    ezo_ph_driver.AtlasPH             raw EZO UART driver
      -> ezo_ph_station.EzoPHStation  Device protocol + the one sim branch
        -> PhMeter (this class)       bus attachment + sim-agnostic atomic ops
          -> PhMeterAtlas / PhMeterApera753   geometry only

The electrode is a plain BNC glass probe, so the same EZO-pH circuit
reads any of them — driver and station are electrode-agnostic, and only
the mechanical envelope differs per variant.

Scene yaml (identical for every variant; only ``type`` changes):

    ph_meter_1:
      type: "ph_meter_apera753"
      has_tool_changer: true
      simulation: true
      port: ""                # "" -> no bus claim; set a /dev/... to claim
      ...attach...

Empty ``port`` means "no device claimed": the component still works (sim
ops return canned readings), it just takes no bus id and renders no
Devices-panel row. Set a port to claim the circuit — the dot then shows
hardware truth (sim is orthogonal, device-guide §16). Use the stable
``/dev/serial/by-id/...`` symlink, not ``/dev/ttyUSB0`` (device-guide
§9): the tty number moves when other adapters come and go, and a run
that opens the wrong device succeeds and returns garbage.

Keep the analog run SHORT. A glass electrode is a ~250 MΩ source;
leakage across a long BNC run shunts signal (reads as low slope), picks
up noise (wandering readings), and every extra connector is another
failure point. For a robot-mounted probe, mount the EZO circuit at the
arm with the shortest possible BNC lead and send the UART back over the
distance instead.

Variants merge the layers themselves so the chain stays readable where
the geometry is declared::

    prm = deepcopy(Gripper.DEFAULTS)   # base    - kinematics
    merge(prm, PhMeter.DEFAULTS)       # family  - device link + passive IO
    merge(prm, self.DEFAULTS)          # self    - geometry
    merge(prm, cfg)                    # cfg
    merge(prm, kwargs)                 # kwargs
"""

from __future__ import annotations

import logging

from workspace.components.gripper.gripper import Gripper
from workspace.components.ph_meter.ezo_ph_driver import Reading
from workspace.components.ph_meter.ezo_ph_station import EzoPHStation
from workspace.devices import AutoRecover, attach_device


log = logging.getLogger(__name__)


class PhMeter(Gripper):
    """Not registered — instantiate a variant, never this class."""

    DEFAULTS = dict(
        #cfg
        has_tool_changer = False,
        # Passive dip tool — no IO of its own, so enable/disable are no-ops.
        output_enable=[[None, None, 0], [None, None, 0]],
        output_disable=[[None, None, 0], [None, None, 0]],
        # Wrist roll (j5, degrees) pinned during immerse/retract — the same
        # contract the needle gripper declares: a long glass probe must not
        # roll while it is inside a tube. null frees the wrist. immerse /
        # retract read this off the mounted tool (recipe.py _tool_lock_j5).
        lock_j5=None,
        # ── device link ──────────────────────────────────────────────
        port="",           # "" → no bus claim; non-empty → claim + panel row
        baud=9600,         # EZO factory default
        timeout=2.0,       # s — per-command read deadline
        simulation=True,
        # ── reading / settling ───────────────────────────────────────
        # A freshly dipped electrode drifts for tens of seconds, so ``read``
        # waits for a settled reading by default. These tune what "settled"
        # means; every one is overridable per install from scene yaml, and
        # per call from the method arguments.
        settle=True,               # read() settles unless a call says otherwise
        settle_n=3,                # consecutive readings that must agree
        settle_tolerance=0.08,     # ...within this many pH units
        settle_max_readings=20,    # give-up budget (~0.9 s per reading)
        # ``critical`` controls whether a non-sim, unreachable transition
        # pauses the runtime. A probe we can't read mid-run is a real fault
        # worth pausing for; set ``critical: false`` in scene yaml where the
        # pH value is genuinely advisory.
        critical=True,
    )

    def __init__(self, name: str, workspace, type=None, **kwargs):
        # The variant has already merged Gripper → PhMeter → self → cfg →
        # kwargs, so ``kwargs`` here IS the fully resolved prm.
        prm = kwargs

        super().__init__(
            name=name,
            workspace=workspace,
            type=type,
            **prm
        )

        # Wrist-roll pin for immerse/retract (None = unconstrained).
        v = prm.get("lock_j5")
        self.lock_j5 = None if v is None else float(v)

        # Authored simulation intent — failures must NOT flip this (same
        # rule as Core / the meter / the pump). An unreachable real probe is
        # a fault we surface, not a reason to silently switch to sim.
        self._simulation_mode = bool(prm["simulation"])
        self._port = prm["port"] or ""
        self._critical = bool(prm["critical"])

        # Settle tuning — the defaults every read() call falls back to.
        self._settle = bool(prm["settle"])
        self._settle_n = int(prm["settle_n"])
        self._settle_tolerance = float(prm["settle_tolerance"])
        self._settle_max_readings = int(prm["settle_max_readings"])

        # ── The one sim/real branch — the station handles the unified API;
        #    recipes and operator buttons never think about it. ──
        self.probe = EzoPHStation(
            port=self._port,
            baud=int(prm["baud"]),
            timeout=float(prm["timeout"]),
            simulation=self._simulation_mode,
            label=self.name,
        )

        # Always attempt the initial real connection, regardless of sim — bus
        # state reflects hardware truth (device-guide §16). In sim AutoRecover
        # is suspended via ``attachment.set_sim``, so a failed connect sits
        # red without retry storms. Failure does NOT raise.
        if self._port:
            self.probe.recover()

        # Bus attachment — gated on ``port``, same rule as Core's ip and the
        # multimeter's / pipettor's port.
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
                # Adapter wiring must NOT take down the component — the probe
                # is still usable for direct reads.
                log.exception("%s[%s]: attach_device failed",
                              self.__class__.__name__, self.name)

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
    # This device does two things: it READS and it CALIBRATES. That is the
    # whole API. Sim-agnostic by construction — the station branches
    # internally; returns ``Reading`` / bool on success, ``None``/False when
    # disconnected and not in sim. Never raises on transient failures — the
    # station transitions state to ``down`` so AutoRecover takes over.
    #
    # ``sim_return`` (device-guide §17) — explicit sim injection, passed
    # straight to the station. Its default IS the canned sim value, inline in
    # the signature and shaped like the real return. Pass your own to inject
    # a different reading; real mode ignores it.

    def is_connected(self) -> bool:
        return self.probe.is_connected()

    def read(self, settle: bool = None, n: int = None, tolerance: float = None,
             max_readings: int = None,
             sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """THE reading call (``Reading`` or None).

        ``settle`` unset -> the component's ``settle`` setting (on unless
        scene yaml turns it off): block until ``n`` consecutive readings
        agree within ``tolerance`` pH units, giving up after
        ``max_readings``. Each unset tuning argument falls back to the
        scene-yaml values (``settle_n`` / ``settle_tolerance`` /
        ``settle_max_readings``). ``settle=False`` -> one instantaneous
        reading.
        """
        settle = self._settle if settle is None else bool(settle)
        if not settle:
            return self.probe.read(sim_return=sim_return)
        return self.probe.read_stable(
            n=self._settle_n if n is None else n,
            tolerance=self._settle_tolerance if tolerance is None else tolerance,
            max_readings=self._settle_max_readings if max_readings is None else max_readings,
            sim_return=sim_return,
        )

    def calibrate(self, value: float, sim_return: bool = True):
        """Calibrate against the buffer the probe is sitting in — the point
        (low/mid/high) is picked from ``value``. Mid (~pH 7) must come
        first and WIPES the other points; the chip enforces it. Returns
        True/False, never raises."""
        return self.probe.calibrate(value, sim_return=sim_return)

    # ── Operator actions (component-guide §8) ─────────────────────────
    # Buttons in the Operator Controls panel — every method here takes no
    # required args and returns something the runtime can stringify.
    # Read uses the default settle tuning; the three Cal buttons pin the
    # standard buffer values so no typing is needed at the bench.
    # Consecutive entries sharing a ``group`` render as ONE row.

    def _cal_result(self, which: str, ok: bool) -> str:
        """Confirmation string for a Cal button: the point that was set,
        plus the chip's CURRENT slopes so the operator sees them move as
        points are added the first time or overridden later."""
        if not ok:
            return f"{which} calibration FAILED"
        s = self.probe.slope()
        if s is None:
            return f"{which} calibrated (slopes unavailable)"
        return (f"{which} calibrated — acid {s.acid_percent:.1f}% / "
                f"base {s.base_percent:.1f}%, offset {s.offset_mv:+.1f} mV")

    def calibrate_4(self):
        """Operator button — recalibrate the LOW point in pH 4.00 buffer."""
        return self._cal_result("low", self.calibrate(4.00))

    def calibrate_7(self):
        """Operator button — recalibrate the MID point in pH 7.00 buffer.
        Do this one FIRST: the chip wipes low/high when mid is set."""
        return self._cal_result("mid", self.calibrate(7.00))

    def calibrate_10(self):
        """Operator button — recalibrate the HIGH point in pH 10.00 buffer."""
        return self._cal_result("high", self.calibrate(10.00))

    def reconnect(self):
        """Re-run the connection sequence (same path AutoRecover uses)."""
        return self.probe.recover()

    def release_probe(self):
        """Close the serial port and mark the probe down — lets the operator
        unplug it without a connection-lost alarm."""
        self.probe.release()

    def simulation(self, on: bool = True):
        """Live sim/real flip — device-guide §16 parity rule. Flips the
        authored intent, republishes ``info.sim`` for the SIM pill, and
        suspends/re-arms AutoRecover. The serial connection (if open) stays
        open; bus state keeps reflecting hardware truth."""
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
            {"label": "Read",      "method": "read",         "icon": "activity", "group": "read"},
            {"label": "Cal 4",     "method": "calibrate_4",  "icon": "rotate",   "group": "cal"},
            {"label": "Cal 7",     "method": "calibrate_7",  "icon": "rotate",   "group": "cal"},
            {"label": "Cal 10",    "method": "calibrate_10", "icon": "rotate",   "group": "cal"},
            {"label": "Reconnect", "method": "reconnect",    "icon": "rotate",   "group": "conn"},
            {"label": "Release",   "method": "release_probe","icon": "link-off", "group": "conn"},
        ]

    # ── Teardown ──────────────────────────────────────────────────────

    def close(self):
        """Release the bus attachment + close the serial port. Idempotent."""
        try:
            if self._attachment is not None:
                self._attachment.close()
        except Exception:
            log.exception("%s[%s]: attachment close raised",
                          self.__class__.__name__, self.name)
        finally:
            self._attachment = None
            self.probe.release()
