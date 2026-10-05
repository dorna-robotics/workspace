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

- **The robot-mounted camera** — a component like any other, bolted to
  a robot link (the core has no camera keys):

  ```yaml
  inspection_d405_robot_1:
    type: "inspection_d405_robot"
    simulation: false
    camera_cfg:
      serial_number: "218622272001"
      ip: "10.0.0.20"
      port: 4001
    attach:
      parent_name: "core"
      parent_solid: "robot_A5"     # the camholder link
      parent_anchor: "hole_0"
      child_solid: "body"
      child_anchor: "hole_0"
      offset: [0, 0, 0, 0, 0, 0]
  ```

  Its `collision_box` (the component's own) becomes part of `robot_A5`
  for the planner — a *link box*: it moves with the wrist, blocks the
  world and non-adjacent links, and is never a self-hit against the
  wrist or its neighbours. One body. (project-guide §2 "Riding the arm".)

Rules (each one is a bug we actually hit):

- **Quote the serial.** Unquoted, YAML parses it as an int; the pool
  keys by string, and the mismatch surfaces as a 10 s `camera_add`
  timeout, not a type error. (The platform now str()-coerces at the
  boundary, but explicit strings are the convention.)
- `serial_number` / `ip` / `port` identify the server + camera; every
  OTHER key in `camera_cfg` (`type`, `stream`, `K`, `D`, `native_res`,
  `mode`, `exposure`, `filter`, `focus`) is forwarded to the server's
  `camera_add`.
- **`type` picks the driver** on the vision server: `"d405"` (RealSense,
  depth + color — the default) or `"ueye_xs"` (IDS uEye XS, color +
  autofocus — §8). Write it explicitly.
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

## 5. The Inspector and detections

ONE recipe class — `Inspector` — for fixed and robot-mounted cameras.
Fixed vs mobile is a SCENE property (where the camera solid sits in
the kinematic tree), not a class split:

- **station form** (`component: inspection_...`): `present()` positions
  the held item (`soft_approach=False` — presenting is not an
  insertion), then `detect()`.
- **core-camera form** (no `component:` key): detections through the
  robot-mounted camera; no motion surface (`present()` raises — the
  arm IS the positioning).

Every capture/detect states the lens's world pose at imaging time
(`camera_in_world`) — the workspace is the single kinematic authority
and the vision server never models the robot. The contract this rests
on: **capture at rest** (`present()` ends checkpointed; detection
while moving is unsupported), and **a `lens` anchor in the scene
tree** on the owning component. Fixed stations author it directly.
The robot camera is no different: an `inspection_*_robot` component
written in the scene, bolted to `robot_A5`'s camholder holes
(`hole_0`/`hole_1` — the J4 housing, which rotates with joints[4] but
NOT the wrist's joints[5]); its `lens` anchor IS the camera frame, and
it owns the VisionStation. The core is a robot, not a camera: it has
no camera keys and answers no detection call. A recipe names the camera
component like any station (`component: inspection_d405_robot_1`); an
`Inspector` on a camera attached under a robot link carries no station
geometry, since the arm moves the camera — the scene's `attach` decides
that (`Workspace.rides_robot`), there is no class flag. No eye-in-hand /
eye-to-hand distinction anywhere, no `robot_host`: the tree gives the
lens pose, whichever link or plate the camera sits on, and the vision
server never models the robot. No camera component = the launch fails
naming it, never a silent sim camera.

`detect()` runs the named detection via RPC (canned `sim_return` in
simulation, so workflow timing is sim-identical).

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

**`display.client_save_img` / `client_save_img_roi` — the same, on the
client's disk.** Same values as the table above, but the file is written
on the computer that added the detection (the workspace Pi, a laptop):
`True` -> the folder `output/` (`<timestamp>.jpg` inside), a folder ->
one `<timestamp>.jpg` / `roi_<timestamp>.jpg` per run, a file -> that
file, overwritten. The frame travels with no extra request: the vision
server encodes it on its own thread AFTER the run replies and pushes it
over the detection's own socket (a `detection_img` event + one binary
frame); the client writes it on its event thread. Full resolution,
encoded exactly as `save_img` would write that name — JPEG for a folder
or `.jpg` (~40 ms, <1 MB at 6 MP), lossless for `.png` (~300 ms, 5 MB).
Set both pairs to keep a copy on each machine. A relative path is
relative to the file it is written in (the one rule, project-guide §1):
in a config file, its folder — bna's `vision/tube_od.yaml` says
`"../captures/tube_od/"` and lands in `bna/captures/tube_od/`; written
in Python (a notebook, `main.py`), the folder that program runs in
(list `captures` in launch.yaml `folders:` to see it in the file
browser). `true` is the folder `output/` under the same rule. Nothing
is placed anywhere implicitly. At most 16 frames per
client are queued; past that the newest is dropped and `[push] dropped`
logged once — a slow link never grows memory or stalls a camera.
`vc.on_event(fn)` sees every frame after its file is written
(`event["path"]`).

**`save_img()` on the client — on demand.** For one image when you
ask, rather than every run: after a run, the client pulls the image over
the API (one extra request) and writes it on the computer calling it.

    det = vc.detection("cnt")
    det.run()
    det.save_img("captures/a.jpg", type="img_roi")               # quality 100
    det.save_img("captures/", type="img_roi")                    # roi_<timestamp>.jpg
    det.save_img("captures/a.jpg", type="img", quality=90)

Full resolution (the server's downscale is preview-only and never
applies here), JPEG at `quality` — 100 by default, because a saved
frame is usually a dataset. Path rules match the server table: a folder
(trailing `/` or an existing dir) gets `<timestamp>.jpg` /
`roi_<timestamp>.jpg`, anything else is that file, overwritten; folders
are created, the written path is returned, and the bytes are JPEG
whatever the extension. `type`: `img` (annotated), `img_roi` (the
unannotated crop — the one for training), `img_thr`, `color_img`,
`depth_img`, `ir_img`. `get_img(type, quality)` returns the bytes
instead. Reference: the client README and
`example/jupyters/api_call.ipynb` in the vision repo.

**ROI from a 3D box.** Two forms. The static one: `roi.corners`, a
pixel polygon `[[u, v], ...]` — `detection_box_corners(name, box)`
generates it once (fixed camera / fixed look pose; omit K/D — the
last capture's true intrinsics are used). The live one: put the box
straight into the roi — `roi: {box: [...], offset: 10}` — and the
server projects it to corners PER FRAME using that frame's
`camera_in_world`: a moving camera needs no ROI re-authoring, the box
is bench geometry and pixels are derived. `corners` wins when both
are present. Per-call `detect(roi={"box": ...})` also works (and
becomes the detection's roi from then on). Box format:

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

### Config files — a detection's settings in a file

`config` names a YAML file holding any of a detection's settings, with
the same keys and nesting the detection takes. It works for every
detection type, and in recipes.j2 it sits in `detection_preset` like any
other key:

```yaml
# <project>/vision/tube_od.yaml
detection: {cmd: od, path: tube_autosampler_2ml.pkl, conf: 0.5}   # the model beside it
roi: {corners: [[100, 50], [700, 600]], inv: 0, crop: 1}
display: {label: 1, save_img: false, client_save_img: false}
```
```yaml
detection_preset: {config: vision/tube_od.yaml}
detection_preset: {config: vision/tube_od.yaml, detection: {conf: 0.6}}   # only conf changes
```

- **Inline wins**, key by key at every depth; a list (e.g. `corners`) is
  replaced whole. The merged result comes back as `config` in the
  `detection_add` reply.
- **Any setting may go in the file** — `detection`, `roi`, `display`,
  `limit`, `sort`, ... — or stay inline. Nothing decides that but you.
- **Every relative path in it is relative to the file's own folder**
  (or absolute; `~` expands): `detection.path` (a model), a vlm
  detection's `references[].image` and `key_path`, and the
  `display.client_save_*` folders this computer writes (`true` =
  `output/`, same rule). One model or one set of references can serve
  several configs, and the file means the same thing from whatever
  folder it is added. A config ON the vision unit (`{server: ...}`)
  cannot name a folder on the calling computer relatively — its
  `client_save_*` are absolute or `false`, else the add is refused.
- **Where configs live in a project: `vision/`** — each model beside the
  config that names it, a vlm config's references and key beside it too
  (project-guide §1 "The `vision/` folder").
- **Where it is read**: `config:` in recipes.j2 is relative to
  recipes.j2 (the loader makes it absolute); passed from Python, to the
  folder that program runs in. The vision client reads the file and ships what it
  references (a model's bytes, reference images) with the call;
  `config: {server: /abs/file.yaml}` names a file on the vision unit,
  merged there.
- A `config` that cannot be added (a missing file, a bad key) fails the
  LAUNCH; a detection without one keeps its old behaviour.

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
- **A server-reported error is not a dead link**: an error the server
  sent back (a bad preset, a failed vlm call, a detection that raised)
  proves the socket is alive, so the session stays; only a transport
  failure marks it for re-establishment on the next call.

## 7. Operational notes

- Viewer/GUI socket blips during a Capture are benign: the JPEG encode
  burst can delay a keepalive; the client auto-reconnects within a
  second and the run never pauses (observability never blocks).
- The workspace's `camera_add` at launch is instant when the GUI (or a
  prior launch) already added the camera — pool idempotency.
- Restart order after code changes: camera/vision repo changes need a
  vision-server restart; GUI-only changes need a browser hard-refresh.
- **Files page — the vision machine's captures, from the browser.** The
  server GUI's Files section browses one folder: `~/captures` of the
  user who started the server (SUDO_USER under sudo, not /root),
  created on start; `--captures <folder>` points it elsewhere. Same
  grammar as the workspace file browser: breadcrumbs, a row per entry,
  and a preview pane that opens when a file is clicked (images shown
  fitted, ↑/↓ steps through a folder). Upload takes many files (button
  or drop onto the panel), files download as themselves, folders as a
  zip — streamed, stored not deflated, so a big capture folder never
  sits in memory. Delete takes a file or an EMPTY folder only, and
  everything the server writes is handed back to the invoking user.
  Pointing `display.save_img_roi` at a folder under `~/captures` is
  what makes server-side saves show up here.

## 8. The uEye XS — color + autofocus

The second camera type. Same pool, same MQTT health adapter, same
AutoRecover loop, same honest-fail workflow semantics as the D405 —
`type: "ueye_xs"` in `camera_cfg` is the only authoring difference:

```yaml
inspection_horizontal_1:
  type: "inspection_horizontal"
  simulation: false
  camera_cfg:
    serial_number: "4103698214"
    ip: "10.0.1.40"
    port: 80
    type: "ueye_xs"
```

What differs, stated plainly:

- **Color only.** No depth, no IR — `xyz()` and depth feeds fail
  loudly. 2D detections (label, barcode, blob, OCR, roi crops, saves)
  work as-is.
- **Intrinsics are authored or nominal.** No factory calibration
  exists; author `K`/`D`/`native_res` for metric work (roi.box
  projection). Without them the server reports a NOMINAL pinhole,
  labeled `"nominal"` in `camera_info` and the GUI lightbox.
- **No manual prerequisites**: the normal vision-unit upgrade installs
  everything — `pyueye` from pip, and the IDS runtime (`libueye_api` +
  the `ueyeusbdrc` daemon) from debs vendored in the camera repo
  (`ids/`), version-idempotently. A fresh unit sees the uEye XS after
  one standard upgrade. Without the runtime the camera type simply
  isn't available — enumeration returns nothing and `camera_add` says
  why.

### Focus — the lens is a parameter

The XS has a liquid autofocus lens. Three modes, all runtime-settable
(`camera_focus` over the WS API, or the GUI):

| mode | meaning |
|---|---|
| `{"mode": "continuous"}` | SDK continuous AF (connect default) |
| `{"mode": "once"}` | one-shot AF, then hold |
| `{"mode": "manual", "position": N}` | pin the lens (N in the device range, ~0..255) |

**Region focus** — the GUI workflow: Cameras → expand a frame →
**Focus region** → drag a rectangle → the server finds the sharpest
lens position for that rect and pins it. Under the hood it prefers the
SDK's native AF-AOI (~1-2 s) and falls back to the lens sweep (coarse
→ fine → micro, Laplacian sharpness, ~10-20 s); the **XS Rev 1.1
lacks the AF-AOI capability** (verified), so on it region focus is
always the sweep. The settled position comes back in the toast —
persist it, don't re-sweep in production.

**Exposure & white balance** — the XS ISP owns both, and the platform
reports what it verified on hardware instead of pretending:

- Exposure: **auto only** (`camera_exposure` reads the live ms;
  pinning raises with the explanation). Software brightness lives in
  the detection's `intensity`.
- White balance: `camera_wb(auto=True)` or `camera_wb(hold=True)` —
  hold freezes WB at its current convergence. Bench recipe: let auto
  settle on the lit scene, then hold — deterministic color from then
  on (same philosophy as pinning focus). Fixed kelvin/rgb are
  ISP-rejected on the XS.

**Where focus is authored** (explicit, two levels):

- **Camera-level**: `camera_cfg: {focus: {mode: "manual", position: 164}}`
  — the lens state established at connect.
- **Per-detection**: `focus` in the detection preset (next to `roi`),
  applied BEFORE each capture — different detections on the same camera
  can each pin their own position:

  ```yaml
  detection_preset:
    focus: {mode: "manual", position: 164}
    roi: {...}
  ```

  Per-call `detect(focus={"mode": "manual", "position": 180})` also
  works and becomes the detection's pin from then on (same update
  semantics as roi). A no-change apply is a no-op — pinned focus adds
  ~0 latency to the cycle.

The region SWEEP is a tuning tool (~20 s, moves the lens through its
whole range); the PIN is the production artifact. Sweep once in the
GUI, paste the position into the preset.

**Frame averaging** (`frames_avg`, camera-agnostic) — deterministic
noise reduction at capture: N>1 grabs N color frames, registers each
to the first by sub-pixel translation (phase correlation — absorbs
the servo/PID jitter of a robot holding position) and averages; noise
drops ~√N with no AI enhancement, no invented texture. Frames shifted
more than a few px are dropped (that is real motion, not jitter);
depth/IR stay single-frame. `frames_avg: 1` = off (the default —
single grab, zero cost). Author it like `focus`: in the detection
preset (`frames_avg: 4`) or per call
(`detection_capture(..., frames_avg=4)`, sticky). Cost is grab time
(~N/fps) plus alignment compute — measured on a Pi at XS full res:
~1 s total at N=4 (noise ÷2), ~2.5 s at N=8 (noise ÷2.8). The default
is 1 — averaging never turns itself on; enabling it is always the
user's explicit call. When opting in, N=4 is the sweet spot (N=8 only
where the cycle can afford it). Use it on stationary stations; leave
it off for moving captures.

