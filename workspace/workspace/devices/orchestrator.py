"""MQTTOrchestrator — orchestrator-side consumer of the device MQTT spec.

Tracks every device on the bus via retained ``device/<id>/info`` and
``device/<id>/state`` topics, pauses :class:`workspace.runtime.Runtime`
on critical-down transitions, and exposes round-trip ``recover`` /
``release`` commands.

See ``docs/device-guide.md`` (Appendix A for the wire-level protocol).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

import paho.mqtt.client as mqtt

if TYPE_CHECKING:
    from workspace.runtime import Runtime


log = logging.getLogger(__name__)


# Default broker connection — overridable via env vars or constructor args.
DEFAULT_BROKER_HOST = os.environ.get("DEVICE_MQTT_HOST", "localhost")
DEFAULT_BROKER_PORT = int(os.environ.get("DEVICE_MQTT_PORT", "1883"))


@dataclass
class DeviceEntry:
    """Cached view of one device's state on the orchestrator side."""

    id: str
    state: str = "down"
    msg: str = "no state yet"
    kind: str = "device"
    critical: bool = True
    # Whether this device is in simulation mode. Sourced from the
    # ``info`` payload's ``sim`` field. Sim devices are excluded from
    # the critical-down auto-pause path (sim has no failure mode) and
    # rendered in the panel with a "SIM" badge.
    sim: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    ts: float = 0.0
    # Whether the device service process is alive on the bus. Set True by
    # any incoming info/state message (something is publishing), flipped
    # False only by an LWT payload that carries ``online: false``. Used
    # by recover/release to fast-fail when nobody is listening.
    online: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "msg": self.msg,
            "kind": self.kind,
            "critical": self.critical,
            "sim": self.sim,
            "meta": dict(self.meta),
            "ts": self.ts,
            "online": self.online,
        }


@dataclass
class _PendingRequest:
    event: threading.Event
    payload: Optional[dict[str, Any]] = None


