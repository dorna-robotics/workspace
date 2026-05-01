# Device Author Guide

How to add a new physical device (printer, pipette, scale, syringe pump, …)
to the system so the orchestrator monitors its health, pauses workflows on
failure, and lets the operator recover it from the workspace UI.

This guide implements the convention in [device-mqtt-spec.md](device-mqtt-spec.md).
Read that first if you want the full wire protocol; this doc is the playbook
for "I have a new device, what do I do?".

---

## 1. The audience

You're adding a new device because:

- You wrote a Python class that talks to some hardware (USB, serial, network).
- You want workflows in `workspace` to pause when this device fails.
- You want operators to recover it from a button in the workspace UI.

If your device is purely virtual (e.g. a software pipeline like the
`Detection` class), this guide does not apply — it's for hardware.

---

## 2. The contract — 6 things your device class must expose

Your device class must structurally conform to the **Device shape**. No
inheritance, no base class — just expose the right attributes and methods:

| Member | Type | Purpose |
|---|---|---|
| `id` | `str` (attribute or property) | Stable, system-unique identifier. Convention: `<kind>:<natural-id>` — e.g. `"camera:130322274110"`, `"printer:zebra-zd420-front"`, `"pipette:pumpA"`. |
| `state` | `str` ∈ `{"ok", "down", "recovering"}` | Current health. Initial value should be `"down"` until the device successfully connects. |
| `msg` | `str` | Human-readable detail for the current state. Empty when ok. |
| `on_state_change(callback)` | method | Register a listener `callback(new_state, msg)`. The bus subscribes here once during adapter setup. |
| `recover()` | method, returns `bool` | Attempt to bring the device back to `"ok"`. Should fire `recovering → ok` (or `recovering → down`) state transitions. |
| `release()` | method | Tear down — close handles, stop threads. |

That's it. Anyone who follows this gets MQTT publishing for free via the
adapter — no further work.

---

## 3. The minimal device skeleton

```python
import threading


class MyDevice:
    """Replace 'MyDevice' with whatever you're building (Printer, Pipette, …).

    The internals are yours to design — only the listed attributes/methods
    are part of the Device contract.
    """

    KIND = "mydevice"  # used by the adapter as the topic prefix

    def __init__(self, natural_id: str):
        self._natural_id = natural_id
        self.state = "down"
        self.msg  = "not connected"
        self._listeners = []
        self._listeners_lock = threading.Lock()

    @property
    def id(self) -> str:
        return f"{self.KIND}:{self._natural_id}"

    # Device contract ────────────────────────────────────────────────────
    def on_state_change(self, callback):
        with self._listeners_lock:
            self._listeners.append(callback)

    def recover(self) -> bool:
        self._set_state("recovering", "rebuilding connection")
        try:
            ok = self._real_connect()
        except Exception as ex:
            self._set_state("down", f"recovery failed: {ex}")
            return False
        self._set_state("ok" if ok else "down", "" if ok else "recovery failed")
        return ok

    def release(self) -> None:
        # Stop background threads, close handles, etc.
        ...

    # Internals ──────────────────────────────────────────────────────────
    def _set_state(self, new_state: str, msg: str = "") -> None:
        """Always go through this so listeners learn about transitions.
        No-op if state didn't change — listeners only see real transitions."""
        if self.state == new_state:
            return
        self.state = new_state
        self.msg   = msg or ""
        with self._listeners_lock:
            cbs = list(self._listeners)
        for cb in cbs:
            try:
                cb(new_state, self.msg)
            except Exception:
                pass

    def _real_connect(self) -> bool:
        # ... your hardware connection logic here.
        # Return True on success, False on failure.
        ...

    def do_real_work(self):
        """Your hardware operations. Wrap in try/except; on hardware errors,
        call self._set_state('down', '...') so the orchestrator pauses."""
        try:
            ...  # talk to hardware
        except OSError as ex:
            self._set_state("down", f"hardware error: {ex}")
            raise
```

