"""HealthBus — central registry and event fan-out for device state changes.

Wires any conforming :class:`Device` into a single channel. The orchestrator
subscribes once and applies a uniform policy: when a critical device drops to
``"down"`` the bus pauses the runtime; recovery to ``"ok"`` clears the
internal block but does NOT auto-resume — the operator decides when work can
safely continue.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

from workspace.devices.protocol import Device, DeviceEvent, DeviceState

if TYPE_CHECKING:
    from workspace.runtime import Runtime


class HealthBus:
    """Central event bus for :class:`Device` health.

    Args:
        runtime: Optional ``Runtime`` reference. When set, the bus calls
            ``runtime.pause()`` on a critical device transitioning to
            ``"down"``. Recovery does not auto-resume.
    """

    def __init__(self, runtime: Optional["Runtime"] = None):
        self.runtime = runtime
        self._lock = threading.RLock()
        # device_id -> (device, critical, unsubscribe_callback_on_device)
        self._devices: dict[str, tuple[Device, bool]] = {}
        # device_id -> the closure we subscribed to that device's on_state_change
        self._device_callbacks: dict[str, Callable[[DeviceState, str], None]] = {}
        self._subscribers: list[Callable[[DeviceEvent], None]] = []

    # ── Registration ──────────────────────────────────────────────────────

    def register(self, device: Device, *, critical: bool = True) -> None:
        """Track ``device`` and start fanning out its state changes.

        Re-registering with the same ``device.id`` replaces the previous entry.
        Emits an initial event reflecting the device's current state so
        subscribers always see at least one event per registration.
        """
        with self._lock:
            # Re-registering replaces the previous entry. We can't truly
            # unsubscribe from the old device (the Protocol exposes no
            # remove-listener method), so the old closure remains attached
            # to the old device — but it self-guards: forwarding only
            # happens when it's still the active closure for this id.
            if device.id in self._devices:
                self._devices.pop(device.id, None)
                self._device_callbacks.pop(device.id, None)

            def _forward(state: DeviceState, msg: str, _id: str = device.id, _self_ref=None) -> None:
                # _self_ref is bound below to point at this exact closure.
                with self._lock:
                    if self._device_callbacks.get(_id) is not _self_ref:
                        return  # superseded by a newer registration
                self._on_change(_id, state, msg)

            # Bind _self_ref to the closure itself so the guard above can
            # check identity. Done via the function's __defaults__ to avoid
            # capturing a name that doesn't exist yet at def-time.
            _forward.__defaults__ = (device.id, _forward)

            self._devices[device.id] = (device, critical)
            self._device_callbacks[device.id] = _forward
            device.on_state_change(_forward)

        # Initial event — emit OUTSIDE the lock so subscribers don't block
        # other registrations.
        self._on_change(device.id, device.state, device.msg)

    def unregister(self, device_id: str) -> None:
        """Stop tracking the device with ``device_id``. Silently ignored if absent."""
        with self._lock:
            self._devices.pop(device_id, None)
            self._device_callbacks.pop(device_id, None)

    # ── Subscription ──────────────────────────────────────────────────────

    def subscribe(self, callback: Callable[[DeviceEvent], None]) -> Callable[[], None]:
        """Register ``callback`` to receive every :class:`DeviceEvent`.

        Returns:
            An unsubscribe callable — invoke it to stop receiving events.
        """
        with self._lock:
            self._subscribers.append(callback)

        def _unsub() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return _unsub

    # ── Internals ─────────────────────────────────────────────────────────

    def _on_change(self, device_id: str, state: DeviceState, msg: str) -> None:
        """Build a DeviceEvent and fan it out. Safe to call from any thread."""
        with self._lock:
            entry = self._devices.get(device_id)
            if entry is None:
                # Device unregistered between event scheduling and delivery.
                return
            _, critical = entry
            subscribers_snapshot = list(self._subscribers)

        event = DeviceEvent(
            id=device_id,
            state=state,
            msg=msg,
            critical=critical,
            ts=time.time(),
        )

        # Deliver outside the lock so a slow listener can't stall registrations.
        for cb in subscribers_snapshot:
            try:
                cb(event)
            except Exception:
                # One bad listener must not break the bus or its peers.
                pass

        # Runtime policy: pause on critical-down. Recovery is manual.
        if self.runtime is not None and critical and state == "down":
            try:
                self.runtime.pause()
            except Exception:
                # pause() failure must not break event delivery to others.
                pass

    # ── Diagnostics ───────────────────────────────────────────────────────

    def _all_critical_ok(self) -> bool:
        """True iff every critical device's current state is ``"ok"``."""
        with self._lock:
            for device, critical in self._devices.values():
                if critical and device.state != "ok":
                    return False
            return True

    def status(self) -> dict:
        """Return ``{device_id: {state, msg, critical}}`` snapshot for diagnostics."""
        with self._lock:
            return {
                did: {
                    "state": dev.state,
                    "msg": dev.msg,
                    "critical": critical,
                }
                for did, (dev, critical) in self._devices.items()
            }

    def down_devices(self) -> list[str]:
        """Return ids of devices not currently in the ``"ok"`` state."""
        with self._lock:
            return [did for did, (dev, _) in self._devices.items() if dev.state != "ok"]
