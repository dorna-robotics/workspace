"""OHAUS Scout SPX222 balance — device component.

Stationary bench instrument: a 3D/kinematic body PLUS a device-bus
attachment to the real balance over TCP. The component owns the
device link (a :class:`SPX222Station`) and exposes a sim-agnostic
weighing API; recipes / actions / the operator UI call the same
methods regardless of sim or real.

Same shape as ``MultiMeterBk879b`` (the canonical workspace-owned
device). The difference is the SPX222 talks over TCP, so the bus
identity is keyed on ``ip`` (like Core) rather than a serial port.

Wire it up in a scene layout like any other component, plus the
device fields:

    scale_spx222_1:
      type: "scale_spx222"
      ip: "192.168.254.132"   # balance address; "" → no bus claim
      port: 9761              # MT-SICS TCP port
      simulation: false
      critical: false
      attach:
        parent_name: "fixture_plate_1"
        ...

The type "scale_spx222" maps to static/CAD/scale_spx222.glb.

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
from workspace.components.scale.spx222_station import SPX222Station
from workspace.components.scale.spx222_driver import Reading
from workspace.devices import AutoRecover, LivenessProbe, attach_device


log = logging.getLogger(__name__)


# NOTE: the anchors and collision_box below are PLACEHOLDERS copied from the
# generic ``scale`` component. Replace them with the real SPX222 values
# measured off the CAD model.
@register("scale_spx222")
class ScaleSpx222:

    DEFAULTS = dict(
        anchors={
            "body": {
                "center": [0, 0, 0, 0, 0, 0],
                "top":    [0, 0, 53.34+3.5, 0, 0, 0],     # placeholder — set from CAD
                "place":  [42.815, 0, 53.34+3.5, 0, 0, 0],      # placeholder — set from CAD
                "hole_0": [ 50,  75, 0, 0, 0, 0],   # placeholder
                "hole_1": [-50,  75, 0, 0, 0, 0],   # placeholder
                "hole_2": [-50, -75, 0, 0, 0, 0],   # placeholder
                "hole_3": [ 50, -75, 0, 0, 0, 0],   # placeholder
            },
        },
        collision_box={
            "body": [
                # placeholder — [x,y,z,a,b,c], [lx,ly,lz]; set from CAD
                {"pose": [0.0, 0.678, (53.34+3.5)/2, 0.0, 0.0, 0.0], "scale": [233.789, 213.458, 53.34+3.5]},
            ],
        },
        # ── device link ──────────────────────────────────────────────
        ip="",            # balance IP; empty → no bus claim (component still works)
        port=9761,        # MT-SICS TCP port
        simulation=True,
        # ``critical`` controls whether a non-sim, unreachable transition
        # pauses the runtime. A scale we can't read is often advisory, so
        # default True (set ``critical: true`` where a weight is mandatory).
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

        # slot — you place items on the pan
        self.slot = {"body": ["place"]}

        # ── Device link ──
        # Authored sim intent — failures must NOT flip it (same rule as
        # Core / the meter). An unreachable real balance is a fault we
        # surface, not a reason to silently switch to sim.
        self._simulation_mode = bool(prm["simulation"])
        self._ip = prm["ip"] or ""
        self._port = int(prm["port"])
        self._critical = bool(prm["critical"])

        # The one sim/real branch — the station hides it from recipes.
        self.scale = SPX222Station(
            ip=self._ip,
            port=self._port,
            simulation=self._simulation_mode,
            label=self.name,
        )

        # Always attempt the initial real connect, regardless of sim
        # (device-guide §16). Failure does NOT raise.
        if self._ip:
            self.scale.recover()

        # Bus attachment — gated on ``ip`` (mirrors Core). Empty ip →
        # no device claimed, no panel row; the component still works.
        self._attachment = None
        if self._ip:
            def _make_recover() -> AutoRecover:
                return AutoRecover(
                    recover_fn=self.scale.recover,
                    set_status=self.scale._set_state,
                    log_label=self.scale.id,
                )

            try:
                self._attachment = attach_device(
                    self.scale,
                    kind=SPX222Station.KIND,
                    sim=self._simulation_mode,
                    critical=self._critical,
                    meta={"ip": self._ip, "port": self._port},
                    recover_factory=_make_recover,
                )
            except Exception:
                log.exception("ScaleSpx222[%s]: attach_device failed", self.name)

        # ── Liveness watchdog ──
        # AutoRecover is reactive (device-guide §5: "not a watchdog").
        # Without a heartbeat, a mid-run cable pull is only noticed on the
        # next blocking read — up to the stable-weigh timeout (~10 s) of
        # "green but dead". This probe pings the balance (MT-SICS I2, short
        # timeout) every second; the moment a ping fails it flips the bus
        # ``down`` (red dot in ~1-2 s) and nudges AutoRecover to reconnect.
        # Started only for a real, bus-attached device — sim has nothing to
        # watch; suspended/re-armed in ``simulation()`` alongside recovery.
        self._liveness = None
        if self._ip and not self._simulation_mode and self._attachment is not None:
            self._liveness = LivenessProbe(
                probe_fn=self.scale.ping,          # real I2 round-trip, not socket-present
                on_down=lambda msg: self.scale._set_state("down", msg),
                recover=self._attachment.recover,
                is_ok_fn=lambda: self.scale.state == "ok",
                interval=1.0,
                down_msg="unreachable (liveness probe)",
                log_label=self.scale.id,
            )
            self._liveness.start()

    # ── DeviceComponent contract ──────────────────────────────────────

    @property
    def device_ids(self) -> list[str]:
        """Device ids this component claims. Empty when ``ip`` is unset
        (no bus presence). Mirrors Core's ip behaviour."""
        return [self.scale.id] if self._ip else []

    def device_claim(self, device_id: str) -> str:
        """Project-level sim/real claim for ``device_id`` — drives the
        panel's SIM pill from one consistent source."""
        if device_id == self.scale.id:
            return "sim" if self._simulation_mode else "real"
        return "real"

    # ── Atomic weighing API (component-level — recipes call these) ─────
    # Sim-agnostic by construction. Returns ``Reading`` / float on
    # success, ``None`` when disconnected and not in sim. Never raises on
    # transient failures — the station transitions state to ``down`` so
    # AutoRecover takes over.

    def is_connected(self) -> bool:
        return self.scale.is_connected()

    # ``sim_return`` (device-guide §17) — explicit sim injection, passed
    # straight to the station. Its default IS the canned sim value, in the
    # signature, shaped like the real return: a ``Reading`` for weigh /
    # weigh_stable, a ``float`` for weight. Real mode ignores it.

    def weigh(self, sim_return=Reading(status="stable", weight=12.345, unit="g", raw="sim")):
        """Instantaneous reading (``Reading`` or None)."""
        return self.scale.weigh(sim_return=sim_return)

    def weigh_stable(self, timeout: float = 10.0,
                     sim_return=Reading(status="stable", weight=12.345, unit="g", raw="sim")):
        """Block for a settled reading (``Reading`` or None)."""
        return self.scale.weigh_stable(timeout=timeout, sim_return=sim_return)

    def weight(self, stable: bool = True, timeout: float = 10.0, sim_return: float = 12.345):
        """Weight in grams (float or None). ``stable=True`` waits for a
        settled reading (``weigh_stable``); ``stable=False`` takes an
        instantaneous one (``weigh``). In sim, returns ``sim_return``."""
        return self.scale.weight(stable=stable, timeout=timeout, sim_return=sim_return)

    # ── Operator actions (component-guide §8) ─────────────────────────

    def weigh_once(self):
        """Operator button — one settled weigh; returns a printable str."""
        r = self.scale.weigh_stable()
        return None if r is None else str(r)

    def reconnect(self):
        """Re-run the connection sequence (AutoRecover's path)."""
        return self.scale.recover()

    def release_scale(self):
        """Close the socket + mark the scale down (clean unplug)."""
        self.scale.release()

    def simulation(self, on: bool = True):
        """Live sim/real flip — mirrors ``Core.simulation`` /
        ``MultiMeterBk879b.simulation`` (device-guide §16 parity rule).
        Flips the authored intent, republishes ``info.sim``, and
        suspends/re-arms AutoRecover + the liveness watchdog. The TCP
        connection stays open."""
        new_sim = bool(on)
        if new_sim == self._simulation_mode:
            return
        self._simulation_mode = new_sim
        self.scale.set_simulation(new_sim)
        if self._attachment is not None:
            self._attachment.set_sim(new_sim)

        # Liveness watchdog tracks sim alongside recovery: stop it going
        # into sim (nothing to watch); re-create it going back to real so
        # it binds to the FRESH AutoRecover the attachment just re-armed
        # (the old recover reference is now stale).
        if self._liveness is not None:
            try:
                self._liveness.stop()
            except Exception:
                log.exception("ScaleSpx222[%s]: liveness stop raised", self.name)
            self._liveness = None
        if not new_sim and self._ip and self._attachment is not None:
            self._liveness = LivenessProbe(
                probe_fn=self.scale.ping,
                on_down=lambda msg: self.scale._set_state("down", msg),
                recover=self._attachment.recover,
                is_ok_fn=lambda: self.scale.state == "ok",
                interval=1.0,
                down_msg="unreachable (liveness probe)",
                log_label=self.scale.id,
            )
            self._liveness.start()

        print(f"{'🔵' if new_sim else '🟡'} {self.name} simulation "
              f"{'enabled' if new_sim else 'disabled'}")

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Weigh",     "method": "weigh_once"},
            {"label": "Reconnect", "method": "reconnect"},
            {"label": "Release",   "method": "release_scale"},
        ]

    # ── Teardown ──────────────────────────────────────────────────────

    def close(self):
        """Stop the liveness watchdog, release the bus attachment + close
        the socket. Idempotent."""
        try:
            if self._liveness is not None:
                self._liveness.stop()
        except Exception:
            log.exception("ScaleSpx222[%s]: liveness stop raised", self.name)
        finally:
            self._liveness = None
        try:
            if self._attachment is not None:
                self._attachment.close()
        except Exception:
            log.exception("ScaleSpx222[%s]: attachment close raised", self.name)
        finally:
            self._attachment = None
            self.scale.release()
