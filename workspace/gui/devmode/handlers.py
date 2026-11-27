"""Developer mode — /dev/?project=<name> and its API (/dev/api/…).

Logged-in units only: the page answers 401 with a "Log in for Developer
mode" notice, every API route 401 {error: "login required"} (contract:
offline mode runs existing projects and nothing else).

    GET  /dev/api/ports                                serial ports on this unit
    GET  /dev/api/projects/<n>/info                    project dir, launch state, components (+ each part's settings)
    GET  /dev/api/projects/<n>/params?component=c      the Parameters window's fields
    POST /dev/api/projects/<n>/params                  {component, values: {key: value}}
    POST /dev/api/projects/<n>/simulation              {components: [..] | component, on}
    POST /dev/api/projects/<n>/scene/save              {files: {file: {component: cfg}}, base_sig}  (scene builder → files)
    GET  /dev/api/projects/<n>/camera/<c>.jpg          one live frame (JPEG), or JSON {image: false, code, reason}
    GET  /dev/api/projects/<n>/camera/<c>/setup        the camera window: fields, vision servers, feed reason, vision Pi link, detections
    POST /dev/api/projects/<n>/camera/<c>/find         {server? | ip, port} → the cameras that vision server sees (camera_list)
    POST /dev/api/projects/<n>/camera/<c>/adjust       {exposure_ms | auto_exposure, white_balance: auto|hold} → done, settings
    POST /dev/api/projects/<n>/camera/<c>/roi          {detection, corners, inv, crop, size} → roi written into vision/<detection>.yaml
    GET  /dev/api/projects/<n>/hmi                     the HMI screens' specs + a change signature
    GET  /dev/api/projects/<n>/hmi/pendant/<file>      the Main screen's files, before launch
    GET  /dev/api/projects/<n>/calibration/plan        the calibration plan (gui/calibration/plan.py)
    GET  /dev/api/projects/<n>/workflow                the workflow: steps (actions), stations (recipes), charts (flow/)
    GET  /dev/api/projects/<n>/flowchart?name=         one chart (flow/<name>.json) and its checks
    POST /dev/api/projects/<n>/flowchart               {name, doc} → checked; saved only when it checks out
    GET  /dev/api/projects/<n>/part?type=              a project component for the component builder (parts.py)
    POST /dev/api/projects/<n>/part                    {type, anchors, collision_boxes, glb_b64?, …} → saved;
                                                       {type, delete: true} → removed (refused while the scene uses it)
    GET  /dev/api/projects/<n>/part/<type>/<file>      its model (<type>.glb, and a compressed one's .glb.bin)
    POST /dev/api/component/template                   {kind?, type, dims, top, holes, pegs, xy} → the kind's anchors
    POST /dev/api/component/mesh                       {tris} → {id}: the model kept for the assistant's measuring
    POST /dev/api/component/ai                         {message, history, …} → the assistant's anchors and boxes
"""
import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import tornado.ioloop
import tornado.web

from gui.orchestrator.handlers import ProjectScreenFileHandler

from . import access, camera, component_ai, component_kinds, mesh_measure, params, parts, scene_edit, vision_cfg, workflow

DEV_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(DEV_DIR, "web")
GUI_DIR = os.path.dirname(DEV_DIR)
WS_ROOT = os.path.dirname(GUI_DIR)
PLAN_PY = os.path.join(GUI_DIR, "calibration", "plan.py")

LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Developer mode</title>
<link rel="stylesheet" href="/vendor/base.css">
<script src="/vendor/theme.js"></script>
<link rel="stylesheet" href="/vendor/account.css"><link rel="stylesheet" href="/vendor/ai_chat.css">
<style>body{background:var(--bg);color:var(--text);font-family:var(--font,system-ui);margin:0}
.dv-gate{max-width:440px;margin:18vh auto 0;padding:28px;background:var(--surface);border-radius:var(--radius-lg,16px);box-shadow:var(--shadow-md);text-align:center}
.dv-gate h1{font-size:20px;margin:0 0 8px}.dv-gate p{color:var(--muted);font-size:14px;line-height:1.5;margin:0 0 18px}
.dv-gate a{display:inline-flex;align-items:center;min-height:44px;padding:0 18px}</style></head>
<body><div class="dv-gate"><h1>Log in for Developer mode</h1>
<p>Developer mode edits projects and uses your account. Log in with the button at the top right, then reload this page.</p>
<a class="btn" href="/">Back to projects</a></div>
<script src="/vendor/account.js"></script><script src="/vendor/ai_chat.js"></script></body></html>"""


def _off_loop(fn, *a):
    return tornado.ioloop.IOLoop.current().run_in_executor(None, fn, *a)


class _Base(tornado.web.RequestHandler):
    def initialize(self, orch=None):
        self.orch = orch

    def on_finish(self):
        if self.request.method == "POST" and self.get_status() < 400:
            from gui.account.sync import kick      # a save: the Dorna team's copy follows soon
            kick()

    def set_default_headers(self):
        self.set_header("Cache-Control", "no-store")

    def prepare(self):
        if not access.logged_in():
            self.set_status(401)
            self.set_header("Content-Type", "application/json")
            self.finish({"error": "login required"})

    def body(self):
        try:
            return json.loads(self.request.body.decode() or "{}")
        except ValueError:
            raise tornado.web.HTTPError(400, reason="invalid JSON")

    def fail(self, code, msg):
        self.set_status(code)
        self.finish({"ok": False, "error": msg})

    def project(self, name):
        """(WorkspaceInfo, project dir) or None after answering 404/400."""
        ws = self.orch.workspaces.get(name)
        if ws is None:
            self.fail(404, f"no project {name}")
            return None
        if ws.is_remote():
            self.fail(400, f"{name} runs on another unit — open Developer mode there")
            return None
        d = Path(ws.path_to_file).parent
        if not (d / "launch.yaml").is_file():
            self.fail(400, f"{name} has no launch.yaml")
            return None
        return ws, d


class DevStaticHandler(tornado.web.StaticFileHandler):
    """The page's own files (css/js/html) — logged-in units only, like
    the page itself (the path is matched after URL-decoding, so a gate
    on the literal name alone could be walked around)."""
    def set_extra_headers(self, path):
        self.set_header("Cache-Control", "no-store")

    def prepare(self):
        if not access.logged_in():
            self.set_status(401)
            self.set_header("Content-Type", "application/json")
            self.finish({"error": "login required"})


class DevPageHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Cache-Control", "no-store")
        if not access.logged_in():
            self.set_status(401)
            self.finish(LOGIN_PAGE)
            return
        with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
            self.set_header("Content-Type", "text/html; charset=utf-8")
            self.finish(f.read())


class PortsHandler(_Base):
    def get(self):
        self.finish({"ok": True, "ports": params.serial_ports()})


def _components(scene):
    """The scene's parts for the page: kind, simulation, and — for a part
    with a settings menu — its Settings window (the /params schema), so
    the page opens one without a round trip."""
    comps = []
    for n, cfg in scene.items():
        if not isinstance(cfg, dict):
            continue
        t = str(cfg.get("type") or "")
        dev = params.is_device(t, cfg)
        try:
            sch = params.schema(n, cfg, scene)
        except Exception:
            sch = {"fields": []}
        comps.append({"name": n, "type": t, "kind": params.kind_of(t), "device": dev,
                      "simulation": cfg.get("simulation", True) is not False if dev else None,
                      "no_connection": params.no_connection(sch, scene) if sch["fields"] else None,
                      "camera": t.startswith("inspection_"), "menu": bool(sch["fields"]),
                      "settings": sch if sch["fields"] else None})
    return comps


class InfoHandler(_Base):
    async def get(self, name):
        p = self.project(name)
        if not p:
            return
        ws, d = p
        try:
            scene = await _off_loop(scene_edit.merged, d)
            scene_err = None
        except Exception as e:
            scene, scene_err = {}, f"{type(e).__name__}: {e}"
        comps = await _off_loop(_components, scene)
        try:
            files = [os.path.relpath(f, d) for f in scene_edit.scene_files(d)]
            sig = scene_edit.files_sig(d)
        except Exception:
            files, sig = [], ""
        self.finish({"ok": True, "name": name, "label": ws.label, "dir": str(d),
                     "main": ws.path_to_file, "launched": self.orch.is_launched(name),
                     "port": ws.port, "scene_files": files, "scene_sig": sig, "scene_error": scene_err,
                     "components": comps, "own_types": parts.project_types(d)})


class ParamsHandler(_Base):
    async def get(self, name):
        p = self.project(name)
        if not p:
            return
        comp = self.get_argument("component", "")
        try:
            scene = await _off_loop(scene_edit.merged, p[1])
        except Exception as e:
            return self.fail(400, f"the scene does not load: {e}")
        if comp not in scene:
            return self.fail(404, f"no component {comp}")
        out = params.schema(comp, scene[comp], scene)
        out.update(ok=True, launched=self.orch.is_launched(name))
        self.finish(out)

    async def post(self, name):
        p = self.project(name)
        if not p:
            return
        b = self.body()
        comp = b.get("component") or ""
        values = b.get("values") or {}
        if not isinstance(values, dict) or not values:
            return self.fail(400, "values: {key: value}")
        try:
            scene = await _off_loop(scene_edit.merged, p[1])
            if comp not in scene:
                return self.fail(404, f"no component {comp}")
            fields = {f["key"]: f for f in params.schema(comp, scene[comp], scene)["fields"]}
            typed = {}
            for k, v in values.items():
                if k not in fields:
                    return self.fail(400, f"{k} is not a parameter of {comp}")
                if v in ("", None) and fields[k]["value"] is None:
                    continue                # left empty and never set: stays unset
                typed[k] = params.coerce(fields[k], v)
            # Only what changed: an untouched field keeps its text, and an
            # unset one left at the library default stays unset.
            typed = {k: v for k, v in typed.items() if fields[k]["value"] != v
                     and not (fields[k]["value"] is None and "default" in fields[k] and fields[k]["default"] == v)}
            touched = await _off_loop(scene_edit.set_values, p[1], comp, typed) if typed else {}
        except (ValueError, scene_edit.SceneEditError) as e:
            return self.fail(400, str(e))
        # Keys the scene builder also writes (rail, has_rail, critical…):
        # a builder holding the old values would save them back, so the
        # page must reload the builder before its next Save.
        builder_keys = sorted(k for k in typed if not scene_edit.builder_skips(k))
        self.finish({"ok": True, "changed": sorted(typed), "files": touched,
                     "builder_keys": builder_keys,
                     "scene_sig": scene_edit.files_sig(p[1]),
                     "relaunch": bool(typed) and self.orch.is_launched(name)})


class SimulationHandler(_Base):
    async def post(self, name):
        p = self.project(name)
        if not p:
            return
        b = self.body()
        names = b.get("components") or ([b["component"]] if b.get("component") else [])
        on = b.get("on")
        if not isinstance(on, bool) or not names:
            return self.fail(400, "{components: [...], on: true|false}")
        done, errors = [], {}
        scene = await _off_loop(scene_edit.merged, p[1])
        for n in names:
            cfg = scene.get(n)
            if not isinstance(cfg, dict) or not params.is_device(str(cfg.get("type") or ""), cfg):
                errors[n] = "not a device"
                continue
            if (cfg.get("simulation", True) is not False) == on:
                done.append(n)
                continue
            try:
                await _off_loop(scene_edit.set_values, p[1], n, {"simulation": on})
                done.append(n)
            except Exception as e:
                errors[n] = str(e)
        self.finish({"ok": not errors, "done": done, "errors": errors,
                     "scene_sig": scene_edit.files_sig(p[1]),
                     "relaunch": self.orch.is_launched(name)})


class SceneSaveHandler(_Base):
    async def post(self, name):
        p = self.project(name)
        if not p:
            return
        b = self.body()
        files = b.get("files")
        if not isinstance(files, dict):
            return self.fail(400, "{files: {scene file: {component: cfg}}}")
        # The builder loaded the files at base_sig; anything written since
        # (the AI, the Parameters window, an editor) would be undone.
        if not isinstance(b.get("base_sig"), str) or not b["base_sig"]:
            return self.fail(400, "base_sig is required: the scene_sig the files had when you read them "
                                  "(GET …/info) — a save without it could undo newer edits.")
        if b["base_sig"] != scene_edit.files_sig(p[1]):
            return self.fail(409, "The scene files changed since the builder loaded them — "
                                  "reload the Scene builder, then make your change again.")
        try:
            summary = await _off_loop(scene_edit.save_components, p[1], files)
        except Exception as e:
            return self.fail(400, str(e))
        self.finish({"ok": True, **summary, "scene_sig": scene_edit.files_sig(p[1]),
                     "relaunch": self.orch.is_launched(name)})


class CameraFrameHandler(_Base):
    async def get(self, name, comp):
        p = self.project(name)
        if not p:
            return
        try:
            scene = await _off_loop(scene_edit.merged, p[1])
            host, port, serial, cam = camera.resolve(scene, comp)
            jpg = await _off_loop(camera.grab, host, port, serial, cam)
        except camera.NoImage as e:
            return self.finish({"ok": True, "image": False, "code": e.code, "reason": str(e)})
        except Exception as e:
            self.set_status(502)
            return self.finish({"ok": False, "image": False, "code": "unreachable",
                                "reason": f"No picture from camera {serial or cam.get('ip')} on the vision server at "
                                          f"{host}:{port} — {type(e).__name__}: {e}"})
        self.set_header("Content-Type", "image/jpeg")
        self.finish(jpg)


def _camera_setup(d, comp):
    """The camera window's state: the part's fields, the scene's vision
    servers, why there is no live picture (or none), the vision Pi's
    link, the detections this camera runs."""
    scene = scene_edit.merged(d)
    cfg = scene.get(comp)
    if not isinstance(cfg, dict):
        raise KeyError(comp)
    out = params.schema(comp, cfg, scene)
    out["servers"] = [{"name": n, "ip": c.get("ip") or "", "port": c.get("port") or "",
                       "simulation": c.get("simulation", True) is not False}
                      for n, c in scene.items() if isinstance(c, dict) and c.get("type") == "vision_server"]
    if out["type"] == "vision_server":
        out["cameras"] = [{"name": n, "serial_number": str((c.get("camera_cfg") or {}).get("serial_number") or ""),
                           "ip": str((c.get("camera_cfg") or {}).get("ip") or "")}
                          for n, c in scene.items() if isinstance(c, dict) and isinstance(c.get("camera_cfg"), dict)
                          and (c["camera_cfg"].get("server") == comp)]
    else:
        try:
            camera.resolve(scene, comp)
            out["feed"] = {"live": True}
        except camera.NoImage as e:
            out["feed"] = {"live": False, "code": e.code, "reason": str(e)}
        out["detections"] = vision_cfg.detections(d, comp)
    try:
        out["vision"] = {"available": True, **camera.configurator(scene, comp)}
    except camera.NoImage as e:
        out["vision"] = {"available": False, "reason": str(e)}
    return out


class CameraSetupHandler(_Base):
    """GET the camera window; POST …/find lists the cameras a vision
    server sees, …/adjust sets exposure / white balance, …/roi writes a
    detection's ROI."""
    async def get(self, name, comp):
        p = self.project(name)
        if not p:
            return
        try:
            out = await _off_loop(_camera_setup, p[1], comp)
        except KeyError:
            return self.fail(404, f"no component {comp}")
        except Exception as e:
            return self.fail(400, f"the scene does not load: {e}")
        self.finish({"ok": True, "launched": self.orch.is_launched(name), **out})


