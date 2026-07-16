# Devices

How external hardware (cameras, printers, pipettes, scales, …) plugs into
the orchestrator so workflows pause when something fails and the operator
can recover it from the UI.

This is the single doc for everything device-related. If you're adding a
new device, this guide is the playbook. The wire-level protocol details
are in the appendix at the end.

---

## 1. The big picture

Three roles, three layers:

```
┌───────────────────────────────┐    health + commands    ┌────────────────────────────┐
│  Device service               │  ─────────────────────  │  Orchestrator (workspace)  │
│  (runs near the hardware)     │      device bus         │  - subscribes to bus       │
│  - holds USB/serial/network   │ ◄────────────────────►  │  - pauses on critical-down │
│    handle                     │                         │  - exposes recover/release │
│  - publishes health           │                         │  - shows devices in UI     │
│  - accepts recover/release    │                         │                            │
└───────────────────────────────┘                         └────────────────────────────┘
                ▲
                │   data path (frames / commands / serial bytes)
                │   varies per device kind
                ▼
┌───────────────────────────────┐
│  Workspace component          │  (inside the orchestrator process,
│  (e.g. Inspection, Pipette)   │   per scene config)
└───────────────────────────────┘
```

- **Device service** owns the hardware. Lives on whichever machine the
  USB/serial/network connection lands on (often a separate Pi).
- **Device bus** carries health updates (`ok` / `down` / `recovering`),
  recover/release commands, and dead-publisher detection. Implemented
  with MQTT under the hood — you don't need to know more than that for
  this guide; see Appendix A if curious.
- **Orchestrator** automatically sees every device on the bus and pauses
  workflows on critical failures.
- **Workspace components** are the per-project glue: a scene declares
  which devices it needs, and these components hold the data-path
  connection (frames for cameras, commands for pipettes, …) plus tell
  the orchestrator UI which devices the project depends on.

Health monitoring is fully generic — once a device service is on the
bus, the orchestrator handles it automatically. The data path varies
per device kind: cameras use a vision client, pipettes use whatever
their library exposes, etc.

### The four bulletproof rules

The bus's invariants — break any one and the panel can lie to the
operator. Every code path in `workspace.devices` exists to enforce one
of these:

1. **One publisher per device id, by hardware-handle ownership.** The
   process that holds the USB / serial / TCP socket is the sole writer
   for that id's retained MQTT topics. Any second publisher is
   refused at startup with `DevicePublisherConflict` (see §8).
2. **Bus state is hardware truth.** No process overwrites another's
   bus entry. The panel's dot color always reflects what the publisher
   reports.
3. **Sim is a project-level annotation, not a bus-level state.** Each
   workspace declares per-device claim modes (`real` / `sim`) via
   `DeviceComponent.device_claim`. The annotation rides alongside the
   bus snapshot in workspace state — it does not displace the
   publisher's truth on MQTT (see §10).
4. **Auto-pause respects both signals.** If `info.sim` is true on the
   bus OR the project claims `sim` for a device, a critical-down on
   that device does not pause the runtime. Either signal alone is
   sufficient to opt out. Device-down is one of four pause triggers —
   see [project-guide.md §9 "What triggers Pause"](project-guide.md#what-triggers-pause)
   for the full list and the entry/atomicity/resume semantics that
   apply to all of them uniformly.
5. **Bus presence is gated by an explicit identifier.** Every device
   component takes one config field that names the physical device
   (`ip` for Core, `port` for the multimeter, `serial_number` for the
   camera, etc.). Empty value → no `device_ids` entry, no panel row.
   Non-empty value → row appears. The `simulation:` flag is
   **separate** and controls how a *declared* device is treated, not
   whether it's declared. Same rule for every kind. Two shapes
   depending on where the publisher lives:

   - **Workspace-owned** (robot, multimeter, in-process pumps): the
     component also calls `attach_device` itself, gated on the same
     identifier. Two gates, same condition.
   - **Daemon-owned** (camera served by a vision-server process,
     printer served by a printer daemon, etc.): the daemon process
     owns `attach_device`. The component only gates `device_ids`.
     One gate; the daemon does its own.

   See §10 for both code shapes.

If you're adding a new device, the rest of this guide is mechanical —
follow the patterns and these invariants hold by construction.

---

## 2. The Device contract — six members on your hardware class

Your device class must structurally expose this shape. No inheritance,
no base class — just the right attributes and methods at runtime:

| Member | Type | Purpose |
|---|---|---|
| `id` | `str` | Stable identifier in `<kind>:<natural-id>` form (see §9). |
| `state` | `str` ∈ `{"ok", "down", "recovering"}` | Current health. Initial value should be `"down"` until a successful connect. |
| `msg` | `str` | Human-readable detail. Empty when `ok`. |
| `on_state_change(callback)` | method | Register a `callback(new_state, msg)` listener. The adapter subscribes once during setup. |
| `recover()` | method, returns `bool` | Attempt to bring the device back to `ok`. Should fire `recovering → ok` (or `recovering → down`) state transitions. |
| `release()` | method | Tear down — close handles, stop threads, free resources. |

Anything that exposes these gets full health monitoring + remote
recover/release for free.

---

## 3. The minimal device skeleton

```python
import threading


class MyDevice:
    """Replace 'MyDevice' with whatever you're building (Printer, Pipette, …).

    The internals are yours to design — only the listed attributes and
    methods are part of the contract.
    """

    KIND = "mydevice"  # used by the adapter as the topic prefix

    def __init__(self, natural_id: str):
        self._natural_id = natural_id
        self.state = "down"
        self.msg = "not connected"
        self._listeners = []
        self._listeners_lock = threading.Lock()

    @property
    def id(self) -> str:
        return f"{self.KIND}:{self._natural_id}"

    # Contract ───────────────────────────────────────────────────────────
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
        Fires when state OR msg changes (so 'down' with a refreshed
        message still notifies listeners)."""
        new_msg = str(msg or "")
        if self.state == new_state and self.msg == new_msg:
            return
        self.state = new_state
        self.msg = new_msg
        with self._listeners_lock:
            cbs = list(self._listeners)
        for cb in cbs:
            try:
                cb(new_state, self.msg)
            except Exception:
                pass

    def _real_connect(self) -> bool:
        # ... your hardware connection logic here.
        ...

    def do_work(self):
        """Hardware operations. On hardware errors, call self._set_state('down', '...')
        so the orchestrator pauses."""
        try:
            ...
        except OSError as ex:
            self._set_state("down", f"hardware error: {ex}")
            raise
```

Key idea: **always go through `_set_state`** when health changes. That's
the single channel listeners (and therefore the bus, and therefore the
orchestrator) hear about transitions through.

---

## 4. The device adapter

Each device service runs a small adapter that wraps the device class,
publishes its health to the bus, and accepts recover/release commands.

**Use `attach_device` — the canonical wiring helper.** It owns the
adapter, the AutoRecover loop, and the publisher-conflict check in one
call. Hand-rolling `MQTTDeviceAdapter` is allowed but skips the
conflict check; only do that for tests or one-shot probes.

```python
from workspace.devices import attach_device, AutoRecover
from mydevice import MyDevice

device = MyDevice(natural_id="pumpA")
device.connect()  # or whatever brings it up — calls _set_state("ok") on success

# AutoRecover is wired by attach_device when sim=False. Authoring it as
# a factory (zero-arg callable) lets the helper re-arm recovery if the
# operator toggles sim → real at runtime.
def make_recover():
    rec = AutoRecover(
        recover_fn=device.recover,
        set_status=device._set_state,
        log_label=f"mydevice:{device.id}",
    )
    # Plug the trigger into whatever "hardware available" signal your
    # device exposes (e.g. cam.on_hardware_available(recover.trigger)).
    return rec

attachment = attach_device(
    device,
    kind="mydevice",            # short family name, lowercase (see §9)
    sim=False,                  # authored intent; failures must NOT flip this
    critical=True,              # see §7
    meta={"location": "bench-A", "model": "X-200"},
    recover_factory=make_recover,
    broker_host="orchestrator-pi.local",   # the bus's host
)

# Adapter runs in background; main thread does whatever the service is for.
import time
while True:
    time.sleep(60)

# On shutdown:
attachment.close()
```

That's it. `attach_device`:

- **Refuses to attach if another publisher already owns this device id**
  on the bus — raises `DevicePublisherConflict` with the conflicting
  `publisher_id` so the operator sees who's claiming the topic. This is
  the load-bearing guard against rule-1 violations (§1).
- Publishes the device's `info` (id, kind, critical, sim, publisher_id,
  meta) on connect.
