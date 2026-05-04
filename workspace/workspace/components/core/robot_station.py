"""RobotStation — wraps a dorna2.Dorna with the Device protocol.

Mirrors the VisionStation pattern: pure composition, no modification of
dorna2. Exposes ``id`` / ``state`` / ``msg`` / ``on_state_change`` /
``recover`` / ``release`` so :class:`workspace.devices.MQTTDeviceAdapter`
and :class:`workspace.devices.AutoRecover` can publish health to the
device bus and self-heal connection drops.

Two failure modes are detected and surfaced as ``state="down"`` on the
device bus:

  * **Connection lost.** Any underlying ``ConnectionError`` / ``OSError``
    raised by the wrapped Dorna call (TCP drop, host unreachable, …).
    The exception still propagates to the caller so the recipe can
    react; the bus state goes red so the operator notices.

  * **Robot alarm.** Motion commands return ``int < 0`` on alarm (limit
    hit, IK failed, E-stop, etc.). We translate that into a device-bus
    transition with ``msg="alarm code N"`` so the alarm shows up in the
    same panel as every other device, with a Recover button.

Successful calls (anything that returns non-negative or non-numeric)
clear ``state`` back to ``"ok"`` if it had been down — so a recipe that
auto-retries after a transient blip resolves the panel state itself,
without operator intervention.

Kinematic-only attributes (``self.dorna.kinematic``) pass through as
plain delegations — no wrapping, no state changes — because they're
local math, not network calls.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from dorna2 import Dorna


log = logging.getLogger(__name__)


class RobotStation:
    """Composition wrapper around dorna2.Dorna implementing the Device
    protocol used by ``workspace.devices.MQTTDeviceAdapter``.

    Args:
        ip: Robot host (used for both the dorna2 connect call and the
            device id ``f"dorna:{ip}"``).
        port: Robot port (default 443; passed through to dorna2).
        connect_timeout: Seconds to wait for the initial connect.
        label: Free-form name used in log lines (e.g. component name).

    Recipes use this transparently: ``core.dorna.move(...)`` proxies to
    the underlying ``Dorna.move``, returning the same value, but with
    state tracking layered on top. ``core.dorna.kinematic.inv(...)``
    works the same way (kinematic is a pass-through attribute).

    For direct access to the unwrapped client (rare; only needed if a
    Dorna attribute name collides with the Device protocol — currently
    ``msg``), use ``core.dorna._client``.
    """

    def __init__(
        self,
        *,
        ip: str,
        port: int = 443,
        connect_timeout: int = 5,
        label: str = "robot",
    ):
        self._client = Dorna()
        self.ip = ip
        self.port = port
        self.connect_timeout = connect_timeout
        self.label = label

        # Device-protocol attributes (override Dorna's own .msg).
        self.id = f"dorna:{ip}" if ip else None
        self.state = "down"
        self.msg = "not connected"
        self._listeners: list[Callable[[str, str], None]] = []
        self._listeners_lock = threading.Lock()

        # Optional callback: fired once when state transitions TO "down"
        # (and only on the edge). The service layer wires this to
        # AutoRecover.trigger() so a connection drop kicks the retry
        # loop without polling. Camera does the equivalent via
        # on_hardware_available — IP devices have no hotplug, so we use
        # the state edge instead.
        self._on_down_listeners: list[Callable[[], None]] = []

        # Initial connect — non-fatal: a flaky network must not crash
        # workspace startup. AutoRecover (wired by Core) will retry.
        if self.ip:
            try:
                if self._client.connect(self.ip):
                    self._set_state("ok", "")
                    print(f"✅ {self.label} connected @ {self.ip}")
                else:
                    self._set_state("down", "initial connect returned False")
                    print(f"❌ {self.label} initial connect @ {self.ip} returned False")
            except Exception as ex:
                self._set_state("down", f"initial connect failed: {ex}")
                print(f"❌ {self.label} initial connect @ {self.ip} failed: {ex}")

    # ── Device protocol ──────────────────────────────────────────────────

    def on_state_change(self, callback: Callable[[str, str], None]) -> None:
        with self._listeners_lock:
            self._listeners.append(callback)

    def on_state_down(self, callback: Callable[[], None]) -> None:
        """Fired on the rising edge of state→down. Used by the service
        layer to nudge an AutoRecover loop without polling. callback
        signature: ``callback() -> None``."""
        with self._listeners_lock:
            self._on_down_listeners.append(callback)

    def recover(self) -> bool:
        """Attempt to bring the robot back to a usable state. Called by
        AutoRecover (auto) and by the operator clicking Recover (manual).

        Strategy: close (best-effort), reconnect, verify with a cheap
        read (``get_alarm`` if available), then ``state="ok"``. Returns
        True on success, False otherwise — AutoRecover backs off and
        retries on False.
        """
        self._set_state("recovering", "reconnecting")
        try:
            try:
                self._client.close()
            except Exception:
                pass
            ok = self._client.connect(self.ip)
            if not ok:
                self._set_state("down", "reconnect returned False")
                return False
            # Verify with a cheap read if available — confirms two-way
            # comms, not just a TCP handshake.
            verify = getattr(self._client, "get_alarm", None)
            if callable(verify):
                try:
                    verify()
                except Exception as ex:
                    self._set_state("down", f"verify after reconnect failed: {ex}")
                    return False
            self._set_state("ok", "")
            return True
        except Exception as ex:
            self._set_state("down", f"recover failed: {type(ex).__name__}: {ex}")
            return False

    def release(self) -> None:
        """Device-protocol method — close the connection cleanly."""
        self.close()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    # ── Internal: state + delegation ─────────────────────────────────────

    def _set_state(self, new_state: str, msg: str = "") -> None:
        """Update state and fire listeners. No-op if both state and msg
        are unchanged. Same shape as Camera._set_state — listeners only
        see real updates, but msg-only changes do fire (so the panel can
        update its detail line without a state transition)."""
        new_msg = str(msg or "")
        if self.state == new_state and self.msg == new_msg:
            return
        prev_state = self.state
        self.state = new_state
        self.msg = new_msg
        with self._listeners_lock:
            cbs = list(self._listeners)
            down_cbs = list(self._on_down_listeners) if (
                new_state == "down" and prev_state != "down"
            ) else []
        for cb in cbs:
            try:
                cb(new_state, self.msg)
            except Exception:
                log.exception("RobotStation[%s]: listener raised", self.label)
        for cb in down_cbs:
            try:
                cb()
            except Exception:
                log.exception("RobotStation[%s]: down-listener raised", self.label)

    def __getattr__(self, name: str):
        """Delegate everything not on RobotStation to the wrapped Dorna.

        Callable attributes get a thin wrapper that intercepts return
        codes (negative = alarm) and exceptions (connection lost) to
        update device state. Non-callable attributes (``kinematic``,
        ``model``, ``config``, …) pass through unchanged — those are
        local data, not network calls.
        """
        # Avoid recursion during __init__ before _client exists.
        if name == "_client":
            raise AttributeError(name)
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr
        return self._wrap_call(attr, name)

    def _wrap_call(self, fn: Callable[..., Any], name: str) -> Callable[..., Any]:
        """Wrap a Dorna method to layer device-state tracking on its
        return value and exceptions, without changing what the caller
        sees (return value or raised exception is unchanged)."""
        station = self

        def wrapper(*args, **kwargs):
            try:
                result = fn(*args, **kwargs)
            except (ConnectionError, OSError) as ex:
                station._set_state("down", f"connection lost: {ex}")
                raise
            except Exception as ex:
                # Non-network exception (bad arg, kinematic singularity,
                # …): don't flag the device down — let the caller handle.
                raise
            # Negative numeric result = robot alarm (motion convention).
            if isinstance(result, (int, float)) and result < 0:
                station._set_state("down", f"alarm code {int(result)} ({name})")
            elif station.state != "ok":
                # Successful call after a non-ok state proves the robot
                # is reachable and operating — clear state back to ok.
                station._set_state("ok", "")
            return result

        return wrapper
