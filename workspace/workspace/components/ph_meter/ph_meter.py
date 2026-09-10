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
from workspace.components.ph_meter.ezo_ph_driver import Reading, Slope
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
        # A freshly dipped electrode drifts for tens of seconds, so ``ph()``
        # waits for a settled reading by default. These tune what "settled"
        # means; every one is overridable per install from scene yaml, and
        # per call from the method arguments.
        settle=True,               # ph() settles unless a call says otherwise
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

        # Settle tuning — the defaults every read/ph call falls back to.
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
    # Sim-agnostic by construction: the station branches internally. Returns
    # ``Reading`` / ``Slope`` / float on success, ``None`` when disconnected
    # and not in sim. Never raises on transient failures — the station
    # transitions state to ``down`` so AutoRecover takes over.
    #
    # ``sim_return`` (device-guide §17) — explicit sim injection, passed
    # straight to the station. Its default IS the canned sim value, inline in
    # the signature and shaped like the real return. Pass your own to inject
    # a different reading; real mode ignores it.

    def is_connected(self) -> bool:
        return self.probe.is_connected()

    def read(self, sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Single reading (``Reading`` or None)."""
        return self.probe.read(sim_return=sim_return)

    def read_at_temperature(self, celsius: float,
                            sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Compensate for the sample temperature and read in one call."""
        return self.probe.read_at_temperature(celsius, sim_return=sim_return)

    def read_stable(self, n: int = None, tolerance: float = None,
                    max_readings: int = None,
                    sim_return=Reading(status="ok", ph=7.000, raw="sim")):
        """Block until the electrode settles (``Reading`` or None).

        Unset arguments fall back to this component's configured tuning
        (``settle_n`` / ``settle_tolerance`` / ``settle_max_readings``), so a
        bench that needs a different definition of "settled" sets it once in
        scene yaml instead of at every call site.
        """
        return self.probe.read_stable(
            n=self._settle_n if n is None else n,
            tolerance=self._settle_tolerance if tolerance is None else tolerance,
            max_readings=self._settle_max_readings if max_readings is None else max_readings,
            sim_return=sim_return,
        )

    def ph(self, stable: bool = None, sim_return: float = 7.000):
        """pH value (float or None).

        ``stable`` defaults to the component's ``settle`` setting (on unless
        scene yaml turns it off), so recipes just call ``ph()``. Pass
        ``stable=False`` for an instantaneous reading.

        The settled path goes through :meth:`read_stable` so the component's
        settle tuning applies — ``station.ph()`` would use the station's own
        defaults instead. sim still branches exactly once, in the station.
        """
        stable = self._settle if stable is None else bool(stable)
        if not stable:
            return self.probe.ph(stable=False, sim_return=sim_return)
        r = self.read_stable(sim_return=Reading(status="ok", ph=sim_return, raw="sim"))
        return None if r is None or not r.ok else r.ph

    def slope(self, sim_return=Slope(acid_percent=99.5, base_percent=99.2, offset_mv=0.0, raw="sim")):
        """Probe health vs an ideal electrode (``Slope`` or None). Healthy is
        95-105% both sides with offset within +/-30 mV. A bad signal path —
        crushed connector, RF adapter, long analog run — degrades these the
        same way a worn electrode does, so check the wiring before condemning
        the probe."""
        return self.probe.slope(sim_return=sim_return)

    def calibration_points(self, sim_return: int = 3):
        """Stored calibration points, 0–3 (int or None). Calibration lives in
        the EZO's EEPROM, not the electrode, so it survives power cycles —
        and it does NOT follow the probe if you swap electrodes."""
        return self.probe.calibration_points(sim_return=sim_return)

    def calibrate(self, value: float, sim_return: bool = True):
        """Calibrate against the buffer on the probe — the point is picked
        from ``value`` (mid must come first; the chip enforces it). Returns
        True/False, never raises."""
        return self.probe.calibrate(value, sim_return=sim_return)

    def calibrate_clear(self, sim_return: bool = True):
        """Wipe all stored calibration points."""
        return self.probe.calibrate_clear(sim_return=sim_return)

    def set_temperature_compensation(self, celsius: float, sim_return: bool = True):
        """Store the sample temperature on the chip (persists). The EZO has no
        temperature sensor — this is the value it compensates with, not a
        measurement."""
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
    # Groups are paired deliberately: consecutive entries sharing a ``group``
    # render as ONE row of equal-width buttons, so two per group keeps every
    # label readable on a sidebar-width tablet.

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
            {"label": "Read pH",      "method": "read",               "icon": "activity", "group": "read"},
            {"label": "Read Settled", "method": "read_stable",        "icon": "activity", "group": "read"},
            {"label": "Health",       "method": "slope",              "icon": "eye",      "group": "cal"},
            {"label": "Cal Points",   "method": "calibration_points", "icon": "eye",      "group": "cal"},
            {"label": "Reconnect",    "method": "reconnect",          "icon": "rotate",   "group": "conn"},
            {"label": "Release",      "method": "release_probe",      "icon": "link-off", "group": "conn"},
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
