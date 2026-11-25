"""A project's detection configs (vision/<detection>.yaml) — read, merge,
and write them back keeping the text of every top-level section that did
not change (its comments too). Shared by the camera window (handlers.py)
and the assistant's update_detection_config (gui/ai/vision.py).

    rel, path, cfg = load(project_dir, "cap_40ml")
    write(path, {"roi": {"corners": [[x, y], …], "inv": 0, "crop": 1}})
    detections(project_dir, "inspection_horizontal_1")   # the ones this camera runs
"""
import copy
import re
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


class ConfigError(ValueError):
    pass


def cfg_rel(det):
    """'cap_40ml' → 'vision/cap_40ml.yaml'; a path stays a path."""
    det = str(det or "").strip()
    if "/" not in det:
        det = "vision/" + det
    if not det.endswith((".yaml", ".yml")):
        det += ".yaml"
    return det


def inside(base, rel):
    """``rel`` under the project folder ``base``, or ConfigError."""
    base = Path(base).resolve()
    rel = str(rel or "").strip().lstrip("/")
    target = (base / rel).resolve()
    if target != base and base not in target.parents:
        raise ConfigError(f"{rel}: outside the project")
    if ".git" in target.relative_to(base).parts:
        raise ConfigError("the .git folder is not editable")
    return target


def load(base, det):
    """(rel, path, cfg) of an existing config; ConfigError names the ones
    there are when it is missing."""
    base = Path(base).resolve()
    rel = cfg_rel(det)
    p = inside(base, rel)
    if not p.is_file():
        have = sorted(x.name for x in (base / "vision").glob("*.y*ml")) if (base / "vision").is_dir() else []
        raise ConfigError(f"no {rel} — the configs in vision/: {', '.join(have) or 'none'}")
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{rel} doesn't parse: {e}")
    if not isinstance(data, dict):
        raise ConfigError(f"{rel} is not a mapping")
    return rel, p, data


def merge(base, over):
    """Inline wins key by key at every depth; lists are replaced whole;
    None removes a key (vision-guide §5 'Config files')."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _top_segments(text):
    """The file as [(top-level key or None, its lines)] — comments and
    blank lines before a key belong to it."""
    segs, cur_key, cur, pending = [], None, [], []
    for line in text.splitlines(keepends=True):
        s = line.rstrip("\n")
        if s and not s[0].isspace() and not s.startswith("#") and ":" in s:
            if cur or cur_key is not None:
                segs.append((cur_key, cur))
            cur_key, cur = s.split(":", 1)[0].strip().strip("'\""), pending + [line]
            pending = []
        elif not s.strip() or s.startswith("#"):
            pending.append(line)
        else:
            cur.extend(pending + [line])
            pending = []
    segs.append((cur_key, cur + pending))
    return segs


def dump_key(k, v):
    """One top-level key as YAML text — roi and friends on one flow line."""
    flow = k in ("roi", "intensity", "color", "focus") or not isinstance(v, (dict, list))
    if isinstance(v, dict) and flow:
        return f"{k}: " + yaml.safe_dump(v, default_flow_style=True, sort_keys=False, width=10_000).strip() + "\n"
    return yaml.safe_dump({k: v}, sort_keys=False, default_flow_style=False, width=120)


def _trailing_comment(line):
    """The ``  # …`` after a one-line value (outside quotes), or ''."""
    quote = None
    for i, ch in enumerate(line.rstrip("\n")):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "#" and i and line[i - 1] in " \t":
            j = i
            while j and line[j - 1] in " \t":
                j -= 1
            return line[j:].rstrip("\n")
    return ""


