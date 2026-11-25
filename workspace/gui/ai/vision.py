"""The assistant's eyes on the bench: the project's cameras, through the
vision server, on a PRIVATE session (gui/devmode/camera.py) — never a
running project's VisionStation, whose failures fault the server.

    camera_view            a frame + objective stats (exposure, clipping, cast, sharpness)
    roi_preview            an ROI outlined on the frame + its crop (corners, a 3D box, or a config's)
    camera_settings        read; or set exposure / white balance / focus (approval)
    detection_test         run a project's vision/<name>.yaml once, under a private name
    update_detection_config  write keys (roi, intensity, detection.conf …) into vision/<name>.yaml

Every tool names the camera by its scene component; in simulation (or not
wired) the answer is the reason there is no picture, never an image.
"""
import copy
import json
import os
import time
import uuid

from gui.devmode import vision_cfg

from . import images
from .images import Shot
from .tools import ToolError, _inside, _name, _scene, project_dir, _tool, _P

SETTLE_S = 1.2           # after an exposure / WB change, before the "after" frame
GRAB_QUALITY = 90

_CAM = {"type": "string", "description": "The camera's component name in the scene (e.g. 'inspection_horizontal_1'). "
                                         "Optional when the scene has one camera."}
_GRID = {"type": "boolean", "description": "Draw a pixel ruler labelled in FRAME pixels — use it to read ROI coordinates."}
_POINTS = {"type": "array", "items": {"type": "array", "items": {"type": "number"}},
           "description": "The ROI polygon in FRAME pixels: [[x, y], …] (3+ points), or 2 points = a rectangle's opposite corners."}

TOOLS = [
    _tool("camera_view", "Look through one of the project's cameras: the live frame (shown to you AND the user) "
          "plus measured stats — luma mean / percentiles, % clipped highlights and shadows, RGB means and the color "
          "cast they imply, sharpness (Laplacian variance). crop = [x0, y0, x1, y1] in frame pixels also shows that "
          "region enlarged with its own stats. No approval needed; nothing on the camera changes.",
          {"project": _P, "camera": _CAM,
           "crop": {"type": "array", "items": {"type": "number"}, "description": "[x0, y0, x1, y1] in frame pixels."},
           "grid": _GRID,
           "caption": {"type": "string", "description": "What the user should look at (shown under the picture)."}}),
    _tool("roi_preview", "Preview a region of interest on the live frame: the ROI outlined (outside dimmed) and the "
          "crop the detection would see, both shown to the user, with the crop's stats and the roi block to paste. "
          "Give corners (frame pixels), or box (a 3D box [x,y,z, a,b,c, w,d,h], vision-guide §5 'ROI from a 3D box': "
          "the vision server projects it), or detection alone to preview that config's current roi. Nothing is "
          "saved — after the user confirms it, save it with update_detection_config.",
          {"project": _P, "camera": _CAM, "corners": _POINTS,
           "box": {"type": "array", "items": {"type": "number"}, "description": "[x, y, z, a, b, c, w, d, h]"},
           "detection": {"type": "string", "description": "A config in vision/ (e.g. 'cap_40ml') — its roi when "
                                                          "no corners/box are given."},
           "inv": {"type": "boolean", "description": "An inverted ROI (everything but the polygon)."},
           "grid": _GRID}),
    _tool("camera_settings", "Read a camera's settings (camera info, exposure, white balance, focus) — or change "
          "them: exposure_ms (pins exposure; the uEye XS allows auto only), auto_exposure, white_balance "
          "('auto' or 'hold' — hold freezes the current auto convergence; fixed kelvin is not possible on the "
          "XS), focus ({mode: continuous|once|manual, position} or {region: [x0,y0,x1,y1]}). A change ASKS THE "
          "USER to approve it in the chat first, then returns the frame stats before and after and the new frame.",
          {"project": _P, "camera": _CAM,
           "exposure_ms": {"type": "number"},
           "auto_exposure": {"type": "boolean"},
           "white_balance": {"type": "string", "enum": ["auto", "hold"]},
           "focus": {"type": "object", "description": "{mode: 'continuous'|'once'|'manual', position: N} or {region: [x0,y0,x1,y1]}"},
           "reason": {"type": "string", "description": "Why — shown to the user on the approval card."}}),
    _tool("detection_test", "Test a detection: register the project's vision/<detection>.yaml on the live camera "
          "under a PRIVATE name (never the running project's own), capture, run it once, return the results and "
          "the annotated picture (shown to the user), then remove it. `set` overrides keys of the config for this "
          "test only (e.g. {roi: {...}, detection: {conf: 0.4}}) — nothing is saved. The config's focus pin is "
          "applied only with apply_focus (that moves the lens: the user approves it).",
          {"project": _P, "camera": _CAM,
           "detection": {"type": "string", "description": "The config: 'cap_40ml' or 'vision/cap_40ml.yaml'."},
           "set": {"type": "object", "description": "Keys merged over the config for this test (inline wins)."},
           "apply_focus": {"type": "boolean"}},
          ["detection"]),
    _tool("update_detection_config", "Write settings into a detection config (vision/<detection>.yaml): `set` is "
          "merged key by key (a list such as roi.corners is replaced whole; null removes a key). Top-level "
          "sections you don't touch keep their text and comments. Do it only once the user agreed (e.g. after "
          "roi_preview). The project uses it from its next launch.",
          {"project": _P, "detection": {"type": "string"}, "set": {"type": "object"}}, ["detection", "set"]),
]

