"""Keyto SP28 pipettor station — the thin wrapper that the workspace's
``PipettorSP2840mm`` component holds and that the device bus attaches.

Mirrors the ``BK879BStation`` pattern (see
``workspace/components/multi_meter/bk879b_station.py``):

* One class, sim flag baked in at construction.
* Implements the **Device protocol** (``id``, ``state``, ``msg``,
  ``on_state_change``, ``recover``, ``release``) so
  ``workspace.devices.attach_device`` can publish bus state and wire
  AutoRecover for the real path.
* Exposes a **unified pump API** (``is_connected``, ``initialize``,
  ``aspirate``, ``dispense``, ``eject_tip``, ``has_tip``) so the
  component and recipes never branch on the sim flag.
* In real mode wraps ``Keyto`` (the raw serial driver). In sim mode
  returns the per-call ``sim_return`` verbatim (device-guide §17).

See ``docs/device-guide.md`` §10.5 for the architectural rule (one
sim/real branch per device, lives in the component / station, never
in recipes).
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional

from workspace.components.pipettor.keyto_driver import Keyto


log = logging.getLogger(__name__)


class KeytoStation:
    """Wraps the Keyto serial driver in a Device-bus-shaped object.

    Constructor:
        port:        explicit serial port, or empty/None to auto-scan
                     on ``recover()``.
        simulation:  True → pump ops return their ``sim_return``;
                     the real connect is still attempted so bus state
                     reflects hardware truth (device-guide §16).
        label:       human-friendly tag for logs / state messages.
    """

    KIND = "pipettor"

    def __init__(
        self,
        port: Optional[str] = None,
        simulation: bool = True,
        label: str = "",
    ):
        self.port = port
        self.simulation = bool(simulation)
        self.label = label or "pipettor"

        # Bus-visible state ALWAYS reflects real hardware reachability,
        # regardless of the sim flag (device-guide §16). Starts down;
        # the component calls ``recover()`` in __init__ to attempt the
        # initial connect and update state to the real outcome.
        self.state: str = "down"
        self.msg: str = "not connected"

        # Wired by ``attach_device`` — fires on every _set_state.
        self._listeners: list[Callable[[str, str], None]] = []

        # Real driver instance; None until connect() succeeds.
        self._driver: Optional[Keyto] = None

    # ── Device protocol ────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """``pipettor:<basename(port)>`` per device-guide §9 — basename
        keeps the id slash-free (slashes break the bus's single-level
        MQTT wildcards). Sim does not change the id."""
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
                log.exception("KeytoStation[%s]: listener raised", self.label)

    def recover(self) -> bool:
        """Re-establish the serial link. ALWAYS attempts the real
        connect — sim does not change this (device-guide §16). Always
        fires a ``recovering → result`` transition so the panel sees
        an event even when the final state matches the previous one.

        Note the driver's ``connect()`` also homes the plunger
        (``initialize``) — reconnecting a live pump produces motion,
        same contract as power-cycling it.
        """
        self._set_state("recovering", "reconnecting")
        try:
            # Rebuild the driver from scratch: after a USB unplug the
            # old serial.Serial still reports is_open == True, so a
            # reused driver would short-circuit connect() onto a dead
            # fd (see BK879BStation.recover for the war story).
            if self._driver is not None:
                try:
                    self._driver.close()
                except Exception:
                    pass
                self._driver = None
            self._driver = Keyto(port=self.port or None)
            if not self._driver.connect():
                self._set_state("down", "connect failed")
                return False
            self.port = self._driver.port  # auto-detected
            self._set_state("ok", "")
            return True
        except Exception as ex:
            self._set_state("down", f"connect failed: {type(ex).__name__}: {ex}")
            return False

    def release(self) -> None:
        """Close the serial port + drop the driver. Does NOT branch on
        sim — release frees the real handle if one exists."""
        drv, self._driver = self._driver, None
        if drv is not None:
            try:
                drv.close()
            except Exception:
                log.exception("KeytoStation[%s]: close raised", self.label)
        self._set_state("down", "released")

    def set_simulation(self, sim: bool) -> None:
        """Live sim/real flip — flag only (device-guide §16 parity).
        The serial connection (if open) stays open across the flip."""
        self.simulation = bool(sim)

    # ── Unified pump API ───────────────────────────────────────────────
    # ``sim_return`` (device-guide §17) — explicit sim injection. Its
    # default matches the real return type (the driver ops return a
    # bool), inline in each signature. In sim the method returns
    # ``sim_return`` verbatim; real mode ignores it. Real path returns
    # ``None`` when the pump is disconnected and not in sim, and
    # transitions state to "down" on op failures so the bus +
    # AutoRecover learn about it.

    def is_connected(self) -> bool:
        if self.simulation:
            return True
        return self._driver is not None and self._driver.is_connected()

    def _safe_op(self, fn_name: str, sim_return, *args, **kw):
        if self.simulation:
            return sim_return
        if self._driver is None or not self._driver.is_connected():
            return None
        try:
            return getattr(self._driver, fn_name)(*args, **kw)
        except Exception as ex:
            self._set_state("down", f"{fn_name} failed: {type(ex).__name__}: {ex}")
            return None

    def initialize(self, speed: int = 16000, sim_return: bool = True) -> Optional[bool]:
        """Home the plunger (no tip eject)."""
        return self._safe_op("initialize", sim_return, speed)

    def aspirate(self, volume_ul: float, speed: int = 200, sim_return: bool = True) -> Optional[bool]:
        return self._safe_op("aspirate", sim_return, volume_ul, speed)

    def dispense(self, volume_ul: float, speed: int = 500, blowout: bool = False,
                 sim_return: bool = True) -> Optional[bool]:
        return self._safe_op("dispense", sim_return, volume_ul, speed, blowout)

    def eject_tip(self, speed: int = 64000, sim_return: bool = True) -> Optional[bool]:
        """Home + eject the mounted tip."""
        return self._safe_op("eject_tip", sim_return, speed)

    def has_tip(self, sim_return: bool = True) -> Optional[bool]:
        """Tip-presence sensor read."""
        return self._safe_op("has_tip", sim_return)
