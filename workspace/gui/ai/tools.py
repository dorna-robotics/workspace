"""The on-device assistant's tools: they act on THIS Workspace.

Project files (scene, launch.yaml, recipes, actions, hmi/), the merged
scene and a quick scene check, the component registry (filtered to the
components the account was given), the live runtime through the
orchestrator's own HTTP routes on localhost (status, logs, launch / start /
pause / resume / park / kill / replan, devices, run parameters), and the
Workspace's own knowledge (docs/, .claude/skills/, examples/, the source),
and the files the user attached (uploads.py): list / read them, make a 3D
model a project component, install a vision model in vision/.

``run_tool(session, name, args) -> (content, ui_events)``: content is a
text, or a list of texts and images.Shot (a picture the model sees and the
chat shows); a ToolError is the model's mistake (shown to it as an error
result, not a crash). ``approval(session, name, args)`` says which calls
the user approves first (hardware). The camera tools are in vision.py.
"""
import ast
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

GUI_DIR = Path(__file__).resolve().parent.parent
WS_ROOT = GUI_DIR.parent                       # workspace/
REPO_ROOT = WS_ROOT.parent                     # the repo: docs/, .claude/, examples/

# The Workspace's knowledge a customer may read (it ships in the repo).
DOC_ROOTS = ("docs", ".claude/skills", "examples", "scenes", "workspace", "README.md", "CLAUDE.md")
DOC_HIDDEN = ("docs/internal",)
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".ipynb_checkpoints", "log", "rec", ".sessions"}
TEXT_SUFFIXES = {".md", ".py", ".j2", ".yaml", ".yml", ".json", ".js", ".html", ".css", ".txt", ".csv",
                 ".toml", ".cfg", ".ini", ".sh", ".ipynb", ""}
MAX_FILE_BYTES = 400_000

ORCH = None                                    # the server's Orchestrator (handlers.setup)


class ToolError(Exception):
    pass


# ── schemas ──────────────────────────────────────────────────────────

def _tool(name, desc, props=None, required=()):
    return {"name": name, "description": desc, "eager_input_streaming": True,
            "input_schema": {"type": "object", "properties": props or {}, "required": list(required),
                             "additionalProperties": False}}


_P = {"type": "string", "description": "The project's name (as on the projects page). Defaults to the project the user has open."}

TOOLS = [
    _tool("list_projects", "List the projects on this robot with their state (offline / idle / running …)."),
    _tool("list_project_files", "List a project's files (or one folder of it).",
          {"project": _P, "dir": {"type": "string", "description": "A folder inside the project, e.g. 'scene' or 'hmi'."}}),
    _tool("read_project_file", "Read a text file of a project (scene/*.j2, launch.yaml, recipes.j2, actions*.py, hmi/…). "
          "Lines are numbered in the output only for reference.",
          {"project": _P, "path": {"type": "string"},
           "offset": {"type": "integer", "description": "First line (1-based)."},
           "limit": {"type": "integer", "description": "Number of lines."}}, ["path"]),
    _tool("write_project_file", "Create or replace a file in a project. Prefer edit_project_file for changes to an existing file.",
          {"project": _P, "path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _tool("edit_project_file", "Replace an exact piece of text in a project file. old_text must match exactly and be "
          "unique unless replace_all is true.",
          {"project": _P, "path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"},
           "replace_all": {"type": "boolean"}}, ["path", "old_text", "new_text"]),
    _tool("get_scene", "The project's scene as its run builds it (launch.yaml's scene files rendered and merged): each "
          "component's type, simulation flag, connection settings and what it is attached to. full=true returns every setting.",
          {"project": _P, "full": {"type": "boolean"}, "component": {"type": "string", "description": "Only this component, in full."}}),
    _tool("check_scene", "A quick check of the project's scene and launch.yaml: unknown component types, attach parents "
          "that don't exist, attach loops, missing files, devices in simulation vs real. Run it after editing the scene.",
          {"project": _P}),
    _tool("list_catalog", "The component types this account can use, by category.",
          {"category": {"type": "string"}}),
    _tool("describe_component", "A component type's settings (with defaults), anchors and its source file (readable with read_docs).",
          {"type": {"type": "string"}}, ["type"]),
    _tool("runtime_status", "The project's live state: launched or not, run state, current step, errors, device summary.",
          {"project": _P}),
    _tool("runtime_logs", "The last lines of the project's console log (what the run printed).",
          {"project": _P, "tail": {"type": "integer", "description": "Lines (default 200, max 2000)."},
           "grep": {"type": "string", "description": "Only lines matching this regular expression."}}),
    _tool("runtime_command", "Control the project's run. launch = start the project's runtime process; start = begin a run "
          "(with the run parameters); pause / resume; park = finish safely and send the robot home; kill = stop the "
          "runtime process at once; relaunch = restart the runtime (after scene or HMI edits); replan / replan_cancel; "
          "speed (simulation only). launch, start, resume, park, kill and relaunch move or stop real hardware: the user "
          "approves them in the chat before they run (pause, replan and speed need no approval).",
          {"project": _P, "cmd": {"type": "string", "enum": ["launch", "start", "pause", "resume", "park", "kill",
                                                             "relaunch", "replan", "replan_cancel", "speed"]},
           "kwargs": {"type": "object", "description": "start only: run parameters for this run (default: the saved ones)."},
           "speed": {"type": "number", "description": "speed only: 0.25–16."}}, ["cmd"]),
    _tool("list_devices", "The running project's devices: state (ok / error / down), message, simulated or real, online.",
          {"project": _P}),
    _tool("device_command", "Recover a device that reports an error, or release a device's claim (the user approves "
          "it in the chat before it runs).",
          {"project": _P, "device": {"type": "string", "description": "The device id from list_devices."},
           "action": {"type": "string", "enum": ["recover", "release"]}}, ["device", "action"]),
    _tool("diagnose_run", "Everything at once about why a run paused, stopped or failed: the run state and its "
          "last error, the last steps (with their levels — the failed one), the operator notice the run is waiting "
          "on (its title, text and picture), the devices that are not ok, the log's last error lines, and the "
          "latest per-item records. Start here for any 'why did it stop / what is this error' question.",
          {"project": _P}),
    _tool("get_run_parameters", "The project's run parameters: the schema from launch.yaml, the saved values, and the "
          "values of the run in progress.", {"project": _P}),
    _tool("set_run_parameters", "Save run parameters for the next run (merged into the saved values). Refused while the "
          "project is launched.", {"project": _P, "values": {"type": "object"}}, ["values"]),
    _tool("read_hmi_spec", "The project's HMI: which files make the Main page (launch.yaml pendant:) and the Parameters "
          "page (setup:), the run-parameter schema file (default:), and what is in hmi/. Edit them with the file tools.",
          {"project": _P}),
    _tool("read_docs", "Read the Workspace's documentation and code: docs/*.md, .claude/skills/<skill>/SKILL.md, "
          "examples/<project>/…, scenes/, workspace/… (the platform source). A folder path lists it.",
          {"path": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}}, ["path"]),
    _tool("search_docs", "Search the Workspace's documentation and code for a regular expression.",
          {"pattern": {"type": "string"}, "path": {"type": "string", "description": "Limit to this folder, e.g. 'docs'."}},
          ["pattern"]),
    _tool("list_uploads", "The files the user attached in this conversation: id, name, kind, size, where it is saved "
          "(and its copy in the project's uploads/ folder)."),
    _tool("read_upload", "Read an attached file: a text file's lines, a PDF's text by page (first_page/last_page), "
          "what a zip / tar holds, a 3D model's size and parts, or a vision model's type.",
          {"upload": {"type": "string", "description": "The upload's id (or its file name)."},
           "first_page": {"type": "integer"}, "last_page": {"type": "integer"},
           "offset": {"type": "integer"}, "limit": {"type": "integer"},
           "grep": {"type": "string", "description": "A text file: only the lines matching this regular expression."}},
          ["upload"]),
    _tool("add_component_from_file", "Make an attached 3D model (.glb, .gltf or .stl) a component of the project: "
          "creates components/<type>/<type>.glb + <type>.py (+ __init__.py), with the model's measured bounding box "
          "as its first collision box and center / place / top anchors. Units: millimetres, Z up (the Workspace's "
          "convention) — say units='m' for a model exported in metres and y_up=true for a Y-up export. "
          "origin='base_center' moves the model so its footprint is centred on the origin and it stands on z=0. "
          "It does not place it in the scene: add a scene entry after (write-scene-yaml skill).",
          {"project": _P, "upload": {"type": "string", "description": "The upload's id (or its file name)."},
           "type": {"type": "string", "description": "The new component type: lowercase letters, digits and _ "
                                                     "(e.g. 'holder_falcon_6x'). The folder, file and @register name."},
           "units": {"type": "string", "enum": ["auto", "mm", "m", "cm", "in"],
                     "description": "The model's units (default auto: a model under 3 units across is taken as metres)."},
           "y_up": {"type": "boolean", "description": "The model was exported Y-up (glTF's default): turn it Z-up."},
           "origin": {"type": "string", "enum": ["keep", "base_center"]},
           "description": {"type": "string", "description": "One line on what it is, for the class docstring."},
           "overwrite": {"type": "boolean", "description": "Replace a project component of that type."}},
          ["upload", "type"]),
    _tool("install_vision_model", "Put an attached vision model (a dorna_vision .pkl from training, or .onnx / .pt …) "
          "in the project's vision/ folder, where a detection config names it (vision-guide §5 'Config files': "
          "detection.path is relative to the config's own folder). With config: point that config's detection.path "
          "at the model (the config is created when it does not exist; cmd then says the detection kind). Without "
          "config: copied only, and the configs in vision/ are listed.",
          {"project": _P, "upload": {"type": "string", "description": "The upload's id (or its file name)."},
           "config": {"type": "string", "description": "The detection config, e.g. 'vision/tube_od.yaml' (or 'tube_od')."},
           "cmd": {"type": "string", "enum": ["od", "rod", "cls", "kp", "anom"],
                   "description": "The detection kind, for a new config (read_upload tells a .pkl's own type)."},
           "conf": {"type": "number", "description": "A new config's confidence threshold (default 0.5)."},
           "name": {"type": "string", "description": "The model's file name in vision/ (default: the upload's)."}},
          ["upload"]),
]
TOOL_NAMES = {t["name"] for t in TOOLS}

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