LABELS = {
    "camera_view": lambda a: f"Looking through {a.get('camera') or 'the camera'}",
    "roi_preview": lambda a: "Previewing the ROI",
    "camera_settings": lambda a: ("Changing the camera settings" if _changes(a) else "Reading the camera settings"),
    "detection_test": lambda a: f"Testing the detection {a.get('detection', '')}",
    "update_detection_config": lambda a: f"Saving {vision_cfg.cfg_rel(a.get('detection', ''))}",
}


# ── helpers ──────────────────────────────────────────────────────────

def _cameras(scene):
    return [n for n, c in scene.items() if isinstance(c, dict) and isinstance(c.get("camera_cfg"), dict)
            and c.get("has_camera") is not False]


def _resolve(session, a):
    """(project, camera name, (host, port, serial, cam_cfg)) — or a ToolError
    with the reason there is no picture (simulation, not wired)."""
    from gui.devmode import camera
    name = _name(session, a)
    scene = _scene(name)
    cams = _cameras(scene)
    comp = (a.get("camera") or "").strip()
    if not comp:
        if len(cams) != 1:
            raise ToolError(f"name the camera: the scene's cameras are {', '.join(cams) or 'none'}")
        comp = cams[0]
    if comp not in scene:
        raise ToolError(f"no component {comp!r} in the scene — its cameras: {', '.join(cams) or 'none'}")
    try:
        return name, comp, camera.resolve(scene, comp)
    except camera.NoImage as e:
        raise ToolError(f"NO CAMERA IMAGE for {comp}: {e}. There is no picture to look at — say so; don't "
                        f"describe one.")


def _grab(where):
    from gui.devmode import camera
    try:
        jpg = camera.grab(*where, quality=GRAB_QUALITY)
    except Exception as e:
        raise ToolError(f"the camera did not answer: {type(e).__name__}: {e}")
    try:
        return images.decode(jpg)
    except Exception as e:
        raise ToolError(f"the vision server sent something that is not a picture ({type(e).__name__})")


def _call(where, fn):
    from gui.devmode import camera
    try:
        return camera.call(*where, fn)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"the vision server refused: {type(e).__name__}: {e}")


def _scale_note(im):
    w, h = im.size
    k = min(1.0, images.MAX_SIDE / max(w, h))
    if k >= 1:
        return f"frame {w}x{h} px (shown at full size)"
    return (f"frame {w}x{h} px, shown at {round(w * k)}x{round(h * k)} (x{k:.3f}) — coordinates are FRAME "
            f"pixels: a point read off the shown picture is x/{k:.3f}; grid=true labels frame pixels")


