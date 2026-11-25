"""Showing the user what the assistant looked at, and the bench's setup at a
glance.

    show_image     a picture from the project (captures/, results/ …) or an attached file: the model sees it, the chat shows it
    show_excerpt   lines of the log, a project file, an attached file or the run's records, the relevant ones highlighted, in the chat
    setup_status   the project's setup: devices real / simulated, cameras wired or why not, the scene check, the calibration plan

An ``Excerpt`` rides a tool's content like an images.Shot: the agent sends
it to the chat (``t: excerpt``) and keeps it in the transcript.
"""
import json
import re
from pathlib import Path

from . import images
from .images import Shot
from .tools import (ToolError, _P, _inside, _local, _name, _q, _runtime_get, _runtime_port, _scene, _tool,
                    project_dir)

MAX_LINES = 80


class Excerpt:
    def __init__(self, title, lines, source=""):
        self.title = title
        self.lines = lines          # [{"n": line number or None, "text": str, "hit": bool}]
        self.source = source

    def public(self):
        return {"title": self.title, "source": self.source, "lines": self.lines}


TOOLS = [
    _tool("show_image", "Look at a picture and show it to the user in the chat: a project file (e.g. "
          "'captures/tube_od/20261125_101500.jpg', a results/ image) or an attached file (upload id or name). "
          "Use it to look again at an attached photo or screenshot, or at what a run saved.",
          {"project": _P, "path": {"type": "string", "description": "An image file in the project."},
           "upload": {"type": "string", "description": "An attached image's id or name."},
           "crop": {"type": "array", "items": {"type": "number"}, "description": "[x0, y0, x1, y1] in the picture's pixels."},
           "caption": {"type": "string"}}),
    _tool("show_excerpt", "Show the user lines that matter, in the chat, with the important ones highlighted: "
          "from the project's log (source 'log'), a project file ('file' + path), an attached file ('upload' + "
          "upload) or this run's records ('records'). Choose lines by grep (a regular expression, with `context` "
          "lines around each hit) or by first_line/last_line; `highlight` (a regular expression) marks lines. "
          "Quote what you show in your answer only briefly — the user sees the excerpt.",
          {"project": _P, "source": {"type": "string", "enum": ["log", "file", "upload", "records"]},
           "path": {"type": "string"}, "upload": {"type": "string"},
           "grep": {"type": "string"}, "context": {"type": "integer"},
           "first_line": {"type": "integer"}, "last_line": {"type": "integer"},
           "highlight": {"type": "string"},
           "title": {"type": "string", "description": "What this is, e.g. 'The gripper fault in the log'."}},
          ["source"]),
    _tool("setup_status", "The project's setup at a glance, for setting up a bench step by step: every device "
          "component real or simulated (and its connection settings), every camera wired to a vision server or "
          "why not, the scene check, whether the project is launched and its devices' state, and the calibration "
          "plan (what is calibrated and what still needs it) when the project has one.",
          {"project": _P, "calibration": {"type": "boolean", "description": "Build the calibration plan too "
                                                                            "(slower: up to a few minutes the first time)."}}),
]

LABELS = {
    "show_image": lambda a: "Showing the picture",
    "show_excerpt": lambda a: f"Showing {a.get('title') or 'an excerpt'}",
    "setup_status": lambda a: "Checking the setup",
}


def _rx(p, what):
    if not p:
        return None
    try:
        return re.compile(p, re.I)
    except re.error as e:
        raise ToolError(f"{what}: bad pattern: {e}")


def t_show_image(session, a):
    if bool(a.get("path")) == bool(a.get("upload")):
        raise ToolError("give path (a project file) or upload (an attached file)")
    if a.get("upload"):
        from .tools import _upload
        rec = _upload(session, a["upload"])
        p, label = Path(rec["path"]), rec["name"]
    else:
        base = project_dir(_name(session, a))
        p = _inside(base, a["path"])
        label = str(p.relative_to(base))
        if not p.is_file():
            raise ToolError(f"no such file: {a['path']}")
    try:
        im = images.decode(p.read_bytes())
    except Exception:
        raise ToolError(f"{label} is not a picture this robot can read (png / jpg / webp / gif / bmp)")
    note = f"{label}: {im.size[0]}x{im.size[1]} px"
    if a.get("crop") is not None:
        c = a["crop"]
        if not (isinstance(c, list) and len(c) == 4):
            raise ToolError("crop: [x0, y0, x1, y1]")
        box = [max(0, int(c[0])), max(0, int(c[1])), min(im.size[0], int(c[2])), min(im.size[1], int(c[3]))]
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            raise ToolError(f"crop {c} is empty in the {im.size[0]}x{im.size[1]} picture")
        im = im.crop(tuple(box))
        note += f", crop {box}"
    st = images.stats(im)
    return [note + "\n" + images.stats_text(st, "picture"), Shot(images.encode(im), a.get("caption") or label)], []