def validate(name, args):
    """The tool input against its schema → an error string, or None."""
    spec = next((t for t in TOOLS if t["name"] == name), None)
    if spec is None:
        return f"unknown tool {name}"
    if not isinstance(args, dict):
        return "the input must be an object"
    sch = spec["input_schema"]
    for r in sch["required"]:
        if r not in args:
            return f"missing required input: {r}"
    for k, v in args.items():
        p = sch["properties"].get(k)
        if p is None:
            return f"unknown input: {k} (allowed: {', '.join(sch['properties']) or 'none'})"
        t = _TYPES[p["type"]]
        if not isinstance(v, t) or (p["type"] in ("integer", "number") and isinstance(v, bool)):
            return f"input {k} must be a {p['type']}"
        if "enum" in p and v not in p["enum"]:
            return f"input {k} must be one of {p['enum']}"
    return None


# ── helpers ──────────────────────────────────────────────────────────

def _local(method, path, body=None, timeout=60):
    """The GUI server's own routes, over localhost (same rules as the pages)."""
    port = getattr(ORCH, "_orch_port", None) or int(os.environ.get("PORT", "5000"))
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    tok = os.environ.get("ORCH_TOKEN", "").strip()
    if tok:
        req.add_header("X-Orch-Token", tok)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as e:
        raw, code = e.read(), e.code
    except OSError as e:
        raise ToolError(f"the Workspace server didn't answer: {e}")
    try:
        return code, json.loads(raw.decode() or "null")
    except ValueError:
        return code, raw.decode(errors="replace")


def _q(s):
    return urllib.parse.quote(str(s), safe="")


def _name(session, args):
    name = (args.get("project") or session.project or "").strip()
    if not name:
        raise ToolError("no project: name one (list_projects shows them) — the user has none open")
    if ORCH is not None and name not in ORCH.workspaces:
        raise ToolError(f"no project named {name!r} — list_projects shows the projects on this robot")
    return name


def project_dir(name):
    ws = ORCH.workspaces.get(name) if ORCH is not None else None
    if ws is None:
        raise ToolError(f"no project named {name!r}")
    if ws.is_remote():
        raise ToolError(f"{name} runs on another unit — its files aren't on this one")
    p = Path(ws.path_to_file)
    return (p.parent if p.suffix else p).resolve()


def _inside(base, rel):
    rel = str(rel or "").strip().lstrip("/")
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise ToolError(f"{rel}: outside the project")
    if ".git" in target.relative_to(base).parts:
        raise ToolError("the .git folder is not editable")
    return target


def _read_text(path, offset=None, limit=None):
    if not path.is_file():
        raise ToolError(f"no such file: {path.name}")
    if path.stat().st_size > MAX_FILE_BYTES and not limit:
        raise ToolError(f"{path.name} is large ({path.stat().st_size} bytes): read it in parts with offset/limit")
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError as e:
        raise ToolError(str(e))
    start = max(1, int(offset or 1))
    end = len(lines) if not limit else min(len(lines), start - 1 + int(limit))
    body = "\n".join(f"{i:>5}  {lines[i - 1]}" for i in range(start, end + 1))
    more = f"\n… lines {end + 1}–{len(lines)} not shown" if end < len(lines) else ""
    return f"{path.name} ({len(lines)} lines)\n{body}{more}"