Key idea: **always go through `_set_state`** when the device's health
changes. That's the single channel listeners (and therefore MQTT, and
therefore the orchestrator) hear about transitions through.

---

## 4. Wrap it with `MQTTDeviceAdapter`

Copy `MQTTDeviceAdapter` from
[`dorna_vision/server/mqtt_adapter.py`](https://github.com/dorna-robotics/dorna_vision/blob/server/dorna_vision/server/mqtt_adapter.py)
into your service. (When we have 3+ device services, this gets pulled into
a shared package; for now copy-paste is the rule.)

Then in your service's startup:

```python
from mqtt_adapter import MQTTDeviceAdapter   # the file you copied
from mydevice import MyDevice

device = MyDevice(natural_id="pumpA")
device.connect()  # or whatever brings it up — it should call _set_state("ok") on success

adapter = MQTTDeviceAdapter(
    device,
    kind="mydevice",            # short family name, lowercase
    critical=True,              # see §5
    meta={"location": "bench-A", "model": "X-200"},
    broker_host="orchestrator-pi.local",  # or env DEVICE_MQTT_HOST
)

# Run forever — adapter has a background thread; main thread just sleeps.
import time
while True:
    time.sleep(60)
```

That's it. The adapter:
- Publishes `device/<id>/info` (retained) on connect.
- Publishes `device/<id>/state` (retained) on every transition fired by
  your `_set_state`.
- Sets a Last-Will-and-Testament so the broker auto-marks the device down
  if your service crashes.
- Subscribes to `device/<id>/cmd/recover` and `device/<id>/cmd/release`,
  routes them to your `recover()` / `release()` methods, and replies on
  the `.../reply` topics.

---

## 5. Choosing `critical`

| `critical` | Behavior | Use when |
|---|---|---|
| `True` | Workspace runtime calls `pause()` on `down` transitions. Operator must manually resume after fixing. | The workflow can't proceed safely without this device (camera, robot, syringe pump in a liquid-handling step). |
| `False` | State transitions are still published and visible in the UI, but no auto-pause. | Diagnostic / observability devices. Auxiliary tools that aren't on the critical path. |

Default is `True`. Only choose `False` if you're sure the workflow is
fine running while the device is offline.

---

## 6. Where the adapter must live — the only rule

**Co-locate the adapter with the hardware.** The process that holds
the USB handle / serial port / TCP socket to the physical device is the
only process that can observe its real failure modes. Anyone else is
guessing.

Concretely:

- USB camera plugged into Pi A → adapter runs on Pi A (inside the
  service that manages the camera).
- Printer on Pi B's serial port → adapter runs on Pi B.
- Pipette plugged directly into the orchestrator Pi → adapter can run
  inside `workspace` itself (because the hardware is on that Pi).

Don't put the adapter "wherever is convenient" — false-positive `down`
events from network blips and missed real failures from USB drops are
the consequence.

---

## 7. ID convention

`device.id` must be `<kind>:<natural-id>`:

- `kind` = the family name you pass as `kind=` to the adapter — `camera`,
  `printer`, `pipette`, `scale`, `syringe`. Lowercase, no colons.
- `natural-id` = whatever distinguishes one instance from others of the
  same kind. Serial numbers are ideal because they're stable across
  reboots; physical labels (`pumpA`, `front-bench`) are fine when no
  serial exists.

Examples:

```
camera:130322274110     ← USB serial
printer:zd420-front     ← physical position
pipette:pumpA           ← physical label
scale:ohaus-12345       ← serial
```

Bad:

```
my_camera               ← no kind prefix → unclear in topic dumps
camera:0                ← non-stable id (resets on reboot)
camera::                ← empty natural-id
Camera:130322274110     ← uppercase kind (be consistent)
```

---

## 8. Testing the device service

Once your service is running and connected to the broker, in another
terminal:

**Watch every device on the bus:**
```bash
mosquitto_sub -t 'device/#' -v
```

You should immediately see your service's `info` and `state` (retained
messages — the broker had them stored from your last publish).

