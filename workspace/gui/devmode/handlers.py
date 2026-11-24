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
    GET  /dev/api/projects/<n>/camera/<c>.jpg          one live frame (JPEG), or JSON {image: false, reason} in sim
    GET  /dev/api/projects/<n>/vision/<c>              {available, url, server, serial} of the camera's vision configurator, or {available: false, reason}
    GET  /dev/api/projects/<n>/hmi                     the HMI screens' specs + a change signature
    GET  /dev/api/projects/<n>/hmi/pendant/<file>      the Main screen's files, before launch
    GET  /dev/api/projects/<n>/calibration/plan        the calibration plan (gui/calibration/plan.py)
    GET  /dev/api/projects/<n>/workflow                the workflow: steps (actions), stations (recipes), charts (flow/)
    GET  /dev/api/projects/<n>/flowchart?name=         one chart (flow/<name>.json) and its checks
    POST /dev/api/projects/<n>/flowchart               {name, doc} → checked; saved only when it checks out
"""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import tornado.ioloop
import tornado.web

from gui.orchestrator.handlers import ProjectScreenFileHandler

from . import access, camera, params, scene_edit, workflow

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
                     "components": comps})


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
            return self.finish({"ok": True, "image": False, "sim": True, "reason": str(e)})
        except Exception as e:
            self.set_status(502)
            return self.finish({"ok": False, "image": False, "reason": f"The camera did not answer: {type(e).__name__}: {e}"})
        self.set_header("Content-Type", "image/jpeg")
        self.finish(jpg)


class VisionConfiguratorHandler(_Base):
    """Where to open the vision configurator for one camera part."""
    async def get(self, name, comp):
        p = self.project(name)
        if not p:
            return
        try:
            scene = await _off_loop(scene_edit.merged, p[1])
            where = camera.configurator(scene, comp)
        except camera.NoImage as e:
            return self.finish({"ok": True, "available": False, "reason": str(e)})
        except Exception as e:
            return self.fail(400, f"{type(e).__name__}: {e}")
        self.finish({"ok": True, "available": True, **where})


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
        (P + r"/vision/([^/]+)", VisionConfiguratorHandler, o),
        (P + r"/hmi", HmiHandler, o),
        (P + r"/hmi/pendant/(.*)", PendantFileHandler, dict(orch=orch, key="pendant")),
        (P + r"/calibration/plan", CalibrationPlanHandler, o),
        (P + r"/workflow", WorkflowHandler, o),
        (P + r"/flowchart", FlowchartHandler, o),
        (r"/dev/(.*)", DevStaticHandler, {"path": WEB_DIR}),
    ]
