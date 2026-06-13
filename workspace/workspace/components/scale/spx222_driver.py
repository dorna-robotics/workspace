from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class Reading:
    status: str
    weight: Optional[float]   # grams; None when not a numeric reading
    unit: str
    raw: str

    @property
    def connected(self) -> bool:
        return self.status != "disconnected"

    @property
    def stable(self) -> bool:
        return self.status == "stable"

    def __str__(self) -> str:
        if not self.connected:
            return "DISCONNECTED"
        if self.weight is None:
            return self.status
        return f"{self.weight:g} {self.unit} ({self.status})"


class SPX222:

    _STATUS = {"S": "stable", "D": "unstable", "+": "overload", "-": "underload"}

    def __init__(self, ip: str = "192.168.254.132", port: int = 9761, timeout: float = 3.0):
        self.ip = ip
        self.port = int(port)
        self.timeout = float(timeout)
        self.sock: Optional[socket.socket] = None

    # ==================================================
    # Connection lifecycle
    # ==================================================

    def is_connected(self) -> bool:
        return self.sock is not None

    def connect(self) -> bool:
        if self.is_connected():
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((self.ip, self.port))
            self.sock = s
        except OSError:
            self.sock = None
            return False
        return True

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    # ==================================================
    # Low-level I/O
    # ==================================================

    def _query(self, cmd: str, read_timeout: Optional[float] = None) -> str:
        """Send one command, return its first reply line (stripped), or ""
        when the balance said nothing — which the callers read as a dropped
        link (NOT a zero reading)."""
        if self.sock is None:
            return ""
        to = self.timeout if read_timeout is None else float(read_timeout)
        try:
            self.sock.sendall((cmd + "\r\n").encode("ascii"))
            self.sock.settimeout(to)
            buf = b""
            while True:
                d = self.sock.recv(256)
                if not d:                 # peer closed the socket
                    self.close()
                    return ""
                buf += d
                if buf.endswith(b"\r\n"):
                    break
            return buf.decode("ascii", errors="ignore").strip()
        except socket.timeout:
            return ""                     # no reply in time → disconnected
        except OSError:
            self.close()
            return ""

    # ==================================================
    # Identity / connection check
    # ==================================================

    def info(self) -> str:
        """MT-SICS I2 → model + capacity, e.g. 'SPX222 220.90 g'. "" if silent."""
        raw = self._query("I2")
        if '"' in raw:                    # I2 A "SPX222 220.90 g"
            return raw.split('"')[1].strip()
        return ""

    def check_connection(self) -> bool:
        """True only if the balance actually answered — socket up AND talking."""
        return bool(self.info())

    # ==================================================
    # Weighing
    # ==================================================

    def _parse_weight(self, raw: str) -> Reading:
        if not raw:
            return Reading("disconnected", None, "", raw)
        # Expected: "S <status> <weight> <unit>", e.g. "S S     -74.57 g"
        parts = raw.split()
        if len(parts) >= 2 and parts[0] == "S":
            code = parts[1]
            status = self._STATUS.get(code, "unstable")
            if code in ("+", "-"):        # over/underload carry no value
                return Reading(status, None, "", raw)
            weight, unit = None, ""
            if len(parts) >= 4:
                try:
                    weight = float(parts[2])
                    unit = parts[3]
                except ValueError:
                    pass
            return Reading(status, weight, unit, raw)
        # Anything else (incl. an "ES" error echo) is not a usable reading.
        return Reading("disconnected", None, "", raw)

    def weigh(self) -> Reading:
        return self._parse_weight(self._query("SI"))

    def weigh_stable(self, timeout: float = 10.0) -> Reading:
        """Block until the balance reports a stable weight (MT-SICS S).
        Falls back to a 'disconnected' Reading if nothing arrives in time."""
        return self._parse_weight(self._query("S", read_timeout=timeout))