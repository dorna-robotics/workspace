"""A project's own components, made or edited in the Scene tab's component
builder (web/compbuilder.js) — the AI builder's editor (newparts.py) on the unit.

One folder a part, the layout the AI assistant's add_component_from_file
writes and main.py walks into:

    components/<type>/<type>.py      the class (@register), its anchors and collision boxes
    components/<type>/<type>.glb     its model, converted to Workspace's frame (mm, Z up)
    components/<type>/__init__.py

A part of a known kind (component_kinds.py — tube, cap, rack…) subclasses
that kind's library base, so recipes see a Tube's cap_seat and size, a Cap's
twist and pitch, a Rack's slot grid.

    project_types(proj)          the project's own component types
    read(proj, type)             a part back for the editor
    save(proj, data)             write it (a new part, or an edit of one)
    delete(proj, type, scene)    remove it — refused while the scene uses it
"""
import ast
import base64
import json
import math
import re
import shutil
import struct
from pathlib import Path

from . import component_kinds as CK


class PartError(ValueError):
    """What the component builder refuses, in words for the page."""


TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{2,48}$")

TEMPLATE = '''from copy import deepcopy
from mergedeep import merge
from dorna2 import Solid
from workspace.components.factory import register


@register("{type}")
class {cls}:
    """{doc}
    """
    KIND = "{kind}"

    DEFAULTS = dict(
        anchors={{"body": {anchors}}},
        collision_box={{"body": {boxes}}},
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy(self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))
        self.name = name
        self.workspace = workspace
        self.type = prm["type"]
        self.assembly = {{
            k: Solid(type=self.type, anchors=prm["anchors"][k], component=self.name,
                     **({{"collision_box": cb[k]}} if (cb := prm.get("collision_box")) and k in cb else {{}}))
            for k in prm["anchors"]
        }}
'''

TEMPLATE_BASED = '''from copy import deepcopy
from mergedeep import merge
from workspace.components.factory import register
from {mod} import {base}


@register("{type}")
class {cls}({base}):
    """{doc}
    """
    KIND = "{kind}"

    DEFAULTS = dict(
        anchors={{"body": {anchors}}},
        collision_box={{"body": {boxes}}},{extra}
    )

    def __init__(self, name: str, cfg: dict, workspace, **kwargs):
        prm = deepcopy({base}.DEFAULTS)
        merge(prm, self.DEFAULTS)
        merge(prm, cfg)
        merge(prm, kwargs)
        prm.setdefault("type", getattr(self.__class__, "_registered_type", prm.get("type")))
        super().__init__(name=name, workspace=workspace, **prm){after}
'''


def folder(proj, ctype):
    return Path(proj) / "components" / ctype


def project_types(proj):
    comp = Path(proj) / "components"
    return sorted(d.name for d in comp.iterdir() if d.is_dir() and TYPE_RE.match(d.name)
                  and (d / f"{d.name}.py").is_file()) if comp.is_dir() else []


def _check_type(ctype):
    ctype = str(ctype or "").strip().lower()
    if not TYPE_RE.match(ctype):
        raise PartError("the part id must be lowercase letters, digits and _ (e.g. reagent_holder_2x)")
    return ctype


def _num(v, n, what):
    if not isinstance(v, (list, tuple)) or len(v) != n:
        raise PartError(f"{what} needs {n} numbers")
    return [round(float(x), 3) for x in v]


def wrap_gltf(raw, scale=1.0, up="z"):
    """The model with a root node that takes it to Workspace's frame — Z up,
    millimetres: +90° about X for a Y-up file, −90° about Y for an X-up one,
    a uniform scale for the units."""
    if scale == 1.0 and up == "z":
        return raw
    node = {"name": "workspace_frame", "scale": [scale] * 3}
    h = math.sqrt(0.5)
    if up == "y":
        node["rotation"] = [h, 0, 0, h]
    elif up == "x":
        node["rotation"] = [0, -h, 0, h]

    def patch(j):
        nodes = j.setdefault("nodes", [])
        for sc in j.get("scenes") or [{"nodes": list(range(len(nodes)))}]:
            nodes.append({**node, "children": list(sc.get("nodes", []))})
            sc["nodes"] = [len(nodes) - 1]
        return j

    if raw[:4] == b"glTF":
        jlen = struct.unpack("<I", raw[12:16])[0]
        j = patch(json.loads(raw[20:20 + jlen].decode("utf-8").rstrip(" \x00")))
        jb = json.dumps(j, separators=(",", ":")).encode()
        jb += b" " * ((4 - len(jb) % 4) % 4)
        body = struct.pack("<I4s", len(jb), b"JSON") + jb + raw[20 + jlen:]     # the BIN chunk as it was
        return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body
    return json.dumps(patch(json.loads(raw.decode("utf-8")))).encode()


