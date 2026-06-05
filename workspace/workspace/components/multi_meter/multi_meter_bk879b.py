"""Multimeter / LCR-meter component.

Stationary bench instrument — no robot motion, no tool changer, no
flange attachment. The component owns the device-bus attachment and
exposes a sim-agnostic measurement API; recipes / actions / the
operator UI call the same methods regardless of the underlying sim
or real driver.

Wire it up in ``scene/base.j2`` like any other component:

    multi_meter_1:
      type: "multi_meter_bk879b"
      simulation: false        # port omitted → auto-detect USB port
      # port: "/dev/ttyUSB0"   # or pin a specific COM port

The model-specific driver (BK Precision 879B today, future Keithley /
Fluke / etc. tomorrow) lives behind a :class:`BK879BStation`-shaped
station that implements the Device protocol from
``workspace/devices/component_contract.py``. Adding a new meter model
is "swap the station class", not "rewrite the component".

See ``docs/device-guide.md`` for the device contract this component
follows, and ``docs/component-guide.md`` §7 for the rule that puts
atomic device operations on the component (here) instead of the
recipe.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Optional

from mergedeep import merge
from dorna2 import Solid

from workspace.components.factory import register
from workspace.components.multi_meter.bk879b_station import BK879BStation
from workspace.devices import AutoRecover, attach_device


log = logging.getLogger(__name__)


@register("multi_meter_bk879b")
class MultiMeterBk879b:
    DEFAULTS = dict(
        anchors={"body": {
            "center": [0, 0, 0, 0, 0, 0],
            "top":    [0, 0, 42, 0, 0, 0],
        }},
        collision_box={"body": [
            {"pose": [7.5, 0, 25, 0, 0, 0], "scale": [105, 190, 50]},
        ]},
        port=None,
        simulation=True,
        # ``critical`` controls whether a non-sim, non-reachable
        # transition pauses the runtime. Default to True — a meter
        # we can't read is usually a real fault worth pausing for.
        # Set ``critical: false`` in scene yaml on projects where
        # the meter is genuinely advisory.
        critical=True,
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault(
            "type",
            getattr(self.__class__, "_registered_type", cfg.get("type")),
        )

        self.name = name
        self.workspace = workspace
        self.type = prm["type"]

        # Kinematic assembly — single body solid with the configured
        # collision box. Multi-solid meters can extend this later by
        # adding entries to ``anchors`` / ``collision_box``.
        self.assembly = {
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                **({"collision_box": cb[k]} if (cb := prm.get("collision_box")) and k in cb else {}),
            )
            for k in prm["anchors"]
        }

        # Authored simulation intent — failures must NOT flip this
        # (same rule as Core). The operator either authored sim or
        # didn't; an unreachable real meter is a fault we surface, not
        # a reason to silently switch to sim.
        self._simulation_mode = bool(prm["simulation"])
        self._port = prm["port"]
        self._critical = bool(prm["critical"])

        # ── The one sim/real branch — component constructor, single
        #    place. Station handles the unified API; recipes and
        #    operator buttons never have to think about sim/real. ──
        self.meter = BK879BStation(
            port=self._port,
            simulation=self._simulation_mode,
            label=self.name,
        )

        # Always attempt the initial real connection, regardless of
        # sim — device-guide §16: bus state reflects the publisher's
        # truth (hardware reachability), sim only controls whether
        # recipes use canned data and whether auto-pause is gated.
        # In sim mode, AutoRecover is suspended via
        # ``attachment.set_sim``, so a failed connect just sits red
        # without retry storms. Failure does NOT raise.
        if self._port:
            self.meter.recover()

        # Bus attachment — gated on ``port`` being set, exactly the
        # same rule Core uses for ``ip``. Empty port means "no device
        # claimed here": the component still works (sim returns canned
        # data, real auto-detects the meter), it just doesn't take an
        # id on the bus and doesn't render a Devices-panel row. Set
        # ``port: "/dev/ttyUSB0"`` (or any non-empty string) to claim
        # a device id and show up in the panel.
        self._attachment = None

        if self._port:
            def _make_recover() -> AutoRecover:
                return AutoRecover(
                    recover_fn=self.meter.recover,
                    set_status=self.meter._set_state,
                    log_label=self.meter.id,
                )

            try:
                self._attachment = attach_device(
                    self.meter,
                    kind=BK879BStation.KIND,
                    sim=self._simulation_mode,
                    critical=self._critical,
                    meta={"port": self._port},
                    recover_factory=_make_recover,
                )
            except Exception:
                # Adapter wiring must NOT take down the component —
                # the meter is still usable for direct measurements.
                log.exception("MultiMeterBk879b[%s]: attach_device failed", self.name)

    # ── DeviceComponent contract (workspace.devices.DeviceComponent) ───

    @property
    def device_ids(self) -> list[str]:
        """Device ids this component claims. See docs/device-guide.md §9.

        Empty when ``port`` is unset — the meter is then in
        "no device claimed" mode (component still works, just no bus
        presence). Mirrors Core's behaviour: empty ``ip`` → no
        ``device_ids`` entry → no Devices-panel row.
        """
        return [self.meter.id] if self._port else []

    def device_claim(self, device_id: str) -> str:
        """Project-level sim/real claim for ``device_id``. Mirrors
        the bus's own ``sim`` flag, but exposed at the workspace
        layer so the panel can render the SIM pill from one
        consistent source (see Inspection.device_claim for the same
        pattern)."""
        if device_id == self.meter.id:
            return "sim" if self._simulation_mode else "real"
        return "real"

    # ── Atomic measurement API (component-level — recipes call these) ──
    # These are sim-agnostic by construction: the station handles the
    # branch internally. Returns ``Measurement`` on success, ``None``
    # when the meter is disconnected and not in sim. Never raises on
    # transient failures — the station transitions state to ``down``
    # so AutoRecover takes over.

    def is_connected(self) -> bool:
        return self.meter.is_connected()

    def read_capacitance(self, mode: str = "Cp", frequency: int = 1000):
        return self.meter.read_capacitance(mode=mode, frequency=frequency)

    def read_inductance(self, mode: str = "Ls", frequency: int = 1000):
        return self.meter.read_inductance(mode=mode, frequency=frequency)

    def read_resistance(self, frequency: int = 1000):
        return self.meter.read_resistance(frequency=frequency)

    def read_impedance(self, frequency: int = 1000):
        return self.meter.read_impedance(frequency=frequency)

    # ── Operator-action contract (component-guide §8) ─────────────────
    # Operator-facing buttons in the Operator Controls panel. Wired
    # so the operator can sanity-check a meter mid-pause without
    # touching the workflow.

    def read_once_capacitance(self):
        m = self.read_capacitance()
        return None if m is None else m.raw

    def read_once_inductance(self):
        m = self.read_inductance()
        return None if m is None else m.raw

    def read_once_resistance(self):
        m = self.read_resistance()
        return None if m is None else m.raw

    def reconnect(self):
        """Re-run the connection sequence (same code path AutoRecover
        uses). Returns True on success, False otherwise. Surfaces as
        a clean toast in the GUI either way."""
        return self.meter.recover()

    def release_meter(self):
        """Close the serial port and mark the meter down. Useful when
        the operator wants to unplug it physically without a
        connection-lost alarm spraying the panel."""
        self.meter.release()

    def simulation(self, on: bool = True):
        """Live sim/real flip — mirrors ``Core.simulation``. See
        ``docs/device-guide.md`` §16 for the parity rule that requires
        every workspace-owned device component to expose this.

        Sim is orthogonal to the connection: this method flips the
        authored intent, republishes ``info.sim`` for the panel, and
        suspends/re-arms AutoRecover via the attachment. The serial
        connection (if open) stays open across the flip — bus state
        keeps reflecting hardware reachability either way.
        """
        new_sim = bool(on)
        if new_sim == self._simulation_mode:
            return
        self._simulation_mode = new_sim
        self.meter.set_simulation(new_sim)
        if self._attachment is not None:
            self._attachment.set_sim(new_sim)
        print(
            f"{'🔵' if new_sim else '🟡'} {self.name} simulation "
            f"{'enabled' if new_sim else 'disabled'}"
        )

    def operator_actions(self) -> list[dict]:
        return [
            {"label": "Read C (once)",  "method": "read_once_capacitance"},
            {"label": "Read L (once)",  "method": "read_once_inductance"},
            {"label": "Read R (once)",  "method": "read_once_resistance"},
            {"label": "Reconnect",      "method": "reconnect"},
            {"label": "Release",        "method": "release_meter"},
        ]

    # ── Teardown ──────────────────────────────────────────────────────

    def close(self):
        """Release the bus attachment + close the serial port. Idempotent."""
        try:
            if self._attachment is not None:
                self._attachment.close()
        except Exception:
            log.exception("MultiMeterBk879b[%s]: attachment close raised", self.name)
        finally:
            self._attachment = None
            self.meter.release()