- Publishes health on every state transition.
- Sets a "last will" so the bus marks the device down if your service
  crashes.
- Listens for recover/release commands and routes them to your device's
  methods, replying with the result.
- Wires AutoRecover when `sim=False`; suspends it on `sim=True`.

`attachment.set_sim(True/False)` flips sim mode at runtime, republishes
info, and arms/disarms recovery. Use this when the operator toggles a
component into simulation mid-run.

---

## 5. Self-healing with AutoRecover

The adapter publishes state. **AutoRecover** decides what to do when the
device is unhappy — it's the difference between "operator must click
Recover and watch every retry" and "device picks itself back up while
nobody's looking."

Use it whenever a transient failure (USB hiccup, network blip, serial
re-enumeration) might fix itself within seconds-to-minutes. That covers
nearly every device — leave it off only if your hardware genuinely
shouldn't auto-retry (e.g. a dosing pump where re-trying might double-
dispense).

### What AutoRecover does

Wraps your `device.recover()` in an exponential-backoff loop on a
background thread:

```
attempt 1: fire immediately
fail → wait 2s, retry
fail → wait 4s, retry
fail → wait 8s, retry
fail → wait 16s, retry
fail → wait 32s, retry
fail → wait 60s (capped), retry … and stay at 60s forever
```

It never gives up entirely — the device might come back hours later, and
you don't want a silently-abandoned recipe.

### What the operator sees

While the loop runs, AutoRecover keeps `device.msg` informative so the
panel can show what's happening:

| Attempts | Message in the panel |
|---|---|
| 1–3 | `recovering (attempt N)` |
| 4+ | `N attempts failed — please check, still trying` |

The panel pill / button stays consistent with state:

- `state=ok` → green dot, no button
- `state=recovering` → yellow dot + disabled "Recovering…" button
- `state=down`, service alive → red dot + **Recover** button (manual nudge)
- `state=down`, service dead (LWT) → red dot + **offline** pill

### How recovery is triggered

Anything calls `recover.trigger()`:

- **Hotplug** — for USB devices, plug-back fires
  `cam.on_hardware_available` which calls `recover.trigger()` (see §15).
- **Polling** — for IP devices (printer, robot), have your service ping
  on a timer; first successful ping calls `recover.trigger()`.
- **Operator click** — the cmd handler in `MQTTDeviceAdapter` calls
  `device.recover()`. Wrap your device so `recover()` delegates to
  `recover.trigger()` (see the camera's `_AutoRecoveringCamera` wrapper
  in `dorna_vision/server/pools.py` for the pattern).

If `trigger()` is called while the loop is already running, it just
nudges the current sleep awake and resets the backoff counter — exactly
what you want when the operator gets impatient and clicks Recover during
a long wait.

### Wiring example

```python
from workspace.devices import AutoRecover

recover = AutoRecover(
    recover_fn=device.recover,         # returns True on success
    set_status=device._set_state,       # surfaces attempt counts
    log_label=f"{kind}:{device.id}",
    # tweak only if defaults don't fit your device
    initial_delay=2.0,
    max_delay=60.0,
    flag_after=3,
)

# wire whatever signal means "the hardware might be back":
device.on_hardware_available(recover.trigger)   # USB devices
# or your own polling loop that calls recover.trigger() on ping success

# on shutdown:
recover.stop()
```

### What it is NOT

- Not a watchdog. AutoRecover only acts when something nudges it. If
  nothing tells it the hardware is back (no hotplug event, no polling
  loop), it sits idle.
- Not a replacement for `critical`. The orchestrator still pauses the
  runtime on critical-down — AutoRecover just makes the recovery happen
  in the background while the runtime waits.
- Not a circuit breaker. There's no "give up after N attempts and stop
  trying" — by design. Add one in your service if your hardware genuinely
  needs it (e.g. consumables that wear out).

---

## 6. Data freshness — for read-only devices

For sensors that return data (camera frames, scale readings, encoder
counts, ADC samples), there's a second contract beyond "device is up":
**every read must return fresh data or raise — never silently a stale
or cached value.** Auto-recovery is useless if a frozen sensor keeps
handing the recipe yesterday's measurement.

The three rules:

### a) Validate inline

Every read should check that the returned data is actually new before
returning it. Cheap signals depend on the device, but most sensors
expose at least one of:

- **Sample/frame counter** — monotonic per-read counter from the
  device. Same number twice = buffer is replaying.
- **Device timestamp** — on-device clock per sample. Frozen timestamp =
  hardware clock stalled.
- **Wall-vs-device drift** — over a long-enough window, the device
  clock should advance roughly with real time. Sustained drift =
  underclocking (thermal throttle, bandwidth starvation).

The camera SDK does all three inside ``Camera.frame()`` — see §15 for
the implementation pattern. Plagiarize it for your sensor.

### b) Reset on pipeline disruption

When the device's pipeline is rebuilt (post-recover, post-reconnect),
counters/timestamps may restart from zero. Reset your freshness
baseline whenever the device's ``state`` leaves ``"ok"`` — the next
"ok" frame establishes a fresh reference. Same-state msg updates
(e.g. ``"USB disconnected"`` → ``"USB reconnected"``) preserve the
baseline since no pipeline change happened.

### c) Split capture from process where it helps

If the read is cheap but the **process** step on top of it is heavy
(detection, FFT, regression), expose them as separate typed RPCs so
callers can verify the read succeeded before paying the process
cost. Example: vision splits

```
detection_capture(name)              → fresh frame or ok=false
detection_run(name, use_last=True)   → run on the just-captured frame
```

The orchestrator-side wrapper makes this transparent: ``inspection.detect()``
auto-captures first by default, raises ``CameraUnavailableError`` on
capture failure, and only runs detection on a confirmed-fresh frame.
Recipe code stays clean (``inspection.detect("name")``); the
bulletproof guarantee lives in the component layer.

### What this is NOT

- A guarantee that the data is **correct** — only that it's **fresh**.
  Pixel-corrupt-but-novel frames pass freshness checks. Add content
  validation if your application needs it.
- Applicable to **write** devices (pipette, printer, robot). Write
  ops need verify-after-write, not freshness — a different pattern.
  This section is for sensors only.

---

## 7. Choosing `critical`

| `critical` | Behavior | Use when |
|---|---|---|
| `True` | Workflows pause on `down` transitions; operator must manually resume after fixing. | The workflow can't proceed safely without this device (camera, syringe pump in a liquid step, robot). |
| `False` | State events still published and visible in the UI, but no auto-pause. | Diagnostic / observability devices. Auxiliary tools off the critical path. |

Default is `True`. Only choose `False` when you're sure the workflow is
fine running while the device is offline.

The `critical:` field is also a normal component config field — expose
it in your component's `DEFAULTS` so a scene yaml can override it per
project (e.g. `critical: false` for a meter used only for logging in
one specific run). Do not invent a separate "advisory" mode or a new
flag — same field, same name, same default rule for every kind.

---

## 8. Where the adapter must live — the only rule

**Co-locate the adapter with the hardware.** The process that holds the
USB handle / serial port / TCP socket is the only process that can
observe its real failure modes. Anyone else is guessing.

Concretely:

- USB camera plugged into Pi A → adapter runs on Pi A (inside the
  service that manages the camera).