def _kind_source(kind, anchors, settings, dims):
    """(anchors to write, extra DEFAULTS lines, code after super) for a kind."""
    extra, after = {}, ""
    if kind in ("rack", "capholder"):
        g = CK.slot_grid(anchors)
        if g:      # a regular grid: the Rack base makes A1… and its slot list
            anchors = {k: v for k, v in anchors.items() if not CK.SLOT.match(k)}
            extra.update(g)
        else:      # slots as drawn, listed by hand
            slots = [k for k in anchors if CK.SLOT.match(k)]
            extra.update(rows=[], cols=[])
            after = f"\n        self.slot = {{\"body\": {json.dumps(slots)}}}"
    elif kind == "cap":
        d = CK.cap_settings((anchors.get("top") or [0, 0, 0])[2] or None, max(dims[:2]) if dims else None)
        d.update({k: settings[k] for k in ("cap_type", "twist", "pitch") if settings.get(k) is not None})
        if d["cap_type"] not in ("screw", "snap"):
            raise PartError("cap_type is 'screw' or 'snap'")
        extra.update(cap_type=d["cap_type"], twist=float(d["twist"]), pitch=float(d["pitch"]))
    elif kind == "tube":
        size = settings.get("size") or ([dims[0], dims[1], (anchors.get("top") or [0, 0, dims[2]])[2]] if dims else None)
        if size:
            extra["size"] = [round(float(x), 2) for x in size][:3]
    return anchors, "".join(f"\n        {k}={v!r}," for k, v in extra.items()), after


def _library_types(proj):
    """Types the library (or another project) already registers — not this project's own."""
    import gui.scene_builder.server as sb
    mine = str(Path(proj).resolve() / "components") + "/"
    return {t for t, f in sb.COMPONENT_MAP.items() if not str(Path(f).resolve()).startswith(mine)}


def save(proj, data):
    """{type, edit?, description, kind, settings, dims, anchors, collision_boxes,
    glb_b64?, unit_scale, up, file_name} → {type, kind, files}. A new part
    needs its model; an edit keeps the model it has unless a new one comes."""
    ctype = _check_type(data.get("type"))
    d = folder(proj, ctype)
    if not data.get("edit"):
        if d.exists():
            raise PartError(f"the project already has a component {ctype} — pick another id, or edit that one")
        if ctype in _library_types(proj):
            raise PartError(f"{ctype} is a library component already — pick another id")
    elif not (d / f"{ctype}.py").is_file():
        raise PartError(f"no project component {ctype}")
    anchors = {}
    for name, pose in (data.get("anchors") or {}).items():
        name = str(name).strip()
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", name):
            raise PartError(f"bad anchor name '{name}'")
        anchors[name] = _num(pose, 6, f"anchor {name}")
    anchors = {"center": [0.0] * 6, **anchors}
    boxes = [{"pose": _num(b.get("pose"), 6, f"collision box {i + 1} pose"),
              "scale": _num(b.get("scale"), 3, f"collision box {i + 1} size")}
             for i, b in enumerate(data.get("collision_boxes") or [])]
    kind = str(data.get("kind") or "") or CK.detect(ctype, data.get("file_name"), data.get("description"))
    if kind not in CK.KINDS:
        raise PartError(f"unknown kind {kind!r}")

    glb = d / f"{ctype}.glb"
    raw = None
    if data.get("glb_b64"):
        raw = base64.b64decode(str(data["glb_b64"]).split(",")[-1])
        if raw[:4] != b"glTF" and raw.lstrip()[:1] != b"{":
            raise PartError("that file is not a glTF model (.glb)")
        if len(raw) > 60_000_000:
            raise PartError("the model is over 60 MB — simplify it first")
        if raw[:4] != b"glTF" and any(not str(b.get("uri", "data:")).startswith("data:")
                                      for b in json.loads(raw.decode("utf-8")).get("buffers", [])):
            raise PartError("this glTF references external files — export a single binary .glb instead")
        up = str(data.get("up") or "z").lower()
        if up not in ("x", "y", "z"):
            raise PartError("the up axis is x, y or z")
        raw = wrap_gltf(raw, float(data.get("unit_scale") or 1.0), up)
    elif not glb.is_file():
        raise PartError("upload the part's .glb model")

    desc = str(data.get("description") or "").strip() or f"Custom part {ctype}, made in Developer mode."
    doc = desc.replace('"""', "'''").replace("\\", "/")
    dims = data.get("dims")
    if dims:
        doc += f"\n\n    Measured off {ctype}.glb (mm, origin at the base, centered in XY): " \
               f"{dims[0]:.1f} x {dims[1]:.1f} x {dims[2]:.1f}."
    cls = "".join(w.capitalize() for w in ctype.split("_"))

    def fmt(a):     # one anchor a line, as the library writes them
        return "{\n" + ",\n".join(f"            {json.dumps(k)}: {json.dumps(v)}" for k, v in a.items()) + "\n        }"
    box_src = "[\n" + ",\n".join(f"            {{\"pose\": {json.dumps(b['pose'])}, \"scale\": {json.dumps(b['scale'])}, "
                                   f"\"padding_enabled\": True}}" for b in boxes) + "\n        ]"
    base = CK.KINDS[kind]["base"]
    if base:
        anchors, extra, after = _kind_source(kind, anchors, data.get("settings") or {}, dims)
        src = TEMPLATE_BASED.format(type=ctype, cls=cls, doc=doc, kind=kind, mod=base[0], base=base[1],
                                    anchors=fmt(anchors), boxes=box_src, extra=extra, after=after)
    else:
        src = TEMPLATE.format(type=ctype, cls=cls, doc=doc, kind=kind, anchors=fmt(anchors), boxes=box_src)
    compile(src, f"{ctype}.py", "exec")          # never write a class that would not import

    d.mkdir(parents=True, exist_ok=True)
    (d.parent / "__init__.py").touch()
    (d / "__init__.py").touch()
    if raw is not None:
        for old in d.glob(f"{ctype}.glb*"):       # a new model replaces the whole old one (.bin too)
            old.unlink()
        glb.write_bytes(raw)
    (d / f"{ctype}.py").write_text(src)
    rel = f"components/{ctype}"
    return {"type": ctype, "kind": kind, "files": [f"{rel}/{ctype}.py", f"{rel}/{ctype}.glb", f"{rel}/__init__.py"]}