def _server_addr(scene, comp, b):
    """(host, port) to ask: the ip/port typed in the window (not saved
    yet), else the vision server the part names (or is)."""
    ip = str(b.get("ip") or "").strip()
    if ip:
        if not re.fullmatch(r"[A-Za-z0-9.\-:]+", ip):
            raise ValueError("Vision server IP: an address like 192.168.1.40 or a host name")
        try:
            port = int(str(b.get("port") or 80).strip())
        except ValueError:
            raise ValueError("Vision server port: a whole number")
        if not 0 < port < 65536:
            raise ValueError("Vision server port: 1 to 65535")
        return ip, port
    cfg = scene.get(comp) or {}
    srv_name = comp if cfg.get("type") == "vision_server" else str(b.get("server") or
                                                                    (cfg.get("camera_cfg") or {}).get("server") or "")
    srv = scene.get(srv_name) if srv_name else None
    if not isinstance(srv, dict) or srv.get("type") != "vision_server":
        raise ValueError("Pick the vision server first")
    if not srv.get("ip"):
        raise ValueError(f"The vision server {srv_name} has no IP address — type it in")
    return str(srv["ip"]), int(srv.get("port") or 80)


class CameraFindHandler(_Base):
    async def post(self, name, comp):
        p = self.project(name)
        if not p:
            return
        b = self.body()
        scene = await _off_loop(scene_edit.merged, p[1])
        if comp not in scene:
            return self.fail(404, f"no component {comp}")
        try:
            host, port = _server_addr(scene, comp, b)
        except ValueError as e:
            return self.fail(400, str(e))
        try:
            devs = await _off_loop(camera.devices, host, port)
        except Exception as e:
            return self.fail(502, f"The vision server at {host}:{port} did not answer: {type(e).__name__}: {e}")
        used = {}
        for n, c in scene.items():
            cc = c.get("camera_cfg") if isinstance(c, dict) else None
            if isinstance(cc, dict):
                for k in ("serial_number", "ip"):
                    if cc.get(k):
                        used.setdefault(str(cc[k]), n)
        for dv in devs:
            dv["used_by"] = used.get(str(dv.get("serial_number") or "")) or used.get(str(dv.get("ip") or ""))
        self.finish({"ok": True, "host": host, "port": port, "devices": devs})