- Printer on Pi B's serial port → adapter runs on Pi B.
- Pipette plugged directly into the orchestrator Pi → adapter can run
  inside `workspace` itself (because the hardware is on that Pi).

This rule is **enforced**, not suggested. `attach_device` runs a brief
publisher-conflict check on every attach: it subscribes to the device's
retained `info` and `state` topics, and if another publisher (different
`publisher_id` on `info`, with `online: true` on `state`) already owns
the id, it raises `DevicePublisherConflict` and refuses to attach. So
"adapter in the wrong process" turns into a loud failure at startup
instead of silently corrupting the bus.

Edge cases the check handles correctly:

- **Predecessor was clean-shutdown or LWT-fired** (`online: false` in
  retained state) → allowed. Restart works.
- **Same `publisher_id`** (us reclaiming our own slot before LWT
  fires) → allowed.
- **Broker unreachable** → can't tell, proceed. We don't block startup
  on a broker hiccup.
- **Legacy publisher with no `publisher_id` field** → blocked
  conservatively with `<unknown-legacy-publisher>` so a partial
  rolling upgrade doesn't accidentally let two writers coexist.

If you genuinely need to disable the check (testing only): set the env
var `DEVICE_BUS_DETECT_CONFLICT=0` or pass `detect_conflict=False` to
`attach_device`.

---

## 9. ID convention

Every device id is `<kind>:<natural-id>`. Both halves are required.

### `<kind>` rules

- Lowercase ASCII.
- Singular noun.
- Letters and hyphens only — no underscores, no dots, no colons (the
  colon is the separator between kind and natural-id).

Blessed kinds (use these names verbatim when applicable):

| Kind          | Use for                                              |
|---------------|------------------------------------------------------|
| `camera`      | Cameras (RealSense, USB webcams, etc.)               |
| `printer`     | Label / barcode / inkjet printers                    |
| `pipette`     | Liquid-handling pipettes                             |
| `syringe`     | Syringe pumps                                        |
| `scale`       | Mass / weight balances                               |
| `shaker`      | Plate shakers / mixers                               |
| `decapper`    | Cap removers                                         |
| `feeder`      | Part feeders / vibratory bowls                       |
| `dosing-arm`  | Dosing / dispensing arms                             |
| `tool-changer`| Robot tool-changer mechanisms                        |
| `dorna`       | Dorna robots themselves (when published as devices)  |
| `multimeter`  | LCR / impedance / multimeter bench instruments       |

If you need a kind not in the table, **add it here in the same change
that introduces the device** so the catalog stays the single source of
truth.

### `<natural-id>` rules

- Stable across reboots — USB serial number is best when one exists.
- Physical label (`pumpA`, `front-bench`) when there's no serial.
- Pick a style (lowercase preferred) and stick to it within a kind.
- **No `/` characters.** MQTT subscribers use single-level wildcards
  (`device/+/info`, `device/+/state`, `device/+/cmd/+/reply`).
  A slash in the natural-id pushes the topic suffix to a depth the
  wildcard can't match, so the orchestrator never sees the publisher
  and the device renders as `offline / not on bus` even though the
  station is happily publishing.
  When deriving an id from a filesystem path (e.g. `/dev/ttyUSB0` or
  `/dev/serial/by-id/usb-...-port0`), use the path's **basename**, not
  the full path — see `BK879BStation.id` for the canonical pattern.

Examples:

```
camera:130322274110     ← USB serial
printer:zd420-front     ← physical position
pipette:pumpA           ← physical label
dorna:192.168.1.42      ← host/IP
```

Bad:

```
my_camera               ← no kind prefix
camera:0                ← non-stable id (resets on reboot)
camera::                ← empty natural-id
Camera:130322274110     ← uppercase kind (be consistent)
camera_main:abc         ← underscore in kind
```

### Finding the stable path for a USB-serial device

The raw `/dev/ttyUSB0` / `/dev/ttyACM0` numbers are **not** stable — Linux
assigns them in enumeration order, so they change on reboot, replug, or
when another device is added. For the scene yaml's `port`, use the
udev-managed `by-id` symlink instead. **One command lists every
USB-serial device, ready to paste:**

```bash
ls -d /dev/serial/by-id/*
```

This is the single command for **all** USB-serial devices, whatever the
class — FTDI / CP210x adapters (which appear as `ttyUSB*`) and ACM-class
CDC devices like the Zebra DS457 scanner (which appear as `ttyACM*`) all
show up here:

```
$ ls -d /dev/serial/by-id/*
/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0
/dev/serial/by-id/usb-Symbol_Technologies__Inc__2008_Symbol_Bar_Code_Scanner_...-if00
```

Paste the full path into `port` (multimeter, barcode reader, any
USB-serial device). The symlink survives reboots and replugs and always
resolves to that specific device no matter which USB port it lands on, so
you never chase a `ttyUSB*` / `ttyACM*` number again.

**Caveat — generic serials.** Some chips (the CP2102 ships many devices
with serial `0001`) lack a unique serial, so two of the same chip collide
on the same `by-id` name. Then use `/dev/serial/by-path/*` instead (stable
per physical USB port — always plug that device into the same port).

---

## 10. Workspace-side: declaring the device

Sections 1-9 cover the device service (the process that owns the
hardware). Now the orchestrator side: how a workspace component tells
the orchestrator UI "this project depends on these devices" and "this
is how I'm using each of them."

The contract has two members. Only `device_ids` is required. Two
canonical skeletons depending on where the bus publisher lives —
both gate visibility on the same kind of explicit identifier
(`serial_number`, `ip`, `port`, …).

### A. Workspace-owned publisher (robot, multimeter, in-process pump)

The component itself calls `attach_device`. Two gates, same
condition.

```python
class MyComponent:
    DEFAULTS = dict(
        serial_number="",     # the explicit connection identifier
        simulation=True,
        critical=True,
    )

    def __init__(self, name, cfg, workspace, **kwargs):
        ...
        self._sn = prm["serial_number"]
        self._simulation_mode = prm["simulation"]
        self._critical = prm["critical"]
        self.device = MyDevice(self._sn, simulation=self._simulation_mode)

        # Visibility gate (rule 5 of §1). Empty identifier → no bus
        # presence, no panel row. Non-empty → attach. SAME condition
        # below in device_ids.
        self._attachment = None
        if self._sn:
            self._attachment = attach_device(
                self.device,
                kind="mydevice",
                sim=self._simulation_mode,
                critical=self._critical,
                recover_factory=lambda: AutoRecover(...),
            )

    @property
    def device_ids(self) -> list[str]:
        # SAME condition as the attach_device gate above. If you
        # change one, change both.
        return [f"mydevice:{self._sn}"] if self._sn else []

    def device_claim(self, device_id: str) -> str:
        """Optional. Return 'real' or 'sim'."""
        if self._sn and device_id == f"mydevice:{self._sn}":
            return "sim" if self._simulation_mode else "real"
        return "real"
```

### B. Daemon-owned publisher (camera served by a vision server, etc.)

The bus publisher lives in another process — the daemon on the Pi
that owns the USB / serial handle. The workspace component is a
client: it only declares the dependency via `device_ids`, never
calls `attach_device` itself. The daemon does its own gating, on
the same kind of identifier. Same visibility rule, one gate.

```python
class CameraComponent:
    DEFAULTS = dict(
        camera_cfg={
            "serial_number": "",       # the explicit identifier
            "ip": "127.0.0.1",
            "port": 80,
            # ... stream / K / D / etc.
        },
        simulation=True,
    )

    def __init__(self, name, workspace, **kwargs):
        ...
        cam = prm["camera_cfg"]
        # The data-path helper. Constructs unconditionally — the helper
        # handles "no identifier" internally (forces sim, no client
        # opened). The workspace never calls attach_device; the vision
        # server owns the publisher on the camera's Pi.
        self.vision = VisionStation(
            ip=cam["ip"], port=cam["port"],
            serial_number=cam["serial_number"],
            simulation=prm["simulation"],
            label=self.name,
        )

    @property
    def device_ids(self) -> list[str]:
        # Gate on the same identifier the vision server uses to claim
        # its bus topic. Empty serial → no panel row on the workspace
        # side; the daemon also publishes nothing for this id.
        sn = self.vision.serial_number
        return [f"camera:{sn}"] if sn else []

    def device_claim(self, device_id: str) -> str:
        sn = self.vision.serial_number
        if sn and device_id == f"camera:{sn}":
            return "sim" if self.vision.simulation else "real"
        return "real"
```

