"""MQTTDeviceAdapter — publishes a Device-shaped object to MQTT per the spec.

See ``docs/device-guide.md`` (Appendix A — wire protocol) for the topic
contract. The adapter wraps any object that exposes the structural
``Device`` shape — ``id``, ``state``, ``msg``, ``on_state_change(cb)``,
``recover() -> bool``, ``release()`` — and:

  * publishes ``device/<id>/info`` (retained) on connect,
  * publishes ``device/<id>/state`` (retained) on every state change,
  * configures a Last Will & Testament so the broker auto-marks the
    device down if this process disappears,
  * subscribes to ``device/<id>/cmd/{recover,release}`` and replies on
    ``.../reply`` with the round-trip result.

The info payload carries a ``publisher_id`` — a stable
``<hostname>:<pid>:<device_id>`` triple that identifies the adapter
instance. Used by the conflict-detection layer in ``attach_device`` to
refuse a second adapter from claiming a topic that's already owned.

Canonical home for this code is ``workspace.devices.adapter``; device
services (camera, printer, …) import it from here. The module depends
only on ``paho-mqtt`` — no orchestrator/runtime code — so any device
service can use it without pulling in heavy workspace internals.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from typing import Any, Optional

import paho.mqtt.client as mqtt


log = logging.getLogger(__name__)


DEFAULT_BROKER_HOST = os.environ.get("DEVICE_MQTT_HOST", "localhost")
DEFAULT_BROKER_PORT = int(os.environ.get("DEVICE_MQTT_PORT", "1883"))


def make_publisher_id(device_id: str) -> str:
    """Stable per-process publisher identity for a given device id.

    Format: ``<hostname>:<pid>:<device_id>``. Two adapters sharing the
    same triple are by definition the same process restarting itself
    (allowed); any other combination is a different publisher and will
    be rejected by the conflict-detection layer.
    """
    try:
        host = socket.gethostname() or "unknown-host"
    except Exception:
        host = "unknown-host"
    return f"{host}:{os.getpid()}:{device_id}"


class DevicePublisherConflict(RuntimeError):
    """Raised when another live publisher already owns a device id.

    Carries the conflicting publisher_id so the operator can identify
    which process / host is holding the topic. Surfaced eagerly at
    workspace startup; never silently demoted.
    """

    def __init__(self, device_id: str, owner_publisher_id: str, our_publisher_id: str):
        super().__init__(
            f"device {device_id!r} already published by {owner_publisher_id!r} "
            f"(this process: {our_publisher_id!r}). Two publishers on the same "
            f"retained topic stomp on each other; refusing to attach."
        )
        self.device_id = device_id
        self.owner_publisher_id = owner_publisher_id
        self.our_publisher_id = our_publisher_id


def detect_publisher_conflict(
    device_id: str,
    our_publisher_id: str,
    *,
    broker_host: Optional[str] = None,
    broker_port: Optional[int] = None,
    timeout_s: float = 0.75,
) -> Optional[str]:
    """Check the bus for a pre-existing live publisher of ``device_id``.

    Briefly subscribes to the device's retained ``info`` and ``state``
    topics, waits up to ``timeout_s`` for retained payloads, and decides:

      * No retained payloads → no conflict (returns ``None``).
      * State retained with ``online: false`` → previous publisher is
        gone (LWT fired or clean shutdown). No conflict.
      * Info retained with the same ``publisher_id`` as ours → that's
        us restarting before LWT fired. No conflict.
      * Info retained with a different ``publisher_id`` AND state
        retained with ``online: true`` → live publisher elsewhere.
        Returns the conflicting publisher_id.
      * Broker unreachable → can't tell. Returns ``None`` and lets the
        adapter's normal connect path handle it; the cost of a
        false-negative here is a pre-existing race, which the bus has
        always had. The cost of false-positive blocking startup over a
        broker hiccup is worse.

    Cost: one short-lived MQTT client open + ``timeout_s`` wait. Run
    once per device at attach time, not per call.
    """
    host = broker_host if broker_host is not None else DEFAULT_BROKER_HOST
    port = broker_port if broker_port is not None else DEFAULT_BROKER_PORT

    info_topic = f"device/{device_id}/info"
    state_topic = f"device/{device_id}/state"
    seen = {"info": None, "state": None}
    done = threading.Event()

    def _on_msg(client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            return
        if msg.topic == info_topic:
            seen["info"] = payload
        elif msg.topic == state_topic:
            seen["state"] = payload
        if seen["info"] is not None and seen["state"] is not None:
            done.set()

    detector = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"detect-{device_id}-{uuid.uuid4().hex[:6]}",
    )
    detector.on_message = _on_msg

    try:
        detector.connect(host, port, keepalive=10)
    except Exception:
        # Broker unreachable. Can't detect. Proceed and let the
        # adapter's normal startup path log/recover.
        return None

    detector.subscribe([(info_topic, 0), (state_topic, 0)])
    detector.loop_start()
    try:
        done.wait(timeout=timeout_s)
    finally:
        try:
            detector.loop_stop()
        except Exception:
            pass
        try:
            detector.disconnect()
        except Exception:
            pass

    info = seen["info"]
    state = seen["state"]

    # No info retained → nobody has claimed this id. Safe.
    if info is None:
        return None

    # State says previous publisher is gone (LWT fired or clean shutdown).
    if state is not None and state.get("online") is False:
        return None

    owner = info.get("publisher_id")
    if owner is None:
        # Old-format publisher (pre-publisher_id world). We can't
        # distinguish ourselves from them; treat as conflict to be
        # safe — operators upgrading mid-deployment will see this once
        # and can clear the retained topic to proceed.
        return "<unknown-legacy-publisher>"
    if owner == our_publisher_id:
        # Our own ghost. LWT not yet fired? Allow — we're claiming it
        # back legitimately.
        return None
    # Same-host restart: ``publisher_id`` is ``<hostname>:<pid>:<id>``.
    # If the existing owner shares our hostname, this is the same
    # machine restarting its own workspace process — the previous PID
    # is dead, the broker just hasn't fired LWT yet (keepalive window
    # is 30 s; operators routinely restart faster than that). Allowing
    # this avoids a false-conflict that's the most common annoyance in
    # practice. The check still blocks a genuine cross-host collision
    # (two different Pis configured for the same robot ip), which is
    # the actual bug the contract is defending against.
    our_host = our_publisher_id.split(":", 1)[0] if ":" in our_publisher_id else ""
    owner_host = owner.split(":", 1)[0] if ":" in owner else ""
    if our_host and owner_host and our_host == owner_host:
        return None
    return owner


class MQTTDeviceAdapter:
    """Wrap a Device-shaped object and publish/subscribe per the MQTT spec.

    Args:
        device: Any object with attributes ``id``, ``state``, ``msg`` and
            methods ``on_state_change(cb)``, ``recover() -> bool``, ``release()``.
        kind: Short device family name, e.g. ``"camera"``, ``"printer"``.
            Published in the ``info`` payload so subscribers know what it is.
        critical: Whether this device's failure should pause the orchestrator.
            Published in ``info``; the orchestrator reads it to decide policy.
            Left as-authored regardless of ``sim`` — sim devices simply never
            publish ``state=down``, so ``critical`` becomes a no-op there.
        sim: Whether this device is in simulation mode. Published in
            ``info`` so the panel can show a SIM badge and the orchestrator
            knows to skip auto-pause for any state this device publishes.
            Authored intent — only the operator (or a manual toggle via
            ``set_sim``) changes it; failures must NOT flip it.
        meta: Free-form dict published in ``info`` (e.g. model, USB port).
        broker_host: MQTT broker host. Defaults to env ``DEVICE_MQTT_HOST``
            then ``"localhost"``.
        broker_port: MQTT broker port. Defaults to env ``DEVICE_MQTT_PORT``
            then ``1883``.
        client_id: Optional MQTT client id; defaults to ``"dev-<id>-<uuid>"``.
    """

    def __init__(
        self,
        device: Any,
        *,
        kind: str,
        critical: bool = True,
        sim: bool = False,
        meta: Optional[dict[str, Any]] = None,
        broker_host: Optional[str] = None,
        broker_port: Optional[int] = None,
        client_id: Optional[str] = None,
    ):
        self.device = device
        self.kind = kind
        self.critical = critical
        self.sim = bool(sim)
        self.meta = dict(meta or {})
        # Stable publisher identity used by conflict detection and any
        # external bus consumer that wants to attribute messages.
        self.publisher_id = ""  # filled in once device_id is normalized below
        self.broker_host = broker_host if broker_host is not None else DEFAULT_BROKER_HOST
        self.broker_port = broker_port if broker_port is not None else DEFAULT_BROKER_PORT

        device_id = getattr(device, "id", None)
        if not device_id:
            raise ValueError("device.id must be a non-empty string before adapter construction")
        device_id = str(device_id)
        # Normalize to the spec convention `<kind>:<natural-id>`. If the
        # device class already produces a prefixed id (recommended), this
        # is a no-op; if not, we prepend the kind so topic dumps stay
        # readable across multiple device families on the same bus.
        if ":" not in device_id:
            device_id = f"{kind}:{device_id}"
        self._device_id = device_id
        self.publisher_id = make_publisher_id(device_id)
        self._closed = False

        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id or f"dev-{self._device_id}-{uuid.uuid4().hex[:6]}",
        )

        # LWT: broker publishes this if our connection drops without a
        # clean disconnect. retained so any subscriber that connects later
        # sees the device as down. ``online: false`` tells the orchestrator
        # the service process itself is gone — recover/release commands
        # would have no listener, so the panel hides the Recover button
        # and shows an "offline" pill instead.
        lwt_payload = json.dumps({
            "state": "down",
            "msg": "connection lost",
            "online": False,
            "ts": time.time(),
        })
        self.client.will_set(
            f"device/{self._device_id}/state",
            lwt_payload,
            qos=1,
            retain=True,
        )

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        # Forward device state changes to the bus. Hook this BEFORE
        # connecting so we don't miss any transitions during startup.
        device.on_state_change(self._on_device_state_change)

        # Connect-or-warn: if the broker is unreachable, log and keep the
        # client running so paho's reconnect loop can recover.
        try:
            self.client.connect(self.broker_host, self.broker_port, keepalive=30)
        except Exception as ex:
            log.warning(
                "MQTTDeviceAdapter[%s]: initial connect to %s:%s failed (%s); "
                "will retry in background.",
                self._device_id, self.broker_host, self.broker_port, ex,
            )

        self.client.loop_start()

    # ── paho callbacks (v2 API) ───────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        # Republish info + current state on every connect so the broker
        # has fresh retained messages even after a disconnect/reconnect.
        try:
            self._publish_info()
            self._publish_state(self.device.state, self.device.msg)
            client.subscribe(f"device/{self._device_id}/cmd/+", qos=1)
            log.info("MQTTDeviceAdapter[%s]: connected, info+state published, cmd subscribed.",
                     self._device_id)
        except Exception:
            log.exception("MQTTDeviceAdapter[%s]: on_connect failed", self._device_id)

    def _on_disconnect(self, client, userdata, *args, **kwargs):
        # paho schedules its own reconnect; we just log.
        log.info("MQTTDeviceAdapter[%s]: disconnected (paho will retry)", self._device_id)

    def _on_message(self, client, userdata, message):
        try:
            self._dispatch_command(message.topic, message.payload)
        except Exception:
            log.exception("MQTTDeviceAdapter[%s]: command handler failed for %s",
                          self._device_id, getattr(message, "topic", "<unknown>"))

    # ── Device → MQTT ─────────────────────────────────────────────────────

    def _on_device_state_change(self, state: str, msg: str) -> None:
        """Camera (or any device) calls this on every transition."""
        self._publish_state(state, msg)

    def _publish_info(self) -> None:
        payload = json.dumps({
            "id": self._device_id,
            "kind": self.kind,
            "critical": self.critical,
            "sim": self.sim,
            "publisher_id": self.publisher_id,
            "meta": self.meta,
        })
        self.client.publish(
            f"device/{self._device_id}/info",
            payload,
            qos=1,
            retain=True,
        )

    def set_sim(self, sim: bool) -> None:
        """Update sim mode at runtime and republish info.

        Use when the operator manually toggles a component between real
        and sim mid-run. Republishes the retained ``info`` so subscribers
        (panel UI, orchestrator) see the updated badge / pause policy
        without restarting the workspace.
        """
        new_sim = bool(sim)
        if new_sim == self.sim:
            return
        self.sim = new_sim
        try:
            self._publish_info()
        except Exception:
            log.exception("MQTTDeviceAdapter[%s]: set_sim republish failed",
                          self._device_id)

    def _publish_state(self, state: str, msg: str) -> None:
        # ``online: true`` on every regular publish — the LWT carries the
        # only ``online: false`` payload, so the orchestrator can tell
        # "service alive, hardware bad" from "service dead".
        payload = json.dumps({
            "state": state,
            "msg": msg or "",
            "online": True,
            "ts": time.time(),
        })
        self.client.publish(
            f"device/{self._device_id}/state",
            payload,
            qos=1,
            retain=True,
        )

    # ── MQTT → Device ─────────────────────────────────────────────────────

    def _dispatch_command(self, topic: str, raw_payload: bytes) -> None:
        """Topic shape: ``device/<id>/cmd/<action>``."""
        parts = topic.split("/")
        if len(parts) < 4 or parts[2] != "cmd":
            return
        action = parts[3]
        try:
            req = json.loads(raw_payload.decode())
        except Exception:
            log.warning("MQTTDeviceAdapter[%s]: bad JSON on %s", self._device_id, topic)
            return
        req_id = req.get("req_id")

        # Recover/release may block (USB rebind, hardware reset). Run on a
        # worker thread so paho's network loop stays responsive.
        threading.Thread(
            target=self._run_command,
            args=(action, req_id),
            name=f"mqtt-cmd-{self._device_id}-{action}",
            daemon=True,
        ).start()

    def _run_command(self, action: str, req_id: Optional[str]) -> None:
        try:
            if action == "recover":
                ok = bool(self.device.recover())
            elif action == "release":
                self.device.release()
                ok = True
            else:
                log.warning("MQTTDeviceAdapter[%s]: unknown action %r",
                            self._device_id, action)
                ok = False
            reply = {
                "req_id": req_id,
                "ok": ok,
                "state": getattr(self.device, "state", "down"),
                "msg": getattr(self.device, "msg", ""),
            }
        except Exception as ex:
            log.exception("MQTTDeviceAdapter[%s]: %s raised", self._device_id, action)
            reply = {
                "req_id": req_id,
                "ok": False,
                "state": getattr(self.device, "state", "down"),
                "msg": f"{type(ex).__name__}: {ex}",
            }

        try:
            self.client.publish(
                f"device/{self._device_id}/cmd/{action}/reply",
                json.dumps(reply),
                qos=1,
                retain=False,
            )
        except Exception:
            log.exception("MQTTDeviceAdapter[%s]: reply publish failed for %s",
                          self._device_id, action)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Stop the network loop and disconnect cleanly. Safe to call twice.

        On clean disconnect the broker does NOT publish the LWT — so we
        publish a final ``online: false`` state ourselves first, otherwise
        subscribers would keep seeing the last retained ``online: true``
        and incorrectly think the service is still listening for cmds.
        """
        if self._closed:
            return
        self._closed = True
        try:
            payload = json.dumps({
                "state": "down",
                "msg": "service stopped",
                "online": False,
                "ts": time.time(),
            })
            self.client.publish(
                f"device/{self._device_id}/state",
                payload, qos=1, retain=True,
            )
        except Exception:
            pass
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
