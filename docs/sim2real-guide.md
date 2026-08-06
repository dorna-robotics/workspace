# sim2real — scene → labeled depth datasets for verification vision

**Status: design finalized, implementation pending.** This document is the
single source of truth for the design; it is expected to evolve as the
implementation lands. Module target: `workspace/sim2real/`. Nothing else in
the current structure changes without explicit agreement.

## 1. What this is

A platform capability that renders **labeled depth-image datasets out of the
live workspace scene**, for training verification classifiers ("is the cap
on?", "is the tube present?") that ship to customer sites **without any real
training data and without per-client retraining**.

The primary consumer is step verification (a camera check after robot
actions), but the capability itself is general: *create images out of the
scene*. Hence the neutral name — datasets, not verification.

Why depth, why sim-only (from the original handoff, still valid):

- **Depth over RGB** — RealSense depth is physically measured geometry; the
  sim-to-real gap in the depth channel is near zero. RGB drags lighting,
  materials and background across the gap.
- **Classification over anomaly detection / segmentation** — verification
  failures are known, enumerable states; a small classifier is faster
  (~30–50 ms int8 CPU) and more reliable for known-state checks.
- **Sim-only training** — the workspace scene *is* the digital twin: same
  CAD (GLBs), same poses (kinematic tree), same camera placement as the
  deployed bench. Real captures are used for **evaluation only** (~30 per
  class). If the eval gap is large, add randomization — never real
  training data.

## 2. Ownership split (who runs what)

| Stage | Where | Why |
|---|---|---|
| Image generation | **workspace** (`workspace/sim2real/`) | The scene, kinematic tree, CAD and states are workspace-native; making vision import workspace would invert the dependency direction. |
| Training | **Colab** (dorna_vision `training_notebooks/`) | Consumes the dataset folder, produces the pickle. T4, 30–60 min. |
| Inference | **vision computer** (dorna_vision `CLS` / `Detection(cmd="cls")`) | Consumes the pickle. **Zero changes to inference modules.** |

Contracts between stages are deliberately dumb: workspace ships an
**ImageFolder dataset** (class-named folders of PNGs) plus a `meta.json`
stamp; Colab ships a **pickle in the existing dorna_vision schema**
(`bin, xml, cls, colors, meta`). Each stage is replaceable without touching
the others.

Dependencies: `pyrender` + `trimesh` (both MIT) as an **optional extra** —
imported only by `sim2real/`, never by the runtime platform. BlenderProc is
not used (depth needs no PBR lighting; a z-buffer is the whole job — and it
keeps GPL tooling out of the pipeline).

## 3. The frozen encoding contract (already shipped)

Lives in the **camera module** (`camera/camera.py`), not in any config:

```
depth_mm × depth_alpha → 8-bit (capped 255) → COLORMAP_TURBO → 3-channel BGR
```

- `Camera.depth_alpha = 0.5` — instance attribute, **runtime-settable**
  (`cam.depth_alpha = x` applies on the next `get_all()`), per-call
  override still available. 0.5 spans 0–510 mm at ~2 mm/step — sized so a
  10 mm cap is ~5 quantization steps at D405 working range.
- The scale is **fixed, never per-frame dynamic**: dynamic ranging leaks the
  label into the normalization (the content changes the min/max) and can
  never be reproduced identically in sim.
- Pure scale, **no offset**: `convertScaleAbs` takes an absolute value, so an
  offset would remap z=0 dropouts onto valid depths. With pure scale,
  invalid pixels stay exactly 0 — their own distinctive color the model
  learns as "sensor said nothing".
- Saturation beyond 51 cm is a feature: any far background (customer bench
  clutter) collapses to one uniform color — built-in background
  suppression.
- **TURBO, not JET** (committed): same rainbow family pretrained backbones
  transfer well to, but perceptually smooth — no false banding when humans
  compare sim vs real side by side. Colormap choice is part of the frozen
  contract; it was changed before any model was trained and must not change
  after.
- Because the output is 3-channel, the existing 3-channel `CLS`
  (`[1,3,H,W]`, letterbox, ImageNet norm) runs it **unchanged** — the
  ImageNet normalization is just a fixed linear transform the network
  learns through.
- **Filter alignment rule:** runtime captures RAW depth (no RealSense
  spatial/temporal/hole-filling post-processing) and sim renders raw-noised
  depth. Hole-filling would destroy exactly the dropout structure the model
  must tolerate.

Training images, real eval captures and runtime inference all go through
this identical transform. The generator additionally archives raw 16-bit mm
PNGs beside the encoded ones (re-encodable without re-rendering).

## 4. Rendering: pose-snapshot isolation

`snapshot()` never touches live state:

1. One consistent **pose snapshot** of the kinematic tree (component →
   world pose + mesh reference) into a private list.
2. All mutations (`remove` filters entries) and all jitters (perturb poses)
   operate on the **copy** only.
3. PyRender renders n variants from the copy through a pinhole camera (K
   only; D is applied in ROI projection); the copy is discarded.

No writes to the running program's facts, attachments or display; nothing
to restore; no races with workflow/display/planner threads. `remove` takes
the component **and its attached subtree** (a tube takes its cap) — anything
else renders physical nonsense.

## 5. Noise model (~25 lines, in the engine, named `d405`)

Applied to **metric depth arrays** (mm), never to encoded images; PNG is
just storage.

1. Distance-scaled gaussian — Intel's quadratic model, σ_z ≈ 0.0129·z².
2. **Structured edge dropouts** — stereo fails at depth discontinuities;
   zero pixels near depth-gradient edges with probability, plus a few random
   blobs. (Uniform salt-and-pepper teaches the wrong failure geometry —
   caps/tubes are edge-heavy.)
3. **Edge fattening** — small dilation/blur of foreground silhouettes,
   mimicking stereo block-matching smear.
4. Range clip 7–50 cm → 0.
5. The noise **magnitude itself is randomized 0.5–2×** per render (covers
   unit variation).

Do not build a physically-accurate sensor sim: structural signal-to-noise
for these checks is 5–10×; rough is enough.

## 6. `datasets.j2` — the per-project config

Lives next to `recipes.j2`. It is Jinja: loops for many slots/stations are
authored the same way scene layouts are.

```yaml
decap_check:
  cmd: cls                    # LABEL family — dorna_vision's detection cmd
  modality: depth             # IMAGE channel — selects renderer + encoding
  out: dataset/decap_check    # images land in out/<cls>/…
  n_per_class: 10             # per class per snapshot() call
  max_per_class: 1000         # folder cap across runs; further calls no-op

  # Taxonomy AND counterfactuals. Key = folder name = pickle "cls" entry =
  # runtime verdict string; declaration order fixes the class index order.
  # Value = how to DERIVE the class from the reference state ({} = the
  # reference, as-is). A LIST of states is a union — variants split the
  # class budget. {placeholders} are filled from snapshot() kwargs.
  cls:
    cap_on:  {}
    cap_off: {remove: ["{cap}"]}
    missing: {remove: ["{tube}"]}     # subtree: the cap goes with the tube

  # Lens pose in the WORLD frame, xyzabc — read off the camera component's
  # "lens" anchor (renamed from "camera"; committed).
  lens_in_world: [110.3, 9.0, 372.8, -69.28, 69.28, -69.28]

  # Explicit pinhole intrinsics. K/D plug straight into dorna_vision's
  # box_to_corners(K=, D=) — the SAME projection the runtime uses.
  # Nominal (factory/datasheet) values; unit spread is covered by
  # randomize.intrinsics_jitter. D stays zeros for D405-class sensors.
  intrinsics:
    width:  848
    height: 480
    K: [[448.0,   0.0, 424.0],
        [  0.0, 433.0, 240.0],
        [  0.0,   0.0,   1.0]]
    D: [0.0, 0.0, 0.0, 0.0, 0.0]

  # ROI = dorna_vision's 3D box split into frame + extents:
  #   anchor_in_world: xyzabc of the box frame (bottom-plane center)
  #   whd:             extents mm; h rises from the bottom plane
  # Margin lives HERE in millimeters — size for target + jitter range,
  # under the rack pitch — so offset stays 0 and crops never contain
  # neighbors or customer background (scene-agnostic by construction;
  # neighbor states vary independently and would train spurious
  # correlations).
  roi:
    anchor_in_world: [352.5, 137.5, 18.0, 0.0, 0.0, 0.0]
    whd: [40.0, 40.0, 130.0]
    offset: 0                 # ROI pixel padding (dorna_vision's knob)

  # ± uniform per render: [x, y, z] mm + [a, b, c] deg.
  randomize:
    object_jitter: [2.0, 2.0, 1.0, 3.0, 3.0, 10.0]
    camera_jitter: [3.0, 3.0, 3.0, 1.0, 1.0, 1.0]
    intrinsics_jitter: {focal: 0.02, pp: 4.0}   # ±2% fx/fy, ±4 px ppx/ppy
    noise: d405
```

Two orthogonal axes describe every entry, declared separately because
they combine freely (a `cls` model could someday train on RGB; an `od`
model on depth):

- **`cmd` — the label family / writer.** `cls` → class folders
  (ImageFolder). A future `cmd: od` carries an objects payload and emits
  box annotations — auto-labeled from PyRender's per-object masks.
- **`modality` — the image channel / renderer + encoder.** `depth` (the
  only supported value today) binds the entry to the frozen encoding
  contract (§3), the z-buffer renderer (§4) and the `d405` noise stack
  (§5). A future `rgb` would carry its own renderer (PBR — a different
  beast) and its own contract without touching the label machinery;
  `mask` / `ir` / `pointcloud` likewise.

Unknown `cmd` or `modality`, or a family missing its payload, fails at
**load**, not mid-generation.

## 7. The API

One method. Called from inside actions (states are already coherent there —
the running program is the labeler's context).

```python
# In an action's execute(), at the REFERENCE state (the {} class — e.g.
# cap present). One call renders EVERY class: reference as-is, the rest
# derived from the pose-snapshot copy via their mutations.
slot = ws.components[RACK].slot["body"][tube]
pose = ws.components[RACK].assembly["body"].pose(anchor=slot)
ws.sim2real.snapshot("decap_check",
                     anchor_in_world=list(pose),
                     cap=f"cap_falcon_{slot}",
                     tube=f"tube_falcon_{slot}")
```

Rules:

- **call > j2** — every key in the entry is overridable by keyword
  (`n_per_class`, `out`, `anchor_in_world`, …). No special-cased kwargs.
- **`{placeholders}`** anywhere in the entry's strings are filled from call
  kwargs. A placeholder left unfilled (when mutations run) is an immediate
  error — never a silent literal.
- Component names in mutations are plain instance-name strings, validated
  against the scene at call time.
- "Resolve from the scene" is ordinary user code (`assembly[...].pose(anchor=…)`)
  — the engine stays a dumb primitive; composition happens above it.
- Counts: images per call = `n_per_class × len(cls)`; unions split their
  class budget across variants; folders always come out balanced.
  Rule of thumb `n ≈ target ÷ batch_size`, or just rely on `max_per_class`
  (collection saturates; a big batch with small n beats a small batch with
  big n at equal totals — slot/pose diversity).
- Files append across runs (run-id + index in filenames), never overwrite.
- Jitter semantics: **the declared frame never moves** — the ROI box and
  the projection stay exactly as configured (that is how the runtime
  computes its crop, from nominal knowledge); objects and the *rendering*
  camera jitter underneath. Crops therefore show off-center/tilted targets
  — the runtime's true distribution. A box that tracked the object would
  train perfectly-centered crops that never occur (and "centered-ness"
  would leak the label — `missing` has nothing to center on).

## 8. Initialization & gating

```yaml
# launch.yaml
datasets: datasets.j2        # presence initializes ws.sim2real at boot

default:
  collect:
    type: bool
    default: false
    label: Collect training images
    hint: Snapshot calls render datasets during this run (sim).
```

- The launcher wires it like recipes/checks: loads + validates
  `datasets.j2` at boot (bad entries fail at launch). Projects without the
  key get a **stub** whose `snapshot()` is a silent no-op — snapshot calls
  are portable across projects.
- Per run: `ws.sim2real.enabled = bool(kwargs.get("collect", False))`. The
  calls live in `actions.py` permanently (like `rt.step`); the operator's
  `collect` checkbox is the only switch. Disabled → every call returns
  instantly, zero cost.
- Guard: `collect=true` on a non-simulation core → loud warning, stays
  disabled. Never mix live-bench runs with sim-rendered "training data".
- `ws.sim2real.enabled` stays assignable programmatically.

Typical dataset run: `collect=true`, `batch_size` large, motion-off sim —
one Start fills every folder to its cap in a single pass.

## 9. The label chain (why nothing can drift)

The class name is written **once** — as a key in `datasets.j2` — and every
other appearance is derived:

```
datasets.j2 cls key ──► dataset/<cls>/ folder ──► pickle["cls"] (order = declaration order)
        ──► CLS verdict string at inference ──► checks.py comparison
```

No enum in Python, no second list in the notebook, no mapping table on the
vision side. Renaming/adding a class = edit yaml → regenerate → retrain.
Unknown class names anywhere are validation errors, not new folders.

## 10. Training & evaluation

- Dataset feeds dorna_vision's existing `train_classification.ipynb`
  essentially as-is: TURBO images are 3-channel, ImageFolder layout
  matches, letterbox + export + int8 + pickle packaging unchanged.
  Backbone per handoff: `mobilenetv4_conv_small` (timm), ~5–10 MB int8,
  30–50 ms CPU.
- `meta.json` written next to the dataset stamps the contract:
  `depth_alpha`, colormap, intrinsics, ROI, class order, scene hash — the
  notebook copies it into the pickle's `meta`, so a model carries its own
  provenance.
- Evaluation: ~30 REAL D405 captures per class (raw depth → same encode),
  captured at the bench; never trained on. Target ≥98% real accuracy from
  sim-only training; if short — widen randomization, don't add real data.

## 11. Deferred (explicitly out of scope today)

- **Runtime check design** — `conf` gate, multi-frame consensus,
  expected-class assertion from actions, `Detection(cmd="cls")` wiring,
  fail-safe halt semantics. The j2 gains runtime keys when this lands.
  Agreed convention to implement with it: dorna_vision's `frame`
  parameter (today: frame-in-lens, inversion-prone) is DELETED and
  replaced by a single `base_in_world` — the root of the camera's
  transform chain expressed in the world (robot-mounted: robot base in
  world; fixed camera: lens in world). Old `frame` configs then fail
  loudly instead of silently inverting; detections come out
  world-frame, matching the workspace.
- **`source: real` capture mode** — same `snapshot()` API grabbing real
  D405 frames (labeled via `cls=`, since reality can't be mutated) to build
  eval sets on the bench. The API seat is reserved.
- **`cmd: od` family** — object-detection datasets with auto-labeled boxes
  from render masks.
- **Mutation verbs `add` / `offset` / `joint`** — foreign-object,
  misaligned/tilted, articulated-device states. `remove` covers presence
  checks (the three shipped stations).
- Randomize numbers above are handoff rules-of-thumb (2–3× expected real
  variance) pending Phase-0 captures.

## 12. Implementation roadmap

1. **Engine MVP** (`workspace/sim2real/`): j2 loader + validation, pose
   snapshot, `remove` mutation (subtree), PyRender depth render, encode
   (contract), ImageFolder + meta.json writer, `snapshot()` with call
   overrides + placeholders. No randomization. Smoke test: 3 clean images,
   one per class, from a sim run of an example project.
2. **Phase-0 reality check** (bench, ~10 min): ~30 raw D405 depth captures
   of one station, cap on/off → permanent eval set + side-by-side gate
   against MVP renders. Go/no-go before investing further.
3. **Randomization + noise**: jitters, intrinsics jitter, `d405` noise
   stack, magnitude randomization.
4. **Launcher wiring**: `datasets:` key, `collect` kwarg, stub, sim guard.
5. **Colab**: run the existing classification notebook on a generated
   dataset; int8 pickle; evaluate against Phase-0 captures.
6. **Runtime check chapter** (separate design pass — §11).

## 13. Decision log (chronological, with the why)

| Decision | Why |
|---|---|
| Depth-only, classification, sim-only | Handoff rationale; depth kills the sim-to-real gap, classes are known states. |
| PyRender primary, BlenderProc dropped | Depth = geometry + intrinsics; z-buffer is the whole job; MIT-only stack. |
| Generation in workspace, training Colab, inference vision | Dependency direction; scene is workspace-native; inference untouched. |
| All sim2real code in `workspace/sim2real/` | No changes to current structure without agreement. |
| Encoding contract in camera module: `depth_alpha=0.5` (runtime-settable) + TURBO | One transform for sim/capture/runtime; 2 mm/step at D405 range; dropouts keep color 0; TURBO ≥ JET for humans, equal for CNNs. Committed: camera repo `0f08cf2`, `cbe39d0`. |
| Fixed scale, never per-frame dynamic | Dynamic ranging leaks the label into normalization and can't be reproduced in sim. |
| 3-channel TURBO instead of 1-channel CLS fork | Existing `CLS` runs unchanged; prove pipeline first, fork only if eval demands. |
| ROI = dorna_vision 3D box (`anchor_in_world` + `whd` + `offset`) | `box_to_corners(K=,D=)` is the shared projection — sim and runtime crop with the same function. Frame/extents split: pose varies per slot, extents don't. |
| Tight ROI, `offset: 0`, margin in mm in `whd` | Neighbor states vary independently → spurious correlations; tight crop = scene-agnostic by construction. |
| `lens_in_world` + explicit `intrinsics` (K/D) in j2 | Explicit-values house rule; no component coupling; K/D is the vision repo's own vocabulary. Camera components' optical anchor renamed `camera`→`lens` (committed) as the place to read the pose from. |
| K/D mismatch tolerated via `intrinsics_jitter` | Unit spread ±1–2% is dwarfed by pose jitter; runtime projects with its own calibrated K anyway. |
| In-flow generation (`snapshot()` inside actions), not offline generator | The running program owns coherent states; real project scene = digital twin; states like "cap physically in holder" come free. |
| Pose-snapshot isolation (copy, not hide/show) | Zero writes to live state; no races; nothing to restore. |
| `remove` takes the attached subtree | A cap floating where its tube was is physical nonsense. |
| One call at reference state renders ALL classes | Simpler mental model; with tight ROI, mutation-derived and flow-visited crops are identical, so a flow-labeled mode adds nothing (returns only for `source: real`). |
| `cls` map = taxonomy + counterfactuals; key order = class index order | Validation (typos are errors), stable softmax order, completeness accounting for `max_per_class`. |
| `{placeholders}` filled from call kwargs; call > j2 for every key | One uniform override mechanism; slot-dependent names/poses bound by the action that knows them. |
| `cmd: cls` family tag in each entry | Future `od`/`kp` datasets reuse the engine with different payloads/writers; mirrors `Detection(cmd=…)`. |
| `modality: depth` tag in each entry | Orthogonal to `cmd`: the image channel (renderer + encoding contract). Reserves the seat for `rgb`/`mask`/`ir` without entangling label families. |
| `n_per_class`/`max_per_class`/`out` in j2, call-overridable | Defaults live with the dataset; `max_per_class` makes over-collection structurally impossible. |
| Init via `launch.yaml datasets:` + `collect` kwarg gate | Platform idiom (declare → launcher wires → operator toggles); snapshot calls permanent in code, no-op when off; sim-only guard. |