def _tree(base, rel="", limit=400):
    root = _inside(base, rel) if rel else base
    if not root.is_dir():
        raise ToolError(f"no such folder: {rel}")
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") or d in (".claude", ".workspace"))
        for f in sorted(files):
            p = Path(dirpath) / f
            out.append(f"{p.relative_to(base)}  ({p.stat().st_size} B)")
            if len(out) >= limit:
                out.append("… more files not listed — name a folder")
                return "\n".join(out)
    return "\n".join(out) or "(empty)"


def _ui_for(path_rel, project):
    parts = Path(path_rel).parts
    first = parts[0] if parts else ""
    if first == "hmi" or path_rel.endswith((".js", ".html", ".css")) and first != "scene":
        return {"type": "reload", "what": "hmi", "project": project}
    if first in ("scene", "components") or path_rel in ("launch.yaml",) or path_rel.startswith("recipes"):
        return {"type": "reload", "what": "scene", "project": project}
    return {"type": "reload", "what": "files", "project": project}


def _launch(base):
    f = base / "launch.yaml"
    if not f.is_file():
        raise ToolError("the project has no launch.yaml")
    try:
        return yaml.safe_load(f.read_text()) or {}
    except yaml.YAMLError as e:
        raise ToolError(f"launch.yaml doesn't parse: {e}")


def _scene(name):
    try:
        return ORCH.workspaces[name].scene()
    except Exception as e:
        raise ToolError(f"the scene doesn't build: {type(e).__name__}: {e}")


def _component_map():
    try:
        import gui.scene_builder.server as sb
        return {**sb.COMPONENT_MAP, **getattr(sb, "_project_component_map", {})}
    except Exception:
        return {}


def _allowed():
    from gui.account import account
    return account.allowed_types()


def _attach_parent(cfg):
    a = cfg.get("attach") if isinstance(cfg, dict) else None
    return a.get("parent_name") if isinstance(a, dict) else None


CONNECTION_KEYS = ("ip", "port", "serial_number", "server", "baud", "rail_cfg", "camera_cfg")


def _summary(scene):
    rows = []
    for n, c in scene.items():
        if not isinstance(c, dict):
            continue
        bits = [f"{n}: {c.get('type', '?')}"]
        if "simulation" in c:
            bits.append("sim" if c.get("simulation") else "REAL")
        for k in CONNECTION_KEYS:
            if c.get(k) not in (None, "", {}):
                bits.append(f"{k}={json.dumps(c[k], default=str)}")
        p = _attach_parent(c)
        if p:
            bits.append(f"on {p}")
        rows.append("  ".join(bits))
    return f"{len(rows)} components\n" + "\n".join(rows)


def _doc_path(rel):
    rel = str(rel or "").strip().lstrip("/")
    target = (REPO_ROOT / rel).resolve()
    ok = any(target == (REPO_ROOT / r).resolve() or (REPO_ROOT / r).resolve() in target.parents for r in DOC_ROOTS)
    if not ok or any(target == (REPO_ROOT / h).resolve() or (REPO_ROOT / h).resolve() in target.parents for h in DOC_HIDDEN):
        raise ToolError(f"{rel}: not part of the Workspace documentation (try docs/, .claude/skills/, examples/, workspace/)")
    return target


# ── the tools ────────────────────────────────────────────────────────

def t_list_projects(session, a):
    code, j = _local("GET", "/orchestrator/api/workspaces/status", timeout=20)
    statuses = (j or {}).get("statuses", {}) if isinstance(j, dict) else {}
    out = []
    for name, ws in (ORCH.workspaces.items() if ORCH else []):
        st = statuses.get(name) or {}
        out.append(f"{name}{' (' + ws.label + ')' if ws.label else ''}: {st.get('state', '?')}"
                   f"{' — ' + str(st.get('last_error'))[:160] if st.get('last_error') else ''}")
    return "\n".join(out) or "no projects on this robot yet", []


def t_list_project_files(session, a):
    name = _name(session, a)
    return _tree(project_dir(name), a.get("dir") or ""), []


def t_read_project_file(session, a):
    base = project_dir(_name(session, a))
    return _read_text(_inside(base, a["path"]), a.get("offset"), a.get("limit")), []