def _box(v, im):
    if not (isinstance(v, list) and len(v) == 4 and all(isinstance(x, (int, float)) for x in v)):
        raise ToolError("crop: [x0, y0, x1, y1] in frame pixels")
    w, h = im.size
    x0, y0, x1, y1 = (int(round(x)) for x in v)
    x0, x1 = sorted((max(0, min(w, x0)), max(0, min(w, x1))))
    y0, y1 = sorted((max(0, min(h, y0)), max(0, min(h, y1))))
    if x1 - x0 < 4 or y1 - y0 < 4:
        raise ToolError(f"crop {v} is empty inside the {w}x{h} frame")
    return [x0, y0, x1, y1]


def _load_cfg(base, det):
    try:
        return vision_cfg.load(base, det)
    except vision_cfg.ConfigError as e:
        raise ToolError(str(e))


def _absolutize(cfg, folder):
    """The config's relative paths made absolute against its folder (what
    the vision client does for config:), so the model's bytes ship."""
    det = cfg.get("detection")
    if isinstance(det, dict):
        for k in ("path", "key_path"):
            if isinstance(det.get(k), str) and det[k] and not os.path.isabs(os.path.expanduser(det[k])):
                det[k] = str((folder / det[k]).resolve())
        for ref in det.get("references") or []:
            if isinstance(ref, dict) and isinstance(ref.get("image"), str) and not os.path.isabs(ref["image"]):
                ref["image"] = str((folder / ref["image"]).resolve())
    return cfg


def _changes(a):
    return any(a.get(k) is not None for k in ("exposure_ms", "auto_exposure", "white_balance", "focus"))


def _running(name):
    from . import tools
    try:
        return bool(tools.ORCH and tools.ORCH.is_launched(name))
    except Exception:
        return False


def _short(v, n=1500):
    s = json.dumps(v, default=str)
    return s if len(s) <= n else s[:n] + "…"


# ── approval ─────────────────────────────────────────────────────────

def approval(name, a, session):
    """What the user must approve before this call runs (a text), or None."""
    if name == "camera_settings" and _changes(a):
        bits = []
        if a.get("exposure_ms") is not None:
            bits.append(f"exposure → {a['exposure_ms']:g} ms (manual)")
        if a.get("auto_exposure") is not None:
            bits.append("exposure → auto" if a["auto_exposure"] else "auto exposure off")
        if a.get("white_balance"):
            bits.append("white balance → " + ("auto" if a["white_balance"] == "auto" else "hold (freeze the current balance)"))
        if a.get("focus") is not None:
            bits.append("focus → " + json.dumps(a["focus"]))
        cam = a.get("camera") or "the camera"
        proj = (a.get("project") or session.project or "")
        warn = " The project is running: its next pictures use these settings." if proj and _running(proj) else ""
        why = f" Why: {a['reason'].strip()}" if (a.get("reason") or "").strip() else ""
        return f"Change {cam}: " + "; ".join(bits) + "." + why + warn
    if name == "detection_test" and a.get("apply_focus"):
        return f"Test {a.get('detection')} with its focus pin applied — the camera's lens moves to it."
    return None


# ── the tools ────────────────────────────────────────────────────────

def t_camera_view(session, a):
    name, comp, where = _resolve(session, a)
    im = _grab(where)
    st = images.stats(im)
    lines = [f"{comp} ({name}): {_scale_note(im)}", images.stats_text(st)]
    shown = images.grid(im) if a.get("grid") else im
    shots = [Shot(images.encode(shown), a.get("caption") or f"{comp} — live frame")]
    if a.get("crop") is not None:
        b = _box(a["crop"], im)
        cs = images.stats(im, b)
        crop = im.crop(tuple(b))
        lines.append(f"crop {b} ({b[2] - b[0]}x{b[3] - b[1]} px): " + images.stats_text(cs, "crop"))
        shots.append(Shot(images.encode(images.grid(crop) if a.get("grid") else crop), f"{comp} — crop {b}"))
    return ["\n".join(lines), *shots], []