def read(proj, ctype):
    """A project part as the editor needs it: description, kind, settings,
    anchors (a Rack grid as its A1… anchors), collision boxes, and whether it
    has a model. A file that computes its values (not plain literals) is
    refused: saving it from the editor would lose them."""
    ctype = _check_type(ctype)
    src = folder(proj, ctype) / f"{ctype}.py"
    if not src.is_file():
        raise PartError(f"no project component {ctype}")
    anchors, boxes, doc, kind, settings, grid = {}, [], "", None, {}, {}
    for node in ast.walk(ast.parse(src.read_text())):
        if isinstance(node, ast.ClassDef):
            doc = (ast.get_docstring(node) or "").split("\n\n")[0].strip()
        if not isinstance(node, ast.Assign):
            continue
        names = {getattr(t, "id", None) for t in node.targets}
        if "KIND" in names:
            kind = ast.literal_eval(node.value)
        if "DEFAULTS" in names:
            for kw in getattr(node.value, "keywords", []):
                try:
                    val = ast.literal_eval(kw.value)
                except ValueError:
                    raise PartError(f"{ctype}.py computes its {kw.arg} — edit that file itself")
                if kw.arg == "anchors":
                    anchors = val.get("body", {})
                elif kw.arg == "collision_box":
                    boxes = val.get("body", [])
                elif kw.arg in ("cap_type", "twist", "pitch", "size") and not (kw.arg == "pitch" and isinstance(val, list)):
                    settings[kw.arg] = val
                elif kw.arg in ("offset", "pitch", "rows", "cols", "rvec_safe", "transpose"):
                    grid[kw.arg] = val
    if grid.get("rows") and grid.get("cols"):       # a Rack grid → its A1… anchors, as the editor shows them
        o, p, rv = grid.get("offset", [0, 0, 0]), grid.get("pitch", [9, 9, 0]), grid.get("rvec_safe", [0, 0, 45])
        tr = bool(grid.get("transpose"))            # Workspace's Rack: transposed = rows along x, columns along y
        for ri, r in enumerate(grid["rows"]):
            for ci, c in enumerate(grid["cols"]):
                i, j = (ri, ci) if tr else (ci, ri)
                anchors[f"{r}{c}"] = [round(o[0] + i * p[0], 3), round(o[1] + j * p[1], 3), o[2], *rv]
    return {"type": ctype, "kind": kind if kind in CK.KINDS else CK.detect(ctype, doc), "settings": settings,
            "description": doc, "anchors": anchors,
            "collision_boxes": [{"pose": b["pose"], "scale": b["scale"]} for b in boxes],
            "glb": (folder(proj, ctype) / f"{ctype}.glb").is_file()}


def delete(proj, ctype, scene):
    """Remove a project part (its whole folder) — refused while ``scene``
    (the merged scene) still has one."""
    ctype = _check_type(ctype)
    d = folder(proj, ctype)
    if not (d / f"{ctype}.py").is_file():
        raise PartError(f"no project component {ctype}")
    used = sorted(n for n, cfg in scene.items() if isinstance(cfg, dict) and cfg.get("type") == ctype)
    if used:
        raise PartError(f"{ctype} is still in the scene ({', '.join(used[:6])}{'…' if len(used) > 6 else ''})"
                        " — remove it there and save the scene first")
    shutil.rmtree(d)
    return {"deleted": ctype}