def t_write_project_file(session, a):
    name = _name(session, a)
    base = project_dir(name)
    target = _inside(base, a["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.is_file()
    target.write_text(a["content"])
    rel = str(target.relative_to(base))
    return f"{'replaced' if existed else 'created'} {rel} ({len(a['content'])} chars)", [_ui_for(rel, name)]


def t_edit_project_file(session, a):
    name = _name(session, a)
    base = project_dir(name)
    target = _inside(base, a["path"])
    if not target.is_file():
        raise ToolError(f"no such file: {a['path']}")
    text = target.read_text()
    n = text.count(a["old_text"])
    if n == 0:
        raise ToolError("old_text not found — read the file again and copy the text exactly")
    if n > 1 and not a.get("replace_all"):
        raise ToolError(f"old_text appears {n} times — give more context or set replace_all")
    text = text.replace(a["old_text"], a["new_text"]) if a.get("replace_all") else text.replace(a["old_text"], a["new_text"], 1)
    target.write_text(text)
    rel = str(target.relative_to(base))
    return f"edited {rel} ({n if a.get('replace_all') else 1} change{'s' if a.get('replace_all') and n > 1 else ''})", [_ui_for(rel, name)]


def t_get_scene(session, a):
    scene = _scene(_name(session, a))
    if a.get("component"):
        c = scene.get(a["component"])
        if c is None:
            raise ToolError(f"no component {a['component']!r} in the scene")
        return json.dumps({a["component"]: c}, indent=1, default=str), []
    if a.get("full"):
        return json.dumps(scene, indent=1, default=str), []
    return _summary(scene), []


def t_check_scene(session, a):
    name = _name(session, a)
    base = project_dir(name)
    launch = _launch(base)
    problems, notes = [], []
    from workspace.scene import scene_paths
    for p in scene_paths(base, launch):
        if not p.is_file():
            problems.append(f"launch.yaml scene: {p.relative_to(base)} does not exist")
    for key in ("default", "setup", "pendant", "replan"):
        rel = launch.get(key)
        if isinstance(rel, str) and rel and not (base / rel).is_file():
            problems.append(f"launch.yaml {key}: {rel} does not exist")
    if problems:
        return "✗ " + "\n✗ ".join(problems), []
    scene = _scene(name)
    known = _component_map()
    allowed = _allowed()
    sims, reals = [], []
    for n, c in scene.items():
        if not isinstance(c, dict):
            problems.append(f"{n}: not a mapping")
            continue
        t = c.get("type")
        if not t:
            problems.append(f"{n}: no type")
        elif known and t not in known:
            problems.append(f"{n}: unknown component type {t!r}")
        elif allowed is not None and t not in allowed:
            notes.append(t)
        p = _attach_parent(c)
        if p and p not in scene:
            problems.append(f"{n}: attached to {p!r}, which is not in the scene")
        (sims if c.get("simulation", True) else reals).append(n)
    for n in scene:                                   # attach loops
        seen, cur = set(), n
        while cur in scene and cur not in seen:
            seen.add(cur)
            cur = _attach_parent(scene[cur])
        if cur in seen and cur is not None:
            problems.append(f"{n}: attach chain loops back to {cur}")
            break
    out = [f"{len(scene)} components; {len(sims)} simulated; real (claim hardware): {', '.join(reals) or 'none'}"]
    out += ["✗ " + p for p in problems]
    if notes:
        from collections import Counter
        out.append("! types not among this account's components (fine when they came with the project): "
                   + ", ".join(f"{t} ×{k}" if k > 1 else t for t, k in Counter(notes).items()))
    out.append("✓ scene OK" if not problems else "scene check FAILED")
    return "\n".join(out), []


def t_list_catalog(session, a):
    code, j = _local("GET", "/scene-builder/api/categories", timeout=20)
    cats = (j or {}).get("categories", []) if isinstance(j, dict) else []
    allowed = _allowed()
    want = (a.get("category") or "").lower().strip()
    out = []
    for c in cats:
        if want and want not in c.get("name", "").lower():
            continue
        items = [i if isinstance(i, str) else (i.get("type") or i.get("name")) for i in c.get("items", [])]
        items = [i for i in items if i and (allowed is None or i in allowed)]
        if items:
            out.append(f"{c.get('name')}: {', '.join(items)}")
    if not out:
        return "no components available to this account" + (f" in {want!r}" if want else ""), []
    return "\n".join(out), []


def t_describe_component(session, a):
    t = a["type"]
    allowed = _allowed()
    if allowed is not None and t not in allowed:
        raise ToolError(f"{t} is not among this account's components (list_catalog shows them)")
    code, j = _local("GET", "/scene-builder/api/type_meta?type=" + _q(t), timeout=20)
    if code != 200 or not isinstance(j, dict) or not j.get("ok"):
        raise ToolError((j or {}).get("error") if isinstance(j, dict) else f"unknown type: {t}")
    meta = j.get("meta") or {}
    fp = _component_map().get(t)
    doc = ""
    if fp:
        try:
            mod = ast.parse(Path(fp).read_text())
            classes = [n for n in mod.body if isinstance(n, ast.ClassDef)]

            def registered(c):          # @register("<type>")
                return any(isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)
                           and d.args[0].value == t for d in c.decorator_list)
            flat = t.replace("_", "").lower()
            cls = next((c for c in classes if registered(c)), None) \
                or next((c for c in classes if c.name.lower() == flat), None)
            doc = (ast.get_docstring(cls) if cls else None) or ast.get_docstring(mod) or ""
        except Exception:
            pass
        try:
            fp = str(Path(fp).resolve().relative_to(REPO_ROOT))
        except ValueError:
            pass
    opts = "\n".join(f"  {o.get('name')} ({o.get('kind')}) default {json.dumps(o.get('default'), default=str)}"
                     for o in meta.get("options") or [])
    anchors = meta.get("anchors")
    anchors = ", ".join(anchors) if isinstance(anchors, list) else json.dumps(anchors, default=str)[:1500]
    return (f"{t}\nsource: {fp or '?'}\n{doc[:2500]}\nsettings:\n{opts or '  (none found)'}\nanchors: {anchors}"), []


def t_runtime_status(session, a):
    name = _name(session, a)
    code, j = _local("GET", f"/orchestrator/api/workspace/{_q(name)}/status", timeout=20)
    if code != 200:
        raise ToolError((j or {}).get("error") if isinstance(j, dict) else f"status failed ({code})")
    return json.dumps(j, indent=1, default=str), []


def t_runtime_logs(session, a):
    name = _name(session, a)
    tail = max(1, min(2000, int(a.get("tail") or 200)))
    code, j = _local("GET", f"/orchestrator/api/workspace/{_q(name)}/logs?tail={tail}", timeout=20)
    if code != 200 or not isinstance(j, dict):
        raise ToolError((j or {}).get("error") if isinstance(j, dict) else f"logs failed ({code})")
    lines = j.get("lines")
    if lines is None:
        lines = (j.get("logs") or j.get("text") or "")
    if isinstance(lines, str):
        lines = lines.splitlines()
    lines = [l if isinstance(l, str) else json.dumps(l, default=str) for l in lines]
    if a.get("grep"):
        try:
            rx = re.compile(a["grep"])
        except re.error as e:
            raise ToolError(f"bad pattern: {e}")
        lines = [l for l in lines if rx.search(l)]
    return "\n".join(lines[-tail:]) or "(the log is empty)", []


def t_runtime_command(session, a):
    name = _name(session, a)
    body = {"cmd": a["cmd"]}
    if a["cmd"] == "start" and a.get("kwargs") is not None:
        body["kwargs"] = a["kwargs"]
    if a["cmd"] == "speed":
        if a.get("speed") is None:
            raise ToolError("speed needs speed (0.25–16)")
        body["speed"] = a["speed"]
    if a["cmd"] == "relaunch":
        body["cmd"] = "relaunch"
    code, j = _local("POST", f"/orchestrator/api/workspace/{_q(name)}/cmd", body, timeout=120)
    if code != 200:
        raise ToolError(f"{a['cmd']} refused: " + ((j or {}).get("error") if isinstance(j, dict) else str(j))[:600])
    return f"{a['cmd']}: " + json.dumps(j, default=str)[:3000], [{"type": "status", "project": name}]


def t_list_devices(session, a):
    name = _name(session, a)
    code, j = _local("GET", f"/orchestrator/api/workspace/{_q(name)}/devices", timeout=20)
    devs = (j or {}).get("devices", []) if isinstance(j, dict) else []
    if not devs:
        return "no devices reported (is the project launched?)" + (f" — {j.get('error')}" if isinstance(j, dict) and j.get("error") else ""), []
    rows = []
    for d in devs:
        rows.append(f"{d.get('id')}: {d.get('state')}{' — ' + str(d.get('msg')) if d.get('msg') else ''}"
                    f"  [{d.get('claim') or ('sim' if d.get('sim') else 'real')}{', offline' if d.get('online') is False else ''}]")
    return "\n".join(rows), []


def t_device_command(session, a):
    name = _name(session, a)
    code, j = _local("POST", f"/orchestrator/api/workspace/{_q(name)}/devices/{_q(a['device'])}/{a['action']}", {}, timeout=40)
    if code != 200:
        raise ToolError(f"{a['action']} {a['device']} refused ({code}): " + json.dumps(j, default=str)[:400])
    return json.dumps(j, default=str)[:2000], [{"type": "status", "project": name}]


def _runtime_port(name):
    ws = ORCH.workspaces.get(name) if ORCH is not None else None
    if ws is None or ws.is_remote() or not ORCH.is_launched(name):
        return None
    return ws.port


def _runtime_get(port, path, timeout=8):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type", "")
    except (OSError, urllib.error.URLError):
        return None, ""


def _op_values(port):
    """The runtime's operator values (rt.op: the notice …) — one snapshot
    from its /ws/op socket."""
    try:
        from websocket import create_connection
        ws = create_connection(f"ws://127.0.0.1:{port}/ws/op", timeout=4)
        try:
            return (json.loads(ws.recv()) or {}).get("set") or {}
        finally:
            ws.close()
    except Exception:
        return {}


ERR_RX = re.compile(r"Traceback|Error|Exception|FAILED|✗|fault|refused|timed out|timeout", re.I)


def t_diagnose_run(session, a):
    from .images import Shot, decode, encode
    name = _name(session, a)
    out, shots = [], []
    code, st = _local("GET", f"/orchestrator/api/workspace/{_q(name)}/status", timeout=20)
    st = st if isinstance(st, dict) else {}
    out.append(f"state: {st.get('state', '?')}" + (f" — last error: {st.get('last_error')}" if st.get("last_error") else ""))
    step = st.get("step") or {}
    steps = step.get("steps") or []
    if steps:
        out.append("last steps (oldest first; level in brackets):")
        for s_ in steps[-12:]:
            if isinstance(s_, dict):
                out.append(f"  [{s_.get('level', 'info')}] {s_.get('label', '')}")
            else:
                out.append(f"  {s_}")
        bad = [x for x in steps if isinstance(x, dict) and x.get("level") in ("error", "warning")]
        if bad:
            out.append(f"the last problem step: [{bad[-1].get('level')}] {bad[-1].get('label')}")
    port = _runtime_port(name)
    if port is None:
        out.append("the project is not launched here: no live notice, devices or records")
    else:
        ops = _op_values(port)
        n = ops.get("notice")
        if isinstance(n, dict):
            out.append(f"operator notice (the run waits on it): [{n.get('level')}] {n.get('title')} — {n.get('text')}"
                       + (f" (caption: {n.get('caption')})" if n.get("caption") else ""))
            if n.get("img"):
                raw, ctype = _runtime_get(port, n["img"])
                if raw:
                    try:
                        shots.append(Shot(encode(decode(raw)), f"The notice's picture — {n.get('title') or ''}"))
                    except Exception:
                        out.append("(the notice's picture didn't decode)")
        else:
            out.append("no operator notice pending")
        code, j = _local("GET", f"/orchestrator/api/workspace/{_q(name)}/devices", timeout=20)
        devs = (j or {}).get("devices", []) if isinstance(j, dict) else []
        notok = [d for d in devs if d.get("state") not in ("ok", None) or d.get("online") is False]
        out.append("devices not ok: " + ("; ".join(f"{d.get('id')}: {d.get('state')}"
                                                     f"{' — ' + str(d.get('msg')) if d.get('msg') else ''}"
                                                     f"{' (offline)' if d.get('online') is False else ''}" for d in notok)
                                         if notok else f"none (of {len(devs)})"))
        raw, _ = _runtime_get(port, "/records")
        try:
            rec = json.loads(raw) if raw else {}
        except ValueError:
            rec = {}
        rows = rec.get("records")
        if isinstance(rows, dict):
            rows = [{"key": k, **(v if isinstance(v, dict) else {"value": v})} for k, v in rows.items()]
        if rows:
            out.append(f"latest records ({len(rows)} in this run; the last 5):")
            out.extend("  " + json.dumps(r, default=str)[:600] for r in rows[-5:])
    code, j = _local("GET", f"/orchestrator/api/workspace/{_q(name)}/logs?tail=400", timeout=20)
    lines = (j or {}).get("lines") if isinstance(j, dict) else None
    if isinstance(lines, str):
        lines = lines.splitlines()
    lines = [l if isinstance(l, str) else json.dumps(l, default=str) for l in (lines or [])]
    hits = [i for i, l in enumerate(lines) if ERR_RX.search(l)]
    if hits:
        last = hits[-1]
        start = max(0, (hits[-8] if len(hits) >= 8 else hits[0]) - 2, last - 40)
        out.append("the log around its last errors:\n" + "\n".join(lines[start:last + 6]))
    else:
        out.append("no error lines in the log's last 400 lines")
    out.append("Next: runtime_logs (grep) for more, read the action that failed in the project's files, "
               "camera_view if a camera check failed.")
    text = "\n".join(out)
    return ([text, *shots] if shots else text), []


def t_get_run_parameters(session, a):
    name = _name(session, a)
    code, j = _local("GET", f"/orchestrator/api/workspace/{_q(name)}/launch_config", timeout=20)
    if code != 200:
        raise ToolError((j or {}).get("error") if isinstance(j, dict) else f"failed ({code})")
    return json.dumps(j, indent=1, default=str), []


def t_set_run_parameters(session, a):
    name = _name(session, a)
    code, j = _local("GET", f"/orchestrator/api/workspace/{_q(name)}/launch_config", timeout=20)
    cur = (j or {}).get("kwargs_values") or {} if isinstance(j, dict) else {}
    schema = (j or {}).get("kwargs_schema") or {} if isinstance(j, dict) else {}
    unknown = [k for k in a["values"] if schema and k not in schema]
    if unknown:
        raise ToolError(f"not run parameters of this project: {', '.join(unknown)} (known: {', '.join(k for k in schema if not k.startswith('_'))})")
    merged = {**cur, **a["values"]}
    code, j = _local("POST", f"/orchestrator/api/workspace/{_q(name)}/kwargs", {"kwargs_values": merged}, timeout=20)
    if code != 200:
        raise ToolError((j or {}).get("error") if isinstance(j, dict) else f"failed ({code})")
    return "saved: " + json.dumps(merged, default=str)[:2000], [{"type": "reload", "what": "parameters", "project": name}]


def t_read_hmi_spec(session, a):
    name = _name(session, a)
    base = project_dir(name)
    launch = _launch(base)
    out = ["HMI pages (launch.yaml):"]
    for key, what in (("pendant", "Main page (shown during the run)"), ("setup", "Parameters page (before the run)"),
                      ("default", "run-parameter schema"), ("replan", "Replan view")):
        rel = launch.get(key)
        if rel and not isinstance(rel, str):
            out.append(f"  {key}: (inline in launch.yaml) — {what}")
        elif rel:
            exists = (base / str(rel)).is_file()
            out.append(f"  {key}: {rel} — {what}{'' if exists else ' — MISSING'}")
        else:
            out.append(f"  {key}: (none — the platform default) — {what}")
    if "pendant_hero" in launch:
        out.append(f"  pendant_hero: {launch['pendant_hero']}")
    hmi = base / "hmi"
    out.append("hmi/:\n" + (_tree(base, "hmi") if hmi.is_dir() else "  (no hmi folder)"))
    out.append("The contract for these files: docs/hmi-guide.md (§0 keys, §4b screens) and docs/hmi-kit-guide.md; "
               "the skill .claude/skills/project-ui/SKILL.md. A Main page edit shows after a relaunch.")
    return "\n".join(out), []


def t_read_docs(session, a):
    target = _doc_path(a["path"])
    if target.is_dir():
        out = []
        for p in sorted(target.iterdir()):
            if p.name in SKIP_DIRS or (p.name.startswith(".") and p.name != ".claude"):
                continue
            rel = str(p.relative_to(REPO_ROOT))
            if any(rel.startswith(h) for h in DOC_HIDDEN):
                continue
            out.append(rel + ("/" if p.is_dir() else ""))
        return "\n".join(out) or "(empty)", []
    return _read_text(target, a.get("offset"), a.get("limit")), []


def t_search_docs(session, a):
    try:
        rx = re.compile(a["pattern"], re.I)
    except re.error as e:
        raise ToolError(f"bad pattern: {e}")
    roots = [_doc_path(a["path"])] if a.get("path") else [REPO_ROOT / r for r in DOC_ROOTS]
    hits = []
    for root in roots:
        files = [root] if root.is_file() else (Path(dp) / f for dp, ds, fs in os.walk(root)
                                               if not any(s in Path(dp).parts for s in SKIP_DIRS) for f in fs)
        for f in files:
            rel = str(f.relative_to(REPO_ROOT))
            if any(rel.startswith(h) for h in DOC_HIDDEN) or f.suffix not in TEXT_SUFFIXES:
                continue
            try:
                if f.stat().st_size > MAX_FILE_BYTES:
                    continue
                for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                        if len(hits) >= 80:
                            return "\n".join(hits) + "\n… more matches — narrow the pattern or the path", []
            except OSError:
                continue
    return "\n".join(hits) or "no matches", []


# ── uploads ──────────────────────────────────────────────────────────

def _upload(session, ref):
    from . import uploads
    rec = uploads.find(session.id, ref)
    if rec is None:
        raise ToolError(f"no attached file {ref!r} in this conversation — list_uploads shows them")
    if not Path(rec["path"]).is_file():
        raise ToolError(f"{rec['name']}: the file is gone from the upload store")
    return rec


def t_list_uploads(session, a):
    from . import uploads
    recs = uploads.records(session.id)
    if not recs:
        return "no files attached in this conversation", []
    return "\n".join(uploads.describe(r) for r in recs), []


def _pkl_meta(path):
    """A dorna_vision model pickle's meta, read WITHOUT running it: only
    plain data (dicts, lists, strings, bytes, numbers) is accepted — any
    class in the pickle stops the read."""
    import pickle

    class Plain(pickle.Unpickler):
        def find_class(self, module, name):
            raise pickle.UnpicklingError(f"{module}.{name}")
    try:
        with open(path, "rb") as f:
            d = Plain(f).load()
    except Exception as e:
        return None, f"not plain data ({type(e).__name__}: {str(e)[:80]})"
    if not isinstance(d, dict):
        return None, "not a dorna_vision model dict"
    meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
    return meta, "keys: " + ", ".join(sorted(str(k) for k in d)[:20])


def _sidecar(session, rec):
    """A .gltf's external buffer, from another upload of this conversation."""
    from . import uploads

    def get(uri):
        name = os.path.basename(urllib.parse.unquote(uri))
        other = uploads.find(session.id, name) or uploads.find(session.id, uploads.safe_name(name))
        if other and Path(other["path"]).is_file():
            return Path(other["path"]).read_bytes()
        return None
    return get


def t_read_upload(session, a):
    from . import uploads
    rec = _upload(session, a["upload"])
    path = Path(rec["path"])
    head = uploads.describe(rec)
    kind, ext, low = rec["kind"], path.suffix.lower(), path.name.lower()
    if kind == "pdf":
        first = max(1, int(a.get("first_page") or 1))
        last = int(a.get("last_page") or first + 19)
        txt = uploads.pdf_text(path, first, last)
        if txt is None:
            raise ToolError("the PDF's text could not be read on this robot (it was shown to you as a document "
                            "when attached)")
        return f"{head}\n{txt}", []
    if ext == ".zip":
        import zipfile
        try:
            with zipfile.ZipFile(path) as z:
                names = [f"{i.filename}  ({i.file_size} B)" for i in z.infolist()[:400]]
        except zipfile.BadZipFile:
            raise ToolError("not a valid zip file")
        return head + "\n" + "\n".join(names), []
    if low.endswith((".tar", ".tgz", ".tar.gz")):
        import tarfile
        try:
            with tarfile.open(path) as t:
                names = [f"{m.name}  ({m.size} B)" for m in t.getmembers()[:400]]
        except tarfile.TarError:
            raise ToolError("not a valid tar file")
        return head + "\n" + "\n".join(names), []
    if ext in (".glb", ".gltf"):
        from . import gltf
        try:
            js, _ = gltf.load(path, _sidecar(session, rec))
        except gltf.GltfError as e:
            raise ToolError(str(e))
        bb = gltf.bbox(js)
        size = [round(bb[1][k] - bb[0][k], 3) for k in range(3)] if bb else None
        mats = [str(m.get("name")) for m in js.get("materials") or []]
        return (f"{head}\nnodes {len(js.get('nodes') or [])}, meshes {len(js.get('meshes') or [])}, "
                f"materials: {', '.join(mats[:30]) or 'none'}; extensions: {', '.join(js.get('extensionsUsed') or []) or 'none'}\n"
                f"bounding box (file units): min {bb and [round(v, 3) for v in bb[0]]} "
                f"max {bb and [round(v, 3) for v in bb[1]]} — size {size}"), []
    if ext == ".pkl":
        meta, note = _pkl_meta(path)
        shown = json.dumps(meta, default=str)[:1500] if meta is not None else "unknown"
        return f"{head}\nvision model meta: {shown} ({note})", []
    if kind == "text":
        if a.get("grep"):
            try:
                rx = re.compile(a["grep"], re.I)
            except re.error as e:
                raise ToolError(f"bad pattern: {e}")
            hits = [f"{i:>6}  {l}" for i, l in enumerate(path.read_text(errors="replace").splitlines(), 1) if rx.search(l)]
            more = f"\n… {len(hits) - 400} more matches" if len(hits) > 400 else ""
            return f"{head}\n{len(hits)} matching lines\n" + "\n".join(hits[:400]) + more, []
        return _read_text(path, a.get("offset"), a.get("limit")), []
    return f"{head}\n(a binary file — nothing to read as text)", []


UNIT_SCALE = {"mm": 1.0, "m": 1000.0, "cm": 10.0, "in": 25.4}

COMPONENT_PY = '''"""{doc}

Made from the uploaded model {src} by the Workspace assistant. The first
collision box is the model's measured bounding box ({sx} x {sy} x {sz} mm);
refine the anchors and boxes to the real part (docs/component-guide.md
sections 4-5).
"""
from copy import deepcopy

from dorna2 import Solid
from mergedeep import merge

from workspace.components.factory import register


@register("{type}")
class {cls}:
    DEFAULTS = dict(
        anchors={{
            "body": {{
                "center": [0, 0, 0, 0, 0, 0],
                "place": [{px}, {py}, {top}, 0, 0, 0],
                "top": [{px}, {py}, {top}, 0, 0, 0],
            }}
        }},
        collision_box={{
            "body": [
                {{"pose": [{cx}, {cy}, {cz}, 0, 0, 0], "scale": [{sx}, {sy}, {sz}]}},
            ]
        }},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", cfg.get("type")))

        self.name = name
        self.workspace = workspace
        self.type = prm.get("type")

        self.assembly = {{
            k: Solid(
                type=self.type,
                anchors=prm["anchors"][k],
                component=self.name,
                **({{"collision_box": cb[k]}} if (cb := prm.get("collision_box")) and k in cb else {{}})
            ) for k in prm["anchors"]
        }}
'''


def _r(v):
    v = round(float(v), 1)
    return int(v) if v == int(v) else v


def _model_json(session, rec):
    """(gltf json, bin) of an uploaded .glb / .gltf / .stl."""
    from . import gltf
    src = Path(rec["path"])
    ext = src.suffix.lower()
    if ext in (".glb", ".gltf"):
        try:
            return gltf.load(src, _sidecar(session, rec))
        except gltf.GltfError as e:
            raise ToolError(str(e))
    if ext in (".stl", ".obj", ".3mf"):
        try:
            import trimesh
        except ImportError:
            raise ToolError(f"{ext} files can't be converted on this robot — export the model as .glb")
        tmp = src.with_name(src.stem + ".conv.glb")
        try:
            trimesh.load(str(src), force="mesh").export(str(tmp))
            return gltf.load(tmp)
        except Exception as e:
            raise ToolError(f"the {ext} file could not be converted: {type(e).__name__}: {e}")
        finally:
            tmp.unlink(missing_ok=True)
    if ext in (".step", ".stp", ".iges", ".igs"):
        raise ToolError("STEP / IGES can't be converted on the robot — export the part as .glb (or .stl) from the "
                        "CAD program and attach that")
    raise ToolError(f"{rec['name']} is not a 3D model (.glb, .gltf or .stl)")


def t_add_component_from_file(session, a):
    from . import gltf
    name = _name(session, a)
    base = project_dir(name)
    rec = _upload(session, a["upload"])
    typ = str(a["type"]).strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,60}", typ):
        raise ToolError("type: lowercase letters, digits and _ only, starting with a letter (e.g. holder_falcon_6x)")
    try:
        import gui.scene_builder.server as sb
        library = sb.COMPONENT_MAP
    except Exception:
        library = {}
    if typ in library:
        raise ToolError(f"{typ} is a library component type already — pick another name")
    folder = base / "components" / typ
    if folder.exists() and not a.get("overwrite"):
        raise ToolError(f"the project already has a component {typ} (components/{typ}/) — pick another name or set overwrite")
    js, binb = _model_json(session, rec)
    bb = gltf.bbox(js)
    if bb is None:
        raise ToolError("the model has no mesh with a known size (no POSITION min/max)")
    extent = max(bb[1][k] - bb[0][k] for k in range(3))
    units = a.get("units") or "auto"
    if units == "auto":
        units = "m" if extent < 3 else "mm"
    scale = UNIT_SCALE[units]
    y_up = bool(a.get("y_up"))
    origin = a.get("origin") or "keep"
    notes = []
    if scale != 1 or y_up or origin == "base_center":
        off = (0.0, 0.0, 0.0)
        if origin == "base_center":
            b2 = gltf.bbox(gltf.wrap(json.loads(json.dumps(js)), scale, y_up))
            off = (-(b2[0][0] + b2[1][0]) / 2, -(b2[0][1] + b2[1][1]) / 2, -b2[0][2])
        js = gltf.wrap(js, scale, y_up, off)
        bb = gltf.bbox(js)
        if scale != 1:
            notes.append(f"scaled x{scale:g} ({units} -> mm)")
        if y_up:
            notes.append("turned Y-up -> Z-up")
        if origin == "base_center":
            notes.append("moved so its footprint is centred on the origin, standing on z=0")
    lo, hi = bb
    size = [hi[k] - lo[k] for k in range(3)]
    if max(size) > 3000:
        notes.append(f"! {max(size):.0f} mm across — check the units")
    if not (lo[0] <= 1 and hi[0] >= -1 and lo[1] <= 1 and hi[1] >= -1 and lo[2] <= 1 and hi[2] >= -1):
        notes.append("! the origin is outside the model — its center anchor sits away from the part "
                     "(origin='base_center' recentres it)")
    folder.mkdir(parents=True, exist_ok=True)
    (base / "components" / "__init__.py").touch()
    (folder / "__init__.py").touch()
    glb = folder / f"{typ}.glb"
    gltf.save_glb(js, binb, glb)
    (folder / f"{typ}.glb.bin").unlink(missing_ok=True)
    cls = "".join(w.capitalize() for w in typ.split("_"))
    doc = (a.get("description") or f"{typ} — a custom component").strip().replace('"""', "'''")
    doc = (doc.splitlines() or [typ])[0][:200]
    c = [_r((lo[k] + hi[k]) / 2) for k in range(3)]
    sz = [_r(v) for v in size]
    (folder / f"{typ}.py").write_text(COMPONENT_PY.format(
        doc=doc, src=rec["name"], type=typ, cls=cls, cx=c[0], cy=c[1], cz=c[2], sx=sz[0], sy=sz[1], sz=sz[2],
        px=c[0], py=c[1], top=_r(hi[2])))
    rel = f"components/{typ}"
    text = (f"created {rel}/{typ}.py, {rel}/{typ}.glb ({glb.stat().st_size} B) and {rel}/__init__.py\n"
            f"bounding box (mm): min {[_r(v) for v in lo]} max {[_r(v) for v in hi]} — size {sz}\n"
            f"collision box: pose [{c[0]}, {c[1]}, {c[2]}, 0, 0, 0] scale {sz}; anchors center, place/top at z={_r(hi[2])}\n"
            + ("\n".join(notes) + "\n" if notes else "")
            + f"Next: a scene entry with type: \"{typ}\" (it shows in the Scene builder once the project reloads).")
    return text, [_ui_for(f"{rel}/{typ}.py", name)]