Note: there is no `critical:` field in DEFAULTS here. The daemon
owns critical-ness (it sets `critical=` in its own `attach_device`
call), and the workspace can't override it from a scene yaml. This
is the legitimate difference between the two shapes — for
workspace-owned devices, the component's DEFAULTS expose `critical`;
for daemon-owned devices, they don't.

**`device_ids`** — required. List of `<kind>:<natural-id>` strings the
component depends on. The scanner that builds the project's device
panel reads this property; components that don't define it are silently
ignored.

> **Visibility rule (normative — same for every device kind).** A
> device row appears in the Devices panel **only if some component
> returns its id from `device_ids`**. The condition that gates that
> return must be the explicit connection field for the device — `ip`
> for Core's robot, `port` for the multimeter, `serial_number` for
> the camera, etc. Empty value → no entry, no row. Non-empty value →
> row appears (sim or real per `simulation:`). The `simulation:`
> flag is **separate**: it controls how a *declared* device is
> treated, not whether it's declared.
>
> Workspace-owned publishers (shape A above) gate `attach_device` on
> the SAME condition — two gates, identical predicate. Daemon-owned
> publishers (shape B) gate only `device_ids` on the workspace side;
> the daemon does its own gating on the daemon side. Either way,
> "empty identifier" means the same thing to every layer: this
> project doesn't claim this device.
>
> Why one mental model across kinds: operators see one rule
> ("identifier = device declared"), and component authors don't get
> to invent per-kind variations. If you catch yourself writing
> "always attach" (no gate) or "attach only when not sim" (wrong
> gate), stop — those are bugs against this rule.

**`device_claim(device_id)`** — optional. Returns `"real"` or `"sim"`,
the project-level claim mode for that device id. This is the
workspace-side surface for rule 3 (§1). Default when the method is
absent: `"real"`.

When to implement `device_claim`:

- The component owns a helper (like `VisionStation`) that has its own
  simulation mode, AND that helper does NOT publish to the bus (because
  the daemon does — see §15). Without `device_claim`, the panel has no
  way to tell that the operator authored sim for this device, and
  auto-pause would block the runtime on the daemon's down events.
- The component owns the bus publisher itself (like `Core` for the
  robot). The bus already carries `info.sim`, but implementing
  `device_claim` keeps the workspace-side surface symmetric and lets
  the panel render the SIM pill from one consistent source.

Aggregation across components (handled by
`workspace.runtime_server._project_device_claims`): **strict-claim
wins.** If any component declares `"real"` for a device id, the
project's net claim is `"real"`. `"sim"` only takes effect when every
declaring component agrees. Auto-pause must respect the strictest
intent — never get fooled into skipping a critical-down by a single
sim claim from an unrelated component.

Four rules — same for every device kind, no exceptions:

1. **Gate visibility on the same explicit identifier — everywhere.**
   One config field per component (`ip`, `port`, `serial_number`, …)
   names the device; empty = no claim, no bus presence, no panel
   row. For workspace-owned publishers (shape A), gate both
   `attach_device` and `device_ids` on it. For daemon-owned
   publishers (shape B), gate `device_ids` on it on the workspace
   side; the daemon gates its own `attach_device` on the same
   identifier on the daemon side.

2. **Declare `device_ids`** on any component that depends on remote
   devices. Return the same id the publisher uses to claim its
   topic. Empty list when the identifier is unset.

3. **Compose a per-kind data-path helper** (e.g.
   [`VisionStation`](../workspace/workspace/components/inspection/vision_station.py)
   for cameras). New kinds get their own helper modeled on
   VisionStation. They don't share a base class; they share a *pattern*
   (constructor takes ip/port/serial/simulation, exposes operations,
   plus `close()`). The helper does NOT publish to the bus when there's
   a separate daemon for the same device id — let the daemon own the
   topic (rule 1 of §1).

4. **Implement `device_claim`** when the helper has a sim mode the
   bus can't see. Default (`"real"`) is safe; only override when there
   really is a sim path the bus is unaware of.

The Protocol that defines the contract is at
[`workspace.devices.DeviceComponent`](../workspace/workspace/devices/component_contract.py)
— `runtime_checkable`, so `isinstance(component, DeviceComponent)` works
when you need it. No inheritance required.

**Why a Protocol and not a base class:** components in
`workspace/components/` are heterogeneous (devices, racks, fixtures,
adapters). Forcing a base on the device subset would be artificial. The
Protocol covers exactly the two members that matter and stays out of
the way for everything else.

---

## 10.5 Where the sim if/else lives — component, not recipe

There is **one place** to branch on sim mode per device: the
component's constructor, when it picks which underlying API to use.
Everywhere else — methods on the component, recipes, actions,
checks — stays sim-agnostic.

### Where to add what — at a glance

When you're adding a new device (printer, scale, pipette, …), this
is the map. The same pattern applies to every device.

| You're adding… | Goes in… | What it does |
|---|---|---|
| The **`simulation:` flag** in YAML | `scene/*.j2` (or `scene/*.yaml`) under the component block | Authored operator intent. The single source of truth. |
| The **sim/real branch** (`if simulation: ... else: ...`) | The component's `__init__`, picking the api/helper to hold on `self` | Exactly once per device. Constructor decision, runtime-immutable. |
| The **sim API stub** (no-op `print()`, `dose()`, …) | A small `XxxSimAPI` class in the same module as the real driver | Same shape as the real driver. Methods return success without touching hardware. |
| The **`device_ids` declaration** | A `@property` on the component — `[f"<kind>:<id>"] if self.<id_field> else []` | Empty list when the field is empty → row hidden from the Devices panel (see §10 visibility rule). |
| The **`device_claim` method** | A method on the component — returns `"sim"` if `self.<simulation_flag>` else `"real"` | Surfaces sim intent to the panel + auto-pause gate (rule 3 in §1). |
| **Method calls on the device** (in recipes, actions, checks) | Anywhere — `printer.print(payload)`, `core.vision.snapshot()`, etc. | Sim-agnostic. Never `if printer.simulation: …` in these call sites. |

If you ever feel the urge to write `if some_component.simulation:` in
a recipe or action, stop — the component is missing a method, or its
sim stub is missing a behaviour. Fix it there, not at the call site.

The pattern, taken verbatim from `Core` ([core.py:292-312]):

```python
# Inside the component constructor:
self._simulation_mode = prm["simulation"]    # from scene yaml

# One branch, once, picks the api:
if not self._simulation_mode:
    self.robot_api = self.dorna           # real driver
else:
    self.robot_api = SimulationAPI()      # no-op stub

# And the camera helper is constructed with the flag baked in:
self.vision = VisionStation(
    ...,
    simulation=(not self.has_camera) or bool(prm["simulation"]),
)
```

After construction, `self.robot_api.jmove(...)` and
`self.vision.snapshot()` always do the right thing — the recipe
calls `core.jmove(...)` / `core.vision.snapshot()` and never has
to know whether it ran against real hardware or a stub.

**Why this is the rule:**

| If the sim branch lives in… | What happens |
|---|---|
| **The component** (current rule) | One well-tested branch per device. Sim swap is a single yaml flag. Recipes and actions are reusable across real + sim runs unchanged. |
| **The recipe** | Every recipe has to know about every device's sim state. Branching explodes across the codebase. One missing `if sim:` is a hardware-damaging bug. |
| **Both** | Inconsistent. Worst of both. |

**Rule for new devices** (printer, scale, pipette, …):

