"""Device protocol — structural typing for device health monitoring.

Devices DO NOT need to import or inherit from this Protocol. They just need
to expose the listed attributes and methods at runtime. The Protocol exists
so the orchestrator can type-hint against it (PEP 544 structural typing) and
so HealthBus can document the shape it expects.

This keeps device packages independent of the orchestrator: a Camera class
in its own package can be used standalone in a script without importing
``workspace``, yet still register with HealthBus when the orchestrator runs.
"""

from dataclasses import dataclass
from typing import Callable, Literal, Protocol, runtime_checkable


DeviceState = Literal["ok", "down", "recovering"]


@dataclass(frozen=True)
class DeviceEvent:
    """A single state-change observation emitted by HealthBus."""

    id: str
    state: DeviceState
    msg: str
    critical: bool
    ts: float


@runtime_checkable
class Device(Protocol):
    """The shape every device must expose to be observed by HealthBus.

    Implementations do not need to subclass this — duck-typed conformance is
    sufficient. Each member is read or invoked by the bus on its own thread,
    so the device is responsible for any internal locking required.
    """

    id: str
    state: DeviceState
    msg: str

    def on_state_change(self, callback: Callable[[DeviceState, str], None]) -> None:
        """Subscribe ``callback`` to be invoked on every state change.

        Called once by HealthBus during ``register``. The device is expected to
        invoke ``callback(new_state, msg)`` whenever its state transitions.
        """
        ...

    def recover(self) -> bool:
        """Attempt to bring the device back to ``"ok"``. Returns True on success."""
        ...

    def release(self) -> None:
        """Tear down the device — close handles, stop threads, free resources."""
        ...