def t_install_vision_model(session, a):
    import shutil
    from . import uploads
    name = _name(session, a)
    base = project_dir(name)
    rec = _upload(session, a["upload"])
    if rec["kind"] != "vision_model":
        raise ToolError(f"{rec['name']} is not a vision model file (.pkl, .onnx, .pt, .engine, .blob …)")
    vdir = base / "vision"
    vdir.mkdir(exist_ok=True)
    ext = Path(rec["file"]).suffix.lower()
    fname = uploads.safe_name(a.get("name") or rec["file"])
    if Path(fname).suffix.lower() != ext:
        fname += ext
    dest = vdir / fname
    replaced = dest.exists()
    shutil.copyfile(rec["path"], dest)
    out = [f"{'replaced' if replaced else 'copied'} vision/{fname} ({uploads.human_size(rec['size'])})"]
    meta = None
    if ext == ".pkl":
        meta, _ = _pkl_meta(dest)
        if meta:
            out.append(f"model meta: {json.dumps(meta, default=str)[:600]}")
    else:
        out.append("! dorna_vision's od / rod / cls / kp / anom detections load the .pkl their training exports; "
                   "this file is kept in vision/, but a detection may need that .pkl instead")
    events = [_ui_for(f"vision/{fname}", name)]
    cfg = (a.get("config") or "").strip()
    if cfg:
        if "/" not in cfg:
            cfg = "vision/" + cfg
        if not cfg.endswith((".yaml", ".yml")):
            cfg += ".yaml"
        cpath = _inside(base, cfg)
        rel_model = os.path.relpath(dest, cpath.parent)
        if cpath.is_file():
            try:
                data = yaml.safe_load(cpath.read_text()) or {}
            except yaml.YAMLError as e:
                raise ToolError(f"{cfg} doesn't parse: {e}")
            if not isinstance(data, dict):
                raise ToolError(f"{cfg} is not a mapping")
            det = data.get("detection")
            if not isinstance(det, dict):
                det = data["detection"] = {}
            old = det.get("path")
            det["path"] = rel_model
            if a.get("cmd"):
                det["cmd"] = a["cmd"]
            cpath.write_text(yaml.safe_dump(data, sort_keys=False))
            out.append(f"{cfg}: detection.path {old!r} -> {rel_model!r}")
        else:
            cmd = a.get("cmd") or str((meta or {}).get("type") or "").lower() or None
            if cmd not in ("od", "rod", "cls", "kp", "anom"):
                raise ToolError(f"vision/{fname} is copied; {cfg} does not exist yet — say the detection kind "
                                "(cmd: od, rod, cls, kp or anom) to create it")
            cpath.parent.mkdir(parents=True, exist_ok=True)
            data = {"detection": {"cmd": cmd, "path": rel_model, "conf": float(a.get("conf") or 0.5)},
                    "display": {"label": 1, "save_img": False, "client_save_img": False}}
            cpath.write_text(yaml.safe_dump(data, sort_keys=False))
            out.append(f"created {cfg}: {json.dumps(data)}")
        out.append(f"A recipe uses it with detection_preset: {{config: {cfg}}} (relaunch the project to load it).")
    else:
        refs = []
        for p in sorted(vdir.glob("*.y*ml")):
            try:
                d = yaml.safe_load(p.read_text()) or {}
                refs.append(f"  vision/{p.name}: detection {json.dumps(d.get('detection') or {}, default=str)[:200]}")
            except Exception:
                refs.append(f"  vision/{p.name}: (doesn't parse)")
        out.append("configs in vision/:\n" + ("\n".join(refs) if refs else "  none yet — name one with config to make it"))
    return "\n".join(out), events


