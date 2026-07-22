"""cab label-printer station — the thin wrapper the workspace's
``PrinterAxon1`` component holds and that the device bus attaches.

Same shape as ``SPX222Station`` (scale), ``BK879BStation`` (multimeter)
and ``VisionStation``:

* One class, sim flag baked in at construction.
* Implements the **Device protocol** (``id``, ``state``, ``msg``,
  ``on_state_change``, ``recover``, ``release``) so
  ``workspace.devices.attach_device`` can publish bus state and wire
  AutoRecover for the real path.
* Exposes a **unified printing API** (``is_connected``, ``print_label``,
  ``dry_run_spin``, ``wait_ready``) so the component / recipes never
  branch on the sim flag.
* Real mode wraps :class:`Cab` (the raw JScript-over-TCP driver). Sim
  mode returns the caller's ``sim_return``, shaped like the real return
  (a ``bool``).

The cab talks JScript over TCP (``ip:port``, port 9100), so the device
id is keyed on ``ip`` — the same way the scale and Core key on ``ip``.
See ``docs/device-guide.md`` §10.5 (one sim/real branch, in the station)
and §16 (sim is orthogonal to connection state).

**Connectionless driver.** Unlike the scale, :class:`Cab` opens a fresh
socket per command and closes it again — there is no long-lived link to
keep alive. ``recover()`` therefore builds a driver, proves the printer
answers (TCP connect + an ``ESC s`` status query), and keeps it;
``release()`` just drops it. ``is_connected()`` reports whether that
handshake ever succeeded, not the state of a socket.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from workspace.components.printer.cab_driver import Cab, CodeType


log = logging.getLogger(__name__)


class CabStation:
    """Wraps the cab JScript driver in a Device-bus-shaped object.

    Constructor:
        ip:          printer IP address (the device's bus identity).
        port:        JScript raw-TCP port (default 9100).
        simulation:  True → no real driver is used; the printing methods
                     return their ``sim_return``; recover / release are
                     no-ops that always succeed.
        label:       human-friendly tag for logs / state messages.
        label_cfg:   stock geometry (``width_in``, ``length_in``,
                     ``gap_in``, ``ptype``) pushed to the driver on every
                     ``recover()`` — the printer is stateless between
                     connections, so the label spec is re-sent each time.
    """

    KIND = "printer"

    def __init__(
        self,
        ip: str = "",
        port: int = 9100,
        simulation: bool = True,
        label: str = "",
        label_cfg: Optional[dict] = None,
    ):
        self.ip = ip or ""
        self.port = int(port)
        self.simulation = bool(simulation)
        self.label = label or "printer"
        self.label_cfg = dict(label_cfg or {})

        # Bus-visible state ALWAYS reflects real hardware reachability
        # (device-guide §16). Starts down; the component calls recover()
        # in __init__ to attempt the initial connect.
        self.state: str = "down"
        self.msg: str = "not connected"

        self._listeners: list[Callable[[str, str], None]] = []
        # Real driver instance; None in sim, or until recover() succeeds.
        self._driver: Optional[Cab] = None

    # ── Device protocol ────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """``printer:<ip>`` per device-guide §9. The ip is already
        slash-free, so no basename munging is needed. Sim does not
        change the id."""
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
                log.exception("CabStation[%s]: listener raised", self.label)

    def recover(self) -> bool:
        """Rebuild the driver and prove the printer answers. ALWAYS
        attempts the real connect — sim does not change this
        (device-guide §16). Fires a ``recovering → result`` transition so
        the bus/UI always see an event. Matches SPX222Station.recover.

        Two gates, because TCP-reachable is not the same as alive: the
        cab's print server accepts a connection long before the firmware
        is ready to answer status queries.
        """
        self._set_state("recovering", "reconnecting")
        try:
            # Rebuild from scratch — the driver caches nothing we want to
            # keep across a drop, and the label spec has to be re-sent.
            self._driver = None
            drv = Cab(ip=self.ip, port=self.port)
            if self.label_cfg:
                drv.set_label(**self.label_cfg)
            if not drv.is_reachable():
                self._set_state("down", "connect failed")
                return False
            if not drv.can_query():
                self._set_state("down", "no response")
                return False
            self._driver = drv
            self._set_state("ok", "")
            return True
        except Exception as ex:
            self._set_state("down", f"connect failed: {type(ex).__name__}: {ex}")
            return False

    def release(self) -> None:
        """Drop the driver. Does not branch on sim. There is no socket to
        close — the driver opens one per command."""
        self._driver = None
        self._set_state("down", "released")

    def set_simulation(self, sim: bool) -> None:
        """Live sim/real flip — flag only (device-guide §16). A driver
        already handshaked stays usable across the flip."""
        self.simulation = bool(sim)

    # ── Unified printing API ───────────────────────────────────────────
    # Real path delegates to the driver, transitioning state to "down" on
    # failure so the bus + AutoRecover learn about it.
    #
    # ``sim_return`` (device-guide §17) — explicit sim injection. Its
    # default IS the canned sim value, written right in the signature and
    # shaped exactly like the real return (a ``bool``). In sim the method
    # returns ``sim_return`` verbatim; real mode ignores it entirely.

    def is_connected(self) -> bool:
        if self.simulation:
            return True
        return self._driver is not None

    def last_result(self):
        """The driver's last :class:`PrintResult` — the detail behind a
        ``False``. ``None`` in sim or before the first operation."""
        return None if self._driver is None else self._driver.last_result

    def print_label(
        self,
        data: str,
        code_type: str = "code128",
        autorun: bool = True,
        verify: bool = True,
        sim_return: bool = True,
    ) -> bool:
        """Print ``data`` encoded as ``code_type`` (``code128`` /
        ``qrcode`` / ``datamatrix``). ``verify=True`` blocks until the
        printer confirms the job id finished. Returns True on success,
        False when the job failed or the printer is disconnected and not
        in sim. In sim, returns ``sim_return``."""
        if self.simulation:
            return sim_return
        if self._driver is None:
            return False
        try:
            ok = self._driver.print_one(
                CodeType(code_type), data, autorun=autorun, verify=verify,
            )
            if not ok:
                r = self._driver.last_result
                self._set_state("down", r.message if r else "print failed")
            return ok
        except Exception as ex:
            self._set_state("down", f"print failed: {type(ex).__name__}: {ex}")
            return False

    def dry_run_spin(self, count: int = 1, sim_return: bool = True) -> bool:
        """Cycle the print head ``count`` times without printing — feeds
        a blank label so the applicator motion can be exercised without
        burning stock. In sim, returns ``sim_return``."""
        if self.simulation:
            return sim_return
        if self._driver is None:
            return False
        try:
            ok = self._driver.dry_run_spin(count=count)
            if not ok:
                r = self._driver.last_result
                self._set_state("down", r.message if r else "dry run failed")
            return ok
        except Exception as ex:
            self._set_state("down", f"dry run failed: {type(ex).__name__}: {ex}")
            return False

    def wait_ready(self, timeout_s: Optional[float] = None,
                   sim_return: bool = True) -> bool:
        """Block until the printer reports online + inactive + idle. In
        sim, returns ``sim_return``."""
        if self.simulation:
            return sim_return
        if self._driver is None:
            return False
        try:
            ok = self._driver.wait_ready(timeout_s=timeout_s)
            if not ok:
                self._set_state("down", "not ready")
            return ok
        except Exception as ex:
            self._set_state("down", f"ready check failed: {type(ex).__name__}: {ex}")
            return False