class MQTTOrchestrator:
    """Subscribe to ``device/+/info`` and ``device/+/state``, route critical-down
    transitions into ``runtime.pause()``, and expose ``recover`` / ``release``.

    The orchestrator does not auto-resume the runtime when a device recovers —
    that's a manual decision (the operator may need to inspect state first).

    Args:
        runtime: Optional Runtime to pause on critical-down.
        broker_host: MQTT broker host. Defaults to ``$DEVICE_MQTT_HOST`` or
            ``"localhost"``.
        broker_port: MQTT broker port. Defaults to ``$DEVICE_MQTT_PORT`` or
            ``1883``.
        client_id: MQTT client id. Defaults to a unique ``orchestrator-<uuid>``.
        client_factory: Internal hook for tests — a callable returning an
            object with the paho-mqtt client interface. Defaults to the real
            ``paho.mqtt.client.Client``.
    """

    def __init__(
        self,
        runtime: Optional["Runtime"] = None,
        *,
        broker_host: Optional[str] = None,
        broker_port: Optional[int] = None,
        client_id: Optional[str] = None,
        client_factory: Optional[Callable[[str], Any]] = None,
        claim_resolver: Optional[Callable[[str], str]] = None,
    ):
        self.runtime = runtime
        # Optional callable returning ``"sim"`` or ``"real"`` for a
        # given device id — the project-level claim mode. When the
        # claim is ``"sim"``, auto-pause is skipped for that device
        # even if the bus reports it down. Set by the workspace at
        # startup; absent in standalone tests.
        self.claim_resolver = claim_resolver
        self.broker_host = broker_host if broker_host is not None else DEFAULT_BROKER_HOST
        self.broker_port = broker_port if broker_port is not None else DEFAULT_BROKER_PORT
        self._client_id = client_id or f"orchestrator-{uuid.uuid4().hex[:8]}"

        self._lock = threading.RLock()
        self._devices: dict[str, DeviceEntry] = {}
        # Tracks every device id we have ever received a `state` message
        # for. Distinct from `_devices` (which is also populated by
        # `info` messages), so that the FIRST state we ever see for a
        # device — even if info arrived first — can be detected as an
        # "initial state" event rather than a transition.
        self._seen_state_ids: set[str] = set()
        self._pending: dict[str, _PendingRequest] = {}
        self._subscribers: list[Callable[[DeviceEntry], None]] = []
        self._closed = False

        if client_factory is None:
            client_factory = lambda cid: mqtt.Client(  # noqa: E731
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=cid,
            )
        self.client = client_factory(self._client_id)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        # Connect-or-warn: a flaky broker must not crash workspace startup.
        try:
            self.client.connect(self.broker_host, self.broker_port, keepalive=30)
            self.client.loop_start()
        except Exception as ex:
            log.warning(
                "MQTTOrchestrator: failed initial connect to %s:%s — %s. "
                "Devices will appear when the broker becomes reachable.",
                self.broker_host, self.broker_port, ex,
            )
            # Even if connect failed, start the loop so reconnect attempts run.
            try:
                self.client.loop_start()
            except Exception:
                pass

    # ── paho callbacks (v2 API) ───────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        # Re-subscribe on every connect so reconnects don't lose subscriptions.
        try:
            client.subscribe([
                ("device/+/info", 1),
                ("device/+/state", 1),
                ("device/+/cmd/+/reply", 1),
            ])
            log.info("MQTTOrchestrator: connected to %s:%s, subscribed.",
                     self.broker_host, self.broker_port)
        except Exception:
            log.exception("MQTTOrchestrator: subscribe failed on connect")

    def _on_disconnect(self, client, userdata, *args, **kwargs):
        # Paho schedules its own reconnect; just log.
        log.info("MQTTOrchestrator: disconnected (reason=%s)", args)

    def _on_message(self, client, userdata, message):
        try:
            self._dispatch(message.topic, message.payload)
        except Exception:
            log.exception("MQTTOrchestrator: failed to handle message on %s",
                          getattr(message, "topic", "<unknown>"))

    # ── Topic dispatch ────────────────────────────────────────────────────

    def _dispatch(self, topic: str, raw_payload: bytes) -> None:
        parts = topic.split("/")
        if len(parts) < 3 or parts[0] != "device":
            return
        device_id = parts[1]

        # Empty payload on a retained-topic is MQTT's "clear retained"
        # signal — the broker delivers a zero-length publish to current
        # subscribers and removes the retained message for future ones.
        # Treat it as a device-removed event so panels don't keep
        # showing a stale entry that no publisher owns anymore.
        if not raw_payload and parts[2] in ("info", "state"):
            self._handle_clear(device_id)
            return

        try:
            payload = json.loads(raw_payload.decode())
        except Exception:
            log.warning("MQTTOrchestrator: bad JSON on %s", topic)
            return

        if parts[2] == "info" and len(parts) == 3:
            self._handle_info(device_id, payload)
        elif parts[2] == "state" and len(parts) == 3:
            self._handle_state(device_id, payload)
        elif (
            len(parts) == 5
            and parts[2] == "cmd"
            and parts[4] == "reply"
        ):
            self._handle_reply(payload)
        # Anything else: silently ignore — not for us.

    def _handle_clear(self, device_id: str) -> None:
        """Drop ``device_id`` from the cache and notify subscribers.

        Called when the broker delivers a zero-length retained publish
        on either ``info`` or ``state`` — i.e. someone (or the broker
        itself, after a clear-retained pub) has signalled that this
        device's persistent metadata is gone. The orchestrator should
        forget the device so the per-project panel can re-render
        without the stale entry.
        """
        with self._lock:
            existed = self._devices.pop(device_id, None) is not None
            self._seen_state_ids.discard(device_id)
        if not existed:
            return
        # Synthetic snapshot tells subscribers (panel WS, runtime pause
        # logic) to drop this id — explicit "cleared" marker so handlers
        # can distinguish from a transient down state.
        self._notify({
            "id": device_id,
            "state": "down",
            "msg": "retained cleared",
            "kind": "device",
            "critical": False,
            "sim": False,
            "meta": {},
            "ts": time.time(),
            "online": False,
            "cleared": True,
        })

    def _handle_info(self, device_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            entry = self._devices.get(device_id)
            if entry is None:
                entry = DeviceEntry(id=device_id)
                self._devices[device_id] = entry
            entry.kind = str(payload.get("kind", entry.kind))
            entry.critical = bool(payload.get("critical", entry.critical))
            entry.sim = bool(payload.get("sim", entry.sim))
            entry.meta = dict(payload.get("meta", entry.meta))
            # info only arrives from a live publisher.
            entry.online = True
            snapshot = entry.to_dict()
        # Notify subscribers (info changes also count as observable events).
        self._notify(snapshot)

    def _handle_state(self, device_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            new_online = bool(payload.get("online", True))
            new_state_str = str(payload.get("state", "down"))
            new_msg = str(payload.get("msg", ""))
            ts = float(payload.get("ts", time.time()))

            entry = self._devices.get(device_id)
            if entry is None:
                entry = DeviceEntry(id=device_id)
                self._devices[device_id] = entry

            # ``online: false`` is the broker's "previous publisher is
            # gone" signal — emitted by an adapter's clean-shutdown
            # path or LWT, then retained on the topic. On a fresh
            # workspace launch we'll see it FIRST (before any live
            # publisher has spoken), and treating it as the device's
            # first state observation would auto-pause every restart
            # against stale data: the operator clicks Start, sees
            # Resume, and never knows why. Bail with presence/ts
            # update only — the next live message owns first-state
            # logic. A genuine LWT mid-run still surfaces in the
            # panel as the "offline" pill (driven by entry.online),
            # so the operator sees the fault without needing the
            # runtime paused on top of it.
            if not new_online:
                entry.online = False
                entry.ts = ts
                snapshot = entry.to_dict()
            else:
                # Live publisher event. First-state detection must be
                # independent of ``_devices`` membership because
                # ``_handle_info`` may have created the entry before
                # the first state arrived.
                is_first_state = device_id not in self._seen_state_ids
                self._seen_state_ids.add(device_id)
                old_state = entry.state
                entry.state = new_state_str
                entry.msg = new_msg
                entry.ts = ts
                entry.online = True
                critical = entry.critical
                sim = entry.sim
                new_state = entry.state
                snapshot = entry.to_dict()

        # online: false branch is presence-only — notify and return,
        # never pause. The live publisher's next message drives any
        # auto-pause decisions.
        if not new_online:
            self._notify(snapshot)
            return

        # Pause runtime on a critical-down event. Two cases count:
        #   (a) a real transition (old != down → new = down), e.g. a
        #       cable was unplugged mid-run, OR
        #   (b) the FIRST state we ever see is "down" (no prior state
        #       to transition from) — happens at workspace launch when
        #       the robot is already unreachable. Without (b) the
        #       operator could click Start while a critical device is
        #       down and the workflow would crash on its first call.
        # Two pause-skip gates, both honoured:
        #   * ``bus.sim`` — the publisher self-flags as sim. No real
        #     failure mode possible. (Used by the robot when Core is
        #     in sim mode — Core IS the publisher.)
        #   * ``claim == "sim"`` — the project declares it's using this
        #     device in sim mode regardless of what the bus says. (Used
        #     for cameras: the daemon publishes truth on the bus, but
        #     this project doesn't care because it uses canned values.)
        is_initial_down = is_first_state and new_state == "down"
        is_transition_to_down = (not is_first_state) and new_state == "down" and old_state != "down"
        claim = None
        if self.claim_resolver is not None:
            try:
                claim = self.claim_resolver(device_id)
            except Exception:
                log.exception("MQTTOrchestrator: claim_resolver raised for %s",
                              device_id)
                claim = None
        project_claims_sim = claim == "sim"
        # "foreign" — the project doesn't declare this device at all
        # (stale retained topics from dead sessions, other projects on
        # a shared broker). Never pause for someone else's corpse.
        is_foreign = claim == "foreign"
        if (
            self.runtime is not None
            and critical
            and not sim
            and not project_claims_sim
            and not is_foreign
            and (is_initial_down or is_transition_to_down)
        ):
            try:
                self.runtime.pause()
            except Exception:
                log.exception("MQTTOrchestrator: runtime.pause() failed for %s",
                              device_id)

        self._notify(snapshot)

    def _handle_reply(self, payload: dict[str, Any]) -> None:
        req_id = payload.get("req_id")
        if not req_id:
            return
        with self._lock:
            pending = self._pending.get(req_id)
        if pending is None:
            return
        pending.payload = payload
        pending.event.set()

    def _notify(self, snapshot: dict[str, Any]) -> None:
        """Fan out to subscribers outside any lock; isolate listener faults."""
        with self._lock:
            listeners = list(self._subscribers)
        for cb in listeners:
            try:
                cb(snapshot)
            except Exception:
                log.exception("MQTTOrchestrator: subscriber raised; suppressed")

    # ── Public API ────────────────────────────────────────────────────────

    def list_devices(self) -> list[dict[str, Any]]:
        """Return a snapshot of every known device."""
        with self._lock:
            return [entry.to_dict() for entry in self._devices.values()]

    def get(self, device_id: str) -> Optional[dict[str, Any]]:
        """Return one device snapshot, or None if unknown."""
        with self._lock:
            entry = self._devices.get(device_id)
            return entry.to_dict() if entry is not None else None

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Register a callback for every device info/state update.

        Returns:
            An unsubscribe callable.
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

    def recover(self, device_id: str, timeout: float = 30.0) -> dict[str, Any]:
        """Send ``device/<id>/cmd/recover`` and wait for the matching reply."""
        return self._send_cmd(device_id, "recover", timeout)

    def release(self, device_id: str, timeout: float = 5.0) -> dict[str, Any]:
        """Send ``device/<id>/cmd/release`` and wait for the matching reply."""
        return self._send_cmd(device_id, "release", timeout)

    def recover_async(self, device_id: str) -> dict[str, Any]:
        """Publish ``device/<id>/cmd/recover`` and return immediately.

        Use this from HTTP handlers / UI code: the visual feedback flows
        through state events rather than the cmd reply, so blocking the
        request thread on a 30 s reply window is wasted latency.
        """
        return self._publish_cmd(device_id, "recover")

    def release_async(self, device_id: str) -> dict[str, Any]:
        """Publish ``device/<id>/cmd/release`` and return immediately."""
        return self._publish_cmd(device_id, "release")

    def _publish_cmd(self, device_id: str, action: str) -> dict[str, Any]:
        """Fire-and-forget publish of a cmd, with offline fast-fail."""
        with self._lock:
            entry = self._devices.get(device_id)
            online = entry.online if entry is not None else False
        if not online:
            return {
                "ok": False,
                "msg": "device service offline — start the service",
                "offline": True,
            }
        try:
            self.client.publish(
                f"device/{device_id}/cmd/{action}",
                json.dumps({"req_id": str(uuid.uuid4())}),
                qos=1,
                retain=False,
            )
        except Exception as ex:
            log.exception("MQTTOrchestrator: publish failed for %s/%s",
                          device_id, action)
            return {"ok": False, "msg": f"publish failed: {ex}"}
        return {"ok": True, "queued": True, "msg": f"{action} sent"}

    def _send_cmd(self, device_id: str, action: str, timeout: float) -> dict[str, Any]:
        # Fast-fail when the device service isn't on the bus — otherwise
        # we'd block the caller for the full timeout waiting for a reply
        # that nobody is listening to send.
        with self._lock:
            entry = self._devices.get(device_id)
            online = entry.online if entry is not None else False
        if not online:
            return {
                "ok": False,
                "msg": "device service offline — start the service",
                "offline": True,
            }
        req_id = str(uuid.uuid4())
        pending = _PendingRequest(event=threading.Event())
        with self._lock:
            self._pending[req_id] = pending
        try:
            self.client.publish(
                f"device/{device_id}/cmd/{action}",
                json.dumps({"req_id": req_id}),
                qos=1,
                retain=False,
            )
        except Exception as ex:
            log.exception("MQTTOrchestrator: publish failed for %s/%s",
                          device_id, action)
            with self._lock:
                self._pending.pop(req_id, None)
            return {"ok": False, "msg": f"publish failed: {ex}"}

        pending.event.wait(timeout)
        with self._lock:
            self._pending.pop(req_id, None)

        if pending.payload is None:
            return {"ok": False, "msg": "command timed out"}
        return pending.payload

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Stop the network loop and disconnect cleanly. Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        try:
            self.client.loop_stop()
        except Exception:
            pass
        try:
            self.client.disconnect()
        except Exception:
            pass

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