from . import show as _show, vision as _vision  # noqa: E402  (their tools use the helpers above)

TOOLS += _vision.TOOLS + _show.TOOLS
TOOL_NAMES = {t["name"] for t in TOOLS}
HANDLERS = {**{n: globals()["t_" + n] for n in TOOL_NAMES if "t_" + n in globals()},
            **_vision.HANDLERS, **_show.HANDLERS}

LABELS = {
    "list_projects": lambda a: "Listing the projects",
    "list_project_files": lambda a: f"Listing files{(' in ' + a['dir']) if a.get('dir') else ''}",
    "read_project_file": lambda a: f"Reading {a.get('path', '')}",
    "write_project_file": lambda a: f"Writing {a.get('path', '')}",
    "edit_project_file": lambda a: f"Editing {a.get('path', '')}",
    "get_scene": lambda a: "Reading the scene",
    "check_scene": lambda a: "Checking the scene",
    "list_catalog": lambda a: "Browsing your components",
    "describe_component": lambda a: f"Looking up {a.get('type', 'a component')}",
    "runtime_status": lambda a: "Reading the run status",
    "runtime_logs": lambda a: "Reading the log",
    "runtime_command": lambda a: {"launch": "Launching the project", "start": "Starting a run", "pause": "Pausing",
                                  "resume": "Resuming", "park": "Parking the robot", "kill": "Stopping the project",
                                  "relaunch": "Relaunching the project", "replan": "Replanning",
                                  "replan_cancel": "Cancelling the replan",
                                  "speed": f"Setting the speed to {a.get('speed')}x"}.get(a.get("cmd"), "Run command"),
    "list_devices": lambda a: "Checking the devices",
    "device_command": lambda a: f"{'Recovering' if a.get('action') == 'recover' else 'Releasing'} {a.get('device', '')}",
    "diagnose_run": lambda a: "Looking into the run",
    "get_run_parameters": lambda a: "Reading the run parameters",
    "set_run_parameters": lambda a: "Saving the run parameters",
    "read_hmi_spec": lambda a: "Reading the HMI pages",
    "read_docs": lambda a: f"Reading {a.get('path', '')}",
    "search_docs": lambda a: f"Searching for “{a.get('pattern', '')}”",
    "list_uploads": lambda a: "Listing the attached files",
    "read_upload": lambda a: "Reading the attached file",
    "add_component_from_file": lambda a: f"Making the component {a.get('type', '')}",
    "install_vision_model": lambda a: "Installing the vision model",
    **_vision.LABELS,
    **_show.LABELS,
}


