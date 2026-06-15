from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import serial
from serial.tools import list_ports


# Zebra / Symbol DS457 in USB CDC (serial) mode.
#
# Set the scanner's Host Interface to "USB CDC" in 123Scan. The scanner then
# appears as a serial port and streams each decoded barcode as plain ASCII
# text + suffix (CR/LF):
#   - Linux:   /dev/ttyACM0      (cdc-acm driver is built in)
#   - Windows: COM3, COM4, ...   (may need Zebra's USB CDC driver installed)
#
# You pass the port explicitly — find it once with `ls /dev/ttyACM*` on Linux
# (or Device Manager → Ports (COM & LPT) on Windows). For multiple scanners,
# give each a stable path: /dev/serial/by-id/<...> on Linux, or its fixed COMx
# on Windows, so the right unit is addressed every time.


@dataclass
class Scan:

    status: str
    data: str

    @property
    def connected(self) -> bool:
        return self.status != "disconnected"

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def __str__(self) -> str:
        return repr(self.data) if self.status == "ok" else self.status.upper()


class DS457:
    def __init__(self, port: str, baud: int = 9600,
                 timeout: float = 3.0, gap: float = 0.1):
        self.port = port
        self.baud = int(baud)
        self.timeout = float(timeout)   # default wait for a scan to arrive
        self.gap = float(gap)           # quiet gap that marks end-of-barcode
        self.ser: Optional[serial.Serial] = None

    # ==================================================
    # Connection lifecycle
    # ==================================================

    def is_connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def _port_present(self) -> bool:
        return any(p.device == self.port for p in list_ports.comports())

    def connect(self) -> bool:
        if self.is_connected():
            return True
        try:
            # read timeout = gap, so scan() wakes every `gap` seconds to check
            # its deadline and the end-of-barcode quiet period.
            self.ser = serial.Serial(self.port, self.baud, timeout=self.gap)
        except (serial.SerialException, OSError):
            self.ser = None
            return False
        return True

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            finally:
                self.ser = None

    def check_connection(self) -> bool:
        """True if the port is open AND still present on the system. (Plain CDC
        has no query command, so this is presence-based.)"""
        return self.is_connected() and self._port_present()

    # ==================================================
    # Scanning
    # ==================================================

    def scan(self, timeout: Optional[float] = None) -> Scan:
        """Block until one barcode arrives and return it. ``timeout`` seconds
        caps the wait. Status 'timeout' = nothing scanned (port still there);
        'disconnected' = the port vanished / serial error.

        A barcode is complete on its CR/LF suffix OR on a quiet gap (so a
        missing suffix can't make it hang)."""
        if not self.is_connected():
            return Scan("disconnected", "")

        to = self.timeout if timeout is None else float(timeout)
        deadline = time.time() + to
        buf = bytearray()
        try:
            while True:
                b = self.ser.read(1)            # blocks up to self.gap
                if b:
                    if b in (b"\r", b"\n"):
                        if buf:
                            return Scan("ok", buf.decode("ascii", errors="ignore").strip())
                        continue                # ignore a leading CR/LF
                    buf += b
                    continue
                # no byte within `gap`
                if buf:                         # burst ended → barcode complete
                    return Scan("ok", buf.decode("ascii", errors="ignore").strip())
                if time.time() >= deadline:     # silence, still connected
                    return Scan("timeout", "")
        except (serial.SerialException, OSError):   # port vanished / error
            self.close()
            return Scan("disconnected", "")
