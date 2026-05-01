"""Tests for workspace.devices.MQTTOrchestrator.

These tests use a fake MQTT client (no broker required). They drive paho's
on_connect / on_message callbacks directly to simulate broker activity and
assert the orchestrator's reactions.
"""

from __future__ import annotations

import json
from typing import Any, Callable, List, Tuple

import pytest

from workspace.devices.orchestrator import MQTTOrchestrator


# ── Fake MQTT client (paho-mqtt v2 callback shape) ────────────────────────


class FakeMessage:
    def __init__(self, topic: str, payload: dict[str, Any] | bytes):
        self.topic = topic
        self.payload = (
            json.dumps(payload).encode() if isinstance(payload, dict) else payload
        )


class FakeClient:
    """Drop-in replacement for paho.mqtt.client.Client used in tests."""

    def __init__(self, client_id: str = ""):
        self.client_id = client_id
        self.on_connect: Callable | None = None
        self.on_message: Callable | None = None
        self.on_disconnect: Callable | None = None
        self.connected = False
        self.loop_started = False
        self.subscriptions: list[Tuple[str, int]] = []
        self.published: List[Tuple[str, str, int, bool]] = []
        self.disconnected = False

    # Methods used by MQTTOrchestrator ────────────────────────────────────
    def connect(self, host: str, port: int, keepalive: int = 30):
        self.connected = True

    def loop_start(self):
        self.loop_started = True

    def loop_stop(self):
        self.loop_started = False

    def disconnect(self):
        self.disconnected = True
        self.connected = False

    def subscribe(self, topics):
        # paho accepts a list of (topic, qos) tuples
        if isinstance(topics, list):
            self.subscriptions.extend(topics)
        else:
            self.subscriptions.append(topics)

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False):
        self.published.append((topic, payload, qos, retain))

    # Test-side helpers ───────────────────────────────────────────────────
    def fire_connect(self):
        if self.on_connect:
            self.on_connect(self, None, {}, 0)

    def fire_message(self, topic: str, payload):
        if self.on_message:
            self.on_message(self, None, FakeMessage(topic, payload))

    def fire_disconnect(self, reason=0):
        if self.on_disconnect:
            self.on_disconnect(self, None, reason)


class FakeRuntime:
    def __init__(self):
        self.pauses = 0
        self.resumes = 0

    def pause(self):
        self.pauses += 1

    def resume(self):
        self.resumes += 1


@pytest.fixture
def make_orch():
    """Build an MQTTOrchestrator wired to a FakeClient. Returns (orch, client, rt)."""

    def _factory(runtime=None):
        client = FakeClient()
        rt = runtime if runtime is not None else FakeRuntime()
        orch = MQTTOrchestrator(
            runtime=rt,
            broker_host="test-broker",
            broker_port=1883,
            client_id="test-client",
            client_factory=lambda cid: client,
        )
        # Simulate the broker accepting our connection.
        client.fire_connect()
        return orch, client, rt

    return _factory


# ── 1. Info + state messages create a device entry ────────────────────────


def test_info_then_state_creates_entry(make_orch):
    orch, client, rt = make_orch()

    client.fire_message(
        "device/cam:1/info",
        {"id": "cam:1", "kind": "camera", "critical": True, "meta": {"sn": "abc"}},
    )
    client.fire_message(
        "device/cam:1/state",
        {"state": "ok", "msg": "", "ts": 100.0},
    )

    devs = orch.list_devices()
    assert len(devs) == 1
    d = devs[0]
    assert d["id"] == "cam:1"
    assert d["kind"] == "camera"
    assert d["critical"] is True
    assert d["state"] == "ok"
    assert d["meta"] == {"sn": "abc"}


# ── 2. Critical-down pauses runtime exactly once ──────────────────────────


def test_critical_down_pauses_runtime_once(make_orch):
    orch, client, rt = make_orch()

    client.fire_message(
        "device/cam:1/info",
        {"id": "cam:1", "kind": "camera", "critical": True, "meta": {}},
    )
    client.fire_message(
        "device/cam:1/state", {"state": "ok", "msg": "", "ts": 1.0}
    )
    assert rt.pauses == 0

    client.fire_message(
        "device/cam:1/state", {"state": "down", "msg": "lost", "ts": 2.0}
    )
    assert rt.pauses == 1

    # Second down-event without intermediate ok must NOT pause again
    # (only edge transitions trigger a pause).
    client.fire_message(
        "device/cam:1/state", {"state": "down", "msg": "still", "ts": 3.0}
    )
    assert rt.pauses == 1


# ── 3. Non-critical down does NOT pause ───────────────────────────────────


def test_non_critical_down_does_not_pause(make_orch):
    orch, client, rt = make_orch()

    client.fire_message(
        "device/aux:1/info",
        {"id": "aux:1", "kind": "aux", "critical": False, "meta": {}},
    )
    client.fire_message(
        "device/aux:1/state", {"state": "down", "msg": "x", "ts": 1.0}
    )
    assert rt.pauses == 0


