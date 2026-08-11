from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import serial
import serial.tools.list_ports


@dataclass
class Reading:
    status: str               # "ok" | "error" | "disconnected"
    ph: Optional[float]       # None when not a numeric reading
    raw: str

    @property
    def connected(self) -> bool:
        return self.status != "disconnected"

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.ph is not None

    def __str__(self) -> str:
        if not self.connected:
            return "DISCONNECTED"
        if self.ph is None:
            return self.status
        return f"pH {self.ph:.3f}"


@dataclass
class Slope:
    """Probe health from ``Slope,?``. A healthy Lab Grade probe reads close
    to 100% on both sides; below ~90% the probe is aging and below ~80%
    it should be replaced (or at minimum recalibrated)."""
    acid_percent: float       # slope of the low (acid) side vs ideal, %
    base_percent: float       # slope of the high (base) side vs ideal, %
    offset_mv: float          # zero-point offset in mV (ideal probe = 0)
    raw: str

    @property
    def healthy(self) -> bool:
        return self.acid_percent >= 90.0 and self.base_percent >= 90.0

    def __str__(self) -> str:
        return (f"acid {self.acid_percent:.1f}% / base {self.base_percent:.1f}% "
                f"/ offset {self.offset_mv:+.2f} mV")


# EZO response codes that terminate a reply. Anything else after '*' is
# still treated as a terminator so an unknown firmware code can't hang us.
_CODES = {
    "*OK": "success",
    "*ER": "unknown command / syntax error",
    "*OV": "over volt (Vcc > 5.5 V)",
    "*UV": "under volt (Vcc < 3.1 V)",
    "*RS": "device reset",
    "*RE": "boot complete",
    "*SL": "entering sleep",
    "*WA": "waking up",
}

# Datasheet processing latency: R and Cal take ~900 ms, everything else
# ~300 ms. The per-command read deadline must comfortably exceed these.
_SLOW_CMDS = ("R", "RT", "Cal")