## 9. VLM detections — a language model as a detection

Status 2026-10-03: BUILT on the vision repo's `pro_vlm` branch and the
workspace's `vlm` branch (neither merged). Tested against a stub backend
and, for the provider module, a faked HTTP layer — no live provider call
yet. This section is the contract; change it here first.

A VLM detection asks a hosted vision-language model about the frame
instead of running a local model. It is a detection like any other —
same `detection_add`, same Inspector, ROI, `save_img`, `client_save_img`,
same result shape — so nothing on the workspace side is new. Its
settings are the `detection` dict, usually kept in a config file (§5
"Config files"):

    detection_preset: {config: vision/cap_check.yaml}

### The settings — `detection: {cmd: vlm, ...}`

```yaml
# <project>/vision/cap_check.yaml   (references beside it, e.g. refs/)
detection:
  cmd: vlm
  model:
    name: flash-3.8                   # the backend's model name — pinned, never "latest" (table below)
    backend: default                  # which server module answers (see "Backends")
    thinking: low                     # minimal | low | medium | high — how much it reasons; the backend maps it
  key: ""                             # the key itself — wins when not empty (quick tests only)
  key_path: vlm.key                   # else: a file holding just the key — relative to this folder, or absolute
  output: cls                         # cls | od | ocr — the same names as the other detections
  labels: [pass, crooked, missing]    # the allowed answers (cls: the verdict, od: the object kinds); [] = the model's own words
  schema: {}                          # extra data on every entry, as a JSON schema; {} = none
  prompt: |
    You inspect a 40 ml amber vial cap from above...
  references:
    - {image: refs/ok_1.jpg,    label: pass,    note: "seated, thread hidden"}
    - {image: refs/crooked.jpg, label: crooked, note: "tilted, gap on the left"}
  image_size: 768                     # long side of every image sent, px — optional: absent = frames sent as captured
  timeout_s: 20
display:                            # (any detection setting may sit in the config)
  label: 1
  client_save_log: false            # each call's views + answer, see "Logging"
```

