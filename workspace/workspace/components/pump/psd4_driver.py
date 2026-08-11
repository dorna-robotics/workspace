"""Hamilton MICROLAB PSD/4 precision syringe drive — raw serial driver.

Protocol per the MICROLAB PSD/4 User Manual (P/N 8892-01 Rev. B),
chapters 6-9. Terminal Protocol over RS-232 or RS-485, 9600 or 38400
baud, 8N1, half duplex.

Data block, both directions (manual §7-4)::

    /<address><data><CR>          host  -> pump
    /0<status><data><CR><LF>      pump  -> host

The host addresses a pump by the character its rotary switch maps to
(switch 0 -> "1", 1 -> "2", … see ADDRESS_CHARS). The pump always
answers as "0", the controlling device's address, and prefixes its
reply with a one-byte Pump Status.

Pump Status byte (manual §6-2)::

    bit 7      always 0
    bit 6      always 1
    bit 5      1 = ready, 0 = busy
    bit 4      always 0
    bits 3-0   error status (see ERRORS)

**Moves are asynchronous.** ``R`` hands the command buffer to the pump
and the reply comes back immediately, with the busy bit set; the
plunger is still moving. Every blocking method here polls ``Q`` until
the ready bit returns. Pass ``wait=False`` to fire and forget.

Two coordinate systems, and mixing them up is the classic PSD/4 bug:

* **steps** — what the pump speaks, over the full 30 mm stroke. How
  many depends on the declared pump variant and resolution mode (see
  ``VARIANTS``): a plain PSD/4 speaks 0…3000 / 0…24000, a Smooth Flow
  0…24000 / 0…192000.
* **µL** — what protocols speak. Converted here using the installed
  syringe's volume, which the driver cannot detect and you must
  declare (``syringe_volume_ul``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import serial
import serial.tools.list_ports


# Rotary address switch position -> the address character the pump
# answers to on a single-pump chain (manual table 7-2).
ADDRESS_CHARS = {
    0: "1", 1: "2", 2: "3", 3: "4", 4: "5", 5: "6", 6: "7", 7: "8",
    8: "9", 9: ":", "A": ";", "B": "<", "C": "=", "D": ">", "E": "?", "F": "@",
}

# Error status, low nibble of the pump status byte (manual §6-2).
ERRORS = {
    0:  "no error",
    1:  "initialization error — valve or syringe failed to initialize",
    2:  "invalid command",
    3:  "parameter out of range",
    4:  "too many loops",
    6:  "EEPROM error",
    7:  "syringe not initialized",
    9:  "syringe overload — motor stalled during a move",
    10: "valve overload — valve motor stalled",
    11: "syringe move not allowed — valve is in bypass",
    15: "pump busy",
}

# Logical valve positions -> plain single-character command (manual
# §8-9). These work with no h-factor enabled, but only cover four of the
# six logical positions and always rotate the valve's default way.
VALVE_COMMANDS = {"input": "I", "output": "O", "bypass": "B", "extra": "E"}

# The h-factor equivalents (manual §9-4/9-5), which move to the same
# positions by the SHORTEST route — and reach the two positions the
# plain letters cannot: wash and return. A 4-port wash valve is
# unusable without these.
VALVE_LOGICAL_H = {
    "input": 23001, "output": 23002, "wash": 23003,
    "return": 23004, "bypass": 23005, "extra": 23006,
}

# ?23000 valve logical position response (manual §9-7).
VALVE_LOGICAL = {
    0: "not at a logical position", 1: "input", 2: "output",
    3: "wash", 4: "return", 5: "bypass", 6: "extra",
}

# h2100x / ?21000 valve types (manual §9-4).
VALVE_TYPES = {
    0: "3-way 120° Y valve",
    1: "4-way 90° T valve",
    2: "3-way 90° distribution valve",
    3: "8-way 45° valve",
    4: "4-way 90° valve",
}

# Speed code -> seconds per stroke (manual table 8-33), for orientation
# when picking a raw code. Holds directly on plain pumps in either
# mode. On SF firmware a code fixes the motor STEP RATE and the seconds
# apply per 24000 steps, so an SF high-res full stroke (192000 steps)
# takes 8x the table's time.
SPEED_CODES = {
    1: 1.2, 2: 1.3, 3: 1.4, 4: 1.6, 5: 1.9, 6: 2.2, 7: 2.6, 8: 2.9,
    9: 3.3, 10: 3.7, 11: 4.3, 12: 5.0, 13: 6.0, 14: 7.5, 15: 10.0,
    16: 15.0, 17: 30.0, 18: 31.0, 19: 33.0, 20: 35.5, 21: 37.5,
    22: 40.0, 23: 43.0, 24: 46.0, 25: 50.0, 26: 55.0, 27: 60.0,
    28: 67.0, 29: 75.0, 30: 86.0, 31: 100.0, 32: 120.0, 33: 150.0,
    34: 200.0, 35: 300.0, 36: 333.3, 37: 375.0, 38: 428.6, 39: 500.0,
    40: 600.0,
}

# Steps per full 30 mm stroke, (standard, high_resolution), per pump
# variant. The serial protocol is identical across variants and the
# pump cannot report which one it is — declare it, like the syringe
# volume. Declared wrong, every move is scaled by the ratio, silently.
#
#   standard:    PSD/4 and PSD/6 (the high-torque drive) — manual
#                8892-01. Same scales for both; PSD/6 differs in force,
#                not steps.
#   smooth_flow: PSD/4 SF / PSD/6 SF (PN 97709-xx) — 8x the
#                microstepping over the same stroke.
VARIANTS = {
    "standard":    (3000, 24000),
    "smooth_flow": (24000, 192000),
}


class PSD4Error(RuntimeError):
    """The pump reported a non-zero error status, or refused a command."""


@dataclass
class Status:
    """Decoded Pump Status byte."""
    ready: bool
    error: int
    raw: str

    @property
    def ok(self) -> bool:
        return self.error == 0

    @property
    def busy(self) -> bool:
        return not self.ready

    @property
    def error_text(self) -> str:
        return ERRORS.get(self.error, f"unknown error {self.error}")

    def __str__(self) -> str:
        state = "ready" if self.ready else "busy"
        return state if self.ok else f"{state}, error: {self.error_text}"


class PSD4:
    """Hamilton PSD/4 over RS-232 / RS-485.

    Args:
        port: serial port (use the ``/dev/serial/by-id/...`` symlink).
        address: rotary-switch position (int 0-9 or "A"-"F"), or the
            address character itself. Switch 0 is the factory default
            and maps to "1".
        baud: 9600 (DIP 3 off, factory) or 38400 (DIP 3 on).
        timeout: per-command read deadline, seconds.
        syringe_volume_ul: volume of the INSTALLED syringe. The pump
            has no way to report this — get it wrong and every
            volumetric call is wrong by the same ratio.
        high_resolution: True if the syringe is in high-resolution mode;
            what that means in steps depends on ``variant``.
        variant: which pump this is — a key of ``VARIANTS``
            (``"standard"`` for PSD/4 / PSD/6 high-torque,
            ``"smooth_flow"`` for the SF drives). Like the syringe
            volume the pump cannot report it; declared wrong, every
            move is scaled by the ratio between the variants' step
            counts, silently.
    """

    def __init__(
        self,
        port: str = "",
        address=0,
        baud: int = 9600,
        timeout: float = 2.0,
        syringe_volume_ul: float = 1000.0,
        high_resolution: bool = False,
        variant: str = "smooth_flow",
    ):
        if variant not in VARIANTS:
            raise PSD4Error(
                f"unknown pump variant {variant!r} — one of {sorted(VARIANTS)}"
            )
        self.port = port or ""
        self.baud = int(baud)
        self.timeout = float(timeout)
        self.syringe_volume_ul = float(syringe_volume_ul)
        self.high_resolution = bool(high_resolution)
        self.variant = variant
        self.ser: Optional[serial.Serial] = None

        # Accept either a switch position or a literal address char.
        if isinstance(address, str) and address not in ADDRESS_CHARS:
            self.address = address
        else:
            self.address = ADDRESS_CHARS.get(address, "1")

    # ==================================================
    # Connection lifecycle
    # ==================================================

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self) -> bool:
        if self.is_connected():
            return True
        try:
            self.ser = serial.Serial(
                self.port, self.baud, bytesize=8,
                parity="N", stopbits=1, timeout=self.timeout,
            )
        except serial.SerialException:
            self.ser = None
            return False
        self._drain()
        return True

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            finally:
                self.ser = None

    def _drain(self, window: float = 0.2) -> bytes:
        """Discard anything already queued. An aborted move can leave a
        stale reply in the buffer that would otherwise be read as the
        answer to the next command."""
        if self.ser is None:
            return b""
        old, drained = self.ser.timeout, b""
        try:
            self.ser.timeout = window
            while True:
                d = self.ser.read(256)
                if not d:
                    break
                drained += d
                if len(drained) > 4096:
                    break
        except serial.SerialException:
            pass
        finally:
            try:
                self.ser.timeout = old
            except serial.SerialException:
                pass
        return drained

    # ==================================================
    # Low-level I/O
    # ==================================================

    @property
    def max_steps(self) -> int:
        return VARIANTS[self.variant][1 if self.high_resolution else 0]

    def _exchange(self, data: str, read_timeout: Optional[float] = None) -> tuple[Status, str]:
        """Send one data block, return ``(status, payload)``.

        Raises PSD4Error when the pump says nothing in time — a silent
        pump is a dropped link, never a zero reading.
        """
        if not self.is_connected():
            raise PSD4Error("not connected")
        deadline = time.monotonic() + (
            self.timeout if read_timeout is None else float(read_timeout)
        )
        self.ser.reset_input_buffer()
        self.ser.write(f"/{self.address}{data}\r".encode("ascii"))

        # Replies end in <CR><LF>; read until LF so a payload containing
        # a CR can't cut the line short.
        buf = b""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PSD4Error(f"no response to {data!r} — pump silent/disconnected")
            self.ser.timeout = remaining
            chunk = self.ser.read_until(b"\n")
            if not chunk:
                raise PSD4Error(f"no response to {data!r} — pump silent/disconnected")
            buf += chunk
            if buf.endswith(b"\n"):
                break

        line = buf.decode("ascii", errors="ignore").strip("\r\n")
        # Expected: /0<status><payload>. Anything shorter is a framing
        # failure, usually a baud or half-duplex-direction problem.
        if len(line) < 3 or not line.startswith("/"):
            raise PSD4Error(f"malformed reply to {data!r}: {line!r}")
        status_byte = ord(line[2])
        # Real firmware (DV01.00.00) terminates every payload with ETX
        # even in Terminal Protocol, which the manual documents as
        # CR/LF-framed only. Left in, that byte rides along into every
        # numeric query and int() blows up on "1400\x03". Strip the
        # Standard-Protocol framing characters here, once, so no caller
        # has to know.
        payload = line[3:].strip("\x02\x03").strip()
        return (
            Status(ready=bool(status_byte & 0x20), error=status_byte & 0x0F, raw=line),
            payload,
        )

    def command(self, cmd: str, wait: bool = True, timeout: Optional[float] = None) -> str:
        """Queue ``cmd`` in the command buffer and execute it (appends
        the ``R`` control command).

        ``wait=True`` blocks until the pump reports ready — the move is
        actually finished. ``wait=False`` returns as soon as the pump
        accepts the buffer, so you can poll ``status()`` yourself or
        fire an async ``T``/``V`` at it mid-move.
        """
        status, payload = self._exchange(f"{cmd}R")
        if not status.ok:
            raise PSD4Error(f"{cmd!r} rejected: {status.error_text}")
        if wait:
            self.wait_ready(timeout=timeout)
        return payload

    def query(self, cmd: str) -> str:
        """Run a query command. Queries need no ``R`` and work while the
        pump is busy (manual §8-20)."""
        status, payload = self._exchange(cmd)
        if not status.ok and status.error != 15:   # 15 = busy, fine for a query
            raise PSD4Error(f"query {cmd!r} failed: {status.error_text}")
        return payload

    def wait_ready(self, timeout: Optional[float] = None, poll: float = 0.1) -> Status:
        """Poll until the move has actually finished.

        Done means BOTH of, on the same poll:

        * the ``Q`` ready bit is set, and
        * the plunger position (``?``) matches the previous poll's.

        The ready bit alone is NOT trustworthy on this SF firmware:
        observed reading ready mid-travel, which let follow-up commands
        fire early and bounce off "pump busy" while the plunger was
        still moving. Position queries are legal while busy (§8-20), so
        motion itself is the arbiter.

        The deadline is progress-based, not a stroke-time estimate: a
        changing position proves the move is alive and resets the
        clock, so no speed model is needed and a 4800 s crawl is never
        cut off. ``timeout`` bounds how long the plunger may sit STILL
        without reporting ready before we call the move lost.

        Valve-only moves rely on the ready bit alone — ``?`` covers the
        plunger, and the valve-angle query is h-factor-gated, which
        this poll cannot assume (it runs during ``enable_h_factor``
        itself). No early-ready has been observed on valve moves.
        """
        idle_limit = 10.0 if timeout is None else float(timeout)
        last_pos: Optional[int] = None
        last_progress = time.monotonic()
        while True:
            st = self.status()
            if not st.ok:
                raise PSD4Error(f"pump error while moving: {st.error_text}")
            pos = int(self.query("?"))
            if pos != last_pos:
                last_pos = pos
                last_progress = time.monotonic()
            elif st.ready:
                return st
            if time.monotonic() - last_progress > idle_limit:
                raise PSD4Error(
                    "plunger idle without reporting ready — move stalled or lost"
                )
            time.sleep(poll)

    # ==================================================
    # Identity / status
    # ==================================================

    def status(self) -> Status:
        """``Q`` — ready/busy plus the last error. Note the pump CLEARS
        its error status once it reports it."""
        st, _ = self._exchange("Q")
        return st

    def check_connection(self) -> bool:
        """True only if the pump actually answered — port open AND
        talking. Uses the firmware-version query, which is harmless and
        works while busy."""
        try:
            return bool(self.firmware_version())
        except PSD4Error:
            return False

    def firmware_version(self) -> str:
        """``&`` — firmware revision string."""
        return self.query("&")

    def firmware_checksum(self) -> str:
        """``#`` — 4-digit hex checksum."""
        return self.query("#")

    def buffer_empty(self) -> bool:
        """``F`` — 0 empty, 1 not empty."""
        return self.query("F").strip() == "0"

    # ==================================================
    # Initialization
    # ==================================================
    # Z / Y / W differ only in which side the OUTPUT ends up on, and
    # whether a valve is configured at all. Everything else — plunger
    # to home, valve to a known position — is common.

    def initialize(self, output_right: bool = True, half_force: bool = False,
                   speed_code: Optional[int] = None, timeout: Optional[float] = None) -> str:
        """``Z``/``Y`` — home the plunger and valve.

        output_right: True -> ``Z`` (output on the right, input left).
                      False -> ``Y`` (input on the left inverted: output
                      left, input right).
        half_force:   True initializes at half plunger force — use it
                      with fragile or small syringes.
        speed_code:   optional 10-40 initialization speed.

        Must be run after every power-up, and again after any stall or
        terminated move: the pump refuses syringe moves with error 7
        until it has homed.
        """
        letter = "Z" if output_right else "Y"
        param = "1" if half_force else "0"
        if speed_code is not None:
            param = str(int(speed_code))
        return self.command(f"{letter}{param}", timeout=timeout if timeout is not None else 60.0)

    def initialize_no_valve(self, half_force: bool = False, timeout: float = 60.0) -> str:
        """``W`` — initialize with no valve configured (syringe only)."""
        return self.command(f"W{'1' if half_force else '0'}", timeout=timeout)

    def initialize_syringe_only(self, speed_code: int = 0, timeout: float = 60.0) -> str:
        """``h100xx`` — home the plunger WITHOUT disabling the valve,
        unlike ``W`` (manual §9-3). Requires h-factor commands enabled."""
        return self.command(f"h{10000 + int(speed_code):05d}", timeout=timeout)

    def initialize_valve(self, timeout: float = 30.0) -> str:
        """``h20000`` — initialize the valve only."""
        return self.command("h20000", timeout=timeout)

    def reset(self) -> str:
        """``h30003`` — reset the pump to power-up defaults."""
        return self.command("h30003", wait=False)

    def syringe_mode(self) -> dict:
        """``?11000`` — decoded syringe mode bit field (manual §9-6)."""
        v = int(self.query("?11000"))
        return {
            "high_resolution":  bool(v & 1),
            "ignore_overload":  bool(v & 2),
            "home_sensor_off":  bool(v & 4),
            "initialize_off":   bool(v & 8),
            "raw": v,
        }

    def set_resolution(self, high_resolution: bool) -> str:
        """``h110xx`` — switch between standard and high resolution;
        what each means in steps per stroke depends on the variant
        (``VARIANTS``).

        Unlike the valve type, this has **no DIP switch** — the command
        is the only way to set it, and the pump keeps it. Re-initialize
        afterwards: the step scale has changed underneath any position
        the pump is currently reporting.

        Reads the current mode first so the other flags in the same
        register (overload handling, home sensor, initialize enable) are
        preserved rather than cleared.
        """
        flags = int(self.query("?11000"))
        flags = (flags & ~1) | (1 if high_resolution else 0)
        out = self.command(f"h{11000 + flags:05d}")
        self.high_resolution = bool(high_resolution)
        return out

    # ==================================================
    # h Factor commands
    # ==================================================
    # The extended command set — multi-port valving, digital I/O,
    # per-subsystem status. OFF at power-up; nothing with an ``h``
    # prefix works until this is sent (manual §9-2).

    def enable_h_factor(self, on: bool = True) -> str:
        return self.command("h30001" if on else "h30000")

    # ==================================================
    # Valve
    # ==================================================

    def valve(self, position: str, shortest: bool = False, timeout: float = 10.0) -> str:
        """Move the valve to a LOGICAL position.

        position: input / output / bypass / extra / wash / return.
        shortest: take the shortest route in degrees (h-factor). The
            plain commands rotate the valve its own default way, which
            is what you want when a fixed path matters for carryover;
            shortest is faster.

        ``wash`` and ``return`` have no plain single-letter command and
        are always routed through the h-factor form, so they need
        h-factor enabled. Not every valve has every position — a 3-port
        distribution valve answers "invalid command" for bypass.
        """
        key = str(position).lower()
        if key in VALVE_COMMANDS and not shortest:
            return self.command(VALVE_COMMANDS[key], timeout=timeout)
        if key in VALVE_LOGICAL_H:
            return self.command(f"h{VALVE_LOGICAL_H[key]:05d}", timeout=timeout)
        raise PSD4Error(
            f"unknown valve position {position!r}; expected one of {sorted(VALVE_LOGICAL_H)}"
        )

    def valve_port(self, port: int, direction: str = "shortest", timeout: float = 10.0) -> str:
        """Move a multi-port distribution valve to NUMBERED port 1-8.

        direction: "shortest" (``h2600x``), "cw" (``h2400x``) or "ccw"
        (``h2500x``). Shortest is fastest; pick a fixed direction when
        carryover matters and you want the path to be repeatable.

        Requires h-factor commands enabled and a distribution valve
        configured — DIP switches 4-6.
        """
        port = int(port)
        if not 1 <= port <= 8:
            raise PSD4Error(f"valve port must be 1-8, got {port}")
        base = {"shortest": 26000, "cw": 24000, "ccw": 25000}.get(str(direction).lower())
        if base is None:
            raise PSD4Error(f"direction must be shortest / cw / ccw, got {direction!r}")
        return self.command(f"h{base + port:05d}", timeout=timeout)

    def move_valve_angle(self, degrees: int, direction: str = "shortest", timeout: float = 10.0) -> str:
        """Rotate the valve to an absolute angle in 15° increments
        (0-345). ``h27xxx`` cw / ``h28xxx`` ccw / ``h29xxx`` shortest.

        The escape hatch for a valve whose ports the logical and
        numbered commands don't describe — you address the geometry
        directly.
        """
        degrees = int(degrees)
        if not 0 <= degrees <= 345 or degrees % 15:
            raise PSD4Error(f"angle must be 0-345 in 15° steps, got {degrees}")
        base = {"shortest": 29000, "cw": 27000, "ccw": 28000}.get(str(direction).lower())
        if base is None:
            raise PSD4Error(f"direction must be shortest / cw / ccw, got {direction!r}")
        return self.command(f"h{base + degrees:05d}", timeout=timeout)

    def valve_type(self) -> int:
        """``?21000`` — the configured valve type."""
        return int(self.query("?21000"))

    def valve_position(self) -> int:
        """``?24000`` — numerical port 1-8 (0 = not at a numbered port)."""
        return int(self.query("?24000"))

    def valve_logical_position(self) -> str:
        """``?23000`` — input / output / wash / return / bypass / extra."""
        return VALVE_LOGICAL.get(int(self.query("?23000")), "unknown")

    def valve_angle(self) -> int:
        """``?25000`` — the valve's actual angle in degrees (0-345).

        The one valve reading that is a real measurement rather than a
        declared setting: it comes off the valve drive's optical
        encoder. Use it to check a valve's geometry against its
        configured type — logical positions sit 90° apart on a 3-way
        90° valve, 120° apart on a Y valve.
        """
        return int(self.query("?25000"))

    def valve_status(self) -> dict:
        """``?20000`` — decoded valve status bit field (manual §9-7)."""
        v = int(self.query("?20000"))
        return {
            "initialized":       not bool(v & 1),
            "init_error":        bool(v & 2),
            "stall":             bool(v & 4),
            "movement_enabled":  not bool(v & 16),
            "busy":              bool(v & 32),
            "raw":               v,
        }

    def enable_valve_movement(self, on: bool = True) -> str:
        """``h20001`` / ``h20002`` — allow or ignore valve moves. Useful
        to hard-stop valve motion during a fault without cutting power."""
        return self.command("h20001" if on else "h20002")

    # ==================================================
    # Syringe — steps
    # ==================================================

    def _check_steps(self, steps: int) -> int:
        steps = int(round(steps))
        if not 0 <= steps <= self.max_steps:
            raise PSD4Error(
                f"position {steps} out of range 0-{self.max_steps} "
                f"({'high' if self.high_resolution else 'standard'} resolution)"
            )
        return steps

    def _check_relative(self, steps: int, sign: int) -> int:
        """Validate a RELATIVE move against where the plunger actually is.

        Dispensing below 0 is refused by the pump itself ("parameter
        out of range", measured on firmware DV01.00.00). Whether a
        pickup past genuine full stroke is refused the same way has NOT
        been verified — the one measurement that suggested it isn't was
        taken while the driver had the SF step scale wrong by 8x, so
        the "overshoot" never actually left the real stroke.

        So the check lives here. One position query per relative move,
        which is cheap next to driving a syringe into its hard limit.
        """
        steps = int(round(steps))
        if steps < 0:
            raise PSD4Error(f"relative move must be positive, got {steps}")
        if steps > self.max_steps:
            raise PSD4Error(f"relative move {steps} exceeds the full stroke {self.max_steps}")
        held = self.position_steps()
        target = held + sign * steps
        if not 0 <= target <= self.max_steps:
            verb = "aspirate" if sign > 0 else "dispense"
            raise PSD4Error(
                f"{verb} of {self.steps_to_ul(steps):.1f} µL would move the plunger to "
                f"{target} steps, outside 0-{self.max_steps} — currently holding "
                f"{self.steps_to_ul(held):.1f} µL of a {self.syringe_volume_ul:.0f} µL syringe"
            )
        return steps

    def move_to_steps(self, steps: int, wait: bool = True, timeout: Optional[float] = None) -> str:
        """``Ax`` — absolute plunger move."""
        return self.command(f"A{self._check_steps(steps)}", wait=wait, timeout=timeout)

    def pickup_steps(self, steps: int, wait: bool = True, timeout: Optional[float] = None) -> str:
        """``Px`` — relative aspirate. Refuses to overfill (the pump won't)."""
        return self.command(f"P{self._check_relative(steps, +1)}", wait=wait, timeout=timeout)

    def dispense_steps(self, steps: int, wait: bool = True, timeout: Optional[float] = None) -> str:
        """``Dx`` — relative dispense. Refuses to push past empty."""
        return self.command(f"D{self._check_relative(steps, -1)}", wait=wait, timeout=timeout)

    def position_steps(self) -> int:
        """``?`` — commanded plunger position."""
        return int(self.query("?"))

    def actual_position_steps(self) -> int:
        """``?4`` — documented as the encoder-measured position, where
        diverging from ``?`` would catch a stall.

        Agreed with ``?`` at every position measured so far — but all
        of those measurements were taken with the SF step scale wrong
        by 8x, i.e. within the first 12.5% of the real stroke and never
        actually stalled. Whether it diverges on a genuine stall is
        unverified; don't build a stall check on it without forcing a
        stall and watching it once.
        """
        return int(self.query("?4"))

    def reset_counter(self) -> str:
        """``z`` — reset the syringe counter position."""
        return self.command("z")

    # ==================================================
    # Syringe — volumes
    # ==================================================
    # Everything above speaks steps. Protocols speak µL. One conversion,
    # in one place, driven by the declared syringe size.

    def ul_to_steps(self, ul: float) -> int:
        return int(round(float(ul) / self.syringe_volume_ul * self.max_steps))

    def steps_to_ul(self, steps: float) -> float:
        return float(steps) / self.max_steps * self.syringe_volume_ul

    def aspirate(self, volume_ul: float, wait: bool = True, timeout: Optional[float] = None) -> str:
        """Draw ``volume_ul`` in. The valve must already be at the port
        you want to draw from — this moves the plunger only."""
        return self.pickup_steps(self.ul_to_steps(volume_ul), wait=wait, timeout=timeout)

    def dispense(self, volume_ul: float, wait: bool = True, timeout: Optional[float] = None) -> str:
        """Push ``volume_ul`` out through the current valve port."""
        return self.dispense_steps(self.ul_to_steps(volume_ul), wait=wait, timeout=timeout)

    def move_to_volume(self, volume_ul: float, wait: bool = True, timeout: Optional[float] = None) -> str:
        """Absolute: leave exactly ``volume_ul`` in the syringe."""
        return self.move_to_steps(self.ul_to_steps(volume_ul), wait=wait, timeout=timeout)

    def empty(self, wait: bool = True, timeout: Optional[float] = None) -> str:
        """Drive the plunger fully home (position 0)."""
        return self.move_to_steps(0, wait=wait, timeout=timeout)

    def position_ul(self) -> float:
        """How much is currently held, in µL."""
        return self.steps_to_ul(self.position_steps())

    # ==================================================
    # Speed
    # ==================================================

    def set_speed_code(self, code: int) -> str:
        """``Sx`` — the pump's raw preset speed, 1 (fastest, 1.2 s per
        full stroke) to 40 (slowest, 600 s). The pump has no continuous
        speed setting; these 40 presets are it. Prefer
        :meth:`set_speed`, which takes the 0-100 scale."""
        code = int(code)
        if code not in SPEED_CODES:
            raise PSD4Error("speed code must be 1-40")
        return self.command(f"S{code}")

    def set_speed(self, percent: float) -> int:
        """Plunger speed, 0-100: 100 = the pump's fastest preset, 0 =
        its slowest, spread over the 40 codes in between. Snaps to a
        preset and returns the code used. Normalized: the same number
        means the same relative speed on any variant in any resolution
        mode, because it addresses the preset ladder, not wall-clock
        time.

        For orientation in seconds see SPEED_CODES — on an SF drive a
        full stroke at 100 measured ~2.3 s in standard resolution, 8x
        that (~18 s) in high resolution. The ladder is roughly
        geometric, so mid-scale sits much closer to the slow end."""
        p = min(100.0, max(0.0, float(percent)))
        code = 1 + round((100.0 - p) / 100.0 * 39)
        self.set_speed_code(code)
        return code

    def set_start_velocity(self, steps_per_sec: int) -> str:
        """``vx`` — 50-1000 motor steps/s."""
        return self.command(f"v{int(steps_per_sec)}")

    def set_max_velocity(self, steps_per_sec: int) -> str:
        """``Vx`` — 5-5800 motor steps/s. Also valid mid-move as an
        async on-the-fly speed change (see :meth:`change_speed`)."""
        return self.command(f"V{int(steps_per_sec)}")

    def set_stop_velocity(self, steps_per_sec: int) -> str:
        """``cx`` — 50-2700 motor steps/s. Setting it zeroes the cutoff
        steps."""
        return self.command(f"c{int(steps_per_sec)}")

    def set_return_steps(self, steps: int) -> str:
        """``Kx`` — backlash compensation, 0-31 standard / 0-248 high
        res. Applied at the end of a dispense."""
        return self.command(f"K{int(steps)}")

    def set_backoff_steps(self, steps: int) -> str:
        """``kx`` — 0-120 standard / 0-640 high res, backed off after
        initialization."""
        return self.command(f"k{int(steps)}")

    def velocities(self) -> dict:
        """``?1`` / ``?2`` / ``?3`` — start, maximum and stop velocity."""
        return {
            "start": int(self.query("?1")),
            "max":   int(self.query("?2")),
            "stop":  int(self.query("?3")),
        }

    # ==================================================
    # Digital I/O
    # ==================================================

    def digital_out(self, value: int) -> str:
        """``Jx`` — set the three auxiliary output lines, 0-7 as a
        3-bit field."""
        value = int(value)
        if not 0 <= value <= 7:
            raise PSD4Error("digital out value must be 0-7")
        return self.command(f"J{value}")

    def last_digital_out(self) -> int:
        """``?37000`` — last value written to the aux outputs."""
        return int(self.query("?37000"))

    def aux_input(self, index: int = 1) -> int:
        """``?13`` / ``?14`` — auxiliary input 1 or 2. 0 low, 1 high."""
        if index not in (1, 2):
            raise PSD4Error("auxiliary input index must be 1 or 2")
        return int(self.query("?13" if index == 1 else "?14"))

    # ==================================================
    # Command strings / flow control
    # ==================================================
    # The pump can run a whole sequence on its own — loops included —
    # which beats round-tripping every step over the wire.

    def run_sequence(self, command_string: str, wait: bool = True,
                     timeout: Optional[float] = None) -> str:
        """Execute a raw command string, e.g. ``"IP3000OD3000G10"`` —
        fill, empty, ten times. ``g`` marks a loop start and ``Gx``
        repeats; ``Mx`` delays x ms; ``Hx`` waits for an input."""
        return self.command(command_string, wait=wait, timeout=timeout)

    def terminate(self) -> str:
        """``T`` — async stop. Aborts the command buffer and the move in
        progress (valve moves finish first).

        The manual is emphatic: terminating a syringe move mid-stroke
        can lose steps, so RE-INITIALIZE afterwards before trusting any
        position.
        """
        status, payload = self._exchange("T")
        if not status.ok and status.error != 15:
            raise PSD4Error(f"terminate rejected: {status.error_text}")
        return payload

    def resume(self) -> str:
        """``R`` on its own — resume a buffer stopped by ``T`` or ``H``."""
        status, payload = self._exchange("R")
        if not status.ok:
            raise PSD4Error(f"resume rejected: {status.error_text}")
        return payload

    def syringe_status(self) -> dict:
        """``?10000`` — decoded syringe status bit field (manual §9-6)."""
        v = int(self.query("?10000"))
        return {
            "initialized": not bool(v & 1),
            "stall":       bool(v & 6),
            "init_error":  bool(v & 8),
            "raw":         v,
        }

    def home_sensor(self) -> bool:
        """``?10001`` — documented as True when the plunger is in the
        home region.

        **Measured `1` at every position tried so far** — but those
        measurements (0, 12000, 24000 steps) were taken with the SF
        step scale wrong by 8x, so they all sat within the first 12.5%
        of the real stroke, plausibly all inside the home region. It
        may well vary over a genuine full stroke. Do not build a check
        on it without re-measuring at true full extension.
        """
        return self.query("?10001").strip() == "1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def list_ports() -> list[dict]:
    """Enumerate serial ports — handy for finding the RS-485 adapter."""
    return [
        {
            "port": info.device,
            "description": info.description or "",
            "vid": f"{info.vid:04x}" if info.vid else "",
        }
        for info in serial.tools.list_ports.comports()
    ]