class CameraAdjustHandler(_Base):
    """A live change on the camera — one button press, one call."""
    async def post(self, name, comp):
        p = self.project(name)
        if not p:
            return
        b = self.body()
        kw = {}
        if b.get("exposure_ms") not in (None, ""):
            try:
                kw["exposure_ms"] = float(b["exposure_ms"])
            except (TypeError, ValueError):
                return self.fail(400, "Exposure: a number of milliseconds")
            if not 0 < kw["exposure_ms"] <= 10000:
                return self.fail(400, "Exposure: more than 0 and at most 10000 ms")
        elif b.get("auto_exposure") is True:
            kw["auto_exposure"] = True
        if b.get("white_balance") in ("auto", "hold"):
            kw["white_balance"] = b["white_balance"]
        scene = await _off_loop(scene_edit.merged, p[1])
        try:
            where = camera.resolve(scene, comp)
        except camera.NoImage as e:
            return self.fail(400, str(e))

        def run(c, serial):
            done, errors = camera.adjust(c, serial, **kw) if kw else ([], [])
            return done, errors, camera.read_settings(c, serial)
        try:
            done, errors, now = await _off_loop(camera.call, *where, run)
        except Exception as e:
            return self.fail(502, f"The camera did not answer: {type(e).__name__}: {e}")
        if kw:
            camera.forget(*where)
        self.finish({"ok": True, "done": [{"what": w, "result": r} for w, r in done],
                     "errors": [{"what": w, "error": r} for w, r in errors], "settings": now})