Every field is written out (the explicit-values rule) — except
`image_size`, the one that may be left out: absent, every image goes as
captured, no resize (a number only shrinks, never enlarges). Written
inline or in a config, merged by the config rule. The format is OURS, not a
provider's: the server builds each request from it, so the settings
never change when the provider's API does. Three fields shape the
answer, and they are the contract for the MACHINE, not instructions for
the model (those are the prompt): `output` says what kind of result
comes back, `labels` is the vocabulary the model must pick from (an enum
in the request schema — it cannot write "Capped" or "the cap is on";
`[]` lets it answer in a few words of its own), and `schema` is the form
for any extra facts you want — JSON Schema, the open standard every
provider accepts for structured output:

```yaml
schema:
  type: object
  properties:
    tubes:  {type: integer}
    full:   {type: boolean}
    color:  {type: string, enum: [clear, yellow, red]}
```

filled in as `data: {tubes: 7, full: false, color: yellow}` on every
entry, typed — no parsing on your side. The provider enforces the shape
while generating; the server checks the answer again (label in your
list, box in a view) before making entries. What is NOT enforced is
truth — whether 7 is right is the model's judgment, which the prompt,
references and `thinking` are for. The reference images travel
with the add; the server keeps the parsed settings in memory keyed by a
hash of their contents — an unchanged set is never re-processed. Edits
take effect when the detection is added again (relaunch, or re-running
the cell).