def tool_label(name, args):
    try:
        return LABELS[name](args if isinstance(args, dict) else {})
    except Exception:
        return name


HARDWARE_CMDS = {"launch": "Launch the project (its devices connect; a real robot powers its motors)",
                 "start": "Start a run — the robot moves",
                 "resume": "Resume the run — the robot moves",
                 "park": "Park — finish safely and send the robot home",
                 "kill": "Kill the project's runtime at once (motion stops where it is)",
                 "relaunch": "Relaunch the project (stops its runtime and starts it again)"}


def approval(session, name, args):
    """The action the user must approve in the chat before this call runs
    (a sentence), or None. Every physical or live-device action needs one;
    reading never does."""
    if not isinstance(args, dict) or validate(name, args):
        return None                     # an invalid call fails on its own, unasked
    proj = args.get("project") or session.project or ""
    if name == "runtime_command" and args.get("cmd") in HARDWARE_CMDS:
        what = HARDWARE_CMDS[args["cmd"]]
        if args["cmd"] == "start" and args.get("kwargs"):
            what += f", with {json.dumps(args['kwargs'], default=str)[:300]}"
        return f"{what} — {proj}."
    if name == "device_command":
        verb = "Recover" if args.get("action") == "recover" else "Release"
        return f"{verb} the device {args.get('device')} — {proj}."
    return _vision.approval(name, args, session)


def run_tool(session, name, args):
    err = validate(name, args)
    if err:
        raise ToolError(f"invalid input: {err}")
    return HANDLERS[name](session, args)
