"""Generic syringe-pump station — the Device-protocol wrapper the
``Pump`` component holds and the device bus attaches.

Same shape as ``BK879BStation`` (multimeter) and ``KeytoStation``
(pipettor):

* One class, sim flag baked in at construction.
* Implements the **Device protocol** (``id``, ``state``, ``msg``,
  ``on_state_change``, ``recover``, ``release``) so
  ``workspace.devices.attach_device`` can publish bus state and wire
  AutoRecover for the real path.
* Speaks **steps and numbered valve ports** — the drive's own units.
  Everything the pump cannot report (syringe volume, the port map,
  what liquid is where) lives one layer up, in the component; the
  station is only the drive.
* Real mode delegates to a brand backend from :data:`DRIVERS`
  (``"psd4"`` is the only one today). Sim mode tracks the plunger and
  valve in memory — **and takes the time the real move would take**,
  modeled from the manual's speed table (table 8-33) and the valve
  drive spec (250 ms per 120°), so a sim run has real timing, not just
  real bookkeeping.

The one sim/real branch of the whole stack is here (device-guide
§10.5); sim is orthogonal to connection state (§16) — ``recover()``
always attempts the real connect and the bus dot always shows hardware
truth.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

from workspace.components.pump.psd4_driver import (
    PSD4,
    PSD4Error,
    VALVE_SECONDS_PER_120_DEG,
    VALVE_TYPES,
    VARIANTS,
    stroke_seconds,
)


log = logging.getLogger(__name__)


# Angular spacing of NUMBERED ports per valve type (?21000) — drives
# the sim's valve timing. 45° on an 8-way body, 90° on the T and
# distribution bodies, 120° on the Y valve.
VALVE_PORT_SPACING_DEG = {0: 120, 1: 90, 2: 90, 3: 45, 4: 90}


class Psd4Backend:
    """Hamilton PSD/4 family behind the station — real hardware ops
    plus the drive's timing model (which the sim path shares, so sim
    and real take the same time at the same speed).

    Every hardware method raises :class:`PSD4Error` on trouble; the
    station's guard classifies the failure (refusal vs dropped link).
    """

    def __init__(self, port: str = "", address=0, baud: int = 9600,
                 timeout: float = 2.0, high_resolution: bool = True,
                 output_right: bool = True, variant: str = "standard"):
        if variant not in VARIANTS:
            raise ValueError(f"unknown pump variant {variant!r} — one of {sorted(VARIANTS)}")
        self.port = port or ""
        self.address = address
        self.baud = int(baud)
        self.timeout = float(timeout)
        self.high_resolution = bool(high_resolution)
        self.output_right = bool(output_right)
        self.variant = str(variant)
        self._driver: Optional[PSD4] = None

    # ── Facts + timing model (no hardware needed — the sim uses these) ─

    @property
    def max_steps(self) -> int:
        return VARIANTS[self.variant][1 if self.high_resolution else 0]

    def stroke_seconds(self, percent: float) -> float:
        """Seconds one full stroke takes at ``percent`` speed."""
        return stroke_seconds(percent, self.variant, self.high_resolution)

    def valve_seconds(self, from_port: Optional[int], to_port: Optional[int],
                      valve_type: Optional[int]) -> float:
        """Seconds a valve move takes: shortest route in degrees at the
        spec's 250 ms per 120°. An unknown side (fresh boot, just
        homed) is charged a half turn."""
        spacing = VALVE_PORT_SPACING_DEG.get(valve_type, 45)
        if from_port is None or to_port is None:
            deg = 180
        else:
            d = (abs(int(to_port) - int(from_port)) * spacing) % 360
            deg = min(d, 360 - d)
        return deg / 120.0 * VALVE_SECONDS_PER_120_DEG

    # ── Connection ─────────────────────────────────────────────────────

    def rebuild(self) -> None:
        """Drop any old driver and build a fresh one. After a USB unplug
        the old ``serial.Serial`` still reports ``is_open``, so a plain
        ``connect()`` would short-circuit onto a dead fd."""
        if self._driver is not None:
            try:
                self._driver.close()
            except Exception:
                pass
        self._driver = PSD4(
            port=self.port, address=self.address, baud=self.baud,
            timeout=self.timeout, high_resolution=self.high_resolution,
            variant=self.variant,
        )

    def connect(self) -> bool:
        return self._driver is not None and self._driver.connect()

    def alive(self) -> bool:
        """True only if the pump actually answered a query."""
        try:
            return self._driver is not None and self._driver.check_connection()
        except Exception:
            return False

    def is_connected(self) -> bool:
        return self._driver is not None and self._driver.is_connected()

    def close(self) -> None:
        drv, self._driver = self._driver, None
        if drv is not None:
            drv.close()

    # ── Hardware ops (real mode only) ──────────────────────────────────

    def configure_and_home(self, declared_valve_type: Optional[int],
                           half_force: bool) -> int:
        """The whole configure-and-home, in order: h-factor on,
        resolution asserted, valve type read and CHECKED against the
        scene's declaration, plunger + valve homed. Returns the valve
        type the pump reports.

        Resolution is asserted because there is no DIP switch for it —
        a mismatch scales every move 8x while the numbers stay
        self-consistent, so make it impossible instead. The valve type
        IS DIP-owned (switches 4-6), so it is verified, not written: a
        mismatch means the switches or the scene are wrong, and nobody
        should pump through a valve they mis-declared.
        """
        d = self._driver
        d.enable_h_factor(True)
        mode = d.syringe_mode()
        if mode["high_resolution"] != self.high_resolution:
            log.warning("Psd4Backend[%s]: pump was in %s resolution, setting %s",
                        self.port, "high" if mode["high_resolution"] else "standard",
                        "high" if self.high_resolution else "standard")
            d.set_resolution(self.high_resolution)
        d.high_resolution = self.high_resolution

        reported = d.valve_type()
        if declared_valve_type is not None and reported != int(declared_valve_type):
            raise PSD4Error(
                f"valve type mismatch: scene declares {int(declared_valve_type)} "
                f"({VALVE_TYPES.get(int(declared_valve_type), '?')}), pump reports "
                f"{reported} ({VALVE_TYPES.get(reported, '?')}) — DIP switches 4-6 "
                f"own it; fix the switches (then power-cycle) or the scene"
            )
        d.initialize(output_right=self.output_right, half_force=half_force)
        return reported

    def set_speed(self, percent: float) -> None:
        self._driver.set_speed(percent)

    def valve_to(self, port: int) -> None:
        self._driver.valve_port(int(port))

    def valve_port(self) -> Optional[int]:
        p = int(self._driver.valve_position())
        return p or None            # 0 = not at a numbered port

    def move_to_steps(self, steps: int) -> None:
        self._driver.move_to_steps(int(steps))

    def position_steps(self) -> int:
        return self._driver.position_steps()

    def terminate(self) -> None:
        self._driver.terminate()

    def status(self) -> dict:
        st = self._driver.status()
        return {"ready": st.ready, "error": st.error, "error_text": st.error_text}


# Scene ``driver:`` key → backend class. One entry today; a new brand
# means a new ``<brand>_driver.py`` + backend with the same surface.
DRIVERS = {"psd4": Psd4Backend}


class PumpStation:
    """One syringe drive on the bus.

    Constructor:
        port:           serial port of the RS-232/485 adapter. ``""`` →
                        no claim; the component gates the bus
                        attachment on the same field.
        driver:         key into :data:`DRIVERS` (``"psd4"``).
        driver_kwargs:  passed to the backend — address, baud, timeout,
                        high_resolution, output_right, variant.
        valve_type:     the valve BODY the scene declares (?21000
                        code); initialize refuses on mismatch. ``None``
                        skips the check.
        speed:          initial plunger speed, 0-100 percent. The last
                        speed set is remembered and re-applied at every
                        initialize, so a freshly homed pump always runs
                        at a known speed.
        simulation:     True → ops are bookkeeping (with modeled
                        timing); the real connect is still attempted so
                        the bus dot keeps showing hardware truth.
        label:          human-friendly tag for logs.

    ``last_error`` is ``(code, text)`` of the most recent refusal or
    failure — ``(0, "")`` when clean. Codes mirror the pump's error
    nibble where one applies (3 = out of range, 7 = not initialized),
    ``-1`` for driver/link exceptions.
    """

    KIND = "pump"

    def __init__(self, port: str = "", driver: str = "psd4",
                 driver_kwargs: Optional[dict] = None, valve_type=None,
                 speed: float = 100.0, simulation: bool = True, label: str = ""):
        if driver not in DRIVERS:
            raise ValueError(f"unknown pump driver {driver!r} — one of {sorted(DRIVERS)}")
        self.port = port or ""
        self.driver_name = str(driver)
        self.backend = DRIVERS[driver](port=self.port, **(driver_kwargs or {}))
        self.declared_valve_type = None if valve_type is None else int(valve_type)
        self.simulation = bool(simulation)
        self.label = label or "pump"

        self._speed = float(speed)
        self._adopted_valve_type: Optional[int] = None
        self.last_error: tuple = (0, "")

        # Bus-visible state ALWAYS reflects real hardware reachability
        # (device-guide §16). Starts down; the component calls recover()
        # in __init__ to attempt the initial connect.
        self.state: str = "down"
        self.msg: str = "not connected"
        self._listeners: list[Callable[[str, str], None]] = []

        # Sim bookkeeping — the plunger and valve in memory, in the
        # drive's own units, so a sim run exercises the same limits the
        # real drive enforces.
        self._sim_steps: int = 0
        self._sim_port: Optional[int] = None
        self._sim_initialized: bool = False
        self._real_initialized: bool = False
        # terminate() sets this to cut a modeled sim move short, the
        # way ``T`` aborts a real one.
        self._stop_event = threading.Event()

    # ── Device protocol ────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """``pump:<basename(port)>`` per device-guide §9 — ``pump`` is
        the blessed kind for syringe / dosing pumps; the basename keeps
        the id slash-free so ``device/+/info`` matches. Sim does not
        change it."""
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
                log.exception("PumpStation[%s]: listener raised", self.label)

    def recover(self) -> bool:
        """(Re)establish the serial link. ALWAYS attempts the real
        connect — sim does not change this (device-guide §16). Fires a
        ``recovering → result`` transition so the bus/UI always see an
        event.

        **Reconnecting configures and homes the pump** (real mode): a
        recovered pump is a pump in a known state, and one that has not
        homed refuses every syringe move with "syringe not initialized"
        anyway. So recovery produces motion, exactly as power-cycling
        would — safe because recovery never auto-resumes the run; the
        operator is present and expected to have cleared the deck.
        Initialize failure does NOT fail the recover: the link is up,
        which is what the bus dot reports, and the operator can re-home
        from the Initialize button.
        """
        self._set_state("recovering", "reconnecting")
        try:
            self.backend.rebuild()
            if not self.backend.connect():
                self._set_state("down", "connect failed")
                return False
            if not self.backend.alive():
                self._set_state("down", "no response to '&' — check address / baud / wiring")
                return False
            ok = self.initialize()
            self._set_state(
                "ok",
                "" if ok else
                f"connected — initialize failed: {self.last_error[1]} — press Initialize",
            )
            return True
        except Exception as ex:
            self._set_state("down", f"connect failed: {type(ex).__name__}: {ex}")
            return False

    def release(self) -> None:
        """Close the serial port + drop the driver. Does not branch on
        sim — release frees the real handle if one exists."""
        try:
            self.backend.close()
        except Exception:
            log.exception("PumpStation[%s]: close raised", self.label)
        self._real_initialized = False
        self._set_state("down", "released")

    def set_simulation(self, sim: bool) -> None:
        """Live sim/real flip — flag only (device-guide §16). The serial
        connection, if open, stays open across the flip."""
        self.simulation = bool(sim)

    # ── Failure classification ─────────────────────────────────────────

    def _op_failed(self, what: str, ex: Exception) -> None:
        """Decide whether a failure means "link lost" or "the pump
        refused that command". A refusal (out of range, not
        initialized, valve type mismatch) happens on a perfectly
        healthy link and must not paint the device red. Re-probe with
        ``&``: a pump that still answers refused the command and stays
        ``ok``."""
        if self.backend.alive():
            log.warning("PumpStation[%s]: %s refused: %s", self.label, what, ex)
        else:
            self._set_state("down", f"{what} failed: {type(ex).__name__}: {ex}")

    def _guard(self, what: str, fn, failure):
        """Run a real-mode op, classifying any exception. Returns
        ``failure`` when the op could not run."""
        if not self.backend.is_connected():
            self.last_error = (-1, f"{what}: not connected")
            return failure
        try:
            return fn()
        except Exception as ex:
            self.last_error = (-1, f"{what}: {ex}")
            self._op_failed(what, ex)
            return failure

    # ── Sim timing ─────────────────────────────────────────────────────

    def _sim_wait(self, seconds: float) -> bool:
        """Block for the modeled duration of a sim move — parity with
        the real ops, which block until the motion finishes. Returns
        False when terminate() cut the move short."""
        self._stop_event.clear()
        if seconds <= 0:
            return True
        return not self._stop_event.wait(seconds)

    # ── Drive facts ────────────────────────────────────────────────────

    def max_steps(self) -> int:
        """Steps per full stroke — the component's µL↔steps scale."""
        return self.backend.max_steps

    def speed(self) -> float:
        """Last speed asked for, 0-100 percent."""
        return self._speed

    def valve_type(self) -> Optional[int]:
        """The valve body: what the pump reported at the last real
        initialize, else what the scene declares."""
        return self._adopted_valve_type if self._adopted_valve_type is not None \
            else self.declared_valve_type

    @property
    def initialized(self) -> bool:
        return self._sim_initialized if self.simulation else self._real_initialized

    def is_connected(self) -> bool:
        if self.simulation:
            return True
        return self.backend.is_connected()

    # ── Ops ────────────────────────────────────────────────────────────
    # ``sim_return`` (device-guide §17) — explicit sim injection, shaped
    # like the real return. Real mode ignores it. Sim honors it AFTER
    # the bookkeeping: a refused move (out of range, not initialized)
    # returns False regardless, exactly like the real drive.

    def initialize(self, half_force: bool = True, sim_return: bool = True) -> bool:
        """Configure the pump and home the plunger and valve. Required
        after every power-up and after any stall or ``terminate()`` —
        until then the drive refuses syringe moves. Homing expels the
        barrel through the output port, so clear the deck first. The
        last speed set is re-applied afterwards.

        Sim models it: a valve homing turn plus expelling whatever the
        plunger holds at the current speed.
        """
        if self.simulation:
            secs = (360 / 120.0 * VALVE_SECONDS_PER_120_DEG
                    + self._sim_steps / self.backend.max_steps
                    * self.backend.stroke_seconds(self._speed))
            done = self._sim_wait(secs)
            self._sim_initialized = done
            if not done:
                self.last_error = (-1, "initialize terminated")
                return False
            self._sim_steps = 0
            self._sim_port = None       # homed to logical output, not a numbered port
            return sim_return

        if not self.backend.is_connected():
            self.last_error = (-1, "initialize: not connected")
            return False
        try:
            self._adopted_valve_type = self.backend.configure_and_home(
                self.declared_valve_type, half_force)
            self.backend.set_speed(self._speed)
            self._real_initialized = True
            self.last_error = (0, "")
            return True
        except Exception as ex:
            self._real_initialized = False
            self.last_error = (-1, str(ex))
            self._op_failed("initialize", ex)
            return False

    def set_speed(self, percent: float, sim_return: bool = True) -> bool:
        """Plunger speed, 0-100: 100 = the pump's fastest preset, 0 =
        its slowest. Snaps to one of the 40 presets (table 8-33).
        Normalized — the same number means the same relative speed on
        any variant in any resolution mode. Remembered and re-applied
        at every initialize."""
        self._speed = min(100.0, max(0.0, float(percent)))
        if self.simulation:
            return sim_return
        return bool(self._guard(
            "set_speed", lambda: (self.backend.set_speed(self._speed), True)[1], False))

    def valve_to(self, port: int, sim_return: bool = True) -> bool:
        """Rotate the valve to NUMBERED port ``port``, shortest route.
        Sim charges the spec's 250 ms per 120° over the shortest-route
        angle for the declared valve body."""
        port = int(port)
        if self.simulation:
            secs = self.backend.valve_seconds(self._sim_port, port, self.valve_type())
            if not self._sim_wait(secs):
                self.last_error = (-1, "valve move terminated")
                return False
            self._sim_port = port
            return sim_return
        return bool(self._guard(
            "valve_to", lambda: (self.backend.valve_to(port), True)[1], False))

    def valve_port(self) -> Optional[int]:
        """The numbered port the valve sits at (``None`` = not at one,
        e.g. right after homing)."""
        if self.simulation:
            return self._sim_port
        return self._guard("valve_port", self.backend.valve_port, None)

    def move_to_steps(self, steps: int, sim_return: bool = True) -> bool:
        """Absolute plunger move. Blocks until the motion is finished —
        in sim for the time the real move would take (stroke fraction ×
        table 8-33 seconds at the current speed)."""
        steps = int(round(steps))
        if self.simulation:
            if not self._sim_initialized:
                self.last_error = (7, "syringe not initialized")
                return False
            if not 0 <= steps <= self.backend.max_steps:
                self.last_error = (3, f"position {steps} out of range 0-{self.backend.max_steps}")
                return False
            start = self._sim_steps
            secs = (abs(steps - start) / self.backend.max_steps
                    * self.backend.stroke_seconds(self._speed))
            t0 = time.monotonic()
            if not self._sim_wait(secs):
                # Terminated mid-stroke: position is untrustworthy, the
                # same contract as the real ``T`` — re-initialize.
                frac = min(1.0, (time.monotonic() - t0) / secs) if secs > 0 else 1.0
                self._sim_steps = int(round(start + (steps - start) * frac))
                self._sim_initialized = False
                self.last_error = (-1, "move terminated mid-stroke — re-initialize")
                return False
            self._sim_steps = steps
            return sim_return
        return bool(self._guard(
            "move_to_steps", lambda: (self.backend.move_to_steps(steps), True)[1], False))

    def position_steps(self) -> Optional[int]:
        """Commanded plunger position; ``None`` when unreachable."""
        if self.simulation:
            return self._sim_steps
        return self._guard("position_steps", self.backend.position_steps, None)

    def terminate(self, sim_return: bool = True) -> bool:
        """Emergency stop — abort the move in progress. Position is no
        longer trustworthy afterwards (mid-stroke termination can lose
        steps), so the pump refuses further moves until the next
        initialize; the component's ops re-initialize automatically."""
        if self.simulation:
            self._stop_event.set()
            self._sim_initialized = False
            return sim_return
        self._real_initialized = False
        return bool(self._guard(
            "terminate", lambda: (self.backend.terminate(), True)[1], False))

    def status(self, sim_return: dict = {"ready": True, "error": 0,
                                         "error_text": "no error"}) -> Optional[dict]:
        """Ready/busy plus the pump's last error. Note the pump CLEARS
        its error status once it reports it, so a caller that swallows
        this has thrown the only copy away."""
        if self.simulation:
            return sim_return
        return self._guard("status", self.backend.status, None)