### The key — `key`, or the file `key_path` names

```
# <project>/vision/vlm.key          (plain text: just the key — *.key is git-ignored)
AIza...
```

**Where the key lives: beside the config, as `<name>.key`.** The
config's `key_path: vlm.key` names it — no separate folder, no path to
walk. Each repo's `.gitignore` carries `*.key`, so a key is ignored
wherever it sits; add the line BEFORE the file exists, and
`git check-ignore -v vision/vlm.key` must name the rule. Several configs
in one folder share one `vlm.key`. Create it once:

    echo 'PASTE-YOUR-KEY' > vision/vlm.key && chmod 600 vision/vlm.key

`vision/` is never a launch.yaml `folders:` entry; only declared folders
are shown, so the file browser cannot list or serve the key.

Both fields are written out. `key`, when not empty, is the key itself and
wins — for a quick test only: a config gets committed, and a key in it
is one commit from a public repo. Otherwise `key_path` names a
plain-text file holding just the key (surrounding whitespace ignored),
relative to the config file's folder or absolute (`~` expands). It is
read where the config is: by the vision client for a config on the
calling computer (the usual case — a notebook, a project), by the vision
unit for a `{server: ...}` config. Neither given, a missing file or an
empty one fails the add — never a call without a key.

The key travels to the server as its own argument of `detection_add`,
NEVER inside the detection dict (that dict is reported, printed and
kept for reconnect replays; `key` and `key_path` are taken out of it
first). The server holds it privately for the session: never
written to the vision unit's disk, never logged, never echoed in a reply;
the generic proxy refuses private names, so it cannot be read back. It crosses the LAN once per session on the detection's WebSocket —
acceptable on a closed bench network. Only the vision unit needs
internet.

