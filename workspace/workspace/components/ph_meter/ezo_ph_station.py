"""Atlas Scientific EZO-pH station — the thin wrapper the workspace's
``PhMeterAtlas`` component holds and that the device bus attaches.

Same shape as ``BK879BStation`` (multimeter), ``SPX222Station`` (scale)
and ``KeytoStation`` (pipettor):

* One class, sim flag baked in at construction.
* Implements the **Device protocol** (``id``, ``state``, ``msg``,
  ``on_state_change``, ``recover``, ``release``) so
  ``workspace.devices.attach_device`` can publish bus state and wire
  AutoRecover for the real path.
* Exposes a **unified pH API** (``is_connected``, ``read``,
  ``read_stable``, ``ph``, ``slope``, ``calibrate``, …) so the
  component / recipes never branch on the sim flag.
* Real mode wraps :class:`AtlasPH` (the raw EZO UART driver). Sim mode
  returns canned ``Reading`` / ``Slope`` objects matching the real
  shape.

The EZO circuit talks over a serial port, so the device id is keyed on
the port basename (device-guide §9 — slash-free, so MQTT single-level
wildcards still match). See device-guide §10.5 (one sim/real branch, in
the station) and §16 (sim is orthogonal to connection state).
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from workspace.components.ph_meter.ezo_ph_driver import AtlasPH, Reading, Slope


log = logging.getLogger(__name__)


class EzoPHStation:
    """Wraps the EZO-pH serial driver in a Device-bus-shaped object.

    Constructor:
        port:        serial port of the EZO circuit (the device's bus
                     identity). ``""`` → no claim; the component gates
                     the attachment on the same field.
        baud:        UART speed (EZO factory default 9600).
        timeout:     per-command read deadline, seconds.
        simulation:  True → measurement methods return canned data;
                     the real connect is still attempted so the bus dot
                     keeps showing hardware truth.
        label:       human-friendly tag for logs / state messages.

    Lifecycle mirrors BK879BStation:
        ``recover()`` rebuilds the driver, opens the port, kills the
        chip's continuous-stream mode and verifies it answers ``i``.
        ``release()`` closes the port. Bus state always reflects real
        reachability, regardless of the sim flag.
    """

    KIND = "ph_meter"

    def __init__(
        self,
        port: str = "",
        baud: int = 9600,
        timeout: float = 2.0,
        simulation: bool = True,
        label: str = "",
    ):
        self.port = port or ""
        self.baud = int(baud)
        self.timeout = float(timeout)
        self.simulation = bool(simulation)
        self.label = label or "ph_meter"

        # Bus-visible state ALWAYS reflects real hardware reachability
        # (device-guide §16). Starts down; the component calls recover()
        # in __init__ to attempt the initial connect.
        self.state: str = "down"
        self.msg: str = "not connected"

        self._listeners: list[Callable[[str, str], None]] = []
        # Real driver instance; None in sim, or until connect() succeeds.
        self._driver: Optional[AtlasPH] = None

    # ── Device protocol ────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """``ph_meter:<basename(port)>`` per device-guide §9. The
        basename keeps the id slash-free — subscribers use
        ``device/+/info`` (single-level wildcard) and a ``/dev/serial/
        by-id/...`` path would push ``info`` past the depth that
        subscription matches, hiding the publisher. Sim does not change
        the id."""
        return f"{self.KIND}:{os.path.basename(self.port)}"

    def on_state_change(self, cb: Callable[[str, str], None]) -> None:
        self._listeners.append(cb)

    def _set_state(self, new_state: str, msg: str = "") -> None:
        if new_state == self.state and msg == self.msg:
            return
        self.state, self.msg = new_state, msg
        for cb in list(self._listeners):
            try:
                cb(new_state, msg)
            except Exception:
                log.exception("EzoPHStation[%s]: listener raised", self.label)

    def recover(self) -> bool:
        """(Re)establish the serial link. ALWAYS attempts the real
        connect — sim does not change this (device-guide §16). Fires a
        ``recovering → result`` transition so the bus/UI always see an
        event even when the outcome matches the previous state."""
        self._set_state("recovering", "reconnecting")
        try:
            # Rebuild from scratch. After a USB unplug the old
            # serial.Serial still reports ``is_open == True`` (pyserial
            # doesn't know the kernel ripped the device away), so
            # ``connect()`` would short-circuit onto a dead fd. A fresh
            # driver re-opens through the by-id symlink, which udev
            # re-resolves to whatever ttyUSB* the chip landed on.
            if self._driver is not None:
                try:
                    self._driver.close()
                except Exception:
                    pass
                self._driver = None
            self._driver = AtlasPH(port=self.port, baud=self.baud, timeout=self.timeout)
            if not self._driver.connect():
                self._set_state("down", "connect failed")
                return False
            # connect() already killed continuous mode + drained; this
            # is the handshake that proves the circuit is actually
            # answering rather than the port merely being open.
            if not self._driver.check_connection():
                self._set_state("down", "no response to 'i'")
                return False
            self._set_state("ok", "")
            return True
        except Exception as ex:
            self._set_state("down", f"connect failed: {type(ex).__name__}: {ex}")
            return False

    def release(self) -> None:
        """Close the serial port + drop the driver. Does not branch on
        sim — release frees the real handle if one exists."""
        drv, self._driver = self._driver, None
        if drv is not None:
            try:
                drv.close()
            except Exception:
                log.exception("EzoPHStation[%s]: close raised", self.label)
        self._set_state("down", "released")

    def set_simulation(self, sim: bool) -> None:
        """Live sim/real flip — flag only (device-guide §16). The serial
        connection, if open, stays open across the flip."""
        self.simulation = bool(sim)

    # ── Failure classification ─────────────────────────────────────────

    def _op_failed(self, what: str, ex: Exception) -> None:
        """Decide whether an op failure means "link lost" or "the chip
        refused that command".

        The driver raises ``RuntimeError`` for both a silent device and
        an EZO ``*ER``, and calibration legitimately refuses commands
        (low/high before mid, bad buffer value) on a perfectly healthy
        link. Marking the device down for those would spray the panel
        with false connection alarms, so re-probe with ``i``: a circuit
        that still answers refused the command and stays ``ok``.
        """
        try:
            alive = self._driver is not None and bool(self._driver.info())
        except Exception:
            alive = False
        if alive:
            log.warning("EzoPHStation[%s]: %s refused: %s", self.label, what, ex)
        else:
            self._set_state("down", f"{what} failed: {type(ex).__name__}: {ex}")

    # ── Unified pH API ─────────────────────────────────────────────────
    # Real path delegates to the driver, transitioning state to "down"
    # on link failure so the bus + AutoRecover learn about it.
    #
    # ``sim_return`` (device-guide §17) — explicit sim injection. Its
    # default IS the canned sim value, written inline in the signature and
    # shaped exactly like the real return (a ``Reading`` for read /
    # read_stable, a ``float`` for ph, a ``Slope`` for slope). In sim the
    # method returns ``sim_return`` verbatim; real mode ignores it.

    def is_connected(self) -> bool:
        if self.simulation:
            return True
        return self._driver is not None and self._driver.is_connected()

    def read(self, sim_return: Reading = Reading(status="ok", ph=7.000, raw="sim")) -> Optional[Reading]:
        """Single reading (EZO ``R``, ~900 ms on-chip). ``None`` when
        disconnected and not in sim. In sim, returns ``sim_return``."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            r = self._driver.read()
            if not r.connected:
                self._set_state("down", "no reading")
            return r
        except Exception as ex:
            self._set_state("down", f"read failed: {type(ex).__name__}: {ex}")
            return None

    def read_at_temperature(self, celsius: float,
                            sim_return: Reading = Reading(status="ok", ph=7.000, raw="sim")) -> Optional[Reading]:
        """``RT,t`` — compensate for the sample temperature and read in
        one round-trip. In sim, returns ``sim_return``."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            r = self._driver.read_at_temperature(celsius)
            if not r.connected:
                self._set_state("down", "no reading")
            return r
        except Exception as ex:
            self._set_state("down", f"read failed: {type(ex).__name__}: {ex}")
            return None

    def read_stable(self, n: int = 3, tolerance: float = 0.02, max_readings: int = 20,
                    sim_return: Reading = Reading(status="ok", ph=7.000, raw="sim")) -> Optional[Reading]:
        """Poll until the probe settles — ``n`` consecutive readings
        within ``tolerance`` pH units, or ``max_readings`` tries. The
        settling wait is the probe's, not the bus's: a freshly dipped
        electrode needs seconds. In sim, returns ``sim_return``."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            r = self._driver.read_stable(n=n, tolerance=tolerance, max_readings=max_readings)
            if not r.connected:
                self._set_state("down", "no reading")
            return r
        except Exception as ex:
            self._set_state("down", f"read failed: {type(ex).__name__}: {ex}")
            return None

    def ph(self, stable: bool = True, sim_return: float = 7.000) -> Optional[float]:
        """Convenience: just the pH value (or None). ``stable=True``
        waits for a settled reading. In sim, returns ``sim_return`` (a
        ``float``) verbatim."""
        if self.simulation:
            return sim_return
        r = self.read_stable() if stable else self.read()
        return None if r is None or not r.ok else r.ph

    def slope(self, sim_return: Slope = Slope(acid_percent=99.5, base_percent=99.2, offset_mv=0.0, raw="sim")) -> Optional[Slope]:
        """``Slope,?`` — probe health vs an ideal electrode. Check
        ``.healthy`` before trusting a reading after a long idle. In
        sim, returns ``sim_return``."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            return self._driver.slope()
        except Exception as ex:
            self._op_failed("slope", ex)
            return None

    # ── Calibration ────────────────────────────────────────────────────
    # Writes, not reads — but they still return a value, so they follow
    # the same inline-``sim_return`` shape as the pipettor's pump ops.
    # The chip's rule: mid (~pH 7) FIRST (it wipes any existing
    # calibration), then low (~4) / high (~10) in either order.

    def calibration_points(self, sim_return: int = 3) -> Optional[int]:
        """``Cal,?`` — number of stored calibration points (0–3). In
        sim, returns ``sim_return``."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            return self._driver.calibration_points()
        except Exception as ex:
            self._op_failed("calibration_points", ex)
            return None

    def calibrate(self, value: float, sim_return: bool = True) -> bool:
        """Calibrate against the buffer currently on the probe. The point
        (low / mid / high) is picked from ``value``. Let the reading
        settle (``read_stable``) before calling.

        Returns True on success, False when the chip refused (e.g.
        low/high before mid) or the link is down — never raises, so a BT
        action can ``return False`` and let the planner re-select
        (declarative retry, project-guide §8)."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return False
        try:
            self._driver.calibrate(value)
            return True
        except Exception as ex:
            self._op_failed("calibrate", ex)
            return False

    def calibrate_clear(self, sim_return: bool = True) -> bool:
        """Wipe all stored calibration points. Same contract as
        :meth:`calibrate`."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return False
        try:
            self._driver.calibrate_clear()
            return True
        except Exception as ex:
            self._op_failed("calibrate_clear", ex)
            return False

    # ── Temperature compensation ───────────────────────────────────────

    def set_temperature_compensation(self, celsius: float, sim_return: bool = True) -> bool:
        """Store the sample temperature on the chip (``T,t``). Persists
        across power cycles. Same contract as :meth:`calibrate`."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return False
        try:
            self._driver.set_temperature_compensation(celsius)
            return True
        except Exception as ex:
            self._op_failed("set_temperature_compensation", ex)
            return False

    def get_temperature_compensation(self, sim_return: float = 25.0) -> Optional[float]:
        """The temperature the chip is currently compensating for (°C).
        In sim, returns ``sim_return``."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            return self._driver.get_temperature_compensation()
        except Exception as ex:
            self._op_failed("get_temperature_compensation", ex)
            return None