```python
class Printer:
    def __init__(self, ..., simulation=False):
        self._api = (
            PrinterSimAPI()                       # no-op stub
            if simulation
            else RealPrinterDriver(host=...)
        )

    def print(self, payload):
        return self._api.print(payload)           # no sim check here, ever

    @property
    def device_ids(self) -> list[str]:
        return [f"printer:{self.serial}"] if self.serial else []

    def device_claim(self, device_id):
        return "sim" if self._simulation else "real"
```

The recipe / action calls `printer.print(payload)` — never
`if printer.simulation: ...`. The single branch at constructor
time is the contract.

[core.py:292-312]: ../workspace/workspace/components/core/core.py#L292-L312

---

## 11. Multi-device components

A component can depend on more than one device — the contract returns a
**list**, so you just list them all.

Pattern: hold one helper instance per device, expose them as separate
attributes, and return all the ids:

```python
class DualInspection:
    """Inspection station with both a top-down and side camera."""

    def __init__(self, ...):
        self.top  = VisionStation(ip=..., serial_number="cam:top",  label="top")
        self.side = VisionStation(ip=..., serial_number="cam:side", label="side")

    @property
    def device_ids(self):
        return [
            f"camera:{self.top.serial_number}",
            f"camera:{self.side.serial_number}",
        ]

    def detect_top(self, name, **kwargs):  return self.top.detect(name, **kwargs)
    def detect_side(self, name, **kwargs): return self.side.detect(name, **kwargs)

    def close(self):
        self.top.close()
        self.side.close()
```

Same pattern for "Core has a camera AND a robot": one helper for the
camera (`self.vision`), one client for the robot (`self.dorna`),
`device_ids` returns both ids when each is on the bus. No extra
machinery — composition + a list-returning property is the whole story.

---

## 12. End-to-end example — adding a pipette

Concrete walkthrough for the next device. Three pieces.

### A. Pipette device class (your hardware abstraction)

Lives in its own pip package (e.g. `pipette_sdk`). Contract from §2:

```python
class Pipette:
    KIND = "pipette"

    def __init__(self):
        self.serial_number = ""    # filled in by connect()
        self.state = "down"
        self.msg = "not connected"
        self._listeners = []

    @property
    def id(self) -> str:
        return self.serial_number  # adapter prepends "pipette:" if missing

    def on_state_change(self, callback):
        self._listeners.append(callback)

    def connect(self, port: str) -> bool:
        # ... open serial port, identify pump
        self.serial_number = self._read_serial_from_pump()
        self._set_state("ok", "")
        return True

    def aspirate(self, volume_ul: float): ...
    def dispense(self, volume_ul: float): ...

    def recover(self) -> bool:
        self._set_state("recovering", "rebuilding port")
        try:
            ok = self._reopen_port()
        except Exception as ex:
            self._set_state("down", f"recovery failed: {ex}")
            return False
        self._set_state("ok" if ok else "down",
                        "" if ok else "recovery failed")
        return ok

    def release(self) -> None:
        self._close_port()

    def _set_state(self, new_state, msg=""):
        new_msg = str(msg or "")
        if self.state == new_state and self.msg == new_msg:
            return
        self.state, self.msg = new_state, new_msg
        for cb in list(self._listeners):
            try: cb(new_state, self.msg)
            except Exception: pass
```

### B. Pipette service (runs on the Pi where the pipette is plugged in)

```python
# pipette_service/main.py
import time
from pipette_sdk import Pipette
from workspace.devices import attach_device, AutoRecover

pump = Pipette()
pump.connect(port="/dev/ttyUSB0")

def make_recover():
    return AutoRecover(
        recover_fn=pump.recover,
        set_status=pump._set_state,
        log_label=f"pipette:{pump.id}",
    )

attachment = attach_device(
    pump,
    kind="pipette",
    sim=False,                  # this service drives real hardware
    critical=True,
    meta={"location": "bench-A", "model": "X-200"},
    recover_factory=make_recover,
    broker_host="orchestrator-pi.local",
)

try:
    while True:
        time.sleep(60)
finally:
    attachment.close()
```

That's the whole device side. Health publishes automatically. If a
second pipette service ever tries to claim the same id (config error,
mistaken host), `attach_device` raises `DevicePublisherConflict` at
startup with the conflicting `publisher_id`.

### C. Workspace component (orchestrator side)

```python
# workspace/components/pipette/pipette.py
from copy import deepcopy
from mergedeep import merge

# Mirror VisionStation but for the pipette's data path. Define
# PipetteStation in this folder, with the same shape as VisionStation
# (constructor takes host/port/serial/simulation, exposes aspirate /
# dispense / close, falls back to simulation cleanly).
from workspace.components.pipette.pipette_station import PipetteStation


class Pipette:
    DEFAULTS = dict(
        pipette_serial="",
        pipette_server_host="127.0.0.1",
        pipette_server_port=8090,
        simulation=True,
    )

    def __init__(self, name, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, kwargs)
        self.name = name
        self.workspace = workspace

        self.pump = PipetteStation(
            host=prm["pipette_server_host"],
            port=prm["pipette_server_port"],
            serial_number=prm["pipette_serial"],
            simulation=prm["simulation"],
            label=name,
        )

    # --- DeviceComponent contract ---
    @property
    def device_ids(self):
        sn = self.pump.serial_number
        return [f"pipette:{sn}"] if sn else []

    def device_claim(self, device_id: str) -> str:
        """Project-level sim/real claim for ``device_id``."""
        sn = self.pump.serial_number
        if sn and device_id == f"pipette:{sn}":
            return "sim" if self.pump.simulation else "real"
        return "real"

    # --- Convenience wrappers ---
    def aspirate(self, volume_ul):
        return self.pump.aspirate(volume_ul)

    def dispense(self, volume_ul):
        return self.pump.dispense(volume_ul)

    def close(self):
        self.pump.close()
```

### D. What just happened

- The pipette service publishes health on the bus — no orchestrator
  config change needed.
