"""Tests for workspace.devices.HealthBus and the Device protocol shape."""

from typing import Callable, List

import pytest

from workspace.devices import Device, DeviceEvent, DeviceState, HealthBus


# ── Test doubles ──────────────────────────────────────────────────────────


class FakeDevice:
    """Plain class — does NOT inherit from Device. Conforms structurally."""

    def __init__(self, id: str, state: DeviceState = "ok", msg: str = ""):
        self.id = id
        self.state = state
        self.msg = msg
        self._listeners: List[Callable[[DeviceState, str], None]] = []

    def on_state_change(self, callback: Callable[[DeviceState, str], None]) -> None:
        self._listeners.append(callback)

    def recover(self) -> bool:
        self.state = "ok"
        self.msg = ""
        for cb in list(self._listeners):
            cb(self.state, self.msg)
        return True

    def release(self) -> None:
        self._listeners.clear()

    def transition(self, state: DeviceState, msg: str = "") -> None:
        """Helper to drive state changes from the test."""
        self.state = state
        self.msg = msg
        for cb in list(self._listeners):
            cb(state, msg)


class FakeRuntime:
    """Minimal runtime double — only counts pause/resume calls."""

    def __init__(self):
        self.pauses = 0
        self.resumes = 0

    def pause(self):
        self.pauses += 1

    def resume(self):
        self.resumes += 1


# ── Sanity: structural typing ─────────────────────────────────────────────


def test_fakedevice_satisfies_device_protocol():
    """FakeDevice doesn't inherit Device but isinstance still works (runtime_checkable)."""
    dev = FakeDevice("d1")
    assert isinstance(dev, Device)


# ── 1. Initial event on registration ──────────────────────────────────────


def test_initial_event_emitted_on_register():
    bus = HealthBus()
    received: List[DeviceEvent] = []
    bus.subscribe(received.append)

    dev = FakeDevice("d1", state="ok", msg="hello")
    bus.register(dev)

    assert len(received) == 1
    evt = received[0]
    assert isinstance(evt, DeviceEvent)
    assert evt.id == "d1"
    assert evt.state == "ok"
    assert evt.msg == "hello"
    assert evt.critical is True


# ── 2. Sequence of state transitions ──────────────────────────────────────


def test_transitions_produce_events_in_order():
    bus = HealthBus()
    received: List[DeviceEvent] = []
    bus.subscribe(received.append)

    dev = FakeDevice("d1", state="ok")
    bus.register(dev)

    dev.transition("down", "usb gone")
    dev.transition("recovering", "reconnecting")
    dev.transition("ok", "back")

    states = [e.state for e in received]
    assert states == ["ok", "down", "recovering", "ok"]
    assert received[1].msg == "usb gone"


# ── 3. Critical-down pauses runtime exactly once ──────────────────────────


def test_critical_down_calls_runtime_pause_once():
    rt = FakeRuntime()
    bus = HealthBus(runtime=rt)

    dev = FakeDevice("cam", state="ok")
    bus.register(dev, critical=True)
    assert rt.pauses == 0  # initial "ok" should NOT pause

    dev.transition("down", "usb error")
    assert rt.pauses == 1


# ── 4. Non-critical down does NOT pause runtime ───────────────────────────


def test_non_critical_down_does_not_pause():
    rt = FakeRuntime()
    bus = HealthBus(runtime=rt)

    dev = FakeDevice("aux", state="ok")
    bus.register(dev, critical=False)

    dev.transition("down", "no biggie")
    assert rt.pauses == 0


# ── 5. One faulty listener doesn't stop the others ────────────────────────


def test_faulty_listener_does_not_block_others():
    bus = HealthBus()
    good_a: List[DeviceEvent] = []
    good_b: List[DeviceEvent] = []

    def bad_listener(_evt):
        raise RuntimeError("boom")

    bus.subscribe(good_a.append)
    bus.subscribe(bad_listener)
    bus.subscribe(good_b.append)

    dev = FakeDevice("d1")
    bus.register(dev)
    dev.transition("down", "x")

    assert len(good_a) == 2
    assert len(good_b) == 2


# ── 6. Unsubscribe ─────────────────────────────────────────────────────────


def test_unsubscribe_stops_delivery():
    bus = HealthBus()
    received: List[DeviceEvent] = []
    unsub = bus.subscribe(received.append)

    dev = FakeDevice("d1")
    bus.register(dev)  # 1 event
    assert len(received) == 1

    unsub()
    dev.transition("down")
    assert len(received) == 1  # no new delivery


# ── 7. _all_critical_ok ────────────────────────────────────────────────────


def test_all_critical_ok_reflects_critical_devices_only():
    bus = HealthBus()

    a = FakeDevice("a", state="ok")
    b = FakeDevice("b", state="ok")
    c = FakeDevice("c", state="down")  # non-critical, should not block

    bus.register(a, critical=True)
    bus.register(b, critical=True)
    bus.register(c, critical=False)

    assert bus._all_critical_ok() is True

    a.transition("down")
    assert bus._all_critical_ok() is False

    a.transition("ok")
    assert bus._all_critical_ok() is True


# ── 8. Re-registering replaces the previous entry ─────────────────────────


def test_reregister_replaces_previous_entry():
    bus = HealthBus()
    events: List[DeviceEvent] = []
    bus.subscribe(events.append)

    dev_old = FakeDevice("d1", state="ok", msg="old")
    bus.register(dev_old)
    events.clear()  # drop the initial "ok" from the old device

    dev_new = FakeDevice("d1", state="ok", msg="new")
    bus.register(dev_new)
    # one initial event from the new device
    assert len(events) == 1
    assert events[0].msg == "new"

    # Old device's transitions must be ignored — its closure was unwired.
    dev_old.transition("down", "ignored")
    assert len(events) == 1  # no new event

    # New device's transitions are received normally.
    dev_new.transition("down", "real")
    assert len(events) == 2
    assert events[1].msg == "real"


# ── Bonus: registry diagnostics ───────────────────────────────────────────


def test_status_and_down_devices():
    bus = HealthBus()
    a = FakeDevice("a", state="ok")
    b = FakeDevice("b", state="down", msg="lost")
    bus.register(a, critical=True)
    bus.register(b, critical=False)

    snap = bus.status()
    assert snap["a"]["state"] == "ok"
    assert snap["a"]["critical"] is True
    assert snap["b"]["state"] == "down"
    assert snap["b"]["msg"] == "lost"
    assert snap["b"]["critical"] is False

    assert sorted(bus.down_devices()) == ["b"]


def test_no_auto_resume_on_recovery():
    """Recovery clears state but does NOT call runtime.resume()."""
    rt = FakeRuntime()
    bus = HealthBus(runtime=rt)

    dev = FakeDevice("cam", state="ok")
    bus.register(dev, critical=True)

    dev.transition("down", "lost")
    dev.transition("ok", "back")

    assert rt.pauses == 1
    assert rt.resumes == 0  # no auto-resume