def write(path, changes):
    """Merge ``changes`` into the config at ``path`` (created when absent)
    and write it: top-level sections that did not change keep their text;
    a rewritten one keeps the comments above it, and a one-line one its
    trailing comment. Read back before it is kept — when the text would
    not read back as the merged config, the file is dumped plain instead.
    Returns {created, changed: [keys], config}."""
    p = Path(path)
    if not isinstance(changes, dict) or not changes:
        raise ConfigError("nothing to write")
    text = p.read_text() if p.is_file() else ""
    try:
        old = (yaml.safe_load(text) or {}) if text else {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{p.name} doesn't parse: {e}")
    if not isinstance(old, dict):
        raise ConfigError(f"{p.name} is not a mapping")
    new = merge(old, changes)
    segs = _top_segments(text) if text else [(None, [])]
    out, seen = [], set()
    for k, lines in segs:
        if k is None:
            out.extend(lines)
            continue
        seen.add(k)
        if k not in new:
            continue
        if new[k] == old.get(k):
            out.extend(lines)
            continue
        lead, body = [], []
        for ln in lines:
            if not body and (not ln.strip() or ln.lstrip().startswith("#")):
                lead.append(ln)
            else:
                body.append(ln)
        out.extend(lead)
        line = dump_key(k, new[k])
        tail = _trailing_comment(body[0]) if len(body) == 1 else ""
        if tail and line.count("\n") == 1:
            line = line.rstrip("\n") + tail + "\n"
        out.append(line)
    for k, v in new.items():
        if k not in seen:
            out.append(dump_key(k, v))
    body = "".join(out)
    if body and not body.endswith("\n"):
        body += "\n"
    try:
        ok = yaml.safe_load(body) == new
    except yaml.YAMLError:
        ok = False
    if not ok:
        body = yaml.safe_dump(new, sort_keys=False)      # never write something that reads back different
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    changed = sorted(k for k in set(new) | set(old) if new.get(k) != old.get(k))
    return {"created": not text, "changed": changed, "config": new}


# ── which detections a camera runs ───────────────────────────────────
_COMP = re.compile(r"\bcomponent\s*:\s*['\"]?([A-Za-z0-9_\-]+)")
_DET = re.compile(r"\bdetection_name\s*:\s*['\"]?([A-Za-z0-9_.\-]+)")
_CFG = re.compile(r"\bconfig\s*:\s*['\"]?(?:\./)?vision/([A-Za-z0-9_.\-]+?)\.ya?ml")


def _recipe_links(project_dir):
    """{detection: set(camera)} as recipes.j2 ties them: a recipe line that
    names its ``component`` and a ``detection_name`` or a
    ``config: vision/<d>.yaml``. Best effort — read as text, the
    template's expressions are never rendered."""
    links = {}
    for f in sorted(Path(project_dir).glob("recipes*.j2")) + sorted(Path(project_dir).glob("recipes/*.j2")):
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        # a recipe's kwargs may span lines: read it block by block (a
        # top-level key starts one)
        for block in re.split(r"\n(?=\S)", text):
            block = "\n".join(ln.split("#", 1)[0] for ln in block.splitlines())
            comps = set(_COMP.findall(block))
            if not comps:
                continue
            for d in set(_DET.findall(block)) | set(_CFG.findall(block)):
                links.setdefault(d, set()).update(comps)
    return links


def detections(project_dir, camera):
    """The project's detection configs, this camera's first:
    [{name, rel, exists, cameras, mine, roi}] — ``mine`` when recipes.j2
    ties the detection to ``camera``; a name recipes.j2 uses without a
    config file yet is listed too (exists: false)."""
    d = Path(project_dir)
    links = _recipe_links(d)
    names = {p.stem for p in (d / "vision").glob("*.y*ml")} if (d / "vision").is_dir() else set()
    out = []
    for n in sorted(names | {k for k, cams in links.items() if camera in cams}):
        rel = cfg_rel(n)
        p = d / rel
        roi = None
        if p.is_file():
            try:
                cfg = yaml.safe_load(p.read_text()) or {}
                roi = cfg.get("roi") if isinstance(cfg, dict) else None
            except yaml.YAMLError:
                roi = None
        cams = sorted(links.get(n, ()))
        out.append({"name": n, "rel": rel, "exists": p.is_file(), "cameras": cams,
                    "mine": camera in cams, "roi": roi if isinstance(roi, dict) else None})
    out.sort(key=lambda x: (not x["mine"], x["name"]))
    return out


def roi_value(corners, inv, crop, size=None):
    """The roi block from the window: 4+ corners [[x, y], …] in frame
    pixels (an empty list clears it), inv/crop 0 or 1. ``size`` (w, h)
    clamps the points into the frame. ConfigError on anything else."""
    if not isinstance(corners, list):
        raise ConfigError("corners: [[x, y], …]")
    pts = []
    for i, p in enumerate(corners, 1):
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            raise ConfigError(f"corner {i}: [x, y]")
        try:
            x, y = (int(round(float(v))) for v in p)
        except (TypeError, ValueError):
            raise ConfigError(f"corner {i}: x and y are numbers of pixels")
        if x < 0 or y < 0:
            raise ConfigError(f"corner {i}: x and y are 0 or more (frame pixels from the top left)")
        if size:
            x, y = min(x, int(size[0])), min(y, int(size[1]))
        pts.append([x, y])
    if pts and len(pts) < 3:
        raise ConfigError("an ROI has 3 corners or more (or none, for the whole frame)")
    if pts:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        if max(xs) - min(xs) < 4 or max(ys) - min(ys) < 4:
            raise ConfigError("the ROI is smaller than 4 pixels across")
    return {"corners": pts, "inv": 1 if inv else 0, "crop": 1 if crop else 0}