### Asking — the four shapes a question takes

| You want | Write | You get, per entry |
|---|---|---|
| a verdict from a fixed set | `output: cls`, `labels: [pass, fail]` | `cls` = one of them |
| an answer to a general question | `output: cls`, `labels: []`, the question in `prompt` | `cls` = the model's words ("deep metallic blue"), `reason` |
| facts, typed | any of the above + `schema` | `data: {tubes: 7, full: false}` |
| every object marked | `output: od`, `labels: [capped, uncapped]` (or `[]`: the model names what it finds) | one entry per object: `cls`, `corners`, `view` |
| the text in the picture | `output: ocr`, `labels: []` | one entry per line: `cls` = the text, `corners` |

`cls` always judges the PART (one entry); `od` / `ocr` report what they
find (one entry each). A free answer and a verdict are the same mode —
labels given or not. Two configs in the vision repo show it:
`example/vlm/cap_check.yaml` (`cls`, one reference) and
`example/vlm/mark_caps.yaml` (`od`, no references, `image_size: 1024` —
boxes need pixels, `client_save_img: true` to see them drawn).

### Calls

```python
det.run()                                   # the live frame: capture -> ROI crop -> one request
det.run(data=img)                           # one image from the calling computer, as any detection
jpeg, meta = vc.camera_get_img(sn, type="color_img", quality=100)  # a frame and nothing else — no detection runs
jpeg2, _  = vc.camera_get_img(sn, type="color_img", quality=100)  # ... the arm moved in between
det.run(data=[jpeg, jpeg2])                 # several images = several views of ONE part, one request

# per call, on any of the above:
det.run(references=[{"image": ..., "label": "pass", "note": "..."}],
        prompt="Judge the cap: seated flat?")  # the config's keys, for THIS call: a key given REPLACES the config's
```