class CameraRoiHandler(_Base):
    async def post(self, name, comp):
        p = self.project(name)
        if not p:
            return
        b = self.body()
        det = str(b.get("detection") or "")
        if not vision_cfg.NAME_RE.match(det):
            return self.fail(400, "Pick a detection")
        size = b.get("size") if isinstance(b.get("size"), list) and len(b["size"]) == 2 else None
        try:
            roi = vision_cfg.roi_value(b.get("corners"), b.get("inv"), b.get("crop"), size)
            rel = vision_cfg.cfg_rel(det)
            path = vision_cfg.inside(p[1], rel)
            if path.is_file():
                # corners always win over a 3D box (dorna_vision detect.py):
                # a drawn ROI replaces it, so the file says what runs
                old = (vision_cfg.load(p[1], det)[2].get("roi") or {})
                if isinstance(old, dict) and "box" in old:
                    roi["box"] = None
            r = await _off_loop(vision_cfg.write, path, {"roi": roi})
        except vision_cfg.ConfigError as e:
            return self.fail(400, str(e))
        self.finish({"ok": True, "file": rel, "created": r["created"], "changed": r["changed"],
                     "roi": r["config"].get("roi"), "relaunch": bool(r["changed"]) and self.orch.is_launched(name)})


class HmiHandler(_Base):
    def get(self, name):
        p = self.project(name)
        if not p:
            return
        ws, d = p
        out = {"ok": True, "launched": self.orch.is_launched(name)}
        sig = []
        for key in ("pendant", "setup"):
            spec = ws.screen_spec(key)
            if spec:
                folder = Path(spec.pop("dir"))
                for f in sorted(folder.glob("*")):
                    if f.is_file():
                        sig.append(f"{f.name}:{f.stat().st_mtime_ns}")
            out[key] = spec
        try:
            import yaml
            launch = yaml.safe_load((d / "launch.yaml").read_text()) or {}
            sig.append(f"launch:{(d / 'launch.yaml').stat().st_mtime_ns}")
            out["pendant_decl"] = launch.get("pendant")
            out["pendant_hero"] = launch.get("pendant_hero", True)
        except Exception:
            pass
        out["sig"] = str(hash("|".join(sig)))
        self.finish(out)