# ── 4. State transitions update the cached entry ──────────────────────────


def test_state_transitions_update_entry(make_orch):
    orch, client, rt = make_orch()

    client.fire_message(
        "device/cam:1/info",
        {"id": "cam:1", "kind": "camera", "critical": True, "meta": {}},
    )
    for state, msg in [("ok", ""), ("down", "lost"), ("recovering", "..."), ("ok", "")]:
        client.fire_message(
            "device/cam:1/state", {"state": state, "msg": msg, "ts": 1.0}
        )

    snap = orch.get("cam:1")
    assert snap["state"] == "ok"
    assert snap["msg"] == ""


# ── 5. recover() publishes and waits for matching reply ───────────────────


def test_recover_round_trips_via_reply(make_orch):
    import threading

    orch, client, _ = make_orch()

    # Pre-register the device so recover() targets it.
    client.fire_message(
        "device/pump:1/info",
        {"id": "pump:1", "kind": "pump", "critical": True, "meta": {}},
    )

    # When the orchestrator publishes the cmd, capture req_id and fire a reply.
    def _wait_for_publish_then_reply():
        # Wait for the publish to land
        for _ in range(100):
            if any(p[0] == "device/pump:1/cmd/recover" for p in client.published):
                break
            import time
            time.sleep(0.005)
        topic, payload, _, _ = next(
            p for p in client.published if p[0] == "device/pump:1/cmd/recover"
        )
        req = json.loads(payload)
        client.fire_message(
            "device/pump:1/cmd/recover/reply",
            {"req_id": req["req_id"], "ok": True, "state": "ok", "msg": ""},
        )

    threading.Thread(target=_wait_for_publish_then_reply, daemon=True).start()

    result = orch.recover("pump:1", timeout=2.0)
    assert result["ok"] is True
    assert result["state"] == "ok"


def test_recover_times_out_when_no_reply(make_orch):
    orch, client, _ = make_orch()
    client.fire_message(
        "device/pump:1/info",
        {"id": "pump:1", "kind": "pump", "critical": True, "meta": {}},
    )
    # No reply will be fired — recover should time out cleanly.
    result = orch.recover("pump:1", timeout=0.1)
    assert result["ok"] is False
    assert "timed out" in result["msg"]


# ── 6. Reconnect re-subscribes ────────────────────────────────────────────


def test_reconnect_resubscribes(make_orch):
    orch, client, _ = make_orch()

    initial = len(client.subscriptions)
    assert initial >= 3  # info / state / cmd-reply each subscribed

    # Simulate a drop-and-reconnect cycle.
    client.fire_disconnect(reason=1)
    client.fire_connect()

    # Subscriptions list grew — the on_connect handler resubscribed.
    assert len(client.subscriptions) >= initial * 2


# ── 7. subscribe() callback receives device snapshots ─────────────────────


def test_subscribe_receives_snapshots(make_orch):
    orch, client, _ = make_orch()
    received: List[dict[str, Any]] = []

    unsub = orch.subscribe(received.append)

    client.fire_message(
        "device/cam:1/info",
        {"id": "cam:1", "kind": "camera", "critical": True, "meta": {}},
    )
    client.fire_message(
        "device/cam:1/state", {"state": "down", "msg": "x", "ts": 1.0}
    )

    assert len(received) == 2
    assert received[0]["id"] == "cam:1"
    assert received[1]["state"] == "down"

    # Unsubscribe stops further deliveries
    unsub()
    client.fire_message(
        "device/cam:1/state", {"state": "ok", "msg": "", "ts": 2.0}
    )
    assert len(received) == 2


# ── 8. Faulty subscriber doesn't break others ─────────────────────────────


def test_faulty_subscriber_isolated(make_orch):
    orch, client, _ = make_orch()
    good: List[dict[str, Any]] = []

    def bad(_):
        raise RuntimeError("boom")

    orch.subscribe(bad)
    orch.subscribe(good.append)

    client.fire_message(
        "device/cam:1/info",
        {"id": "cam:1", "kind": "camera", "critical": True, "meta": {}},
    )
    assert len(good) == 1


# ── 9. Bad payloads are ignored, not fatal ────────────────────────────────


def test_bad_json_is_ignored(make_orch):
    orch, client, _ = make_orch()
    client.fire_message("device/cam:1/info", b"not json")
    client.fire_message("device/cam:1/state", b"also not json")
    assert orch.list_devices() == []


# ── 10. close() is idempotent ─────────────────────────────────────────────


def test_close_is_idempotent(make_orch):
    orch, client, _ = make_orch()
    orch.close()
    orch.close()
    assert client.disconnected is True
