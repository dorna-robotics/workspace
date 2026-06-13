"""BK Precision 879B station — the thin wrapper that the workspace's
``MultiMeterBk879b`` component holds and that the device bus attaches.

Mirrors the ``VisionStation`` pattern (see
``workspace/components/inspection/vision_station.py``):

* One class, sim flag baked in at construction.
* Implements the **Device protocol** (``id``, ``state``, ``msg``,
  ``on_state_change``, ``recover``, ``release``) so
  ``workspace.devices.attach_device`` can publish bus state and wire
  AutoRecover for the real path.
* Exposes a **unified measurement API** (``is_connected``,
  ``read_capacitance`` / ``inductance`` / ``resistance`` /
  ``impedance``, ``read``) so the component and recipes don't have
  to branch on the sim flag.
* In real mode wraps ``BK879BDriver`` (the raw serial driver). In sim
  mode produces canned ``Measurement`` objects that match the real
  shape so downstream code can't tell the difference.

See ``docs/device-guide.md`` §10.5 for the architectural rule (one
sim/real branch per device, lives in the component / station, never
in recipes).
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from workspace.components.multi_meter.bk879b_driver import (
    BK879B as BK879BDriver,
    Measurement,
)


log = logging.getLogger(__name__)


# Canned per-function sim ``Measurement`` defaults — used as the
# in-signature ``sim_return`` default for each read method (device-guide
# §17). Arbitrary but plausible per function so recipes that pretty-print
# them don't render ``0``. Visible here and in each signature; no hidden
# helper. Pass your own ``Measurement`` to a read method to inject a
# different sim value.
SIM_CAPACITANCE = Measurement(primary=1.5e-9, primary_unit="F", secondary=0.001,
                              secondary_unit="", function="C", frequency="1000",
                              raw="sim,1.5e-9,0.001")            # 1.5 nF
SIM_INDUCTANCE  = Measurement(primary=1.0e-3, primary_unit="H", secondary=0.001,
                              secondary_unit="", function="L", frequency="1000",
                              raw="sim,1e-3,0.001")              # 1 mH
SIM_RESISTANCE  = Measurement(primary=1.0e3, primary_unit="Ω", secondary=0.0,
                              secondary_unit="", function="R", frequency="1000",
                              raw="sim,1e3,0.0")                 # 1 kΩ
SIM_IMPEDANCE   = Measurement(primary=1.0e3, primary_unit="Ω", secondary=0.0,
                              secondary_unit="", function="Z", frequency="1000",
                              raw="sim,1e3,0.0")                 # 1 kΩ


class BK879BStation:
    """Wraps the BK 879B serial driver in a Device-bus-shaped object.

    Constructor:
        port:        explicit serial port, or None to auto-detect on
                     ``connect()``. Stored as the natural id basis;
                     ``id`` uses ``"auto"`` until the driver resolves
                     the actual port.
        simulation:  True → no real driver is opened; measurement
                     methods return canned data; ``recover`` /
                     ``release`` are no-ops that always succeed.
        label:       human-friendly tag for logs / state messages.

    Lifecycle:
        Real path:
            ``connect()`` opens the serial port (auto-scan if port is
            None), runs the ``*IDN?`` handshake, sets ``state="ok"``.
            On failure ``state="down"`` with a diagnostic msg.
            ``recover()`` re-runs the same path. ``release()`` closes
            the serial port and drops the driver.
        Sim path:
            All four methods are no-ops that succeed.
    """

    KIND = "multimeter"

    def __init__(
        self,
        port: Optional[str] = None,
        simulation: bool = True,
        label: str = "",
    ):
        self.port = port
        self.simulation = bool(simulation)
        self.label = label or "multimeter"

        # Bus-visible state ALWAYS reflects real hardware reachability,
        # regardless of the sim flag (device-guide §16: "dot color
        # always reflects the publisher's truth"). Starts down; the
        # component calls ``recover()`` in __init__ to attempt the
        # initial connect and update state to the real outcome.
        self.state: str = "down"
        self.msg: str = "not connected"

        # Wired by ``attach_device`` — fires on every _set_state.
        self._listeners: list[Callable[[str, str], None]] = []

        # Real driver instance; None in sim, or in real mode until
        # connect() succeeds for the first time.
        self._driver: Optional[BK879BDriver] = None

    # ── Device protocol ────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """``multimeter:<basename(port)>`` per device-guide §9.

        The basename (e.g. ``ttyUSB0`` or ``usb-Silicon_Labs_..._0001``)
        is used instead of the raw path so the id stays slash-free —
        MQTT subscribers use ``device/+/info`` (single-level wildcard)
        and slashes in the id would push ``info`` to a depth the
        subscription can't match, hiding the publisher from the
        orchestrator.

        Sim does NOT change the id — sim is a separate axis surfaced
        via the SIM pill and ``info.sim`` on the bus. Keeping the id
        stable lets ``set_simulation()`` toggle live mid-run without
        breaking bus topic continuity.
        """
        return f"{self.KIND}:{os.path.basename(self.port)}"

    def on_state_change(self, cb: Callable[[str, str], None]) -> None:
        """Register a callback fired on every state transition.
        ``MQTTDeviceAdapter`` registers itself here so bus retained
        state stays in lockstep with reality."""
        self._listeners.append(cb)

    def _set_state(self, new_state: str, msg: str = "") -> None:
        """Mutate ``state`` + ``msg`` and notify listeners. No-op when
        nothing changed — keeps the bus from spamming retained
        republishes."""
        if new_state == self.state and msg == self.msg:
            return
        self.state, self.msg = new_state, msg
        for cb in list(self._listeners):
            try:
                cb(new_state, msg)
            except Exception:
                log.exception("BK879BStation[%s]: listener raised", self.label)

    def recover(self) -> bool:
        """Re-establish the serial link. ALWAYS attempts the real
        connect — sim flag does not change this (device-guide §16:
        bus state reflects hardware reachability regardless of sim).
        Rebuilds the driver, re-runs ``connect`` + handshake, and
        updates state to the real outcome. Called by AutoRecover on
        connection loss; AutoRecover is suspended in sim via
        ``attachment.set_sim`` so this only fires on real-mode drops
        or explicit operator action.

        Always fires a ``recovering → result`` transition so the
        bus/UI see an event even when the final state matches the
        previous one (otherwise ``_set_state`` would no-op on
        "down → down with same msg" and the panel would sit on the
        operator's RECOVER_FALLBACK_MS deadline). Matches
        :meth:`RobotStation.recover`.
        """
        self._set_state("recovering", "reconnecting")
        try:
            # Always rebuild the driver from scratch. After a USB unplug,
            # the old serial.Serial object still has ``is_open == True``
            # (pyserial doesn't know the kernel ripped the device away),
            # so ``connect()`` would short-circuit and the IDN handshake
            # would fail on the dead fd. Closing + recreating forces a
            # fresh open through the ``by-id`` symlink (which udev
            # re-resolves to whatever ``/dev/ttyUSB*`` the device landed
            # on this time).
            if self._driver is not None:
                try:
                    self._driver.close()
                except Exception:
                    pass
                self._driver = None
            self._driver = BK879BDriver(port=self.port)
            if not self._driver.connect():
                self._set_state("down", "connect failed")
                return False
            ok, _ = self._driver.check_connection()
            if not ok:
                self._set_state("down", "no IDN response")
                return False
            self._driver.initialize()
            self.port = self._driver.port  # auto-detected
            self._set_state("ok", "")
            return True
        except Exception as ex:
            self._set_state("down", f"connect failed: {type(ex).__name__}: {ex}")
            return False

    def release(self) -> None:
        """Hand the meter back: close the serial port + drop the
        driver. Does NOT branch on sim — release closes the real
        handle if one exists, regardless of authored intent."""
        drv, self._driver = self._driver, None
        if drv is not None:
            try:
                drv.close()
            except Exception:
                log.exception("BK879BStation[%s]: close raised", self.label)
        self._set_state("down", "released")

    def set_simulation(self, sim: bool) -> None:
        """Live sim/real flip — flag only. Mirrors
        ``RobotStation.set_simulation``; both follow the parity rule
        in device-guide §16.

        Sim is **orthogonal** to connection state: the bus state
        always reflects real hardware reachability (rule 2). This
        method only flips the authored-sim flag; the component
        republishes ``info.sim`` via ``attachment.set_sim`` and
        swaps recipe routing as needed. The serial connection (if
        open) stays open across the flip — close it explicitly with
        ``release`` if you also want to free the hardware.
        """
        self.simulation = bool(sim)

    # ── Unified measurement API ────────────────────────────────────────
    # Real path delegates to the driver, catching exceptions and
    # transitioning the state to "down" on read failures so the bus +
    # AutoRecover learn about it.
    #
    # ``sim_return`` (device-guide §17) — explicit sim injection. Its
    # default IS the canned per-function ``Measurement`` (above), written
    # in each method's signature. In sim the method returns ``sim_return``
    # verbatim; real mode ignores it. Pass your own ``Measurement`` to
    # inject a different reading.

    def is_connected(self) -> bool:
        if self.simulation:
            return True
        return self._driver is not None and self._driver.is_connected()

    def _safe_read(self, fn_name: str, frequency: int,
                   sim_return: Measurement, **kw) -> Optional[Measurement]:
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            return getattr(self._driver, fn_name)(frequency=frequency, **kw)
        except Exception as ex:
            self._set_state("down", f"read failed: {type(ex).__name__}: {ex}")
            return None

    def read_capacitance(self, mode: str = "Cp", frequency: int = 1000,
                         sim_return: Measurement = SIM_CAPACITANCE) -> Optional[Measurement]:
        return self._safe_read("read_capacitance", frequency, sim_return, mode=mode)

    def read_inductance(self, mode: str = "Ls", frequency: int = 1000,
                        sim_return: Measurement = SIM_INDUCTANCE) -> Optional[Measurement]:
        return self._safe_read("read_inductance", frequency, sim_return, mode=mode)

    def read_resistance(self, frequency: int = 1000,
                        sim_return: Measurement = SIM_RESISTANCE) -> Optional[Measurement]:
        return self._safe_read("read_resistance", frequency, sim_return)

    def read_impedance(self, frequency: int = 1000,
                       sim_return: Measurement = SIM_IMPEDANCE) -> Optional[Measurement]:
        return self._safe_read("read_impedance", frequency, sim_return)

    def read(self, sim_return: Measurement = SIM_CAPACITANCE) -> Optional[Measurement]:
        """Re-read at the current function + frequency. Convenience for
        operator-button 'Read once' style buttons that don't need to
        change settings. In sim, returns ``sim_return``."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            return self._driver.read()
        except Exception as ex:
            self._set_state("down", f"read failed: {type(ex).__name__}: {ex}")
            return None
