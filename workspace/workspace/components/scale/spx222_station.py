"""OHAUS SPX222 scale station — the thin wrapper the workspace's
``ScaleSpx222`` component holds and that the device bus attaches.

Same shape as ``BK879BStation`` (multimeter) and ``VisionStation``:

* One class, sim flag baked in at construction.
* Implements the **Device protocol** (``id``, ``state``, ``msg``,
  ``on_state_change``, ``recover``, ``release``) so
  ``workspace.devices.attach_device`` can publish bus state and wire
  AutoRecover for the real path.
* Exposes a **unified weighing API** (``is_connected``, ``weigh``,
  ``weigh_stable``, ``weight``) so the component / recipes don't
  branch on the sim flag.
* Real mode wraps :class:`SPX222` (the raw MT-SICS TCP driver). Sim
  mode returns canned ``Reading`` objects matching the real shape.

The SPX222 talks over TCP (``ip:port``), so the device id is keyed on
``ip`` — the same way Core keys on ``ip`` and the meter on the serial
port. See ``docs/device-guide.md`` §10.5 (one sim/real branch, in the
station) and §16 (sim is orthogonal to connection state).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from workspace.components.scale.spx222_driver import SPX222, Reading


log = logging.getLogger(__name__)


class SPX222Station:
    """Wraps the SPX222 TCP driver in a Device-bus-shaped object.

    Constructor:
        ip:          balance IP address (the device's bus identity).
        port:        MT-SICS TCP port (default 9761).
        simulation:  True → no real driver is opened; weigh methods
                     return canned data; recover / release are no-ops
                     that always succeed.
        label:       human-friendly tag for logs / state messages.

    Lifecycle mirrors BK879BStation:
        recover() (re)connects + handshakes (``info``), sets state to
        the real outcome. release() closes the socket. Bus state always
        reflects real reachability, regardless of the sim flag.
    """

    KIND = "scale"

    def __init__(
        self,
        ip: str = "",
        port: int = 9761,
        simulation: bool = True,
        label: str = "",
    ):
        self.ip = ip or ""
        self.port = int(port)
        self.simulation = bool(simulation)
        self.label = label or "scale"

        # Bus-visible state ALWAYS reflects real hardware reachability
        # (device-guide §16). Starts down; the component calls recover()
        # in __init__ to attempt the initial connect.
        self.state: str = "down"
        self.msg: str = "not connected"

        self._listeners: list[Callable[[str, str], None]] = []
        # Real driver instance; None in sim, or until connect() succeeds.
        self._driver: Optional[SPX222] = None

    # ── Device protocol ────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """``scale:<ip>`` per device-guide §9. The ip is already
        slash-free, so no basename munging is needed (unlike the
        serial-port meter). Sim does not change the id."""
        return f"{self.KIND}:{self.ip}"

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
                log.exception("SPX222Station[%s]: listener raised", self.label)

    def recover(self) -> bool:
        """(Re)establish the TCP link. ALWAYS attempts the real connect
        — sim does not change this (device-guide §16). Rebuilds the
        driver, connects, and verifies the balance actually answers
        (``check_connection`` via the I2 query). Fires a
        ``recovering → result`` transition so the bus/UI always see an
        event. Matches BK879BStation.recover."""
        self._set_state("recovering", "reconnecting")
        try:
            # Rebuild from scratch — a stale socket after a network drop
            # can still look "open" while the peer is gone.
            if self._driver is not None:
                try:
                    self._driver.close()
                except Exception:
                    pass
                self._driver = None
            self._driver = SPX222(ip=self.ip, port=self.port)
            if not self._driver.connect():
                self._set_state("down", "connect failed")
                return False
            if not self._driver.check_connection():
                self._set_state("down", "no response")
                return False
            self._set_state("ok", "")
            return True
        except Exception as ex:
            self._set_state("down", f"connect failed: {type(ex).__name__}: {ex}")
            return False

    def release(self) -> None:
        """Close the socket + drop the driver. Does not branch on sim."""
        drv, self._driver = self._driver, None
        if drv is not None:
            try:
                drv.close()
            except Exception:
                log.exception("SPX222Station[%s]: close raised", self.label)
        self._set_state("down", "released")

    def set_simulation(self, sim: bool) -> None:
        """Live sim/real flip — flag only (device-guide §16). The TCP
        connection, if open, stays open across the flip."""
        self.simulation = bool(sim)

    # ── Unified weighing API ───────────────────────────────────────────
    # Real path delegates to the driver, transitioning state to "down"
    # on read failure so the bus + AutoRecover learn about it.
    #
    # ``sim_return`` (device-guide §17) — explicit sim injection. Its
    # default IS the canned sim value, written right in the signature and
    # shaped exactly like the real return (a ``Reading`` for weigh /
    # weigh_stable, a ``float`` for weight). In sim the method returns
    # ``sim_return`` verbatim; real mode ignores it entirely.

    def is_connected(self) -> bool:
        """Cheap, NON-round-tripping check — just "is the socket object
        present?". A stale socket after a cable pull still passes this
        (pyserial/sockets don't know the peer vanished). For real liveness
        use :meth:`ping`."""
        if self.simulation:
            return True
        return self._driver is not None and self._driver.is_connected()

    def ping(self) -> bool:
        """Real reachability check — actually round-trips to the balance
        (MT-SICS ``I2`` with the driver's short socket timeout) and returns
        ``True`` only if it answered. Used by the liveness watchdog; unlike
        :meth:`is_connected` this catches a dead-but-open socket. Sim is
        always reachable."""
        if self.simulation:
            return True
        return self._driver is not None and self._driver.check_connection()

    def weigh(self, sim_return: Reading = Reading(status="stable", weight=12.345, unit="g", raw="sim")) -> Optional[Reading]:
        """Instantaneous weight (MT-SICS SI). ``None`` when disconnected
        and not in sim. In sim, returns ``sim_return`` (a ``Reading``)."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            r = self._driver.weigh()
            if not r.connected:
                self._set_state("down", "no reading")
            return r
        except Exception as ex:
            self._set_state("down", f"read failed: {type(ex).__name__}: {ex}")
            return None

    def weigh_stable(self, timeout: float = 10.0,
                     sim_return: Reading = Reading(status="stable", weight=12.345, unit="g", raw="sim")) -> Optional[Reading]:
        """Block until the balance reports a stable weight (MT-SICS S).
        In sim, returns ``sim_return`` (a ``Reading``)."""
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            r = self._driver.weigh_stable(timeout=timeout)
            if not r.connected:
                self._set_state("down", "no reading")
            return r
        except Exception as ex:
            self._set_state("down", f"read failed: {type(ex).__name__}: {ex}")
            return None

    def weight(self, stable: bool = True, timeout: float = 10.0,
               sim_return: float = 12.345) -> Optional[float]:
        """Convenience: just the weight in grams (or None). ``stable``
        waits for a settled reading. In sim, returns ``sim_return`` (a
        ``float`` in grams) verbatim."""
        if self.simulation:
            return sim_return
        r = (self.weigh_stable(timeout=timeout) if stable else self.weigh())
        return None if r is None else r.weight
