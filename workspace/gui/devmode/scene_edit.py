"""Minimal, validated edits to a project's scene files (Jinja2 YAML).

The files are hand-written: comments, ``{% set %}`` knobs and
``{{ expr }}`` values must survive an edit. So nothing here re-dumps a
file. An edit finds the one component block as text (``name:`` at
column 0 down to the next top-level line), changes or inserts the one
line it needs, renders the whole file again and checks the value came
out as asked — or puts the file back and says why.

    set_values(project_dir, component, {"ip": "10.0.0.5", "rail_cfg.offset": 1.5})
    save_components(project_dir, {file: {component: cfg}})   # the scene builder's save
"""
import copy
import json
import os
import re
import time
from pathlib import Path

import yaml

TOP_RE = re.compile(r"""^(?:["']?([A-Za-z0-9_.\-]+)["']?\s*:|\{[%#])""")


class SceneEditError(ValueError):
    pass


# ── files ────────────────────────────────────────────────────────────
def scene_files(project_dir):
    """launch.yaml's ``scene:`` files, absolute, in merge order."""
    project_dir = Path(project_dir)
    with open(project_dir / "launch.yaml") as f:
        launch = yaml.safe_load(f) or {}
    scene = launch.get("scene") or []
    if isinstance(scene, str):
        scene = [scene]
    return [(project_dir / str(p)).resolve() for p in scene]


def files_sig(project_dir):
    """A fingerprint of the scene files — changes on any write."""
    out = []
    for p in scene_files(project_dir):
        try:
            st = p.stat()
            out.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            out.append(f"{p.name}:missing")
    return "|".join(out)


def render(path, text=None):
    path = Path(path)
    if text is None:
        text = path.read_text()
    if path.suffix == ".j2" or "{%" in text or "{{" in text:
        from workspace.j2 import render_text
        text = render_text(text, path.parent)
    return yaml.safe_load(text) or {}


def merged(project_dir):
    out = {}
    for p in scene_files(project_dir):
        out.update(render(p))
    return out


def _backup(path, text):
    """The file as it was before an edit, under
    <project>/.workspace/scene_backup/ — an edit can always be undone."""
    proj = path.parent
    while proj != proj.parent and not (proj / "launch.yaml").is_file():
        proj = proj.parent
    d = proj / ".workspace" / "scene_backup"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{path.name}.{time.strftime('%Y%m%d-%H%M%S')}").write_text(text)


# ── text blocks ──────────────────────────────────────────────────────
def _is_top(line):
    return bool(line) and not line[0].isspace() and not line.startswith("#") and bool(TOP_RE.match(line))


def find_block(lines, name):
    """(start, end) line indexes of ``name:``'s block, end exclusive and
    without the blank/comment lines that lead into the next block; None
    when the component is not written out literally in this file."""
    start = None
    for i, ln in enumerate(lines):
        m = TOP_RE.match(ln)
        if m and m.group(1) == name and not ln[0].isspace():
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _is_top(lines[j]):
            end = j
            break
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    return start, end


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _fmt(value):
    """A value as YAML flow text (JSON is valid YAML)."""
    return json.dumps(value)