def t_roi_preview(session, a):
    name, comp, where = _resolve(session, a)
    base = project_dir(name)
    corners, src, inv = None, "", bool(a.get("inv"))
    if a.get("corners"):
        if len(a["corners"]) < 2 or not all(isinstance(p, list) and len(p) == 2 for p in a["corners"]):
            raise ToolError("corners: [[x, y], …] — 2 points (a rectangle) or 3 and more")
        corners, src = images.corners_of(a["corners"]), "the corners given"
    elif a.get("box"):
        box = a["box"]
        if len(box) != 9:
            raise ToolError("box: [x, y, z, a, b, c, w, d, h]")
        tmp = "_ai_roi_" + uuid.uuid4().hex[:8]

        def project_box(c, serial):
            c.detection_add(tmp, camera_serial_number=serial, timeout=30)
            try:
                snap = c.detection_capture(tmp, timeout=15)
                if not snap.get("ok"):
                    raise ToolError(f"the capture failed: {snap.get('msg')}")
                return c.detection_box_corners(tmp, box, timeout=15)
            finally:
                try:
                    c.detection_remove(tmp, timeout=5)
                except Exception:
                    pass
        r = _call(where, project_box)
        pts = r.get("corners") if isinstance(r, dict) else r
        if not pts:
            raise ToolError(f"the vision server projected no corners: {_short(r, 400)}")
        corners, src = images.corners_of(pts), f"the box {box}, projected by the vision server"
    elif a.get("detection"):
        rel, _, cfg = _load_cfg(base, a["detection"])
        roi = cfg.get("roi") if isinstance(cfg.get("roi"), dict) else {}
        if not roi.get("corners"):
            raise ToolError(f"{rel} has no roi corners yet" + (" (it has a 3D box: pass box)" if roi.get("box") else ""))
        corners, src = images.corners_of(roi["corners"]), f"{rel}'s roi"
        inv = bool(roi.get("inv")) if a.get("inv") is None else inv
    else:
        raise ToolError("give corners, box or detection")
    im = _grab(where)
    b = images.bbox(corners, im.size)
    if b[2] - b[0] < 4 or b[3] - b[1] < 4:
        raise ToolError(f"the ROI {corners} falls outside the {im.size[0]}x{im.size[1]} frame")
    ow = images.outline(im, corners, label=f"ROI {b[2] - b[0]}x{b[3] - b[1]}", inv=inv)
    if a.get("grid"):
        ow = images.grid(ow)
    crop = im.crop(tuple(b))
    cs = images.stats(im, b)
    pts = [[int(round(x)), int(round(y))] for x, y in corners]
    text = (f"{comp}: ROI from {src} — {_scale_note(im)}\n"
            f"bounding box {b} ({b[2] - b[0]}x{b[3] - b[1]} px)\n" + images.stats_text(cs, "ROI crop") +
            f"\nTo save (after the user confirms): update_detection_config set = "
            f"{json.dumps({'roi': {'corners': pts, 'inv': int(inv), 'crop': 1}})}  (crop: 1 = the model sees "
            f"only the crop; 0 = the full frame, masked)")
    return [text, Shot(images.encode(ow), f"{comp} — the proposed ROI"),
            Shot(images.encode(crop), f"{comp} — what the detection would see")], []


def t_camera_settings(session, a):
    from gui.devmode import camera
    name, comp, where = _resolve(session, a)
    if not _changes(a):
        cur = _call(where, camera.read_settings)
        return f"{comp} settings now:\n" + json.dumps(cur, indent=1, default=str)[:6000], []

    before = images.stats(_grab(where))
    ok, bad = _call(where, lambda c, serial: camera.adjust(
        c, serial, exposure_ms=a.get("exposure_ms"), auto_exposure=a.get("auto_exposure"),
        white_balance=a.get("white_balance"), focus=a.get("focus")))
    done = [f"{what}: {_short(r, 600)}" for what, r in ok]
    errors = [f"{what} REFUSED: {why}" for what, why in bad]
    camera.forget(*where)
    time.sleep(SETTLE_S)
    im = _grab(where)
    after = images.stats(im)
    if a.get("auto_exposure") is False and a.get("exposure_ms") is None:
        errors.append("auto exposure off alone is not a setting: give exposure_ms to pin it")
    text = "\n".join([f"{comp}:"] + [f"✓ {d}" for d in done] + [f"✗ {e}" for e in errors] + [
        images.stats_text(before, "BEFORE"), images.stats_text(after, "AFTER"),
        "Compare before/after and tell the user what changed."])
    return [text, Shot(images.encode(im), f"{comp} — after the change")], []