- `workspace.devices` (the orchestrator's listener) sees the new device
  automatically.
- The project's scene YAML adds a `Pipette` component pointed at the
  pipette service's host + port + serial.
- The project page scanner walks the components, reads `device_ids`,
  finds `pipette:<sn>` in the device cache, and renders a row with
  health + Recover.

**Total new code for adding a pipette family:** ~80 lines on the device
side (Pipette class + ~40-line adapter copy), ~40 lines on the
workspace side (`PipetteStation` + `Pipette` component). One paragraph
in this guide noting the new `pipette` kind exists. Zero changes to
`workspace.devices`, the orchestrator runtime, or the project page UI
— they all work generically.

---

## 13. Watching the bus (debugging tools)

The bus uses MQTT under the hood, so the standard MQTT debugging tools
work for any device:

```bash
# Watch every device's state in real time
mosquitto_sub -t 'device/+/state' -v

# Watch absolutely everything on the bus
mosquitto_sub -t 'device/#' -v

# Manually trigger recovery on a device
mosquitto_pub -t 'device/<your-id>/cmd/recover' \
              -m '{"req_id":"manual-1"}'

# Watch the reply
mosquitto_sub -t 'device/<your-id>/cmd/recover/reply' -v
```

`mosquitto-clients` is one apt-install away. Useful when something looks
weird in the UI — the bus is the source of truth.

When implementing your service:

- **Initial state should be `down`**, not `ok`. You transition to `ok`
  on a successful connect — that's a real state change and fires
  listeners. Lying about the initial state means the orchestrator never
  hears the first transition.
- **`mosquitto_sub -t 'device/<your-id>/state'`** lets you see your
  service's transitions live as you exercise it.

---

## 14. Common pitfalls

- **Forgetting to fire `_set_state("ok", "")` on successful connect.**
  Initial state is `"down"` per the contract; you must transition to ok
  explicitly. Without that, the orchestrator thinks the device never
  came up.
- **Calling `_set_state` with both same state AND same message twice.**
  No-op by design — listeners only see real updates. Use a different
  msg if you need a refresh event.
- **Blocking inside `recover()`.** Allowed (the adapter runs each
  command on a worker thread), but if your recovery takes longer than
  the orchestrator's timeout, it'll report a timeout even though
  recovery may eventually succeed. Tune both sides.
- **Publishing your own device-bus messages from inside the device class.**
  Don't. The device class is hardware abstraction; the bus is the
  adapter's job. Crossing that line couples the device to the protocol
  and you can't run it standalone for testing.
- **Putting the adapter on the orchestrator Pi instead of the device's Pi.**
  Read §8 again. False-positive downs and missed real failures are the
  cost. With conflict detection on, you'll get a loud
  `DevicePublisherConflict` at startup — fix the topology, don't
  silence the check.
- **Publishing a "sim stub" from the workspace for a device that has a
  daemon publisher.** Two writers on the same retained topic — last
  writer wins. The workspace's sim publish stomps the daemon's truth
  and the panel goes green when the device is physically down. Use
  `device_claim` instead (§10): annotate sim at the project level,
  let the daemon own the bus entry. The conflict-detection guard in
  `attach_device` will refuse this anyway, but it's faster to get
  right the first time.
- **Falling back to sim on connect failure.** Authored `simulation`
  is the operator's intent and must be honoured exactly. A failed
  initial connect is a **fault** to surface (red dot + auto-pause),
  not a reason to silently switch APIs. Silent demote masks
  deployment problems and guarantees QC will trip over it eventually.
  Let the connect fail loud and let AutoRecover retry in the
  background.
- **Putting `sim` only on the bus and not in `device_claim`.** The
  bus's `info.sim` is the publisher's claim about itself. For devices
  the workspace does NOT publish (cameras, etc.), `info.sim` is
  always whatever the daemon decides, which has no reason to know
  about the workspace's sim mode. Use `device_claim` for project-side
  intent; rely on `info.sim` only when the workspace IS the publisher
  (the robot, in-process devices).
- **Unconditional `attach_device` (no gate on the connection field).**
  Workspace-owned-publisher mistake. Violates rule 5 of §1: the
  device shows in the panel even when the user left the connection
  field empty, so two components with the same scene yaml shape
  behave differently and the operator can't predict what they'll
  see. Always gate on the explicit identifier (`ip`, `port`,
  `serial_number`, etc.) and use the same condition in `device_ids`.
  See the §10A skeleton for the canonical pattern.
- **Mismatched gates on `attach_device` and `device_ids`.**
  Workspace-owned-publisher mistake. If `attach_device` runs but
  `device_ids` returns `[]`, the bus has a publisher with no panel
  row (so the project's claim aggregation can't see it and
  auto-pause is silent). If `device_ids` returns an id but
  `attach_device` never ran, the panel shows a row with no
  publisher (perpetually pending). Both halves must be gated on the
  exact same condition.
- **Calling `attach_device` from the workspace for a daemon-owned
  device.** Daemon-owned-publisher mistake. The vision server /
  printer daemon / etc. already publishes that id; a workspace
  attach is a second writer on the same retained topic. The
  conflict-detection guard in `attach_device` will refuse it
  (`DevicePublisherConflict`), which is what you want — but better:
  don't call it in the first place. Workspace-side, daemon-owned
  components only gate `device_ids`; they never call
  `attach_device`. See the §10B skeleton.

---

## 15. Case study: the camera

The camera SDK ([github.com/dorna-robotics/camera](https://github.com/dorna-robotics/camera))
exposes the contract from §2 directly on its `Camera` class:

- `id` → property returning the USB serial number. (The adapter
  auto-prepends `camera:` since the SDK returns the bare serial.)
- `state`, `msg` → instance attributes initialized to `"down"`.
- `on_state_change(cb)` → appends to a listener list.
- `recover()` → fast-fails when the device isn't on the USB bus,
  attempts firmware reset → USB unbind/bind → pipeline restart, then
  verifies recovery with a real frame fetch before declaring `ok`.
- `release()` → alias for `close()`.

The vision server (the long-running process on the camera Pi) wraps each
acquired camera with the adapter inside
[`CameraPool.acquire`](https://github.com/dorna-robotics/dorna_vision/blob/server/dorna_vision/server/pools.py).
USB unplug fires `_set_state("down", "USB disconnected")` on a
librealsense-internal thread; the listener publishes to the bus; the
orchestrator pauses. When USB comes back, the hotplug callback fires
`cam.on_hardware_available`, which kicks the AutoRecover loop —
`cam.recover()` runs with exponential-backoff retries until the pipeline
rebuilds and state goes `recovering → ok`. Operator-initiated recovers
re-use the same loop, so manual and automatic paths share one mechanism.

### Frame-freshness validation (§6 in practice)

`Camera.frame()` runs three checks between `wait_for_frames` returning
and the alignment step. All three are FPS-independent — they compare
the device hardware clock against the wall clock, both ticking at 1 Hz
in real time regardless of stream config:

1. **Frame-number monotonicity.** Same `frame.get_frame_number()` twice
   = librealsense replaying a buffered frame after the hardware froze.
2. **Device-clock advancement.** Same `frame.get_timestamp()` twice =
   the on-device clock stalled.
3. **Wall-vs-device drift.** Over a ≥ 2 s wall window, the device clock
   must advance at least 1 s. Catches sustained underclocking (thermal
   throttle, USB starvation) where checks 1 and 2 pass per-frame but
   the device falls behind real time.

`_set_state` resets the freshness baseline whenever the state leaves
`"ok"` (down, recovering), so a rebuilt pipeline doesn't get compared
against the old pipeline's counters. Same-state msg updates preserve
the baseline — no pipeline change happened.

Cost: three numeric comparisons per frame. Effect: every caller of
`get_all` / `frame` either gets fresh data or a clear error — never a
silent stale frame slipping into detection.

### Capture → run pattern (§6 in practice)

The vision server splits frame acquisition from the detection pipeline
via two typed RPCs:

```
detection_capture(name, data=None)   →  {ok: True, ts, has_joint}  or  {ok: False, msg}
detection_run(name, use_last=True)   →  detection result, computed on the captured frame
```

The orchestrator-side wrapper in `VisionStation.detect()` makes this
transparent: by default it captures first, raises `CameraUnavailableError`
on capture failure, then runs detection on the just-captured frame.
Recipe code is just `inspection.detect("name")`; the bulletproof
guarantee lives in the component layer. For efficiency, `capture()` is
also exposed so callers can grab one frame and run multiple detections
on it (`detect(..., use_last=True)`).

That's the entire pattern. The next read-only device — scale, encoder,
ADC, lidar — follows exactly the same shape, just with different
hardware internals.

### And the robot — same bus, different wrapping

The robot ([dorna2.Dorna](https://github.com/dorna-robotics/dorna2)) is
also on the bus, via [`RobotStation`](../workspace/workspace/components/core/robot_station.py).
Same Device protocol, same `MQTTDeviceAdapter` + `AutoRecover` wiring
in `Core`, same Devices panel UX (red dot + Recover button) — but two
different failure modes both surface as `state="down"`:

- **Connection lost** — any underlying `ConnectionError` / `OSError`
  from a wrapped Dorna call (TCP drop, host unreachable). The
  exception still propagates to the recipe.
- **Robot alarm** — motion commands return `int < 0` on alarm
  (limit hit, IK failed, E-stop). The wrapper sets
  `state="down"` with `msg="alarm code N"`.

Connection drops kick `AutoRecover.trigger()` automatically via the
state→down edge (IP devices have no hotplug, so the state edge is the
substitute trigger). Successful calls after a non-`ok` state clear
state back to `ok` — recipes that auto-retry resolve the panel state
themselves without operator intervention.

The wrapping is **pure composition** — dorna2 itself is unmodified.
Recipes calling `core.dorna.move(...)` / `core.dorna.kinematic.inv(...)`
keep working unchanged; the wrapper proxies via `__getattr__` and
intercepts return values + exceptions on the way out.

Write devices (printer, pipette) reuse the adapter + AutoRecover
layers but need a different freshness/verify pattern; see §6.

---

## 16. Simulation model

The "is this device fake?" question has two independent sources of
truth, and the contract keeps them straight so neither hides reality.

### Two signals, deliberately separate

| Signal | Set by | What it means |
|---|---|---|
| `info.sim` (bus) | The publisher of the device | "I, the publisher, am running in sim mode for this device." Workspace sets it for the robot (workspace IS the publisher); a future sim-aware vision server might set it for a fake camera; etc. |
| `device_claim(id) == "sim"` (workspace) | A workspace component | "*This project* uses this device in sim mode locally — I take canned values regardless of what the bus says." For cameras, the daemon owns the bus entry, so this is the only place workspace sim intent for the camera lives. |

Both are visible to the panel and the orchestrator. Either one alone
is sufficient to:

- show a SIM pill on the device row in the Devices panel (cyan,
  "SIM"); and
- skip auto-pause on a critical-down for that device.

The dot color always reflects the **publisher's** truth (rule 2). A
sim-claimed camera that's physically down shows red dot + cyan SIM
pill — operator sees both layers, neither hides the other.

### What changes per scenario

| Authored | Bus | Project claim | Panel | Auto-pause on down |
|---|---|---|---|---|
| `simulation: True` on a workspace-published device (e.g. robot) | green, `info.sim=true` | `sim` | green dot + SIM pill | skipped |
| `simulation: False` on a workspace-published device, real and reachable | green | `real` | green dot, no pill | n/a (state is ok) |
| `simulation: False` on a workspace-published device, real and unreachable | red | `real` | red dot, no pill | **paused** |
| `simulation: True` on a daemon-published device (e.g. camera) | whatever the daemon says | `sim` | dot reflects daemon + SIM pill | skipped |
| `simulation: False` on a daemon-published device, daemon down | red | `real` | red dot, no pill | **paused** |
| Operator toggles `core.simulation(True)` mid-run | adapter republishes `info.sim=true` | claim flips to sim on next bus event | SIM pill appears live | newly skipped |

### Sim is orthogonal to connection state

This is the **load-bearing invariant** that lets the panel tell the
truth in every mode:

- **Connection state** (`state` / `msg` on the bus) ALWAYS reflects
  real hardware reachability. The station attempts the real connect
  on startup regardless of sim, and `recover()` always does the real
  reconnect. The dot color is hardware truth.
- **Sim flag** (`info.sim` on the bus + workspace `device_claim`) is
  the operator's authored intent. It controls **what recipes do**
  (canned vs. real I/O) and **whether auto-pause fires on down**
  (skipped when sim). It does NOT change what the dot shows.

The four useful cells of the cross-product:

| Authored sim | Real reachable | Dot | SIM pill | Auto-pause on down |
|---|---|---|---|---|
| `true` | yes | 🟢 | yes | n/a |
| `true` | no  | 🔴 | yes | **skipped** (sim claim) |
| `false` | yes | 🟢 | no | n/a |
| `false` | no  | 🔴 | no | **fires** |

Pre-flight value: develop in sim with the real identifier configured,
and at-a-glance the panel tells you whether real mode would deploy
clean. Red + SIM = "sim is fine for dev, but check the wiring before
you ship."

### Manual sim toggle at runtime

`core.simulation(True/False)` (and any analogous component-level
toggle) flips three layers in lockstep:

- **Bus signal** — the component calls `attachment.set_sim(True/False)`,
  which republishes the retained `info` payload with `sim=true/false`.
  AutoRecover suspends in sim, re-arms on flip back to real.
- **Workspace signal** — the component's `device_claim(id)` reads
  whatever's authoritative (e.g. `self._simulation_mode`) live, so a
  `claim_resolver` walking the components picks up the new mode on
  the next call. No explicit invalidation needed.
- **Station flag** — the component calls
  `station.set_simulation(True/False)`. This is a **flag flip only**
  — it does NOT open or close the hardware handle. Connection state
  remains independent: the bus dot keeps reflecting reachability
  through and after the flip. The station's `id` stays **stable** so
  the bus topic doesn't change.

The panel updates within the WS-push latency. Operator sees the SIM
pill flip on the row and in the modal at the same time as auto-pause
becomes inactive (or active) for that device.

### Parity rule (normative — every workspace-owned device)

Every workspace-owned device component MUST expose a `simulation(on)`
method with the shape Core and MultiMeter use:

```python
def simulation(self, on: bool = True):
    if self._simulation_mode == bool(on):
        return                                   # idempotent
    self._simulation_mode = bool(on)
    self.<station>.set_simulation(on)            # flips station flag
    if self._attachment is not None:
        self._attachment.set_sim(on)             # publishes info.sim
    # (Core also swaps self.robot_api here for its sim/real abstraction.)
```

And the **station** class MUST expose `set_simulation(sim)`:

- Flag-only: just `self.simulation = bool(sim)`. Does NOT touch the
  connection. The bus dot continues to reflect hardware reachability.
- `id` does NOT change across the flip — sim is a separate axis.

For the **initial connect**: the station attempts it on startup
regardless of sim. A fake / unreachable identifier in sim mode
correctly shows red dot + SIM pill ("sim authored, real wouldn't
work"). AutoRecover is suspended in sim (via the attachment's
`set_sim`), so the red state doesn't cause retry storms — it just
sits there as honest pre-flight feedback.

Why mandatory: without this, the operator can't switch a device from
sim to real (or back) mid-run. Daemon-owned devices (camera, etc.)
don't need it on the workspace side — the daemon owns the handle and
exposes its own toggle. But for the workspace-owned shape, missing
`simulation(on)` silently drops a platform feature.

Both Core (`core.py`) and MultiMeter (`multi_meter_bk879b.py`)
already follow this shape — copy from either for new device kinds.

---

## 17. Sim data injection — `sim_return`

§16 decides **whether** a method runs against fake or real hardware.
This section decides **what the fake path returns** — and makes it
explicit, per-call, and uniform across every device.

### The rule (normative)

Every sim-capable device read method takes one optional parameter,
**`sim_return`**, and obeys three rules:

1. **`sim_return` always has the same format as the method's real
   return.** This is the load-bearing rule — the type/shape of
   `sim_return` (and of its in-signature default) is *determined by* what
   the real call produces, never chosen for convenience. A `Reading` for
   the scale's `weigh()`, a `float` for its `weight()`, a `Measurement`
   for the meter's `read_*()`, a list for vision's `detect()`. So a sim
   value is indistinguishable from a real one to every caller —
   `m.primary`, `r.weight`, `len(hits)` all work the same. No wrapping, no
   unwrapping, no cross-type magic. If you change a method's real return
   type, change its `sim_return` to match in the same edit.

2. **The default lives in the signature.** The canned sim value is the
   parameter's default, written right in the method signature — shaped
   like the real return. There is **no hidden `_sim_*()` helper**. Open
   the method and you see the fake value:

   ```python
   def weigh(self, sim_return=Reading(status="stable", weight=12.345, unit="g", raw="sim")):
       if self.simulation:
           return sim_return          # explicit — default visible above
       ... real read ...

   def weight(self, stable=True, timeout=10.0, sim_return=12.345):
       if self.simulation:
           return sim_return
       ... real read ...
   ```

3. **Real mode ignores it.** In real mode the method reads hardware and
   never looks at `sim_return`. The same call site works on a bench with
   no hardware (returns the injected value) and on a real device
   (returns the live reading) — no branching in recipes or actions.

### How you inject (from a recipe / action / test)

Pass `sim_return` at the call site. It is the *only* way to inject — no
scene-yaml field, no scenario file, no stored state on the device:

```python
# scale — inject a per-tube fake weight in a sim run
grams = rcp["scale"].weight(sim_return=10.0 + tube)     # sim → that float; real → balance

# multimeter — inject a specific Measurement
rcp["meter"].read_capacitance(sim_return=Measurement(primary=4.7e-6, primary_unit="F", ...))

# vision — inject detections so a sim run exercises real decision logic
hits = rcp["inspector"].detect(sim_return=[{"label": "cap", "ok": True}])
if hits: ...                                            # decision driven by injected data
```

Omit `sim_return` and you get the canned default — existing sim runs are
unchanged.

### The default literal is inline, at every layer

The default `sim_return` is written **inline in the signature** — at the
station, the component, AND the recipe. No `None`, no "if None look up
the real default", no module constants, no helper. Open the method at any
layer and the canned value is right there. The default is shaped like the
real return: a bare literal when the real method returns a scalar
(`sim_return=12.345` for the scale's `weight()`), a full object literal
when it returns an object:

```python
# scale weight() returns a float → default is a float, at every layer
def weight(self, stable=True, timeout=10.0, sim_return=12.345):
    if self.simulation:
        return sim_return
    ... real read ...

# meter read_resistance() returns a Measurement → default is a
# Measurement literal, inline, at every layer (no SIM_* constant)
def read_resistance(self, frequency=1000,
                    sim_return=Measurement(primary=1.0e3, primary_unit="Ω",
                        secondary=0.0, secondary_unit="", function="R",
                        frequency="1000", raw="sim,1e3,0.0")):  # 1 kΩ
    if self.simulation:
        return sim_return
    ... real read ...
```

Yes, an object default repeats across the three layers. That repetition
is deliberate: the value is explicit and visible everywhere it can be
called, with nothing to chase. Don't replace it with a `None` sentinel or
a shared constant to "DRY it up" — that re-introduces the indirection this
contract exists to remove.

### Scope

`sim_return` applies to device **read** methods — anything that returns a
measurement / detection / reading. It does **not** apply to the robot's
`SimulationAPI`, which is behavioural (it simulates motion and tracks
joint state), not a single reading. Leave the robot sim as-is.

### Naming convention (bundled with this contract)

Driver files use the **`_driver` suffix** uniformly
(`spx222_driver.py`, `bk879b_driver.py`, `cab_driver.py`,
`keyto_driver.py`) — not `_wrapper`. The three-file stack is
`<device>.py` (component) → `<device>_station.py` (station) →
`<device>_driver.py` (raw driver).

Scale, MultiMeter, and Vision all follow the `sim_return` contract today
— copy from any of them for a new device kind.

---

## 18. Open follow-ups (not blocking new device authors)

- **Local UI on each device server.** Each device service should also
  show its own health locally (the vision server's web GUI does this;
  future printer/pipette services should too). Same data as the
  orchestrator — they all subscribe to the bus independently.
- **Standalone device-base package.** The adapter / AutoRecover live in
  `workspace.devices` today. If non-Python device services appear, or
  workspace becomes too heavy a dependency for thin device hosts, factor
  these out into a tiny standalone package.

---

## Appendix A — Wire protocol

The bus is implemented over MQTT. You normally don't need to look at
this — the adapter handles every detail. Open this section only if
you're building a device service in another language, debugging a
protocol-level issue, or curious how it works.

### Setup

Run [mosquitto](https://mosquitto.org) on **one** machine in your
system (typically the orchestrator's Pi, since it's always-on):

```bash
sudo apt install mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

Default broker URL: `mqtt://<broker-host>:1883`.

Each device service installs `paho-mqtt`:

```bash
pip install paho-mqtt
```

### Topics

All payloads are JSON-serialized strings.

#### `device/<id>/info` — discovery / metadata (retained)

Published by the device service. Payload:

```json
{
  "id": "camera:130322274110",
  "kind": "camera",
  "critical": true,
  "sim": false,
  "publisher_id": "host-a:12345:camera:130322274110",
  "meta": { "model": "D405", "usb_port": "..." }
}
```

- `retain=true, QoS=1`.
- Published once on service startup; re-published whenever metadata
  changes (notably on `set_sim()` toggle so the panel sees the new
  badge live).
- `sim`: publisher self-flag. `true` means the publisher is itself
  running in sim mode for this device. Optional for back-compat —
  missing is treated as `false`. **Independent** of the project-level
  claim (§10) — both can be set, and either one alone is sufficient
  to skip auto-pause and show a SIM pill in the panel.
- `publisher_id`: stable `<hostname>:<pid>:<device_id>` triple
  identifying the adapter instance. Used by `attach_device` for
  conflict detection (§8) — a different `publisher_id` on this topic,
  with `online: true` on `state`, indicates a competing publisher and
  blocks the second attach. Optional for back-compat; absent is
  treated as a legacy publisher and conservatively blocks new
  attachments to the same id (forces a clean rolling-upgrade).

#### `device/<id>/state` — health (retained)

Published on every state transition. Payload:

```json
{
  "state": "ok",
  "msg": "",
  "online": true,
  "ts": 1730412345.678
}
```

- `retain=true, QoS=1`.
- `state` ∈ `"ok" | "down" | "recovering"`.
- `msg` is human-readable; empty when ok.
- `online` is a presence flag: `true` while the device service process
  is alive, `false` only in the LWT and clean-shutdown payloads. Lets
  the orchestrator distinguish "service alive, hardware bad" (Recover
  button works) from "service dead" (offline pill, Recover hidden).
  Optional for back-compat; missing is treated as `true`.
- `ts` is Unix epoch seconds (float).

#### Last Will and Testament — auto-published on connection loss

When a device service dies, the broker auto-publishes on its behalf:

- Topic: `device/<id>/state`
- Payload: `{"state": "down", "msg": "connection lost", "online": false, "ts": <broker-set>}`
- `retain=true, QoS=1`

The adapter sets this LWT during its initial connect, and also publishes
an equivalent `online: false` state on clean shutdown (the broker does
NOT fire the LWT on graceful disconnect, so without this final publish
subscribers would keep seeing the last `online: true` retained message).

#### `device/<id>/cmd/recover` and `device/<id>/cmd/release` — commands

Published by the orchestrator. Payload:

```json
{ "req_id": "<uuid>" }
```

Subscribed by the device service.

#### `device/<id>/cmd/recover/reply` and `.../release/reply` — command results

Published by the device service. NOT retained.

```json
{
  "req_id": "<echo-of-request>",
  "ok": true,
  "state": "ok",
  "msg": ""
}
```

- `QoS=1, retain=false`.
- Caller correlates by `req_id`.

### States

| Value | Meaning |
|---|---|
| `"ok"` | Device delivers its data contract correctly. |
| `"down"` | Device cannot deliver its contract. Operator action needed. |
| `"recovering"` | Recovery cycle in progress. Transient — resolves to ok or down. |

Don't invent intermediate states. If a transition is normal (not a
fault), don't fire it.

### Discovery flow

The orchestrator on startup:

1. Connects to the broker.
2. Subscribes to `device/+/info` and `device/+/state`.
3. Mosquitto immediately delivers the **retained** info + state messages
   for every device that has ever published — orchestrator builds its
   view of the world from those.

No explicit "register" or "discover" handshake. Retained messages ARE
the discovery mechanism.

### Why MQTT specifically

- **Retained messages** = the broker remembers each device's last state.
  A new orchestrator (or a reconnecting one) immediately sees current
  state, no resync code needed.
- **Last Will and Testament** = the broker auto-publishes "down" for
  any service whose connection drops unexpectedly. Free dead-publisher
  detection.
- **Wildcard subscriptions** = orchestrator subscribes to `device/+/state`
  and discovers every device automatically. No registration handshake.
- **One broker, many subscribers** = orchestrator + dashboards + loggers
  + monitoring tools all tap the same bus.
- **Tiny** = mosquitto is ~1 MB binary, ~5 MB RAM. Runs on any Pi.

What you give up: no language-level type-checked contract — convention
enforced by this doc + code review. Mitigation: a unit test per device
service that publishes to the spec'd topics with the spec'd payloads
and verifies the broker echoes them back.
