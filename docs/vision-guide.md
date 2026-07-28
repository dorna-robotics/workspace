# Vision

Cameras, the vision server, and inspection — how a workspace sees. This
is the single doc for everything vision-related: wiring a camera into a
scene, intrinsics, detections, and where the images go. It grows as the
vision stack does.

Repos involved: `camera` (the RealSense driver), `vision` (the server +
detection pipeline + web GUI), and this platform's client side
(`workspace/components/inspection/`, `workspace/recipes/inspector.py`).

---

## 1. The big picture

```
┌─────────────────────────────┐   WS (JSON envelope)   ┌────────────────────────────┐
│  Vision server              │  ────────────────────  │  Workspace                 │
│  (the Pi the cameras are    │                        │  Inspection component /    │
│   plugged into)             │                        │  core camera = thin client │
│  - camera pool (by serial)  │                        │  via VisionStation         │
│  - detection pipeline       │                        │  recipes: FixedInspector,  │
│  - web GUI                  │                        │  MobileInspector           │
└─────────────────────────────┘                        └────────────────────────────┘
```

Run the server on the machine the cameras are plugged into:

    cd ~/Downloads/vision && sudo python3 -m dorna_vision.server --port 4001

GUI at `http://<that-pi>:<port>/`. The camera pool is **idempotent and
keyed by serial STRING** — adding a camera that is already streaming
returns it instantly; a second workspace client attaching to the same
camera is free.

## 2. Scene wiring

Two ways a workspace uses a camera:

- **A fixed inspection station** — an `inspection_*` component:

  ```yaml
  inspection_horizontal_1:
    type: "inspection_horizontal"
    simulation: false            # defaults to true — without this the
    camera_cfg:                  # server is never contacted
      serial_number: "315122271350"
      ip: "10.0.0.20"
      port: 4001
  ```

- **The robot-mounted camera** — on the core:

  ```yaml
  core:
    has_camera: true
    camera_cfg:
      serial_number: "218622272001"
      ip: "10.0.0.20"
      port: 4001
  ```

Rules (each one is a bug we actually hit):

- **Quote the serial.** Unquoted, YAML parses it as an int; the pool
  keys by string, and the mismatch surfaces as a 10 s `camera_add`
  timeout, not a type error. (The platform now str()-coerces at the
  boundary, but explicit strings are the convention.)
- `serial_number` / `ip` / `port` identify the server + camera; every
  OTHER key in `camera_cfg` (`stream`, `K`, `D`, `native_res`, `mode`,
  `exposure`, `filter`) is forwarded to the server's `camera_add`.
- Sim is authored intent: `simulation: true` (or an empty ip/serial)
  stubs the camera; real-mode failures raise at launch — they never
  silently demote to sim.

## 3. Intrinsics (K, D) — the contract

**Default: author nothing.** With no K/D in `camera_cfg`, every
computation uses the camera's factory intrinsics for the ACTIVE stream
profile — correct at whatever resolution actually negotiates, always.

**Custom calibration: author all three.**

```yaml
K: [[fx, 0, cx], [0, fy, cy], [0, 0, 1]]
D: [k1, k2, p1, p2, k3]
native_res: [1280, 720]        # the resolution K/D were calibrated at
```

The camera rescales K by `stream/native_res` to the mode that actually
runs. Scaling is exact only between **same-aspect** modes (1280×720 ↔
848×480). Crossing into 4:3 (640×480) is a sensor CROP — no linear
scale is correct there; use factory values or recalibrate at that mode.

Reading the true values:

- `Camera.get_K()` / `get_D()` (camera repo) — plain lists, from the
  ACTIVE profile; `stream="depth"` for the depth stream. In `bgrd`
  mode depth is aligned into the color frame, so **color intrinsics
  are the ones detection uses**.
- The GUI: click a camera image — the lightbox caption shows the
  EFFECTIVE intrinsics (labeled `factory` or `override`) as
  copy-paste-ready Python literals, `native_res` included.
- The `camera_info` WS command returns the same over the API.

## 4. Stream modes and USB fallback