def _lines_for(session, a):
    """(title source, [(line number or None, text)])."""
    src = a["source"]
    if src == "log":
        name = _name(session, a)
        code, j = _local("GET", f"/orchestrator/api/workspace/{_q(name)}/logs?tail=2000", timeout=20)
        lines = (j or {}).get("lines") if isinstance(j, dict) else None
        if lines is None and isinstance(j, dict):
            lines = j.get("logs") or j.get("text") or ""
        if isinstance(lines, str):
            lines = lines.splitlines()
        lines = [l if isinstance(l, str) else json.dumps(l, default=str) for l in (lines or [])]
        return f"{name} — log", [(None, l) for l in lines]
    if src == "file":
        if not a.get("path"):
            raise ToolError("source 'file' needs path")
        base = project_dir(_name(session, a))
        p = _inside(base, a["path"])
        if not p.is_file():
            raise ToolError(f"no such file: {a['path']}")
        if p.stat().st_size > 5_000_000:
            raise ToolError(f"{a['path']} is too large to show")
        return str(p.relative_to(base)), list(enumerate(p.read_text(errors="replace").splitlines(), 1))
    if src == "upload":
        from .tools import _upload
        rec = _upload(session, a.get("upload") or "")
        if rec["kind"] != "text":
            raise ToolError(f"{rec['name']} is not a text file")
        p = Path(rec["path"])
        return rec["name"], list(enumerate(p.read_text(errors="replace").splitlines(), 1))
    if src == "records":
        name = _name(session, a)
        port = _runtime_port(name)
        if port is None:
            raise ToolError("the project is not launched here — its records are in its records/ folder (source 'file')")
        raw, _ = _runtime_get(port, "/records")
        try:
            rec = json.loads(raw) if raw else {}
        except ValueError:
            rec = {}
        rows = rec.get("records") or {}
        if isinstance(rows, dict):
            rows = [{"key": k, **(v if isinstance(v, dict) else {"value": v})} for k, v in rows.items()]
        return f"{name} — this run's records", [(i, json.dumps(r, default=str)) for i, r in enumerate(rows, 1)]
    raise ToolError(f"unknown source {src}")


def t_show_excerpt(session, a):
    where, rows = _lines_for(session, a)
    if not rows:
        raise ToolError(f"{where} is empty")
    grep = _rx(a.get("grep"), "grep")
    hl = _rx(a.get("highlight"), "highlight") or grep
    ctx = max(0, min(20, int(a.get("context") if a.get("context") is not None else 2)))
    if grep:
        keep = set()
        for i, (_, t) in enumerate(rows):
            if grep.search(t):
                keep.update(range(max(0, i - ctx), min(len(rows), i + ctx + 1)))
        idx = sorted(keep)
        if not idx:
            raise ToolError(f"nothing in {where} matches {a['grep']!r}")
        idx = idx[-MAX_LINES:]
    else:
        first = a.get("first_line")
        last = a.get("last_line")
        if first is None and last is None:
            idx = list(range(max(0, len(rows) - 40), len(rows)))       # the end (a log's latest)
        else:
            nums = [n if n is not None else i + 1 for i, (n, _) in enumerate(rows)]
            lo = first if first is not None else (last - MAX_LINES + 1)
            hi = last if last is not None else first + MAX_LINES - 1
            idx = [i for i, n in enumerate(nums) if lo <= n <= hi][:MAX_LINES]
        if not idx:
            raise ToolError(f"no lines in that range ({where} has {len(rows)})")
    lines, prev = [], None
    for i in idx:
        if prev is not None and i != prev + 1:
            lines.append({"n": None, "text": "…", "hit": False, "gap": True})
        n, t = rows[i]
        lines.append({"n": n, "text": t[:500], "hit": bool(hl and hl.search(t))})
        prev = i
    title = a.get("title") or where
    ex = Excerpt(title, lines, where)
    hits = sum(1 for l in lines if l["hit"])
    text = (f"shown to the user: {len([l for l in lines if not l.get('gap')])} lines of {where}"
            f"{f', {hits} highlighted' if hl else ''}:\n"
            + "\n".join(("» " if l["hit"] else "  ") + (f"{l['n']}: " if l["n"] else "") + l["text"] for l in lines))
    return [text, ex], []


def t_setup_status(session, a):
    from gui.devmode import camera
    from . import tools
    name = _name(session, a)
    scene = _scene(name)
    out = [f"project {name}: " + ("launched" if tools._runtime_port(name) else "not launched")]
    devs, cams = [], []
    for n, c in scene.items():
        if not isinstance(c, dict):
            continue
        if isinstance(c.get("camera_cfg"), dict) and c.get("has_camera") is not False:
            try:
                host, port, serial, _ = camera.resolve(scene, n)
                cams.append(f"  {n}: wired — camera {serial} on {host}:{port} (camera_view can look)")
            except camera.NoImage as e:
                cams.append(f"  {n}: no picture — {e}")
        if "simulation" in c:
            conn = {k: c[k] for k in tools.CONNECTION_KEYS if c.get(k) not in (None, "", {})}
            devs.append(f"  {n} ({c.get('type')}): {'SIMULATED' if c.get('simulation', True) else 'REAL'}"
                        + (f" {json.dumps(conn, default=str)[:200]}" if conn else ""))
    out.append("devices:\n" + ("\n".join(devs) or "  none"))
    out.append("cameras:\n" + ("\n".join(cams) or "  none"))
    try:
        chk, _ = tools.t_check_scene(session, {"project": name})
        out.append("scene check:\n" + chk)
    except ToolError as e:
        out.append(f"scene check: {e}")
    if tools._runtime_port(name):
        txt, _ = tools.t_list_devices(session, {"project": name})
        out.append("live devices:\n" + txt)
    if a.get("calibration"):
        code, j = _local("GET", f"/dev/api/projects/{_q(name)}/calibration/plan", timeout=300)
        if code == 200 and isinstance(j, dict):
            out.append("calibration plan: " + json.dumps(j, default=str)[:6000])
        else:
            out.append(f"calibration plan: not available ({code}: {json.dumps(j, default=str)[:300]})")
    else:
        out.append("(calibration=true adds the calibration plan)")
    return "\n".join(out), []


HANDLERS = {t["name"]: globals()["t_" + t["name"]] for t in TOOLS}