An image (`data`, a `data` list entry, a `references` `image`) is one
of: a path on the calling computer, encoded bytes, an OpenCV array, or
`{"server": path}`. A list is for vlm detections only — every other
detection runs one frame and refuses a list. A list is judged AS GIVEN:
the config's `roi` applies to the frame a plain `run()` captures, not to
images you hand over (crop them yourself if the part is small).
A run waits for the detection's own `timeout_s` (the model's budget) plus
the client's ordinary call timeout — set `timeout_s` in the config, and
nothing else, when a model needs longer.
In a recipe the same two steps are `inspector.frame()` (a frame, no
detection; `None` in sim) and `inspector.detect(data=[f1, f2, ...])`:
move, take a frame, move, take a frame, judge once. Nothing is kept on
the unit between calls — every request is exactly the images you passed.

### The request (built by the backend from the settings)

```
prompt                                            ┐ the config's — or this call's, when
"Reference 1 — pass: seated, thread hidden"      + image │ run() gave prompt= / references=
"Reference 2 — crooked: tilted, gap on the left" + image ┘ (the config rule: a key given wins)
                         ─── the prefix: calls on the config's share it, cached ───
"Part to judge — view 1 of N" + image   ...   "view N of N" + image
"All views show ONE part."  + the answer schema for `output`
```