The requested stream (default 1280×720@30) needs a USB 3 link. On
failure the camera walks a ladder — request → 848×480@15 → 640×480@15 —
printing loudly which mode it settled on. `self.stream` keeps the
REQUEST (recover retries the best mode after a re-plug);
`self.stream_actual` is what runs, and it is what the GUI, `camera_info`
and the intrinsics scaling read. A card showing usb 2.x with a small
image means: fix the cable/port, everything re-negotiates up on its own.

## 5. Inspector recipes and detections

`FixedInspector.present()` positions the held item at the station
(`soft_approach=False` — presenting is not an insertion), `detect()`
captures a fresh frame and runs the named detection via RPC (canned
`sim_return` in simulation, so workflow timing is sim-identical).

`detection_preset` shape — see the example in
`workspace/recipes/inspector.py`. Author it by building the detection in
the server GUI, then copying the values.

**`display.save_img` / `save_img_roi` — where frames land.** Paths are
on the VISION server's machine, resolved against its cwd; use absolute:

| value | behavior |
|---|---|
| `false` / `0` | no saving |
| `true` / `1` | timestamped jpgs in `output/` under the server cwd |
| `"/some/folder/"` | trailing slash (or existing dir): auto-created, `<timestamp>.jpg` / `roi_<timestamp>.jpg` inside — per-detection history |
| `"/some/file.jpg"` | that exact file, OVERWRITTEN every detection ("always the latest"); parent dir must exist |

Don't point saves at `/tmp` (tmpfs — gone on reboot), and remember a
save per cycle is an SD write per cycle at production volume.

**ROI from a 3D box.** The preset's `roi.corners` is always a pixel
polygon `[[u, v], ...]`; `detection_box_corners(name, box, K=None,
D=None)` generates it from bench geometry:

    box = [x, y, z,  a, b, c,  w, d, h]

`x y z` — center of the box's BOTTOM plane; `a b c` — rotation VECTOR
(axis-angle, degrees — the dorna2 xyzabc convention, not Euler);
`w d h` — extents along the box's local axes, and the SIGN of `h`
picks the side: `> 0` rises from the bottom plane, `< 0` hangs below
it. The box lives in the frame `base_in_world` defines — the same
frame detections report in — and never the camera's own moving frame.
The two mountings differ only in the chain root:

- **fixed camera**: root = the lens; `base_in_world` is the lens pose
  (default identity: world IS the camera frame, between the lenses).
- **robot-mounted**: root = the ROBOT BASE; `base_in_world` places the
  robot base (default identity: world IS the robot base frame). The
  server composes base -> joints -> mount -> lens per capture — which
  is why capture() snapshots frames AND joints atomically: boxes stay
  bench-fixed no matter where the arm was looking from.

Set `base_in_world` once to work in bench coordinates in either case.
The call returns the convex hull of the 8
projected corners, paste-ready as `corners` — regenerate on camera
moves instead of re-drawing in the GUI.

## 6. Failure semantics — what survives what

- **A camera dies (USB)**: the vision server's per-camera AutoRecover
  reconnects it when it reappears; health flows to the device bus, the
  card goes red in the GUI. Detections fail while it's down — capture
  returns ``ok: False`` → ``detect()`` raises ``CameraUnavailableError``
  → the BT leaf fails → the workflow PAUSES for the operator. Resume
  retries the action.
- **The vision server dies**: the workspace does NOT crash and the
  failure is never absorbed — the failing call marks the session dead
  and surfaces, the workflow pauses per the device's critical flag,
  exactly like any other device. On the operator's Resume, the re-run
  action re-establishes the session first (re-dial, camera_add — the
  pool is idempotent — and re-registration of every detection this
  station authored, since detections are per-session server state) and
  runs ONCE. Honest failures, explicit recovery, no workspace relaunch.
  Session re-establishment is transport plumbing (same class as the
  device bus's own MQTT reconnect); it is not an operation retry.
- **Launch-time**: an unreachable server (or a bad serial) fails the
  LAUNCH loudly — real mode never silently demotes to sim.

## 7. Operational notes

- Viewer/GUI socket blips during a Capture are benign: the JPEG encode
  burst can delay a keepalive; the client auto-reconnects within a
  second and the run never pauses (observability never blocks).
- The workspace's `camera_add` at launch is instant when the GUI (or a
  prior launch) already added the camera — pool idempotency.
- Restart order after code changes: camera/vision repo changes need a
  vision-server restart; GUI-only changes need a browser hard-refresh.