def t_detection_test(session, a):
    name, comp, where = _resolve(session, a)
    base = project_dir(name)
    rel, path, cfg = _load_cfg(base, a["detection"])
    if a.get("set") is not None:
        if not isinstance(a["set"], dict):
            raise ToolError("set: an object of config keys")
        cfg = vision_cfg.merge(cfg, a["set"])
    det = cfg.get("detection") if isinstance(cfg.get("detection"), dict) else {}
    if not det.get("cmd"):
        raise ToolError(f"{rel} names no detection.cmd")
    model = det.get("path")
    if model and not (path.parent / model).is_file() and not os.path.isabs(str(model)):
        raise ToolError(f"{rel}: the model {model!r} is not in vision/ — install it first (install_vision_model)")
    kw = _absolutize(copy.deepcopy(cfg), path.parent)
    disp = dict(kw.get("display") or {})
    disp.update({"label": 1, "save_img": False, "save_img_roi": False,
                 "client_save_img": False, "client_save_img_roi": False})
    kw["display"] = disp
    focus = kw.pop("focus", None)
    kw.pop("config", None)
    tmp = "_ai_test_" + uuid.uuid4().hex[:8]
    out = {}

    def run(c, serial):
        c.detection_add(tmp, camera_serial_number=serial, timeout=60, **kw)
        try:
            snap = c.detection_capture(tmp, focus=focus if a.get("apply_focus") else None, timeout=30)
            if not snap.get("ok"):
                raise ToolError(f"the capture failed: {snap.get('msg', '?')}")
            t0 = time.monotonic()
            out["valid"] = c.detection_run(tmp, use_last=True, timeout=60)
            out["ms"] = round((time.monotonic() - t0) * 1000)
            for kind in ("img", "img_roi"):
                try:
                    out[kind], _ = c.detection_get_img(tmp, type=kind, quality=85, timeout=15)
                except Exception as e:
                    out[kind + "_err"] = str(e)
        finally:
            try:
                c.detection_remove(tmp, timeout=5)
            except Exception:
                pass
    _call(where, run)
    valid = out.get("valid") or []
    rows = valid if isinstance(valid, list) else [valid]
    lines = [f"{rel} on {comp} (private test detection {tmp}, removed): {len(rows)} result(s) in {out.get('ms')} ms"
             + ("" if not a.get("set") else f"; overrides for this test only: {_short(a['set'], 400)}")
             + ("" if not focus or a.get("apply_focus") else "; the config's focus pin was NOT applied (lens left as is)")]
    lines.append(_short(rows[:30], 5000))
    shots = []
    if out.get("img"):
        try:
            shots.append(Shot(images.encode(images.decode(out["img"])), f"{a['detection']} — the detection's result"))
        except Exception:
            lines.append("(the annotated picture didn't decode)")
    if out.get("img_roi") and (cfg.get("roi") or {}).get("corners"):
        try:
            shots.append(Shot(images.encode(images.decode(out["img_roi"])), f"{a['detection']} — the ROI crop"))
        except Exception:
            pass
    if not shots:
        lines.append("no annotated picture came back" + (f": {out.get('img_err')}" if out.get("img_err") else ""))
    return ["\n".join(lines), *shots], []


def t_update_detection_config(session, a):
    name = _name(session, a)
    base = project_dir(name)
    rel = vision_cfg.cfg_rel(a["detection"])
    p = _inside(base, rel)
    if not isinstance(a["set"], dict) or not a["set"]:
        raise ToolError("set: the keys to write, e.g. {roi: {corners: [[x, y], …], inv: 0, crop: 1}}")
    try:
        r = vision_cfg.write(p, a["set"])
    except vision_cfg.ConfigError as e:
        raise ToolError(str(e))
    from .tools import _ui_for
    head = f"{'created' if r['created'] else 'updated'} {rel}: {', '.join(r['changed']) or 'no change'}\n"
    tail = ("(the project is launched: relaunch it to use the new settings)" if _running(name)
            else "(used from the project's next launch)")
    body_txt = "".join(vision_cfg.dump_key(k, r["config"][k]) for k in r["changed"] if k in r["config"])
    return head + body_txt + tail, [_ui_for(rel, name)]


HANDLERS = {t["name"]: globals()["t_" + t["name"]] for t in TOOLS}