### The result — the shape every detection returns

| output | entries |
|---|---|
| `cls` | one: `cls` = the label (or the model's words when `labels: []`), `conf`, `corners` = the ROI, `reason`, `data` |
| `od` | one per object: `cls`, `conf`, `corners`, `center`, `view` (boxes mapped back to that view's pixels), `reason`, `data` |
| `ocr` | one per text line: `cls` = the text, `conf`, `corners`, `view`, `reason`, `data` |

`cls` judges the PART across all views (a defect in one view fails it);
`od` / `ocr` report per view. `data` is the `schema` filled in (`{}` when
the schema is `{}`) and is never drawn — the frame shows `cls` + `conf`
like any detection. `conf` is the model's self-reported confidence, not a
calibrated probability. `od` boxes are approximate — "is it there",
never robot coordinates. Each result also carries
`vlm: {model, backend, latency_ms, tokens_in, tokens_cached, tokens_out}`.

**No usable answer = an empty result.** When the model gives nothing
usable — provider down, timed out, the answer cut off at the output cap
(the model was still listing), or a label outside yours — `run()` returns
`[]`, exactly what every detection returns for a frame with nothing in
it. Nothing is guessed, and nothing is hidden: the reason is printed in
the vision server's log (`[vlm] <name>: no usable answer — ...`) and
written to the client log's `error` field with `entries: []`. The action
decides what an empty result means, the way it does for any detection.
Misconfiguration is different and fails the ADD, not a run: no key, a
bad config, an unknown model name or backend.

### Backends — the provider is one module

Everything provider-specific lives in ONE server module per provider,
behind one interface; nothing else in the server, the client, the
workspace or a config knows which provider answered:

```
dorna_vision/vlm/
  backend.py     the contract: answer(preset, views, extra, key) -> {entries, reason, usage}
  default.py     the first backend (today: Google's Flash model)
```

A backend owns: building the request from the settings, the provider's
structured-output schema, mapping `thinking`, its box convention, its
caching, the HTTP call and its errors. Trying another provider = one new
module + `model: {name, backend}` in a config — every project, call and
result stays as it is.

`model.backend: default` knows these `model.name`s (an unknown one is
refused, naming them): `flash-3.8` (the current Flash), `flash-3.7`, `flash-3.6`,
`flash-lite-3.5` (cheapest, fastest), `pro-3.1` (preview, slowest). The
neutral answer every backend returns, and its mapping to entries, are in
`dorna_vision/vlm/backend.py`.

### Speed

- The stable prefix (prompt + references) leads every request, so the
  provider's prefix cache can hit; each result reports `tokens_cached` to
  show whether it does. (Today's backend caches implicitly from a
  4,096-token prefix — about 16 references at 384 px or 4 at 768.)
- `model.thinking: low`, views at `image_size`, one warm connection per server,
  settings decoded once.

### Logging — the way to a local model

`display.client_save_log` saves every call on the computer that added
the detection, with the `client_save_img` values: `false` (the default)
= off, `true` = the folder `output/`, a path = exactly that folder —
relative to the config file when written there, to the folder the
program runs in when written in Python (the one path rule). Each call writes, named by its timestamp in
MILLISECONDS: `<ms>_view<n>.jpg` (the views as sent), `<ms>_ref<n>.jpg`
(per-call extra references) and `<ms>.json` (model, usage, the neutral
answer, the entries — never the key). The files travel the client-save
push; a Detection with no server behind it writes them itself. A few
hundred judged parts is a labelled dataset for training the local
classifier — the VLM for development and odd cases, the local model for
the line.

### Open

- A first live call with a real `vlm_key`, and a bench timing.
- Merging `pro_vlm` into `pro`.