class AtlasPH:
    """Atlas Scientific EZO-pH circuit (Lab Grade probe) over UART.

    Works unchanged on Windows (``COM16``) and Raspberry Pi
    (``/dev/ttyUSB0`` or ``/dev/serial0``) — only the port string differs.

    Protocol: ASCII commands terminated with ``\\r`` at 9600 8N1. Replies
    are data line(s) followed by a response code (``*OK`` / ``*ER`` / …).
    ``connect()`` disables the factory-default continuous streaming mode
    (``C,0``) and drains the buffer, so every read afterwards is an
    explicit request/response.
    """

    def __init__(self, port: str = "COM16", baud: int = 9600, timeout: float = 2.0):
        self.port = port
        self.baud = baud
        self.timeout = float(timeout)
        self.ser: Optional[serial.Serial] = None

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
        # EZO circuits ship with continuous mode ON — the chip streams a
        # reading every second unprompted. Left running, those stream
        # lines interleave with command replies and every _query returns
        # a stale pH instead of its own answer. Stop the stream first,
        # then drain whatever was already queued.
        self._send("C,0")
        time.sleep(0.3)
        self._drain()
        return True

    def _drain(self, window: float = 0.4) -> bytes:
        """Read and discard whatever is already queued, up to ``window``
        seconds of silence. Safe when nothing is queued."""
        if self.ser is None:
            return b""
        old = self.ser.timeout
        drained = b""
        try:
            self.ser.timeout = window
            while True:
                d = self.ser.read(256)
                if not d:
                    break
                drained += d
                if len(drained) > 4096:   # safety cap — don't spin forever
                    break
        except serial.SerialException:
            pass
        finally:
            try:
                self.ser.timeout = old
            except serial.SerialException:
                pass
        return drained

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            finally:
                self.ser = None

    # ==================================================
    # Low-level I/O
    # ==================================================

    def _send(self, cmd: str) -> None:
        if not self.is_connected():
            return
        self.ser.write((cmd + "\r").encode("ascii"))

    def _query(self, cmd: str, read_timeout: Optional[float] = None) -> tuple[list[str], str]:
        """Send one command; return ``(data_lines, code)``.

        ``code`` is the terminating response code (``*OK`` etc.), or ``""``
        when the device said nothing in time — which callers read as a
        dropped link (NOT a zero reading). EZO lines end with ``\\r``
        only, so this reads with ``read_until(b'\\r')``, never readline().
        """
        if not self.is_connected():
            return [], ""
        deadline = time.monotonic() + (
            self.timeout if read_timeout is None else float(read_timeout)
        )
        self.ser.reset_input_buffer()
        self._send(cmd)
        lines: list[str] = []
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return lines, ""
            self.ser.timeout = remaining
            raw = self.ser.read_until(b"\r")
            if not raw.endswith(b"\r"):
                return lines, ""          # timed out mid-line — silent device
            line = raw.decode("ascii", errors="ignore").strip()
            if not line:
                continue
            if line.startswith("*"):
                if line == "*WA":         # wake echo from sleep — not ours
                    continue
                return lines, line
            lines.append(line)

    def _command(self, cmd: str, read_timeout: Optional[float] = None) -> list[str]:
        """_query + raise on device error. Returns the data lines."""
        to = read_timeout
        if to is None and cmd.split(",")[0] in _SLOW_CMDS:
            to = max(self.timeout, 2.0)
        lines, code = self._query(cmd, read_timeout=to)
        if code == "" and not lines:
            raise RuntimeError(f"No response to {cmd!r} — device silent/disconnected")
        if code not in ("", "*OK"):
            raise RuntimeError(f"{cmd!r} failed: {code} ({_CODES.get(code, 'unknown code')})")
        return lines

    # ==================================================
    # Identity / handshake
    # ==================================================

    def info(self) -> str:
        """``i`` — device type + firmware, e.g. 'pH,2.14'. "" if silent."""
        lines, _ = self._query("i")
        for ln in lines:
            if ln.startswith("?I,") or ln.startswith("?i,"):
                return ln.split(",", 1)[1]
        return ""

    def check_connection(self) -> bool:
        """True only if the circuit actually answered — port open AND talking."""
        return bool(self.info())

    def status(self) -> dict:
        """``Status`` — last restart reason + supply voltage."""
        reasons = {"P": "powered off", "S": "software reset", "B": "brown out",
                   "W": "watchdog", "U": "unknown"}
        lines, _ = self._query("Status")
        for ln in lines:
            if ln.upper().startswith("?STATUS,"):
                _, code, volts = ln.split(",")
                return {"restart": reasons.get(code, code), "vcc": float(volts)}
        return {}

    # ==================================================
    # Reading
    # ==================================================

    def _parse_ph(self, lines: list[str], code: str) -> Reading:
        raw = " | ".join(lines) if lines else code
        if not lines and not code:
            return Reading("disconnected", None, raw)
        for ln in lines:
            try:
                return Reading("ok", float(ln), raw)
            except ValueError:
                continue
        return Reading("error", None, raw)

    def read(self) -> Reading:
        """Single reading (``R``). Takes ~900 ms on-chip."""
        lines, code = self._query("R", read_timeout=max(self.timeout, 2.0))
        return self._parse_ph(lines, code)

    def read_at_temperature(self, celsius: float) -> Reading:
        """``RT,t`` — set temperature compensation and read in one call.
        Use this when the sample temp differs from the stored T value."""
        lines, code = self._query(f"RT,{celsius:.2f}", read_timeout=max(self.timeout, 2.0))
        return self._parse_ph(lines, code)

    def read_stable(self, n: int = 3, tolerance: float = 0.02,
                    max_readings: int = 20) -> Reading:
        """Poll until ``n`` consecutive readings agree within ``tolerance``
        pH units (probe has settled), or give up after ``max_readings``.
        Returns the last reading either way — check ``.ok`` and compare
        expectations yourself if you need a hard guarantee."""
        window: list[float] = []
        last = Reading("disconnected", None, "")
        for _ in range(max_readings):
            last = self.read()
            if not last.ok:
                if not last.connected:
                    return last
                continue
            window.append(last.ph)
            window = window[-n:]
            if len(window) == n and max(window) - min(window) <= tolerance:
                return last
        return last

    # ==================================================
    # Calibration
    # ==================================================
    # EZO-pH supports 1/2/3-point calibration with any standard buffers.
    # RULES the chip enforces:
    #   - Cal,mid (the ~7 buffer) must be FIRST — issuing it wipes any
    #     existing calibration.
    #   - Cal,low (~4) and Cal,high (~10) come after mid, either order.
    # So a full 3-point session is: calibrate(7.00) → rinse →
    # calibrate(4.00) → rinse → calibrate(10.00).

    def calibrate(self, value: float) -> str:
        """One-call calibration: pick the point from the buffer value.

        <= 5 → low, 5–9 → mid, >= 9 → high. Let the probe sit in the
        buffer until readings settle (use ``read_stable()``) before
        calling. Returns the point that was calibrated.
        """
        if value <= 5.0:
            point = "low"
        elif value < 9.0:
            point = "mid"
        else:
            point = "high"
        if point != "mid" and self.calibration_points() == 0:
            raise RuntimeError(
                f"Cannot calibrate {point} first — the chip requires the mid "
                f"point (pH ~7 buffer) before low/high. Run calibrate(7.00) first."
            )
        self._command(f"Cal,{point},{value:.2f}")
        return point

    def calibration_points(self) -> int:
        """``Cal,?`` — number of stored calibration points (0–3)."""
        lines, _ = self._query("Cal,?")
        for ln in lines:
            if ln.upper().startswith("?CAL,"):
                return int(ln.split(",")[1])
        return 0

    def calibrate_clear(self) -> None:
        """Wipe all stored calibration points (``Cal,clear``)."""
        self._command("Cal,clear")

    def slope(self) -> Optional[Slope]:
        """``Slope,?`` — probe health vs an ideal probe. None if silent."""
        lines, _ = self._query("Slope,?")
        for ln in lines:
            if ln.upper().startswith("?SLOPE,"):
                parts = ln.split(",")
                return Slope(float(parts[1]), float(parts[2]), float(parts[3]), ln)
        return None

    def export_calibration(self) -> list[str]:
        """Dump the calibration as importable strings — save these and
        ``import_calibration()`` them on the Pi to skip re-buffering."""
        lines, _ = self._query("Export,?")
        count = 0
        for ln in lines:
            if ln.upper().startswith("?EXPORT,"):
                count = int(ln.split(",")[1])
        out: list[str] = []
        for _ in range(count):
            data = self._command("Export")
            out.extend(data)
        # The chip terminates the dump with a literal *DONE data line.
        self._command("Export")
        return [ln for ln in out if ln != "*DONE"]

    def import_calibration(self, exported: list[str]) -> None:
        """Load a calibration previously dumped by ``export_calibration()``."""
        for ln in exported:
            self._command(f"Import,{ln}")
            time.sleep(0.3)

    # ==================================================
    # Temperature compensation
    # ==================================================
    # pH readings shift with temperature; the chip corrects using this
    # value (default 25 °C). Set it to your sample temp for accurate work.

    def set_temperature_compensation(self, celsius: float) -> None:
        self._command(f"T,{celsius:.2f}")

    def get_temperature_compensation(self) -> Optional[float]:
        lines, _ = self._query("T,?")
        for ln in lines:
            if ln.upper().startswith("?T,"):
                return float(ln.split(",")[1])
        return None

    # ==================================================
    # Housekeeping
    # ==================================================

    def led(self, on: bool) -> None:
        """Status LED on the EZO circuit (``L,1`` / ``L,0``)."""
        self._command(f"L,{1 if on else 0}")

    def find(self) -> None:
        """Blink the LED rapidly white — locate this circuit on a bench
        with several EZO devices. Any next command stops it."""
        self._send("Find")

    def set_name(self, name: str) -> None:
        self._command(f"Name,{name}")

    def get_name(self) -> str:
        lines, _ = self._query("Name,?")
        for ln in lines:
            if ln.upper().startswith("?NAME,"):
                return ln.split(",", 1)[1]
        return ""

    def sleep(self) -> None:
        """Low-power standby. The next command wakes it (that command is
        consumed by the wake — the wrapper's _query retries transparently
        on the *WA echo, but give it one throwaway ``info()`` after wake)."""
        self._send("Sleep")

    def factory_reset(self) -> None:
        """Full factory reset — wipes calibration, name, LED state. The
        chip reboots; reconnect after."""
        self._send("Factory")
        time.sleep(1.0)
        self._drain()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def list_ports() -> list[dict]:
    """Enumerate serial ports — handy for finding the EZO on a new machine."""
    results = []
    for info in serial.tools.list_ports.comports():
        results.append({
            "port": info.device,
            "description": info.description or "",
            "vid": f"{info.vid:04x}" if info.vid else "",
        })
    return results
