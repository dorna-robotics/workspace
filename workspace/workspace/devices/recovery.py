"""AutoRecover — generic exponential-backoff recovery loop for any device.

Plug in a ``recover_fn`` (callable returning ``True`` on success) and a
``set_status`` callback (called with ``state, msg`` updates). The helper
schedules retries on its own background thread, with exponential backoff
capped at ``max_delay`` seconds, until ``recover_fn`` returns ``True`` or
``stop()`` is called.

Each attempt's outcome is reflected in the device's ``msg`` so operators
see ``"3 attempts failed, still trying"`` rather than a frozen state.

Designed to be device-agnostic — used by the camera service today, ready
to be plugged into the printer/robot/pipettor services tomorrow. The only
contract is the two callables; the helper has no idea what kind of device
it's recovering.

Typical wiring (camera):

    recover = AutoRecover(
        recover_fn=camera.recover,
        set_status=camera._set_state,
        log_label=f"camera:{camera.serial_number}",
    )
    # On hotplug "USB attached":
    recover.trigger()
    # On operator clicks Recover (cmd handler):
    recover.trigger()      # idempotent + resets backoff

The helper turns "operator must click Recover, then wait, maybe click
again" into "device self-heals when hardware comes back, operator only
intervenes when retries are flagged as stuck."
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional


log = logging.getLogger(__name__)


class AutoRecover:
    """Backoff scheduler around a device's recover function.

    Args:
        recover_fn: Called on each attempt. Returns ``True`` on success
            (loop exits), ``False`` to schedule another attempt.
        set_status: Called as ``set_status(state, msg)`` to surface
            attempt counts ("3 attempts failed, still trying") to the
            outside world (the MQTT adapter, panels, logs).
        initial_delay: Seconds to wait before the *second* attempt. The
            first attempt fires immediately on ``trigger()``.
        max_delay: Cap on the wait between attempts. After enough
            failures the schedule stays at this rate forever — the
            device may come back hours later and we still want to
            notice.
        backoff_factor: Multiplier applied to the wait between attempts.
        flag_after: After this many consecutive failures, ``msg`` switches
            from ``"recovering"`` to ``"N attempts failed — please check"``
            so the operator knows it's not auto-healing on its own. The
            loop keeps trying.
        log_label: Prefix used in log lines, e.g. ``"camera:130322274110"``.

    Threading:
        ``trigger()`` and ``stop()`` are safe to call from any thread.
        The recovery loop runs on a single background daemon thread; if
        ``trigger()`` is called while a loop is already running, it just
        nudges the existing loop to retry immediately (skips the current
        sleep and resets the backoff counter).
    """

    def __init__(
        self,
        *,
        recover_fn: Callable[[], bool],
        set_status: Callable[[str, str], None],
        initial_delay: float = 2.0,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        flag_after: int = 3,
        log_label: str = "device",
    ):
        self._recover_fn = recover_fn
        self._set_status = set_status
        self._initial_delay = float(initial_delay)
        self._max_delay = float(max_delay)
        self._backoff_factor = float(backoff_factor)
        self._flag_after = int(flag_after)
        self._label = str(log_label)

        self._lock = threading.Lock()
        self._wake = threading.Event()        # nudges the loop to retry now
        self._stop = threading.Event()        # tells the loop to exit
        self._thread: Optional[threading.Thread] = None
        self._attempts = 0

    # ── Public API ────────────────────────────────────────────────────────

    def trigger(self) -> None:
        """Start (or nudge) a recovery loop. Idempotent.

        First call: spawns the loop, fires the first attempt immediately.
        Subsequent calls while the loop is running: resets the attempt
        counter and wakes the loop (next attempt fires immediately).
        Call from a hotplug callback, an operator-initiated cmd, or any
        external signal that the device might be reachable again.
        """
        with self._lock:
            if self._thread and self._thread.is_alive():
                # Already recovering — operator/hotplug nudge: try now.
                self._attempts = 0
                self._wake.set()
                return
            self._stop.clear()
            self._wake.clear()
            self._attempts = 0
            self._thread = threading.Thread(
                target=self._loop, name=f"recover-{self._label}", daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        """Cancel the loop. Safe to call when nothing is running."""
        self._stop.set()
        self._wake.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def attempts(self) -> int:
        return self._attempts

    # ── Internals ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        delay = self._initial_delay
        while not self._stop.is_set():
            self._attempts += 1
            try:
                self._set_status(
                    "recovering",
                    self._status_msg(self._attempts),
                )
            except Exception:
                log.exception("AutoRecover[%s]: set_status raised", self._label)

            log.info("AutoRecover[%s]: attempt %d", self._label, self._attempts)

            try:
                ok = bool(self._recover_fn())
            except Exception:
                log.exception("AutoRecover[%s]: recover_fn raised", self._label)
                ok = False

            if ok:
                log.info("AutoRecover[%s]: recovered after %d attempt(s)",
                         self._label, self._attempts)
                # recover_fn is responsible for setting state=ok itself;
                # we don't override here in case the device wants a
                # specific msg ("connected", model details, etc.).
                return

            if self._stop.is_set():
                return

            # Backoff with cap. Wake event allows trigger()/stop() to
            # interrupt the sleep early.
            log.info("AutoRecover[%s]: failed; next attempt in %.1fs",
                     self._label, delay)
            self._wake.wait(timeout=delay)
            if self._wake.is_set():
                # Nudged: reset backoff for an immediate retry.
                self._wake.clear()
                delay = self._initial_delay
                self._attempts = 0
                continue
            delay = min(delay * self._backoff_factor, self._max_delay)

    def _status_msg(self, attempt: int) -> str:
        if attempt <= self._flag_after:
            return f"recovering (attempt {attempt})"
        return f"{attempt} attempts failed — please check, still trying"
