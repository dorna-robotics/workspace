"""RobotStation — wraps a dorna2.Dorna with the Device protocol.

Mirrors the VisionStation pattern: pure composition, no modification of
dorna2. Exposes ``id`` / ``state`` / ``msg`` / ``on_state_change`` /
``on_state_down`` / ``on_connection_lost`` / ``recover`` / ``release``
so :class:`workspace.devices.MQTTDeviceAdapter` and
:class:`workspace.devices.AutoRecover` can publish health to the device
bus and self-heal connection drops.

Two failure modes are detected and surfaced as ``state="down"`` on the
device bus:

  * **Connection lost.** A wrapped Dorna call raises ``ConnectionError``
    or ``OSError`` (TCP drop, host unreachable). The exception still
    propagates to the caller; the bus state goes red so the operator
    notices. Also fires ``on_connection_lost`` so the service layer
    (Core wires this to ``AutoRecover.trigger``) retries reconnection.

  * **Robot alarm.** A motion command returns a negative numeric value
    (e.g. ``-44``, ``-600``) — robot reports a fault (limit hit, IK
    failure, E-stop, etc.). State goes ``down`` with
    ``msg="alarm code N (method)"``. ``on_connection_lost`` is **NOT**
    fired because reconnecting won't clear an alarm — the operator
    must clear it physically on the robot.

State auto-clears back to ``"ok"`` only on a **successful numeric
return ≥ 0** — i.e. a motion command (jmove/lmove/cmove) that the
robot actually accepted and completed. Read-type calls like ``joint()``
return a list (no state change); ``get_alarm()`` returns an int that
may itself be the alarm code (no state change either, since polling a
status read shouldn't be conflated with running a motion). This avoids
the 3D viewer's continuous ``joint()`` polls silently flipping the dot
back to green right after an alarm sets it red.

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
        # (and only on the edge). General-purpose; service layer can
        # observe any down transition (alarm OR connection issue).
        self._on_down_listeners: list[Callable[[], None]] = []

        # Connection-lost-specific callback: fired ONLY when a wrapped
        # call raises ConnectionError / OSError (TCP drop, host
        # unreachable). Distinguished from generic on_state_down so the
        # service layer can wire AutoRecover.trigger() here without
        # accidentally retrying a robot alarm — alarms aren't fixed by
        # reconnecting; the operator must clear them on the robot
        # itself. Camera devices use on_hardware_available for the
        # equivalent intent.
        self._on_connection_lost_listeners: list[Callable[[], None]] = []

        # Initial connect — non-fatal: a flaky network must not crash
        # workspace startup. AutoRecover (wired by Core) will retry.
        if self.ip:
            try:
                if self._client.connect(self.ip, timeout=self.connect_timeout):
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
        """Fired on the rising edge of state→down (alarm OR connection).
        General-purpose hook. callback signature: ``callback() -> None``."""
        with self._listeners_lock:
            self._on_down_listeners.append(callback)

    def on_connection_lost(self, callback: Callable[[], None]) -> None:
        """Fired ONLY when a wrapped call raises ConnectionError /
        OSError (TCP drop, host unreachable). Use this — not
        ``on_state_down`` — to trigger AutoRecover, since alarms are
        also state-down events but reconnecting won't fix them.
        callback signature: ``callback() -> None``."""
        with self._listeners_lock:
            self._on_connection_lost_listeners.append(callback)

    def recover(self) -> bool:
        """Attempt to bring the robot back to a usable state. Called by
        AutoRecover (auto, on connection-lost only) and by the operator
        clicking Recover (manual).

        Strategy: close (best-effort), reconnect, then **check the
        robot's alarm state via** ``get_alarm()``. Two-way comms +
        ``alarm == 0`` is the only valid path to ``state="ok"``. If
        ``alarm != 0`` the robot is still in a fault state — keep the
        device down with the alarm code so the panel reflects reality
        and the operator knows physical action is needed. Returns True
        only on full recovery (connection AND no alarm).
        """
        self._set_state("recovering", "reconnecting")
        try:
            try:
                self._client.close()
            except Exception:
                pass
            ok = self._client.connect(self.ip, timeout=self.connect_timeout)
            if not ok:
                self._set_state("down", "reconnect returned False")
                return False
            # Verify alarm state (not just connectivity) before declaring
            # ok. ``get_alarm()`` returns the current alarm code; non-zero
            # = robot is still faulted; reconnecting won't clear it.
            get_alarm = getattr(self._client, "get_alarm", None)
            if callable(get_alarm):
                try:
                    alarm_code = get_alarm()
                except Exception as ex:
                    self._set_state("down", f"verify after reconnect failed: {ex}")
                    return False
                if alarm_code:
                    self._set_state(
                        "down",
                        f"robot in alarm state (code {alarm_code}) — clear on the robot",
                    )
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

    def _read_alarm_state(self) -> Any:
        """Query the robot's current alarm code, bypassing the wrapper.

        Returns the value from ``self._client.get_alarm()`` — typically
        ``0`` (no alarm) or a non-zero code (e.g. ``-44``) when the
        robot is in a fault state. Catches network errors and returns
        ``None`` so the wrapper can decide what to do; raises nothing
        the caller must handle.

        Bypasses ``__getattr__`` (which would re-wrap every call) by
        going straight to the underlying Dorna instance.
        """
        get_alarm = getattr(self._client, "get_alarm", None)
        if not callable(get_alarm):
            return None
        try:
            return get_alarm()
        except Exception:
            return None

    def _fire_connection_lost(self) -> None:
        """Notify on_connection_lost listeners. Called by the wrapper
        only when a Dorna call raises a network-level exception, so
        AutoRecover loops fire only for genuine reconnect work — not
        for alarms (which need physical operator clearance instead)."""
        with self._listeners_lock:
            cbs = list(self._on_connection_lost_listeners)
        for cb in cbs:
            try:
                cb()
            except Exception:
                log.exception(
                    "RobotStation[%s]: connection-lost listener raised",
                    self.label,
                )

    def _set_state(self, new_state: str, msg: str = "") -> None:
        """Update state and fire listeners. No-op if both state and msg
        are unchanged. Listeners only see real transitions; msg-only
        changes do fire (so the panel can update its detail line
        without a state transition).

        State + msg writes happen UNDER the listener lock so concurrent
        callers (e.g. wrapper detecting an alarm at the same moment
        recover() is reconnecting) can't interleave a half-updated
        state with listener notifications.
        """
        new_msg = str(msg or "")
        with self._listeners_lock:
            if self.state == new_state and self.msg == new_msg:
                return
            prev_state = self.state
            self.state = new_state
            self.msg = new_msg
            cbs = list(self._listeners)
            down_cbs = list(self._on_down_listeners) if (
                new_state == "down" and prev_state != "down"
            ) else []
        # Fire listeners OUTSIDE the lock so a slow subscriber (e.g.
        # paho publish under broker pressure) can't block other
        # threads' state reads or further state changes.
        for cb in cbs:
            try:
                cb(new_state, new_msg)
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
                station._fire_connection_lost()
                raise

            # A negative numeric return doesn't always mean "device
            # alarmed" — different codes carry different meanings:
            #   * Some (e.g. -44) = robot is in a fault state and won't
            #     move until the operator clears it.
            #   * Others (e.g. -600 = motion rejected / out-of-range)
            #     just mean "this particular motion failed"; the robot
            #     itself is healthy.
            # The canonical signal is ``get_alarm()`` — ask the robot
            # directly. State only goes "down" when the robot reports
            # it's actually alarmed; motion-failed codes still propagate
            # to the caller (so ``runtime.call`` still pauses + surfaces
            # the step entry), but the device dot stays accurate.
            if (
                isinstance(result, (int, float))
                and not isinstance(result, bool)
                and result < 0
            ):
                alarm_code = station._read_alarm_state()
                if alarm_code:
                    station._set_state("down", f"robot alarm (code {alarm_code})")
                # else: motion failed but robot is healthy — don't flip
                # device state.
            elif (
                isinstance(result, (int, float))
                and not isinstance(result, bool)
                and result >= 0
                and station.state != "ok"
            ):
                # Auto-clear state on a successful motion command. Read
                # calls (joint/get_io/...) return a list or non-numeric
                # value and never reach this branch, so the 3D viewer's
                # continuous joint() polls don't silently reset state.
                station._set_state("ok", "")
            return result

        return wrapper
