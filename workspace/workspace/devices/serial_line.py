"""One open serial line per port, shared by every device on it.

A multi-drop line (RS-485 — Hamilton PSD pumps daisy-chained with rotary
addresses, say) carries several devices on ONE tty. Every frame reaches
every device; each answers only to its own address. Two drivers each
opening the tty themselves "work" while commands happen one at a time
from a notebook, and corrupt each other the moment two threads talk —
an AutoRecover ping to pump 1 landing inside a command to pump 0 on a
half-duplex line garbles both replies.

So the platform opens a port ONCE. Drivers ``acquire`` the line by port
path, share its handle, and hold its lock for the whole of one exchange
(write + read-to-terminator). The line closes when its last user
releases it. Settings are the line's, not a device's: a second user
asking for a different baud / framing is a configuration error and is
refused loudly rather than silently re-configured under the first.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional

import serial


class SerialLineSettingsMismatch(RuntimeError):
    """Two devices on one port asked for different line settings."""


class SerialLine:
    """A shared, locked serial handle. Obtain via :meth:`acquire`."""

    _registry: Dict[str, "SerialLine"] = {}
    _registry_lock = threading.Lock()

    def __init__(self, port: str, settings: dict):
        self.port = port
        self.settings = dict(settings)
        # ``serial_for_url`` takes device paths and pyserial URLs alike
        # (``loop://`` for tests).
        self.ser = serial.serial_for_url(port, **settings)
        # Re-entrant: a driver that takes the lock for an exchange may
        # call a helper that takes it again (drain inside connect).
        self.lock = threading.RLock()
        self._users = 0

    # ── lifecycle ─────────────────────────────────────────────────────
    @classmethod
    def acquire(cls, port: str, *, baudrate: int, bytesize: int = 8, parity: str = "N",
                stopbits: int = 1, timeout: float = 2.0) -> "SerialLine":
        """The line for ``port``, opened on first use. ``timeout`` is per
        user and not part of the line's identity — every exchange sets
        the timeout it needs under the lock. Raises
        :class:`SerialLineSettingsMismatch` when the line is already
        open with different framing, and pyserial's ``SerialException``
        when the port cannot be opened."""
        settings = dict(baudrate=int(baudrate), bytesize=int(bytesize),
                        parity=str(parity), stopbits=int(stopbits))
        with cls._registry_lock:
            line = cls._registry.get(port)
            if line is not None and not line.is_open:
                del cls._registry[port]
                line = None
            if line is None:
                line = cls(port, dict(settings, timeout=float(timeout)))
                cls._registry[port] = line
            elif line.settings_key != settings:
                raise SerialLineSettingsMismatch(
                    f"{port} is open at {line.settings_key} by {line._users} device(s); "
                    f"a new device asks for {settings}")
            line._users += 1
            return line

    def release(self) -> None:
        """One user done; the port closes when the last one releases."""
        with self._registry_lock:
            self._users = max(0, self._users - 1)
            if self._users == 0:
                self._registry.pop(self.port, None)
                try:
                    self.ser.close()
                except serial.SerialException:
                    pass

    # ── introspection ─────────────────────────────────────────────────
    @property
    def settings_key(self) -> dict:
        return {k: self.settings[k] for k in ("baudrate", "bytesize", "parity", "stopbits")}

    @property
    def is_open(self) -> bool:
        return bool(self.ser is not None and self.ser.is_open)

    @property
    def users(self) -> int:
        return self._users

    @classmethod
    def open_lines(cls) -> Dict[str, int]:
        """``{port: users}`` for every line currently open — diagnostics."""
        with cls._registry_lock:
            return {p: l._users for p, l in cls._registry.items()}
