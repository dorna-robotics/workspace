# Device MQTT Spec

How devices report state and accept commands across the system.

This is a **convention document, not a Python package**. Each service
implements ~40 lines of paho-mqtt boilerplate (see [Starter code](#starter-code)).
The spec is the contract — anything that follows it works with the
orchestrator, regardless of language.

## Why MQTT

- **Retained messages** = the broker remembers each device's last state.
  A new orchestrator (or a reconnecting one) immediately sees the
  current state of every device, no resync code needed.
- **Last Will & Testament** = the broker auto-publishes "down" for any
  device whose connection drops unexpectedly. Free dead-device detection.
- **Wildcard subscriptions** = orchestrator subscribes to `device/+/state`
  and discovers every device automatically. No registration handshake.
- **One broker, many subscribers** = orchestrator + dashboards + loggers
  + monitoring tools all tap the same bus. No N-connections-per-service.
- **Tiny** = mosquitto is ~1 MB binary, ~5 MB RAM. Runs on any Pi.

## Setup

### Broker

Run mosquitto on **one** machine in your system (typically the
orchestrator's Pi, since it's always-on):

```bash
sudo apt install mosquitto mosquitto-clients
sudo systemctl enable mosquitto
sudo systemctl start mosquitto
```

Default broker URL: `mqtt://<broker-host>:1883`.

### Devices

Each device service installs the Python client:

```bash
pip install paho-mqtt
```

## Identifier

Every device has a stable **`<id>`** that's unique across the system.
Convention: `<kind>:<natural-id>`, e.g.:

- `camera:130322274110` (camera by serial number)
- `syringe:pumpA` (syringe pump by physical label)
- `printer:zebra-zd420-front`

The `<id>` appears in every topic as the device's namespace.

## Topics

All topics are JSON-serialized strings.

### Discovery / metadata — published by device, retained

```
device/<id>/info
```

Payload:
```json
{
  "id": "camera:130322274110",
  "kind": "camera",
  "critical": true,
  "meta": { "any": "extra fields", "model": "D405", "usb_port": "..." }
}
```

- **retain=true, QoS=1**.
- Published once on device service startup.
- Re-published if metadata changes (rare).

### Health state — published by device, retained

```
device/<id>/state
```

Payload:
```json
{
  "state": "ok",
  "msg": "",
  "ts": 1730412345.678
}
```

- **retain=true, QoS=1**.
- Published on every state transition (only when state actually changes).
- `state` ∈ `"ok" | "down" | "recovering"`.
- `msg` is human-readable; empty when ok.
- `ts` is Unix epoch seconds (float).

### Last Will & Testament — broker auto-publishes on connection loss

When a device service dies (process crash, network loss), the broker
auto-publishes this on its behalf:

- Topic: `device/<id>/state`
- Payload: `{"state": "down", "msg": "connection lost", "ts": <set-by-broker>}`
- retain=true, QoS=1

Configure the LWT when connecting to the broker (see Starter code below).

### Commands — published by orchestrator, subscribed by device

```
device/<id>/cmd/recover
device/<id>/cmd/release
```

Request payload:
```json
{ "req_id": "<uuid>" }
```

### Replies — published by device, NOT retained

```
device/<id>/cmd/recover/reply
device/<id>/cmd/release/reply
```

Reply payload:
```json
{
  "req_id": "<echo-of-request>",
  "ok": true,
  "state": "ok",
  "msg": ""
}
```

- QoS=1, retain=false (replies are point-in-time).
- Caller correlates by `req_id`.

## States

| Value | Meaning |
|---|---|
| `"ok"` | Device delivers its data contract correctly. |
| `"down"` | Device cannot deliver its contract. Operator action needed. |
| `"recovering"` | Recovery cycle in progress. Transient — resolves to ok or down. |

Don't invent intermediate states. If a transition is normal (not a
fault), don't fire it through the state channel.

## Critical vs non-critical

Carried in `info.critical`. Drives orchestrator policy:

- **`critical: true`**: when this device transitions to `"down"`, the
  orchestrator runtime auto-pauses workflows. Operator must manually
  resume after fixing.
- **`critical: false`**: state events still published and visible in
  the UI, but no auto-pause. Use for diagnostic / non-essential devices.

## Discovery flow

Orchestrator on startup:

1. Connect to broker.
2. Subscribe to `device/+/info` and `device/+/state`.
3. Mosquitto immediately pushes the **retained** info + state messages
   for every device that has ever published — orchestrator builds its
   view of the world from those.
4. Subscribe to commands as needed.

No explicit "register" / "discover" handshake. The retained messages ARE
the discovery mechanism.

## Starter code

### Device service (Python, paho-mqtt)

A camera or syringe pump or whatever — same pattern.

```python
import json
import threading
import time
from typing import Callable
import paho.mqtt.client as mqtt

BROKER = "mqtt://orchestrator-pi.local:1883"


class MQTTDeviceAdapter:
    """Wraps any object with the Device shape (id, state, msg,
    on_state_change, recover, release) and publishes/subscribes
    according to the spec.
    """

    def __init__(self, device, *, kind: str, critical: bool = True,
                 meta: dict | None = None, broker_host: str = "localhost",
                 broker_port: int = 1883):
        self.device = device
        self.kind = kind
        self.critical = critical
        self.meta = meta or {}
        self.client = mqtt.Client(client_id=f"dev-{device.id}")

        # Last Will & Testament: broker publishes this if WE disappear
        # without saying goodbye. Auto-marks the device "down".
        lwt_payload = json.dumps({
            "state": "down",
            "msg": "connection lost",
            "ts": time.time(),
        })
        self.client.will_set(f"device/{device.id}/state", lwt_payload,
                             qos=1, retain=True)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(broker_host, broker_port, keepalive=30)
        self.client.loop_start()

        # Wire device state events → MQTT publish.
        device.on_state_change(self._publish_state)

    def _on_connect(self, client, userdata, flags, rc):
        # (Re-)publish info + current state on every connect. Retained
        # so any orchestrator that connects later sees them.
        self._publish_info()
        self._publish_state(self.device.state, self.device.msg)
        # Subscribe to our command topics.
        client.subscribe(f"device/{self.device.id}/cmd/+", qos=1)

    def _publish_info(self):
        payload = json.dumps({
            "id": self.device.id,
            "kind": self.kind,
            "critical": self.critical,
            "meta": self.meta,
        })
        self.client.publish(f"device/{self.device.id}/info",
                            payload, qos=1, retain=True)

    def _publish_state(self, state: str, msg: str):
        payload = json.dumps({
            "state": state,
            "msg": msg,
            "ts": time.time(),
        })
        self.client.publish(f"device/{self.device.id}/state",
                            payload, qos=1, retain=True)

    def _on_message(self, client, userdata, message):
        # Topic shape: device/<id>/cmd/<action>
        parts = message.topic.split("/")
        if len(parts) < 4 or parts[2] != "cmd":
            return
        action = parts[3]
        try:
            req = json.loads(message.payload.decode())
        except Exception:
            return
        req_id = req.get("req_id")

        # Run the action; reply with result. Recover/release may block,
        # so spawn a thread to avoid stalling the MQTT loop.
        def _run():
            try:
                if action == "recover":
                    ok = bool(self.device.recover())
                elif action == "release":
                    self.device.release()
                    ok = True
                else:
                    ok = False
                reply = {
                    "req_id": req_id,
                    "ok": ok,
                    "state": self.device.state,
                    "msg": self.device.msg,
                }
            except Exception as ex:
                reply = {
                    "req_id": req_id,
                    "ok": False,
                    "state": self.device.state,
                    "msg": f"{type(ex).__name__}: {ex}",
                }
            self.client.publish(
                f"device/{self.device.id}/cmd/{action}/reply",
                json.dumps(reply), qos=1, retain=False,
            )

        threading.Thread(target=_run, daemon=True).start()


# Usage in a real service:
if __name__ == "__main__":
    from camera import Camera

    cam = Camera()
    cam.connect(serial_number="130322274110")

    adapter = MQTTDeviceAdapter(
        cam,
        kind="camera",
        critical=True,
        meta={"sn": cam.id, "model": "D405"},
        broker_host="orchestrator-pi.local",
    )

    # Adapter runs in background; main thread does whatever the service
    # is for (in dorna_vision's case, serving its own HTTP/WS GUI).
    while True:
        time.sleep(60)
```

### Orchestrator (Python, paho-mqtt)

```python
import json
import threading
import time
import uuid
import paho.mqtt.client as mqtt


class MQTTOrchestrator:
    """Tracks every device on the bus. Pauses runtime on critical-down.
    Exposes recover/release methods that round-trip via MQTT.
    """

    def __init__(self, runtime, broker_host: str = "localhost",
                 broker_port: int = 1883):
        self.runtime = runtime
        # device_id → {kind, critical, meta, state, msg}
        self.devices: dict[str, dict] = {}
        # req_id → threading.Event for command/reply correlation
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

        self.client = mqtt.Client(client_id="orchestrator")
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.connect(broker_host, broker_port, keepalive=30)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        # Wildcards pull in EVERY device's info + state automatically
        # thanks to retained messages.
        client.subscribe([
            ("device/+/info", 1),
            ("device/+/state", 1),
            ("device/+/cmd/+/reply", 1),
        ])

    def _on_message(self, client, userdata, message):
        parts = message.topic.split("/")
        if len(parts) < 3:
            return
        device_id = parts[1]
        try:
            payload = json.loads(message.payload.decode())
        except Exception:
            return

        with self._lock:
            if parts[2] == "info":
                entry = self.devices.setdefault(device_id, {
                    "state": "down", "msg": "no state yet"
                })
                entry.update({
                    "kind": payload.get("kind", "device"),
                    "critical": bool(payload.get("critical", True)),
                    "meta": payload.get("meta", {}),
                })
                return

            if parts[2] == "state":
                entry = self.devices.setdefault(device_id, {
                    "kind": "device", "critical": True, "meta": {}
                })
                old = entry.get("state", "ok")
                entry["state"] = payload.get("state", "down")
                entry["msg"] = payload.get("msg", "")
                # Pause runtime on critical-down transition.
                if (entry.get("critical") and entry["state"] == "down"
                        and old != "down"):
                    try:
                        self.runtime.pause()
                    except Exception:
                        pass
                return

            if len(parts) >= 5 and parts[2] == "cmd" and parts[4] == "reply":
                req_id = payload.get("req_id")
                pending = self._pending.get(req_id)
                if pending is not None:
                    pending["payload"] = payload
                    pending["event"].set()
                return

    # ── Public API ─────────────────────────────────────────────────────

    def list_devices(self) -> list[dict]:
        with self._lock:
            return [
                {"id": did, **info}
                for did, info in self.devices.items()
            ]

    def recover(self, device_id: str, timeout: float = 30.0) -> dict:
        return self._send_cmd(device_id, "recover", timeout)

    def release(self, device_id: str, timeout: float = 5.0) -> dict:
        return self._send_cmd(device_id, "release", timeout)

    def _send_cmd(self, device_id: str, action: str, timeout: float) -> dict:
        req_id = str(uuid.uuid4())
        ev = threading.Event()
        with self._lock:
            self._pending[req_id] = {"event": ev, "payload": None}
        self.client.publish(
            f"device/{device_id}/cmd/{action}",
            json.dumps({"req_id": req_id}), qos=1, retain=False,
        )
        ev.wait(timeout)
        with self._lock:
            entry = self._pending.pop(req_id, None)
        if entry is None or entry["payload"] is None:
            return {"ok": False, "msg": "command timed out"}
        return entry["payload"]
```

That's the full pattern. Each new device service is a copy-paste of
`MQTTDeviceAdapter` + a class that has `id / state / msg / on_state_change
/ recover / release` (the structural Device shape).

## Debugging

```bash
# Watch every device's state in real time:
mosquitto_sub -t 'device/+/state' -v

# Watch everything:
mosquitto_sub -t 'device/#' -v

# Manually trigger recovery on a device:
mosquitto_pub -t 'device/camera:130322274110/cmd/recover' \
              -m '{"req_id":"manual-1"}'

# Watch the reply:
mosquitto_sub -t 'device/camera:130322274110/cmd/recover/reply' -v
```

No custom tooling needed. `mosquitto-clients` is in apt.

## What you gain over the WS approach

- ~150 lines of pyt[h]on per service vs maintaining a custom transport.
- Mosquitto handles reconnect / retain / fan-out / dead-publisher detection.
- Orchestrator doesn't need to know which subsystems exist — discovers them via wildcard subscription.
- New device service = new MQTT publisher. No orchestrator config change.
- Standard tooling (`mosquitto_sub`, MQTT Explorer) for debugging.
- Multiple subscribers (orchestrator + logger + dashboard + ...) tap the same bus naturally.

## What you give up

- No type-checked `Device` Protocol contract — convention enforced by
  this doc + code review. Mitigation: simple unit test in each device
  service that publishes to the spec'd topics with the spec'd payload
  and verifies the broker echoes it back.
- More moving parts (broker is a separate process). Mitigation: it's
  one apt package with a systemd unit; install once, forget.

## Starting points

1. **Install mosquitto** on the orchestrator Pi.
2. **Copy the device-side starter code** above into a new service. Plug
   in your hardware's `Device`-shaped class. Done.
3. **Copy the orchestrator-side starter code** into your runtime
   bootstrap. Done.
4. **Test end-to-end** with `mosquitto_sub` in another terminal. You
   should see state messages appear when the device runs.
