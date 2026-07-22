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

import json
import logging
import threading
from contextlib import contextmanager
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
        simulation: bool = False,
    ):
        self._client = Dorna()
        self.ip = ip
        self.port = port
        self.connect_timeout = connect_timeout
        self.label = label

        # Authored simulation intent. Used by ``set_simulation``
        # to flip the flag at runtime. Per device-guide §16, sim is
        # ORTHOGONAL to connection state — sim does NOT skip the
        # initial connect, and the bus dot keeps reflecting hardware
        # reachability regardless. Sim controls api routing (Core)
        # and auto-pause gating (attachment), not the TCP probe.
        self.simulation = bool(simulation)

        # Device-protocol attributes (override Dorna's own .msg). The
        # id stays stable across sim toggle so the bus topic doesn't
        # break — sim is a separate axis surfaced via ``info.sim``.
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

        # Expected-alarm window depth (see ``expect_alarms``). While > 0
        # the wrapper suppresses fault handling: hard-stop homing WORKS
        # by stalling the motor into the stop, so the resulting alarm —
        # and any transient comms hiccup while the controller is in the
        # fault — is part of the procedure, not a device failure. Firing
        # AutoRecover mid-homing closes + reconnects the client and
        # kills the homing routine.
        self._expected_alarm_depth = 0

        # Initial connect — non-fatal: a flaky network must not crash
        # workspace startup. ALWAYS attempted regardless of sim
        # (device-guide §16: bus state reflects hardware reachability,
        # sim only suppresses auto-pause via the attachment).
        # AutoRecover (wired by Core) retries on connection-lost
        # edges; in sim mode AutoRecover is suspended so a failed
        # connect just sits red without retry storms.
        if self.ip:
            try:
                if self._client.connect(self.ip, timeout=self.connect_timeout):
                    self._set_state("ok", "")
                    print(f"✅ {self.label} connected @ {self.ip}")
                else:
                    self._set_state("down", "connect failed")
                    print(f"❌ {self.label} connect @ {self.ip} failed")
            except Exception as ex:
                self._set_state("down", f"connect failed: {ex}")
                print(f"❌ {self.label} connect @ {self.ip} failed: {ex}")

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

    @contextmanager
    def expect_alarms(self, reason: str = ""):
        """Suppress fault interception for a procedure whose alarms are
        EXPECTED — hard-stop homing being the canonical case: the axis
        homes by stalling the motor into a hard stop, the stall trips a
        robot alarm, and the controller may hiccup comms while in the
        fault. Without this window the wrapper flags the device down /
        fires connection-lost, AutoRecover closes + reconnects the client
        mid-procedure, and the homing never finishes.

        Inside the window: negative returns and comms exceptions still
        propagate to the caller unchanged, but the device state is NOT
        flipped and AutoRecover is NOT triggered. On exit the station
        re-syncs with reality: reachable + no alarm → ok; reachable +
        alarm → down (normal semantics resume); unreachable → down +
        connection-lost fires, so a genuine mid-window drop is recovered
        AFTER the procedure instead of during it. Nestable.
        """
        with self._listeners_lock:
            self._expected_alarm_depth += 1
        try:
            yield self
        finally:
            with self._listeners_lock:
                self._expected_alarm_depth -= 1
                inner = self._expected_alarm_depth > 0
            if not inner:
                alarm_code = self._read_alarm_state()
                if alarm_code is None:
                    # Unreachable (or get_alarm gone): deferred recovery.
                    self._set_state("down", f"connection lost after {reason or 'expected-alarm window'}")
                    self._fire_connection_lost()
                elif alarm_code:
                    self._set_state("down", f"robot alarm (code {alarm_code})")
                else:
                    self._set_state("ok", "")

    def recover(self) -> bool:
        """Attempt to bring the robot back to a usable state. Called by
        AutoRecover (auto, on connection-lost only) and by the operator
        clicking Recover (manual).

        ALWAYS performs the real reconnect regardless of sim
        (device-guide §16: bus state reflects hardware reachability).
        In sim mode, AutoRecover is suspended via
        ``attachment.set_sim`` so this fires only on direct invocation
        — the operator can poke the real link from sim to see if it'd
        be reachable.

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
                self._set_state("down", "connect failed")
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
            self._set_state("down", f"connect failed: {type(ex).__name__}: {ex}")
            return False

    def release(self) -> None:
        """Device-protocol method — close the connection cleanly."""
        self.close()

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def set_simulation(self, sim: bool) -> None:
        """Live sim/real flip — flag only. Parity with
        ``BK879BStation.set_simulation``; both follow the rule in
        device-guide §16: sim is ORTHOGONAL to connection state.

        The TCP connection (if open) stays open across the flip;
        ``state`` keeps reflecting hardware reachability either way.
        The component republishes ``info.sim`` via
        ``attachment.set_sim`` and swaps recipe routing (Core's
        ``robot_api``) as needed.
        """
        self.simulation = bool(sim)

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

    def tmove(self, samples=None, **kwargs):
        """PVT trajectory execution — NOT available on the real robot
        yet: it needs a firmware-side tmove (ring buffer + timed
        sample interpolation). Until that lands, recipes must run with
        ``pvt: false`` (smove). Loud failure by design — no silent
        fallback to a different motion primitive."""
        raise RuntimeError(
            "tmove (PVT trajectory) requires controller firmware support "
            "that is not available yet — set pvt: false in the recipe")

    def raw_output(self, index, val):
        """Track-free, QUEUE-BYPASSING single digital output.

        ``queue: 1`` is the controller's bypass flag: the frame skips
        the normal command queue and executes immediately, ahead of
        whatever motion is in flight. ``queue: 0`` (the default the
        SDK's ``output(config=…)`` uses) means the OPPOSITE — the
        frame enters the normal queue and waits for every command
        before it, motions included. That is right for touch-time IO
        (it stays synced with the motion) and wrong here: a queued
        pin write would sit behind the travel it is supposed to
        overlap, and injecting frames between a cont chain's queued
        sections breaks the chain (bench: gripper never opened,
        motion halted with -600).

        Because the frame bypasses the queue, the controller does NOT
        sequence its inter-pin delays — the CALLER owns the timing
        (see the recipe layer's async chain, which sleeps between
        pins exactly as the queued ``sleep`` frames would have).

        Safe to call from a side thread while the main thread blocks
        in a tracked motion: this bypasses ``play()`` (and therefore
        the client's single ``_track`` slot) entirely — the message
        goes straight to the client's event loop, and ``write()`` is
        cross-thread safe by construction
        (``run_coroutine_threadsafe`` onto one loop; each message
        buffered whole before any await).

        Fire-and-forget means NO completion/error feedback — callers
        must verify the physical outcome afterwards (see the recipe
        layer's approach-IO barrier, which asserts pin states before
        any contact motion)."""
        msg = {
            "cmd": "output",
            f"out{int(index)}": int(val),
            "id": self._client.rand_id(100, 1000000),
            "queue": 1,
        }
        self._client.write(json.dumps(msg))
        return True

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
                # Inside an expect_alarms window (hard-stop homing), a
                # comms hiccup is part of the fault the procedure causes:
                # propagate to the caller but defer state + AutoRecover
                # to the window's exit re-sync.
                if station._expected_alarm_depth <= 0:
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
                # Expected-alarm window: the alarm IS the procedure's
                # signal (stall-homing) — don't flip device state; the
                # window's exit re-sync settles the true state.
                if station._expected_alarm_depth <= 0:
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