class PendantFileHandler(ProjectScreenFileHandler):
    """The Main (pendant) screen's files, served before launch — the
    runtime serves them at /hmi/ only while the project runs."""
    def prepare(self):
        if not access.logged_in():
            self.set_status(401)
            self.finish({"error": "login required"})


class WorkflowHandler(_Base):
    async def get(self, name):
        p = self.project(name)
        if not p:
            return
        self.finish({"ok": True, **await _off_loop(workflow.info, p[1])})


class FlowchartHandler(_Base):
    def get(self, name):
        p = self.project(name)
        if not p:
            return
        chart = self.get_argument("name", "")
        try:
            out = workflow.load_chart(p[1], chart)
        except (OSError, ValueError) as e:
            return self.fail(404, f"no flowchart {chart!r}: {e}")
        self.finish({"ok": True, "name": chart, **out})

    def post(self, name):
        p = self.project(name)
        if not p:
            return
        b = self.body()
        if not isinstance(b.get("doc"), dict):
            return self.fail(400, "{name, doc}")
        try:
            check = workflow.save_chart(p[1], b.get("name") or "", b["doc"])
        except (OSError, ValueError) as e:
            return self.fail(400, str(e))
        self.finish({"ok": True, "saved": check["ok"], "check": check})


# ── the component builder (web/compbuilder.js) ─────────────────────
# what a malformed request raises below parts.PartError's own messages
BAD_INPUT = (ValueError, TypeError, KeyError, IndexError, AttributeError)


def _bad(e):
    return e if isinstance(e, parts.PartError) else f"That request could not be used ({type(e).__name__}: {e})."


def _forget_type(ctype):
    """A deleted project part out of the builder's catalog — or, when it
    shadowed a library type of the same name, that library type back."""
    import gui.scene_builder.server as sb
    lib = sb.scan_registered_components().get(ctype)
    if lib:
        sb.load_module_from_path(lib, f"_library_component_{ctype}")      # its @register again
        sb.COMPONENT_MAP[ctype] = lib
    else:
        sb.COMPONENT_MAP.pop(ctype, None)


class PartHandler(_Base):
    def get(self, name):
        p = self.project(name)
        if not p:
            return
        try:
            self.finish({"ok": True, **parts.read(p[1], self.get_argument("type", ""))})
        except BAD_INPUT as e:
            self.fail(400, str(_bad(e)))

    async def post(self, name):
        p = self.project(name)
        if not p:
            return
        b = self.body()
        try:
            if not isinstance(b, dict):
                raise parts.PartError("send the part as a JSON object")
            if b.get("delete"):
                scene = await _off_loop(scene_edit.merged, p[1])
                out = parts.delete(p[1], b.get("type"), scene)
                _forget_type(out["deleted"])
            else:
                out = await _off_loop(parts.save, p[1], b)
        except BAD_INPUT as e:
            return self.fail(400, str(_bad(e)))
        self.finish({"ok": True, **out})


class PartModelHandler(_Base):
    """A project part's model, by path, so a compressed model's relative
    ``<type>.glb.bin`` resolves next to it."""
    def get(self, name, ctype, fname):
        p = self.project(name)
        if not p:
            return
        f = parts.folder(p[1], ctype) / fname
        if not parts.TYPE_RE.match(ctype) or fname not in (f"{ctype}.glb", f"{ctype}.glb.bin") or not f.is_file():
            raise tornado.web.HTTPError(404)
        raw = f.read_bytes()
        self.set_header("Content-Type", "application/octet-stream" if fname.endswith(".bin") else
                        "model/gltf+json" if raw[:1] == b"{" else "model/gltf-binary")
        self.finish(raw)


class ComponentTemplateHandler(_Base):
    def post(self):
        CK = component_kinds
        d = self.body()
        try:
            kind = d.get("kind") if d.get("kind") in CK.KINDS else CK.detect(d.get("type"), d.get("file_name"), d.get("description"))
            self.finish({"ok": True, "kinds": CK.kinds(),
                         **CK.template(kind, d.get("dims"), d.get("top"), d.get("holes"), d.get("xy"), d.get("pegs"))})
        except BAD_INPUT as e:
            self.finish({"ok": False, "error": str(_bad(e))})


class ComponentMeshHandler(_Base):
    async def post(self):
        try:
            self.finish({"ok": True, **await _off_loop(mesh_measure.store, self.body().get("tris") or "")})
        except BAD_INPUT as e:
            self.finish({"ok": False, "error": str(_bad(e))})