def _set_in(lines, lo, hi, ind, path, value):
    """Set ``path`` inside lines[lo:hi], whose keys sit at ``ind``.
    Returns the new line list."""
    key = path[0]
    key_re = re.compile(r"^" + " " * ind + r"""["']?""" + re.escape(key) + r"""["']?\s*:(.*)$""")
    for i in range(lo, hi):
        if _indent(lines[i]) != ind:
            continue
        m = key_re.match(lines[i])
        if not m:
            continue
        rest = m.group(1).strip()
        if "{{" in rest or "{%" in rest:
            raise SceneEditError(f"`{key}` is set by a template expression ({rest}) — edit the scene file itself")
        if len(path) == 1:
            lines[i] = " " * ind + f"{key}: {_fmt(value)}"
            return lines
        if rest and not rest.startswith("#"):
            # A flow mapping on one line: {type: "x", offset: 0}
            try:
                cur = yaml.safe_load(rest)
            except Exception:
                cur = None
            if not isinstance(cur, dict):
                raise SceneEditError(f"`{key}` is not a mapping — cannot set {'.'.join(path)}")
            node = cur
            for k in path[1:-1]:
                node = node.setdefault(k, {})
            node[path[-1]] = value
            lines[i] = " " * ind + f"{key}: {_fmt(cur)}"
            return lines
        # A nested block: the lines below with more indent.
        j = i + 1
        while j < hi and (not lines[j].strip() or lines[j].lstrip().startswith("#") or _indent(lines[j]) > ind):
            j += 1
        while j > i + 1 and (not lines[j - 1].strip() or lines[j - 1].lstrip().startswith("#")):
            j -= 1
        sub = [ln for ln in lines[i + 1:j] if ln.strip() and not ln.lstrip().startswith("#")]
        sub_ind = _indent(sub[0]) if sub else ind + 2
        return _set_in(lines, i + 1, j, sub_ind, path[1:], value)
    # Not there: insert at the end of this level.
    ins = hi
    while ins > lo and (not lines[ins - 1].strip() or lines[ins - 1].lstrip().startswith("#")):
        ins -= 1
    new = []
    for depth, k in enumerate(path[:-1]):
        new.append(" " * (ind + 2 * depth) + f"{k}:")
    new.append(" " * (ind + 2 * (len(path) - 1)) + f"{path[-1]}: {_fmt(value)}")
    return lines[:ins] + new + lines[ins:]


def _get(cfg, path):
    node = cfg
    for k in path:
        if not isinstance(node, dict) or k not in node:
            return KeyError
        node = node[k]
    return node


def _pick_file(files, component, path):
    """The file to edit: the last one that writes the component out and
    already holds the key's head; else the last one that writes it."""
    fallback = None
    for p in reversed(files):
        lines = p.read_text().split("\n")
        if find_block(lines, component) is None:
            continue
        try:
            cfg = (render(p) or {}).get(component) or {}
        except Exception:
            cfg = {}
        if isinstance(cfg, dict) and path[0] in cfg:
            return p
        fallback = fallback or p
    return fallback


def set_values(project_dir, component, values):
    """Write ``{"a.b": value}`` into ``component``'s block. Every value
    is checked after the write by rendering the file; any failure puts
    every touched file back. Returns {file: [paths]}."""
    files = scene_files(project_dir)
    if not files:
        raise SceneEditError("this project's launch.yaml names no scene files")
    if component not in merged(project_dir):
        raise SceneEditError(f"no component `{component}` in the scene")
    originals, touched = {}, {}
    try:
        for dotted, value in values.items():
            path = [k for k in str(dotted).split(".") if k]
            if not path:
                raise SceneEditError("empty parameter name")
            p = _pick_file(files, component, path)
            if p is None:
                raise SceneEditError(f"`{component}` is generated by a template — edit the scene file itself")
            text = p.read_text()
            originals.setdefault(p, text)
            lines = text.split("\n")
            s, e = find_block(lines, component)
            body = [ln for ln in lines[s + 1:e] if ln.strip() and not ln.lstrip().startswith("#")]
            ind = _indent(body[0]) if body else 2
            lines = _set_in(lines, s + 1, e, ind, path, value)
            new_text = "\n".join(lines)
            got = _get((render(p, new_text) or {}).get(component) or {}, path)
            if got != value:
                raise SceneEditError(f"{dotted}: the file did not read back as {value!r} (got {got!r})")
            p.write_text(new_text)
            touched.setdefault(str(p), []).append(dotted)
        # The merged scene must still give the asked values (a later file
        # could override them).
        final = merged(project_dir).get(component) or {}
        for dotted, value in values.items():
            got = _get(final, [k for k in str(dotted).split(".") if k])
            if got != value:
                raise SceneEditError(f"{dotted}: another scene file overrides it ({got!r})")
    except Exception:
        for p, text in originals.items():
            p.write_text(text)
        raise
    for p, text in originals.items():
        _backup(p, text)
    return touched


