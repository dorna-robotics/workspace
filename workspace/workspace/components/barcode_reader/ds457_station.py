"""Zebra/Symbol DS457 barcode-reader station — the thin wrapper the
workspace's ``BarcodeReaderZebraVertical144mm`` component holds and that
the device bus attaches.

Same shape as ``SPX222Station`` (scale) and ``BK879BStation``
(multimeter):

* One class, sim flag baked in at construction.
* Implements the **Device protocol** (``id``, ``state``, ``msg``,
  ``on_state_change``, ``recover``, ``release``) so
  ``workspace.devices.attach_device`` can publish bus state and wire
  AutoRecover for the real path.
* Exposes a **unified scanning API** (``is_connected``, ``scan``) so the
  component / recipes don't branch on the sim flag.
* Real mode wraps :class:`DS457` (the raw USB-CDC serial driver). Sim
  mode returns canned ``Scan`` objects matching the real shape.

The DS457 talks over a serial port (USB CDC), so the device id is keyed
on ``port`` — the same way the multimeter is. The basename is used so the
id stays slash-free for MQTT single-level wildcards (device-guide §9).
See ``docs/device-guide.md`` §10.5 (one sim/real branch, in the station)
and §16 (sim is orthogonal to connection state).
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from workspace.components.barcode_reader.ds457_driver import DS457, Scan


log = logging.getLogger(__name__)


class DS457Station:
    """Wraps the DS457 serial driver in a Device-bus-shaped object.

    Constructor:
        port:        serial port (the device's bus identity), e.g.
                     ``/dev/ttyACM0`` or a ``/dev/serial/by-id/...`` path.
        baud:        serial baud (default 9600).
        simulation:  True → no real driver is opened; ``scan`` returns
                     canned data; recover / release are no-ops that
                     always succeed.
        label:       human-friendly tag for logs / state messages.

    Lifecycle mirrors SPX222Station:
        recover() (re)opens the port + verifies it's still present, sets
        state to the real outcome. release() closes the port. Bus state
        always reflects real reachability, regardless of the sim flag.
    """

    KIND = "barcode_reader"

    def __init__(
        self,
        port: str = "",
        baud: int = 9600,
        simulation: bool = True,
        label: str = "",
    ):
        self.port = port or ""
        self.baud = int(baud)
        self.simulation = bool(simulation)
        self.label = label or "barcode_reader"

        # Bus-visible state ALWAYS reflects real hardware reachability
        # (device-guide §16). Starts down; the component calls recover()
        # in __init__ to attempt the initial connect.
        self.state: str = "down"
        self.msg: str = "not connected"

        self._listeners: list[Callable[[str, str], None]] = []
        # Real driver instance; None in sim, or until connect() succeeds.
        self._driver: Optional[DS457] = None

    # ── Device protocol ────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """``barcode_reader:<basename(port)>`` per device-guide §9. The
        basename (e.g. ``ttyACM0``) keeps the id slash-free — MQTT
        subscribers use ``device/+/info`` (single-level wildcard) and a
        slash in the id would hide the publisher. Sim does not change the
        id."""
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
                log.exception("DS457Station[%s]: listener raised", self.label)

    def recover(self) -> bool:
        """(Re)open the serial port. ALWAYS attempts the real connect —
        sim does not change this (device-guide §16). Rebuilds the driver
        fresh (a stale ``serial.Serial`` after a USB unplug can still look
        open), opens it, and verifies the port is actually present. Fires
        a ``recovering → result`` transition so the bus/UI always see an
        event. Matches SPX222Station.recover / BK879BStation.recover."""
        self._set_state("recovering", "reconnecting")
        try:
            # Rebuild from scratch — pyserial doesn't know the kernel ripped
            # the device away, so the old handle can still report is_open.
            if self._driver is not None:
                try:
                    self._driver.close()
                except Exception:
                    pass
                self._driver = None
            self._driver = DS457(port=self.port, baud=self.baud)
            if not self._driver.connect():
                self._set_state("down", "connect failed")
                return False
            # Plain USB-CDC has no query command — presence is the check.
            if not self._driver.check_connection():
                self._set_state("down", "port not present")
                return False
            self._set_state("ok", "")
            return True
        except Exception as ex:
            self._set_state("down", f"connect failed: {type(ex).__name__}: {ex}")
            return False

    def release(self) -> None:
        """Close the serial port + drop the driver. Does not branch on sim."""
        drv, self._driver = self._driver, None
        if drv is not None:
            try:
                drv.close()
            except Exception:
                log.exception("DS457Station[%s]: close raised", self.label)
        self._set_state("down", "released")

    def set_simulation(self, sim: bool) -> None:
        """Live sim/real flip — flag only (device-guide §16). The serial
        connection, if open, stays open across the flip."""
        self.simulation = bool(sim)

    # ── Unified scanning API ───────────────────────────────────────────
    # Real path delegates to the driver, transitioning state to "down" on
    # a disconnected read so the bus + AutoRecover learn about it.
    #
    # ``sim_return`` (device-guide §17) — explicit sim injection. Its
    # default IS the canned sim value, written right in the signature and
    # shaped exactly like the real return (a ``Scan``). In sim the method
    # returns ``sim_return`` verbatim; real mode ignores it entirely.

    def is_connected(self) -> bool:
        if self.simulation:
            return True
        return self._driver is not None and self._driver.is_connected()

    def scan(self, timeout: float = 3.0,
             sim_return: Scan = Scan(status="ok", data="SIM-0000000000")) -> Optional[Scan]:
        """Block until one barcode arrives and return it (a ``Scan``).
        ``None`` when disconnected and not in sim. ``timeout`` caps the
        wait. In sim, returns ``sim_return`` (a ``Scan``)."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            r = self._driver.scan(timeout=timeout)
            if not r.connected:
                self._set_state("down", "scanner disconnected")
            return r
        except Exception as ex:
            self._set_state("down", f"read failed: {type(ex).__name__}: {ex}")
            return None
