from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import serial
import serial.tools.list_ports


@dataclass
class Measurement:
    primary: float
    primary_unit: str
    secondary: float
    secondary_unit: str
    function: str
    frequency: str
    raw: str

    def __str__(self) -> str:
        """Readable one-liner for the operator panel / log, e.g.
        ``C @ 1khz: 1.5e-09 F``. (Without this, str() would dump the whole
        dataclass repr.)"""
        val = f"{self.primary:g} {self.primary_unit}".strip()
        sec = f" / {self.secondary:g} {self.secondary_unit}".rstrip() if self.secondary_unit else ""
        return f"{self.function} @ {self.frequency}: {val}{sec}"


_FREQ_MAP = {100: "100hz", 120: "120hz", 1000: "1khz", 10000: "10khz"}
_FREQ_DISPLAY = {"100hz": "100 Hz", "120hz": "120 Hz", "1khz": "1 kHz", "10khz": "10 kHz"}
_UNITS = {"L": "H", "C": "F", "R": "\u03a9", "Z": "\u03a9"}
_USB_VIDS = {0x0403, 0x10C4}
_ERROR_RE = re.compile(r"^E(1[012])\b")


class BK879B:
    def __init__(
        self,
        port: Optional[str] = None,
        baud: int = 9600,
        timeout: float = 2.0,
    ):
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.ser: Optional[serial.Serial] = None
        # Cached state — used by set_function/set_equivalent/set_frequency
        # to skip the SCPI command when the meter is already in the
        # requested state. The BK 879B beeps + flashes its display on
        # every settings change (no SCPI command to disable that), so
        # idempotent guards make repeated reads of the same parameters
        # silent. None means "unknown" — first call always sends.
        self._function: Optional[str] = None
        self._equivalent: Optional[str] = None
        self._frequency: Optional[str] = None

    # ==================================================
    # Connection lifecycle
    # ==================================================

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self) -> bool:
        if self.is_connected():
            return True

        # Explicit port set → use it, no fallback. Falling through to
        # auto-detect when an explicit port fails would silently open
        # whatever FTDI / CP210x device happened to be plugged in
        # (e.g. the robot's USB-RS485 adapter), then waste ~2 s per
        # port in ``check_connection`` waiting for an IDN response
        # that never comes. Respect the operator's authored intent.
        if self.port:
            return self._open(self.port)

        # No explicit port → auto-detect by USB VID / FTDI / CP210x.
        for info in serial.tools.list_ports.comports():
            if info.vid in _USB_VIDS or any(
                h in (info.description or "").upper() for h in ("FTDI", "CP210")
            ):
                if self._open(info.device):
                    return True

        return False

    def close(self) -> None:
        if self.ser:
            try:
                self._send("*GTL")
            except Exception:
                pass
            try:
                self.ser.close()
            finally:
                self.ser = None

    def _open(self, port: str) -> bool:
        try:
            self.ser = serial.Serial(
                port, self.baud, bytesize=8,
                parity="N", stopbits=1, timeout=self.timeout,
            )
            self.port = port
            return True
        except serial.SerialException:
            self.ser = None
            return False

    # ==================================================
    # Low-level I/O
    # ==================================================

    def _send(self, cmd: str) -> None:
        if not self.is_connected():
            return
        self.ser.write((cmd + "\r\n").encode("ascii"))
        time.sleep(0.05)

    def _query(self, cmd: str) -> str:
        if not self.is_connected():
            return ""
        self._send(cmd)
        raw = self.ser.readline()
        if not raw:
            return ""
        text = raw.decode("ascii", errors="replace").strip()
        m = _ERROR_RE.match(text)
        if m:
            codes = {"10": "Unknown command", "11": "Parameter error", "12": "Syntax error"}
            raise RuntimeError(f"Meter error E{m.group(1)}: {codes.get(m.group(1), '?')}")
        return text

    # ==================================================
    # Identity / handshake
    # ==================================================

    def idn(self) -> str:
        self.ser.reset_input_buffer()
        return self._query("*IDN?")

    def check_connection(self) -> Tuple[bool, Optional[str]]:
        try:
            resp = self.idn()
            return (True, resp) if resp else (False, None)
        except Exception:
            return False, None

    def initialize(self, lockout: bool = False) -> str:
        resp = self.idn()
        if not resp:
            raise RuntimeError(
                "No response from meter. Press the USB button on the front "
                "panel until the RMT indicator appears, then retry."
            )
        if lockout:
            self._send("*LLO")
        return resp

    # ==================================================
    # Configuration
    # ==================================================

    def set_function(self, func: str) -> None:
        func = func.upper()
        if self._function == func:
            return  # already set — skip to avoid the meter's change-beep
        self._send(f"FUNCtion:impa {func}")
        self._function = func

    def get_function(self) -> str:
        return self._query("FUNCtion:impa?")

    def set_secondary(self, func: str) -> None:
        self._send(f"FUNCtion:impb {func.upper()}")

    def get_secondary(self) -> str:
        return self._query("FUNCtion:impb?")

    def set_equivalent(self, mode: str) -> None:
        # Normalize for comparison so 'PAR'/'PARallel'/'PARALLEL'
        # all coalesce. The meter accepts any of them and we want
        # repeated identical calls to be silent regardless of casing.
        key = mode.strip().upper()
        if self._equivalent == key:
            return
        self._send(f"FUNCtion:EQUivalent {mode}")
        self._equivalent = key

    def get_equivalent(self) -> str:
        return self._query("FUNCtion:EQUivalent?")

    def set_frequency(self, hz: int) -> None:
        val = _FREQ_MAP.get(int(hz))
        if val is None:
            raise ValueError(f"Frequency must be one of {list(_FREQ_MAP.keys())}, got {hz}")
        if self._frequency == val:
            return
        self._send(f"FREQuency {val}")
        self._frequency = val

    def get_frequency(self) -> str:
        return self._query("FREQuency?")

    # ==================================================
    # Relative
    # ==================================================

    def set_relative(self, on: bool) -> None:
        self._send(f"CALCulate:RELative:STATe {'ON' if on else 'OFF'}")

    def get_relative_state(self) -> str:
        return self._query("CALCulate:RELative:STATe?")

    def get_relative_value(self) -> str:
        return self._query("CALCulate:RELative:VALUe?")

    # ==================================================
    # Tolerance
    # ==================================================

    def set_tolerance(self, on: bool) -> None:
        self._send(f"CALCulate:TOLerance:STATe {'ON' if on else 'OFF'}")

    def get_tolerance_state(self) -> str:
        return self._query("CALCulate:TOLerance:STATe?")

    def get_tolerance_nominal(self) -> str:
        return self._query("CALCulate:TOLerance:NOMinal?")

    def get_tolerance_value(self) -> str:
        return self._query("CALCulate:TOLerance:VALUe?")

    def set_tolerance_range(self, percent: int) -> None:
        self._send(f"CALCulate:TOLerance:RANGe {percent}")

    def get_tolerance_range(self) -> str:
        return self._query("CALCulate:TOLerance:RANGe?")

    # ==================================================
    # Recording
    # ==================================================

    def set_recording(self, on: bool) -> None:
        self._send(f"CALCulate:RECording:STATe {'ON' if on else 'OFF'}")

    def get_recording_state(self) -> str:
        return self._query("CALCulate:RECording:STATe?")

    def get_recording_max(self) -> str:
        return self._query("CALCulate:RECording:MAXimum?")

    def get_recording_min(self) -> str:
        return self._query("CALCulate:RECording:MINimum?")

    def get_recording_avg(self) -> str:
        return self._query("CALCulate:RECording:AVERage?")

    def get_recording_present(self) -> str:
        return self._query("CALCulate:RECording:PRESent?")

    # ==================================================
    # Measurement
    # ==================================================

    def fetch(self) -> str:
        return self._query("FETCh?")

    def read(self) -> Measurement:
        raw = self.fetch()
        if not raw:
            raise RuntimeError("Empty response from FETCh?")

        parts = [p.strip() for p in raw.split(",")]
        if len(parts) < 2:
            raise RuntimeError(f"Bad FETCh? response: {raw!r}")

        primary = float(parts[0])
        secondary = float(parts[1])
        unit = _UNITS.get(self._function, "")

        return Measurement(
            primary=primary,
            primary_unit=unit,
            secondary=secondary,
            secondary_unit="",
            function=self._function,
            frequency=self._frequency,
            raw=raw,
        )

    def read_capacitance(self, mode: str = "Cp", frequency: int = 1000) -> Measurement:
        self.set_function("C")
        self.set_equivalent("SERies" if mode.lower() in ("cs", "series") else "PARallel")
        self.set_frequency(frequency)
        time.sleep(0.3)
        return self.read()

    def read_inductance(self, mode: str = "Ls", frequency: int = 1000) -> Measurement:
        self.set_function("L")
        self.set_equivalent("SERies" if mode.lower() in ("ls", "series") else "PARallel")
        self.set_frequency(frequency)
        time.sleep(0.3)
        return self.read()

    def read_resistance(self, frequency: int = 1000) -> Measurement:
        self.set_function("R")
        self.set_frequency(frequency)
        time.sleep(0.3)
        return self.read()

    def read_impedance(self, frequency: int = 1000) -> Measurement:
        self.set_function("Z")
        self.set_frequency(frequency)
        time.sleep(0.3)
        return self.read()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def list_ports() -> list[dict]:
    results = []
    for info in serial.tools.list_ports.comports():
        likely = info.vid in _USB_VIDS or any(
            h in (info.description or "").upper() for h in ("FTDI", "CP210")
        )
        results.append({"port": info.device, "description": info.description or "", "likely": likely})
    return results


def format_value(value: float, unit: str) -> str:
    a = abs(value)
    if a == 0:      return f"0 {unit}"
    if a >= 1e6:    return f"{value/1e6:.4g} M{unit}"
    if a >= 1e3:    return f"{value/1e3:.4g} k{unit}"
    if a >= 1:      return f"{value:.4g} {unit}"
    if a >= 1e-3:   return f"{value*1e3:.4g} m{unit}"
    if a >= 1e-6:   return f"{value*1e6:.4g} \u00b5{unit}"
    if a >= 1e-9:   return f"{value*1e9:.4g} n{unit}"
    return f"{value*1e12:.4g} p{unit}"


def format_frequency(freq: str) -> str:
    return _FREQ_DISPLAY.get(freq.strip().lower(), freq)
