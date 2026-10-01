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

    @property
    def usable(self) -> bool:
        """A weight to use: the link answered and the pan is in range.
        Underload is also what a balance in STANDBY reports for every
        SI, so this is the "awake and weighing" test too."""
        return self.connected and self.status not in ("underload", "overload")

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
        # Drain any bytes the balance / serial-to-Ethernet bridge had
        # queued from a previous session BEFORE our first command. In the
        # field these are leftover ``ES`` (MT-SICS "not executable") echoes;
        # if not flushed, the first ``I2`` read returns ``ES`` instead of
        # the identity reply and the connect spuriously "fails" until a
        # manual retry. Draining on connect makes the first handshake
        # reliable. (Observed: ``b'ES\r\nES\r\n…'`` on a cold connect.)
        self._drain()
        return True

    def _drain(self, window: float = 0.4) -> bytes:
        """Read and discard whatever is already queued on the socket, up
        to ``window`` seconds of silence. Returns the drained bytes (for
        logging/debug). Safe to call when nothing is queued — it just
        times out quickly and returns ``b""``."""
        if self.sock is None:
            return b""
        old = self.sock.gettimeout()
        drained = b""
        try:
            self.sock.settimeout(window)
            while True:
                d = self.sock.recv(256)
                if not d:
                    break
                drained += d
                if len(drained) > 4096:    # safety cap — don't spin forever
                    break
        except (socket.timeout, OSError):
            pass
        finally:
            try:
                self.sock.settimeout(old)
            except OSError:
                pass
        return drained

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

    def wake(self, timeout: float = 10.0, poll: float = 0.5) -> Reading:
        """Bring a balance out of standby and wait until it weighs.

        A balance in standby still answers the identity query (I2) but
        every SI reads underload until someone presses On/Zero. Sends
        the Ohaus native ``ON`` (not MT-SICS: the reply is a raw
        ``OK!`` line, read as-is), then polls SI every ``poll`` s until
        the reading is usable (not underload / overload) or ``timeout``
        runs out. Returns the last Reading — the caller decides."""
        self._query("ON")
        end = time.monotonic() + float(timeout)
        r = self.weigh()
        while not r.usable and r.connected and time.monotonic() < end:
            time.sleep(poll)
            r = self.weigh()
        return r

    def zero(self) -> str:
        """MT-SICS Z — zero the balance. Valid only when awake: a balance
        in standby answers ``ES``. Returns the raw reply ("" = silent)."""
        return self._query("Z")

    def weigh_stable(self, timeout: float = 10.0) -> Reading:
        """Block until the balance reports a stable weight (MT-SICS S).
        Falls back to a 'disconnected' Reading if nothing arrives in time."""
        return self._parse_weight(self._query("S", read_timeout=timeout))