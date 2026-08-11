"""Hamilton PSD/4 station — the thin wrapper the workspace's
``SyringePumpPsd4`` component holds and that the device bus attaches.

Same shape as ``KeytoStation`` (pipettor), ``BK879BStation``
(multimeter) and ``EzoPHStation`` (pH probe):

* One class, sim flag baked in at construction.
* Implements the **Device protocol** (``id``, ``state``, ``msg``,
  ``on_state_change``, ``recover``, ``release``) so
  ``workspace.devices.attach_device`` can publish bus state and wire
  AutoRecover for the real path.
* Exposes a **unified pump API** in µL and named valve positions, so
  the component and recipes never branch on the sim flag and never
  touch motor steps.
* Real mode wraps :class:`PSD4` (the raw serial driver). Sim mode
  tracks plunger volume and valve position in memory so a sim run
  exercises the same volume bookkeeping the real one does.

See device-guide §10.5 (one sim/real branch, in the station) and §16
(sim is orthogonal to connection state).
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from workspace.components.pump.psd4_driver import (
    PSD4,
    PSD4Error,
    Status,
    VALVE_TYPES,
)


log = logging.getLogger(__name__)


class PSD4Station:
    """Wraps the PSD/4 serial driver in a Device-bus-shaped object.

    Constructor:
        port:               serial port of the RS-232/485 adapter. ``""``
                            → no claim; the component gates the bus
                            attachment on the same field.
        address:            rotary-switch position (0-9, "A"-"F").
        baud:               9600 (factory) or 38400.
        syringe_volume_ul:  volume of the INSTALLED syringe — the pump
                            cannot report it, and every volumetric call
                            scales by it.
        variant:            which pump this is — "standard" (PSD/4 and
                            the PSD/6 high-torque drive) or
                            "smooth_flow" (the SF drives, 8x the
                            steps). The pump cannot report it; declared
                            wrong, every move scales by the ratio.
        high_resolution:    high-resolution step mode; steps per stroke
                            depend on the variant (3000/24000 standard,
                            24000/192000 smooth_flow).
        simulation:         True → pump ops are bookkeeping only; the
                            real connect is still attempted so the bus
                            dot keeps showing hardware truth.
        label:              human-friendly tag for logs / state.
    """

    KIND = "syringe_pump"

    def __init__(
        self,
        port: str = "",
        address=0,
        baud: int = 9600,
        timeout: float = 2.0,
        syringe_volume_ul: float = 1000.0,
        variant: str = "smooth_flow",
        high_resolution: bool = False,
        output_right: bool = True,
        simulation: bool = True,
        label: str = "",
    ):
        self.port = port or ""
        self.address = address
        self.baud = int(baud)
        self.timeout = float(timeout)
        self.syringe_volume_ul = float(syringe_volume_ul)
        self.variant = str(variant)
        self.high_resolution = bool(high_resolution)
        # Last speed asked for (0-100), re-applied at every initialize
        # so a homed pump always runs at a known speed instead of
        # whatever power-up default it has stored. Starts at fastest.
        self._speed: float = 100.0
        # What valve is fitted, as the pump's DIP switches 4-6 report
        # it — read and adopted at every initialize, never declared.
        self.valve_type: Optional[int] = None
        # Which physical side "output" means, assigned at initialization
        # (Z = right, Y = left, viewed from the front). This is not a
        # preference — it is what the plumbing is, and it decides which
        # way every later valve("output") sends fluid.
        self.output_right = bool(output_right)
        self.simulation = bool(simulation)
        self.label = label or "syringe_pump"

        # Bus-visible state ALWAYS reflects real hardware reachability
        # (device-guide §16). Starts down; the component calls recover()
        # in __init__ to attempt the initial connect.
        self.state: str = "down"
        self.msg: str = "not connected"

        self._listeners: list[Callable[[str, str], None]] = []
        self._driver: Optional[PSD4] = None

        # Sim bookkeeping. A pump whose sim path always claimed success
        # would hide the most common protocol bug there is — asking for
        # more volume than the barrel holds — so sim tracks the plunger
        # and refuses the same overflows the real pump refuses.
        self._sim_volume_ul: float = 0.0
        self._sim_valve: str = "input"
        self._sim_initialized: bool = False

    # ── Device protocol ────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """``syringe_pump:<basename(port)>`` per device-guide §9. The
        basename keeps the id slash-free so ``device/+/info`` matches.
        Sim does not change the id."""
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
                log.exception("PSD4Station[%s]: listener raised", self.label)

    def recover(self) -> bool:
        """(Re)establish the serial link. ALWAYS attempts the real
        connect — sim does not change this (device-guide §16). Fires a
        ``recovering → result`` transition so the bus/UI always see an
        event.

        **Reconnecting homes the plunger and valve**, the same contract
        as ``KeytoStation.recover`` on the pipettor: a recovered pump is
        a pump in a known state, and a PSD/4 that has not homed refuses
        every syringe move with "syringe not initialized" anyway. So
        recovery produces motion, exactly as power-cycling it would.

        This is safe because recovery never auto-resumes the run — the
        runtime stays paused until the operator presses Resume, so the
        operator is present and expected to have cleared the deck of
        samples before recovering. Homing failure does NOT fail the
        recover: the link is up, which is what the bus dot reports, and
        the operator can re-home from the Initialize button.
        """
        self._set_state("recovering", "reconnecting")
        try:
            # Rebuild from scratch — after a USB unplug the old
            # serial.Serial still reports is_open, so connect() would
            # short-circuit onto a dead fd.
            if self._driver is not None:
                try:
                    self._driver.close()
                except Exception:
                    pass
                self._driver = None
            self._driver = PSD4(
                port=self.port,
                address=self.address,
                baud=self.baud,
                timeout=self.timeout,
                syringe_volume_ul=self.syringe_volume_ul,
                high_resolution=self.high_resolution,
                variant=self.variant,
            )
            if not self._driver.connect():
                self._set_state("down", "connect failed")
                return False
            if not self._driver.check_connection():
                self._set_state("down", "no response to '&' — check address / baud / wiring")
                return False
            # One call does the whole configure-and-home. Reported, not
            # fatal: the link is up either way, which is what the bus
            # dot is about.
            ok = self.initialize()
            self._set_state(
                "ok",
                "" if ok else "connected — homing failed, press Initialize",
            )
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
                log.exception("PSD4Station[%s]: close raised", self.label)
        self._set_state("down", "released")

    def set_simulation(self, sim: bool) -> None:
        """Live sim/real flip — flag only (device-guide §16). The serial
        connection, if open, stays open across the flip."""
        self.simulation = bool(sim)

    # ── Failure classification ─────────────────────────────────────────

    def _op_failed(self, what: str, ex: Exception) -> None:
        """Decide whether a failure means "link lost" or "the pump
        refused that command".

        A refusal (parameter out of range, syringe not initialized,
        valve in bypass) happens constantly on a perfectly healthy link
        and must not paint the device red. Re-probe with ``&``: a pump
        that still answers refused the command and stays ``ok``.
        """
        try:
            alive = self._driver is not None and self._driver.check_connection()
        except Exception:
            alive = False
        if alive:
            log.warning("PSD4Station[%s]: %s refused: %s", self.label, what, ex)
        else:
            self._set_state("down", f"{what} failed: {type(ex).__name__}: {ex}")

    def _guard(self, what: str, fn, failure):
        """Run a real-mode op, classifying any exception. Returns
        ``failure`` when the op could not run."""
        if self._driver is None or not self._driver.is_connected():
            return failure
        try:
            return fn()
        except Exception as ex:
            self._op_failed(what, ex)
            return failure

    # ── Unified pump API ───────────────────────────────────────────────
    # ``sim_return`` (device-guide §17) — explicit sim injection. Its
    # default IS the canned sim value, inline in the signature and shaped
    # exactly like the real return: a bool for the move ops, a float for
    # volume, a dict for status. Real mode ignores it.

    def is_connected(self) -> bool:
        if self.simulation:
            return True
        return self._driver is not None and self._driver.is_connected()

    # ── Syringe size ──

    def syringe_volume(self, sim_return: float = 0.0) -> float:
        """The syringe size currently in effect, µL. Not a hardware
        read — this is what we were TOLD is fitted. Change it by passing
        ``syringe_volume_ul`` to :meth:`initialize`."""
        return self.syringe_volume_ul

    def _set_volume(self, volume_ul) -> bool:
        """Rescale every volumetric conversion. Returns False on a value
        that cannot be a syringe."""
        try:
            volume_ul = float(volume_ul)
        except (TypeError, ValueError):
            log.warning("PSD4Station[%s]: bad syringe volume %r", self.label, volume_ul)
            return False
        if volume_ul <= 0:
            log.warning("PSD4Station[%s]: syringe volume must be > 0, got %s",
                        self.label, volume_ul)
            return False
        self.syringe_volume_ul = volume_ul
        if self._driver is not None:
            self._driver.syringe_volume_ul = volume_ul
        # Keep the sim plunger physical: a smaller barrel cannot still be
        # holding more than it now holds.
        self._sim_volume_ul = min(self._sim_volume_ul, volume_ul)
        return True

    # ── Initialization ──

    def initialize(self, output_right=None, half_force: bool = False,
                   syringe_volume_ul=None, sim_return: bool = True) -> bool:
        """Configure the pump for this bench and home it. One call.

        Everything fixed about a given pump is applied here rather than
        through separate setters, because none of it is meaningful on
        its own — a syringe volume without a homed plunger, or a homed
        plunger with the wrong resolution mode, is just a wrong number
        waiting to happen. In order:

        1. **Syringe volume** — rescales µL↔steps. Pass
           ``syringe_volume_ul`` to override the configured barrel for
           this run; omit it to keep what the scene declared.
        2. **Resolution mode** — 24000 or 192000 steps per stroke. This
           one is *asserted*: there is no DIP switch for it, so the
           configured value is the only source of truth, and writing it
           means a mismatch cannot survive an initialize.
        3. **Valve type** — *read and adopted*. DIP switches 4-6 own it
           and the pump reports them (``?21000``); the switches are
           trusted as the declaration of what's fitted.
        4. **Home**, assigning valve addressing: ``output_right`` picks
           which physical side "output" means (Z = right, Y = left).
           ``None`` uses the configured value, which is what you want —
           it describes the plumbing, not the call.

        Required after every power-up and after any stall or ``stop()``:
        the pump refuses syringe moves with "syringe not initialized"
        until it has homed. Homing moves the valve several times and
        expels the barrel through the output port (§6-4).

        ``half_force=True`` homes at reduced plunger force — worth it on
        a small or fragile syringe.
        """
        right = self.output_right if output_right is None else bool(output_right)

        if syringe_volume_ul is not None and not self._set_volume(syringe_volume_ul):
            return False

        if self.simulation:
            self._sim_initialized = True
            self._sim_volume_ul = 0.0
            self._sim_valve = "output"
            return sim_return

        if self._driver is None or not self._driver.is_connected():
            return False

        try:
            # h-factor is off at power-up; multi-port valving and every
            # per-subsystem query need it.
            self._driver.enable_h_factor(True)

            # Resolution: assert. A mismatch here scales every volume by
            # 8x while the numbers stay perfectly self-consistent — you
            # command 600 steps, the pump reports 600 steps, the plunger
            # moves an eighth of what you meant. Nothing downstream can
            # catch that, so make it impossible instead.
            mode = self._driver.syringe_mode()
            if mode["high_resolution"] != self.high_resolution:
                log.warning(
                    "PSD4Station[%s]: pump was in %s resolution, setting %s",
                    self.label,
                    "high" if mode["high_resolution"] else "standard",
                    "high" if self.high_resolution else "standard",
                )
                self._driver.set_resolution(self.high_resolution)
            self._driver.high_resolution = self.high_resolution

            # Valve type: DIP switches 4-6 own it and the pump reports
            # them — trusted as-is. The valve carries no ID chip, so a
            # wrong PHYSICAL valve is undetectable; the switches are
            # the declaration of what's fitted.
            self.valve_type = self._driver.valve_type()
            log.info(
                "PSD4Station[%s]: valve type %s (%s)", self.label,
                self.valve_type, VALVE_TYPES.get(self.valve_type, "?"),
            )

            self._driver.initialize(output_right=right, half_force=half_force)
            # A homed pump at a known speed — the pump's own stored
            # power-up default is slow, so leaving it unset makes every
            # fresh connect crawl.
            self._driver.set_speed(self._speed)
            return True
        except Exception as ex:
            self._op_failed("initialize", ex)
            return False

    # ── Valve ──

    def valve(self, position="input", shortest: bool = False, direction: str = "shortest",
              sim_return: bool = True) -> bool:
        """Move the valve. Covers every addressing mode the pump has, so
        this works unchanged when the valve is swapped:

        * logical name — ``input`` / ``output`` / ``bypass`` / ``extra``
          / ``wash`` / ``return``
        * numbered port — int or digit string ``1``-``8``, for
          distribution valves
        * absolute angle — ``"90deg"`` … or pass an int with a
          ``deg``/``°`` suffix, in 15° increments

        shortest:  logical moves take the shortest route in degrees.
        direction: numbered/angular moves — shortest / cw / ccw. Pick a
                   fixed direction when carryover makes the path matter.

        Not every valve has every position; the pump answers "invalid
        command" for one it does not have, which leaves the device green
        (a refusal, not a link fault).
        """
        text = str(position).strip().lower()
        if self.simulation:
            self._sim_valve = text
            return sim_return
        if text.endswith("deg") or text.endswith("°"):
            deg = int(text.rstrip("°").removesuffix("deg").strip())
            return bool(self._guard(
                "move_valve_angle",
                lambda: (self._driver.move_valve_angle(deg, direction=direction), True)[1], False))
        if text.isdigit():
            return bool(self._guard(
                "valve_port",
                lambda: (self._driver.valve_port(int(text), direction=direction), True)[1], False))
        return bool(self._guard(
            "valve", lambda: (self._driver.valve(text, shortest=shortest), True)[1], False))

    # Valve type is deliberately NOT settable from here. DIP switches
    # 4-6 are the source of truth, ``initialize`` reads and adopts
    # them, and a runtime override would revert on the next power cycle
    # anyway. ``PSD4.set_valve_type`` is still there for a bench
    # one-off.

    # ── Plunger ──

    def aspirate(self, volume_ul: float, port=None, sim_return: bool = True) -> bool:
        """Draw ``volume_ul`` in, from ``port``.

        ``port`` is any address the valve understands — a logical name
        (``"input"``), a numbered port on a distribution valve (``3``),
        or an angle (``"90deg"``). The valve moves there first. Omit it
        to draw through wherever the valve already is.

        Returns False if the valve move fails, if the barrel cannot hold
        that much, or if the pump is unreachable.
        """
        if port is not None and not self.valve(port, sim_return=sim_return):
            return False
        if self.simulation:
            if self._sim_volume_ul + float(volume_ul) > self.syringe_volume_ul + 1e-9:
                log.warning(
                    "PSD4Station[%s]: sim aspirate %.1f µL would overflow "
                    "(%.1f/%.1f held)", self.label, volume_ul,
                    self._sim_volume_ul, self.syringe_volume_ul,
                )
                return False
            self._sim_volume_ul += float(volume_ul)
            return sim_return
        return bool(self._guard(
            "aspirate", lambda: (self._driver.aspirate(volume_ul), True)[1], False))

    def dispense(self, volume_ul: float, port=None, sim_return: bool = True) -> bool:
        """Push ``volume_ul`` out through ``port``.

        Same addressing as :meth:`aspirate`. Omit ``port`` to push
        through wherever the valve already is.
        """
        if port is not None and not self.valve(port, sim_return=sim_return):
            return False
        if self.simulation:
            if float(volume_ul) > self._sim_volume_ul + 1e-9:
                log.warning(
                    "PSD4Station[%s]: sim dispense %.1f µL exceeds the %.1f µL held",
                    self.label, volume_ul, self._sim_volume_ul,
                )
                return False
            self._sim_volume_ul -= float(volume_ul)
            return sim_return
        return bool(self._guard(
            "dispense", lambda: (self._driver.dispense(volume_ul), True)[1], False))

    def move_to_volume(self, volume_ul: float, port=None, sim_return: bool = True) -> bool:
        """Absolute: leave exactly ``volume_ul`` in the barrel.

        Whatever the difference is goes **through ``port``** — so name
        it. Moving from 200 µL to 50 µL pushes 150 µL out; moving from
        50 to 200 draws 150 in. Omit ``port`` and it goes through
        wherever the valve already is, which after a dispense is the
        port you last dispensed to.
        """
        if port is not None and not self.valve(port, sim_return=sim_return):
            return False
        if self.simulation:
            if not 0 <= float(volume_ul) <= self.syringe_volume_ul + 1e-9:
                return False
            self._sim_volume_ul = float(volume_ul)
            return sim_return
        return bool(self._guard(
            "move_to_volume",
            lambda: (self._driver.move_to_volume(volume_ul), True)[1], False))

    def empty(self, port=None, sim_return: bool = True) -> bool:
        """Drive the plunger fully home, pushing whatever is held out
        through ``port``. Name it — an unqualified empty dumps the
        contents wherever the valve happens to be pointing."""
        return self.move_to_volume(0.0, port=port, sim_return=sim_return)

    def prime(self, cycles: int = 2, volume_ul=None, from_port="input",
              to_port="output", sim_return: bool = True) -> bool:
        """Flush air out of the fluid path: fill from ``from_port``,
        empty to ``to_port``, ``cycles`` times.

        ``volume_ul`` defaults to the full declared barrel, so
        ``prime(2)`` sweeps 0-100 on a 100 µL syringe and 0-250 on a
        250 with the same call. Absolute moves, so it works from any
        starting volume — including a barrel left part-full by an
        aborted run, which is exactly when you reach for it. Returns
        False on the first failed step.
        """
        full = self.syringe_volume_ul if volume_ul is None else float(volume_ul)
        for _ in range(int(cycles)):
            if not self.move_to_volume(full, port=from_port, sim_return=sim_return):
                return False
            if not self.move_to_volume(0.0, port=to_port, sim_return=sim_return):
                return False
        return True

    def volume(self, sim_return: float = 0.0) -> Optional[float]:
        """How much is currently held, in µL."""
        if self.simulation:
            return self._sim_volume_ul
        return self._guard("volume", lambda: self._driver.position_ul(), None)

    # ── Speed ──

    def set_speed(self, percent: float = 100.0, sim_return: bool = True) -> bool:
        """Plunger speed, 0-100: 100 = the pump's fastest preset, 0 =
        its slowest. Normalized — the same number means the same
        relative speed on any variant in any resolution mode. Snaps to
        one of the 40 presets. Remembered and re-applied at every
        initialize.
        """
        self._speed = float(percent)
        if self.simulation:
            return sim_return
        return bool(self._guard(
            "set_speed",
            lambda: (self._driver.set_speed(percent), True)[1], False))

    # ── Status / diagnostics ──

    def status(self, sim_return: dict = {"ready": True, "error": 0, "error_text": "no error"}) -> Optional[dict]:
        """Ready/busy plus the last error. Note the pump CLEARS its
        error status once it reports it, so a caller that swallows this
        has thrown the only copy away."""
        if self.simulation:
            return sim_return

        def _read():
            st: Status = self._driver.status()
            return {"ready": st.ready, "error": st.error, "error_text": st.error_text}

        return self._guard("status", _read, None)

    # ── Emergency stop ──

    def stop(self, sim_return: bool = True) -> bool:
        """Emergency stop — abort the command buffer and the move in
        progress.

        The manual warns that terminating mid-stroke can lose steps, so
        the pump's reported position is no longer trustworthy: call
        ``initialize()`` before the next move.
        """
        if self.simulation:
            return sim_return
        return bool(self._guard(
            "stop", lambda: (self._driver.terminate(), True)[1], False))