**Simulate failure:**
- USB device → unplug it. State should flip to `down` within seconds.
- Network device → `iptables` block the connection or pull the cable.
- Generic → call `device._set_state("down", "test")` from a debug shell.

**Trigger recovery from outside the service:**
```bash
mosquitto_pub -t 'device/<your-id>/cmd/recover' -m '{"req_id":"manual-1"}'
mosquitto_sub -t 'device/<your-id>/cmd/recover/reply' -v
```

If everything is wired correctly, you'll see:

```
device/<your-id>/state              {"state":"recovering",...}
device/<your-id>/state              {"state":"ok",...}
device/<your-id>/cmd/recover/reply  {"req_id":"manual-1","ok":true,...}
```

---

## 9. Common pitfalls

- **Forgetting to fire `_set_state("ok", "")` on successful connect.**
  Initial state is `"down"` per the contract; you must transition to ok
  explicitly. Without that, the orchestrator thinks the device never
  came up.
- **Calling `_set_state` with the same state twice.** No-op by design —
  listeners only see real transitions. Don't try to "re-emit" — change
  `msg` only via state transitions, not by re-firing the same state.
- **Blocking inside `recover()`.** It's allowed (the adapter runs each
  command on a worker thread), but if your recovery takes longer than
  the orchestrator's `timeout`, the orchestrator will report a timeout
  even though recovery may eventually succeed. Tune both sides.
- **Publishing your own MQTT messages from inside the device class.**
  Don't. The device class is hardware abstraction; MQTT is the adapter's
  job. Crossing that line couples the device to the network protocol
  and you can't run it standalone for testing.
- **Assuming `release()` means "permanently gone".** It means
  "tear down for now". The orchestrator may still expect the device to
  come back. If you want to remove a device permanently from the bus,
  publish an empty retained message to `device/<id>/info` and
  `device/<id>/state` to clear them.

---

## 10. Case study: the camera

Real-world reference. The camera SDK ([github.com/dorna-robotics/camera](https://github.com/dorna-robotics/camera))
exposes the Device shape directly on its `Camera` class:

- `id` → property returning the USB serial number. (The adapter
  auto-prepends `camera:` since the SDK returns the bare serial; the
  resulting topic is `device/camera:<serial>/...` per the convention.)
- `state`, `msg` → instance attributes initialized to `"down"`.
- `on_state_change(cb)` → appends to a listener list.
- `recover()` → tries firmware reset, USB unbind/bind, pipeline restart.
- `release()` → alias for `close()`.

The vision server (the long-running process on the camera Pi) wraps each
acquired camera with `MQTTDeviceAdapter` inside
[`CameraPool.acquire`](https://github.com/dorna-robotics/dorna_vision/blob/server/dorna_vision/server/pools.py).
USB hotplug events trigger `_set_state("down", "USB disconnected")` on a
librealsense-internal thread; the listener publishes to MQTT; the
workspace orchestrator pauses; the operator clicks Recover; the bus
calls `cam.recover()`; pipeline rebuilds; state goes `recovering → ok`.

That's the entire pattern. The next device — printer, pipette, scale —
follows exactly the same shape.

---

## 11. Open follow-ups (not blocking new device authors)

- **Local UI on each device server.** Each device service should also
  show its own health locally (vision server's web GUI, future printer
  service GUI, etc.). Same data as the orchestrator — they all subscribe
  to MQTT independently. Tracked separately.
- **Shared `mqtt_adapter` package.** Once we have 3+ device services,
  pull `MQTTDeviceAdapter` into a tiny pip package so they don't all
  carry their own copy. Until then: copy-paste from the camera/vision
  reference and keep them in sync against the spec.