# ── the scene builder's save ─────────────────────────────────────────
# Keys the builder does not own: connection settings and simulation are
# set in the Parameters window, and the builder's previews blank them
# (scene_builder/server.py) — a save never writes them.
BUILDER_SKIPS = {"simulation", "ip", "port", "serial_number", "baud", "camera_cfg"}


def _emit(value, ind):
    """Block-style YAML in the scene files' own style: mappings nested,
    lists and scalars on one line, strings double-quoted."""
    out = []
    for k, v in value.items():
        if isinstance(v, dict) and v:
            out.append(" " * ind + f"{k}:")
            out += _emit(v, ind + 2)
        else:
            out.append(" " * ind + f"{k}: {_fmt(v)}")
    return out


def _block_text(name, cfg):
    return [f"{name}:"] + _emit(cfg, 2)


def save_components(project_dir, by_file):
    """The scene builder's state → the project's files, minimally:
    a component the builder added is appended, one it deleted is cut,
    one it changed gets its block rewritten from the file's own values
    with the builder's keys laid over (never deleting a key, never
    touching BUILDER_SKIPS). Untouched components keep their text,
    comments and template expressions; a component a template loop
    writes is left to the file (listed as skipped). Returns a summary."""
    files = {p.name: p for p in scene_files(project_dir)}
    summary = {"added": [], "removed": [], "changed": [], "skipped": [], "files": []}
    originals = {}
    try:
        for fname, comps in (by_file or {}).items():
            p = files.get(os.path.basename(fname))
            if p is None:
                raise SceneEditError(f"{fname} is not one of this project's scene files")
            if not isinstance(comps, dict):
                raise SceneEditError(f"{fname}: components must be an object")
            text = p.read_text()
            cur = render(p, text)
            if cur and not comps:
                raise SceneEditError(f"{p.name}: the builder sent it empty — not saved (reload the builder)")
            lines = text.split("\n")
            changed = False
            for name in [n for n in cur if n not in comps]:
                blk = find_block(lines, name)
                if blk is None:                   # written by a {% for %}: the file owns it
                    summary["skipped"].append(name)
                    continue
                s, e = blk
                del lines[s:e]
                while s < len(lines) and s > 0 and not lines[s].strip() and not lines[s - 1].strip():
                    del lines[s]
                summary["removed"].append(name)
                changed = True
            for name, cfg in comps.items():
                if not isinstance(cfg, dict):
                    continue
                if name not in cur:
                    new = {k: v for k, v in cfg.items() if not k.startswith("__")}
                    while lines and not lines[-1].strip():
                        lines.pop()
                    lines += [""] + _block_text(name, new) + [""]
                    summary["added"].append(name)
                    changed = True
                    continue
                old = cur[name] if isinstance(cur[name], dict) else {}
                patched = copy.deepcopy(old)
                for k, v in cfg.items():
                    if k in BUILDER_SKIPS or k.startswith("__"):
                        continue
                    patched[k] = v
                if patched == old:
                    continue
                blk = find_block(lines, name)
                if blk is None:
                    summary["skipped"].append(name)
                    continue
                s, e = blk
                lines[s:e] = _block_text(name, patched)
                summary["changed"].append(name)
                changed = True
            if not changed:
                continue
            new_text = "\n".join(lines)
            if not new_text.endswith("\n"):
                new_text += "\n"
            render(p, new_text)               # must still parse
            originals[p] = text
            p.write_text(new_text)
            summary["files"].append(p.name)
        merged(project_dir)                   # the whole scene still loads
    except Exception:
        for p, text in originals.items():
            p.write_text(text)
        raise
    for p, text in originals.items():
        _backup(p, text)
    return summary