class ComponentAIHandler(_Base):
    """The assistant reaches the model through the account's relay
    (gui/ai/agent.client) — metered there; nothing here counts cost."""
    async def post(self):
        import anthropic
        from gui.ai import agent
        try:
            return self.finish({"ok": True, **await component_ai.design(self.body())})
        except agent.NotLoggedIn:
            msg = "Log in (top right) to use the assistant — everything else here works without it."
        except anthropic.PermissionDeniedError:
            msg = agent.OUT_OF_CREDITS
        except anthropic.RateLimitError:
            msg = "The assistant is busy right now — wait a moment and try again."
        except anthropic.APIConnectionError:
            msg = "Couldn't reach the Dorna server — check this robot's internet connection."
        except anthropic.APIStatusError as e:
            msg = f"Dorna AI couldn't answer (error {e.status_code}). Try again in a moment."
        except BAD_INPUT as e:
            msg = str(_bad(e))
        self.finish({"ok": False, "error": msg})


# ── calibration plan ────────────────────────────────────────────────
_plan_lock = asyncio.Lock()
_plan_cache = {}              # main.py → (signature, result)


def _plan_sig(d):
    out = []
    for f in [d / "launch.yaml", d / "core" / "calibrate.json", d / "calibration.py"] + \
            list(d.glob("scene/*")) + list(d.glob("*.j2")) + list(d.glob("components/**/*.py")):
        try:
            out.append(f"{f}:{f.stat().st_mtime_ns}")
        except OSError:
            pass
    return "|".join(out)


class CalibrationPlanHandler(_Base):
    async def get(self, name):
        p = self.project(name)
        if not p:
            return
        ws, d = p
        main = ws.path_to_file
        if not os.path.isfile(main):
            return self.fail(400, f"no main.py at {main}")
        sig = _plan_sig(d)
        cached = _plan_cache.get(main)
        if cached and cached[0] == sig and not self.get_argument("refresh", ""):
            return self.finish(cached[1])
        async with _plan_lock:           # plan.py builds a sim Workspace on one fixed port
            env = dict(os.environ, PYTHONPATH=WS_ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, PLAN_PY, main, cwd=str(d), env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out, err = await asyncio.wait_for(proc.communicate(), timeout=240)
            except asyncio.TimeoutError:
                proc.kill()
                return self.fail(504, "building the plan took over 4 minutes")
            try:
                res = json.loads(out.decode().strip().splitlines()[-1])
            except Exception:
                tail = err.decode(errors="replace").strip().splitlines()[-6:]
                return self.fail(500, "the plan did not build: " + " / ".join(tail))
        if res.get("ok"):
            _plan_cache[main] = (sig, res)
        self.finish(res)


def routes(orch):
    o = dict(orch=orch)
    P = r"/dev/api/projects/([^/]+)"
    return [
        (r"/dev/?", DevPageHandler),
        (r"/dev/index\.html", DevPageHandler),
        (r"/dev/api/ports", PortsHandler, o),
        (P + r"/info", InfoHandler, o),
        (P + r"/params", ParamsHandler, o),
        (P + r"/simulation", SimulationHandler, o),
        (P + r"/scene/save", SceneSaveHandler, o),
        (P + r"/camera/([^/]+)\.jpg", CameraFrameHandler, o),
        (P + r"/camera/([^/]+)/setup", CameraSetupHandler, o),
        (P + r"/camera/([^/]+)/find", CameraFindHandler, o),
        (P + r"/camera/([^/]+)/adjust", CameraAdjustHandler, o),
        (P + r"/camera/([^/]+)/roi", CameraRoiHandler, o),
        (P + r"/hmi", HmiHandler, o),
        (P + r"/hmi/pendant/(.*)", PendantFileHandler, dict(orch=orch, key="pendant")),
        (P + r"/calibration/plan", CalibrationPlanHandler, o),
        (P + r"/workflow", WorkflowHandler, o),
        (P + r"/flowchart", FlowchartHandler, o),
        (P + r"/part", PartHandler, o),
        (P + r"/part/([a-z0-9_]+)/([A-Za-z0-9_.]+)", PartModelHandler, o),
        (r"/dev/api/component/template", ComponentTemplateHandler, o),
        (r"/dev/api/component/mesh", ComponentMeshHandler, o),
        (r"/dev/api/component/ai", ComponentAIHandler, o),
        (r"/dev/(.*)", DevStaticHandler, {"path": WEB_DIR}),
    ]
