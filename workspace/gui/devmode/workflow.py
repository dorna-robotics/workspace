"""The Workflow tab — what the project's own files say its workflow is.

Ported from the AI builder (builder/workflow.py, builder/engine/flowchart.py),
reading the project folder on this unit: the steps are the Action classes
of actions.py (or the actions/ package), the stations are recipes.j2, the
chart is flow/<name>.json. A project has ONE workflow, for one sample —
the scheduler runs the batch; loops are pre/eff between actions; a step's
time is its ``duration``.

    info(project_dir)              → {actions, recipes, charts, scheduler, plan_window}
    load_chart(project_dir, name)  → {doc, check}
    save_chart(project_dir, name, doc) → check (saved only when it checks out)

Building actions.py from the chart is the AI builder's (its compiler
replaces the actions); here a chart is read, edited and checked.
"""
import ast
import json
import re
from pathlib import Path

import yaml

from . import scene_edit

KINDS = {"start", "end", "step", "measure", "decision", "hmi", "manual", "external", "note"}
ROBOT = {"step", "measure"}
DECIDE_ON = {"measure", "field", "answer", "items"}
SCREENS = {"input", "choice", "confirm", "display", "review"}
NAME_RE = re.compile(r"[A-Za-z0-9_\-]+")


def _launch(proj):
    try:
        return yaml.safe_load((Path(proj) / "launch.yaml").read_text()) or {}
    except Exception:
        return {}


def actions(proj):
    """The Action classes: name, first doc line, duration, resource, tool,
    per item (it has params)."""
    proj = Path(proj)
    target = proj / (_launch(proj).get("actions") or "actions.py")
    files = sorted(target.rglob("*.py")) if target.is_dir() else [target]
    out = []
    for fp in files:
        if not fp.is_file():
            continue
        try:
            tree = ast.parse(fp.read_text())
        except Exception:
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
            methods = {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
            if "execute" not in methods and not (bases & {"Action"}):
                continue
            attrs = {}
            for n in node.body:
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                    try:
                        attrs[n.targets[0].id] = ast.literal_eval(n.value)
                    except Exception:
                        pass
            doc = (ast.get_docstring(node) or "").strip().split("\n")[0]
            out.append({"name": node.name, "doc": doc, "duration": attrs.get("duration"),
                        "resource": attrs.get("resource"), "tool": attrs.get("tool"),
                        "per_item": bool(attrs.get("params")), "file": str(fp.relative_to(proj))})
    return out


def recipes(proj):
    """recipes.j2's stations: name, recipe class, component."""
    rp = Path(proj) / (_launch(proj).get("recipes") or "recipes.j2")
    if not rp.is_file():
        return []
    try:
        defs = scene_edit.render(rp)
    except Exception as ex:
        return [{"name": "(recipes.j2 does not load)", "class": str(ex)}]
    return [{"name": n, "class": str((s or {}).get("class", "")).split(".")[-1],
             "component": ((s or {}).get("kwargs") or {}).get("component")} for n, s in defs.items()]


def chart_path(proj, name):
    name = str(name).removesuffix(".json")
    if not NAME_RE.fullmatch(name):
        raise ValueError("a chart name is letters, digits, - and _")
    return Path(proj) / "flow" / f"{name}.json"


def charts(proj):
    rows = []
    for p in sorted((Path(proj) / "flow").glob("*.json")):
        try:
            d = json.loads(p.read_text())
            rows.append({"name": p.stem, "title": d.get("title", p.stem), "nodes": len(d.get("nodes") or [])})
        except Exception as ex:
            rows.append({"name": p.stem, "title": p.stem, "error": str(ex)})
    return rows


def info(proj):
    launch = _launch(proj)
    return {"actions": actions(proj), "recipes": recipes(proj), "charts": charts(proj),
            "scheduler": launch.get("scheduler"), "plan_window": launch.get("plan_window")}


def load_chart(proj, name):
    doc = json.loads(chart_path(proj, name).read_text())
    return {"doc": doc, "check": validate(doc)}


def save_chart(proj, name, doc):
    """Check the chart; write it (the old file backed up) only when it
    checks out."""
    path = chart_path(proj, name)
    check = validate(doc)
    if check["ok"]:
        if path.is_file():
            scene_edit._backup(path, path.read_text())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    return check


# ── the chart's checks (the AI builder's engine/flowchart.py validate) ──
def _index(doc):
    nodes = {n["id"]: n for n in doc.get("nodes") or [] if isinstance(n, dict) and n.get("id")}
    out, inc = {k: [] for k in nodes}, {k: [] for k in nodes}
    for e in doc.get("edges") or []:
        if e.get("from") in nodes and e.get("to") in nodes:
            out[e["from"]].append(e)
            inc[e["to"]].append(e)
    return nodes, out, inc


def _cycles(nodes, out):
    """Every elementary cycle's node list (charts are small)."""
    found, seen = [], set()

    def dfs(start, v, path, on):
        for e in out[v]:
            w = e["to"]
            if w == start:
                key = frozenset(path)
                if key not in seen:
                    seen.add(key)
                    found.append(list(path))
            elif w not in on and w > start:
                on.add(w)
                dfs(start, w, path + [w], on)
                on.discard(w)

    for s in sorted(nodes):
        dfs(s, s, [s], {s})
    return found


def validate(doc):
    errs, warns = [], []
    if not isinstance(doc, dict):
        return {"ok": False, "errors": ["the flowchart must be a JSON object"], "warnings": []}
    for key in ("id", "title", "nodes", "edges"):
        if not doc.get(key):
            errs.append(f"missing {key!r}")
    ids = [n.get("id") for n in doc.get("nodes") or []]
    dup = sorted({str(i) for i in ids if ids.count(i) > 1})
    if dup:
        errs.append("duplicate node ids: " + ", ".join(dup))
    lanes = {l["id"] for l in doc.get("lanes") or [] if l.get("id")}
    fields = doc.get("fields") or {}
    nodes, out, inc = _index(doc)
    for e in doc.get("edges") or []:
        for end in ("from", "to"):
            if e.get(end) not in nodes:
                errs.append(f"edge {e.get('from')} → {e.get('to')}: no node {e.get(end)!r}")

    for nid, n in nodes.items():
        k, where = n.get("kind"), f"{nid} ({n.get('label', '')[:40]})"
        if k not in KINDS:
            errs.append(f"{where}: kind {k!r} is not one of {', '.join(sorted(KINDS))}")
            continue
        if not n.get("label"):
            errs.append(f"{nid}: no label")
        if n.get("lane") and n["lane"] not in lanes:
            errs.append(f"{where}: lane {n['lane']!r} is not declared in lanes")
        if k != "end" and k != "note" and not out[nid]:
            errs.append(f"{where}: nothing follows it — connect it or make it an end")
        if k == "start" and inc[nid]:
            warns.append(f"{where}: a start with arrows into it")
        if k == "measure":
            oc = n.get("outcomes") or []
            if len(oc) < 2 or len(set(oc)) != len(oc):
                errs.append(f"{where}: a measure needs two or more distinct outcomes")
        if k == "hmi" and n.get("screen") and n["screen"] not in SCREENS:
            errs.append(f"{where}: screen {n['screen']!r} is not one of {', '.join(sorted(SCREENS))}")
        if k in ROBOT and n.get("duration") is None:
            warns.append(f"{where}: no duration estimate — the schedule will guess")
        if k == "decision":
            on = n.get("on") or {}
            kind = next((x for x in DECIDE_ON if x in on), None)
            if "option" in on:
                errs.append(f"{where}: '{n.get('label', '')}' asks what the cell can do — that's a fact, not a branch: "
                            "draw the path that's true, and note the fact")
                continue
            if not kind:
                errs.append(f"{where}: a decision needs 'on': one of {', '.join(sorted(DECIDE_ON))}")
                continue
            outs = out[nid]
            if len(outs) < 2:
                errs.append(f"{where}: a decision needs two or more arrows out")
            if kind == "measure":
                m = nodes.get(on["measure"])
                if not m or m.get("kind") != "measure":
                    errs.append(f"{where}: decides on {on['measure']!r}, which is not a measure")
                    continue
                cover = [w for e in outs for w in (e.get("when") or [])]
                if any(not e.get("when") for e in outs):
                    errs.append(f"{where}: every arrow of a measure decision says 'when' (which outcomes)")
                bad = sorted(set(cover) - set(m.get("outcomes") or []))
                if bad:
                    errs.append(f"{where}: {', '.join(bad)} not outcomes of {m['id']}")
                if len(cover) != len(set(cover)):
                    errs.append(f"{where}: an outcome goes two ways")
            elif kind == "field" and on["field"] not in fields:
                errs.append(f"{where}: field {on['field']!r} is not declared in fields")
            if n.get("max_attempts") is not None:
                if not isinstance(n["max_attempts"], int) or n["max_attempts"] < 1:
                    errs.append(f"{where}: max_attempts must be a whole number ≥ 1")
                if n.get("exhausted") not in nodes:
                    errs.append(f"{where}: a bounded loop needs 'exhausted': the node when attempts run out")
        coll = n.get("collection")
        if coll and coll not in (doc.get("collections") or {}):
            errs.append(f"{where}: collection {coll!r} is not declared in collections")
        if k == "decision" and "items" in (n.get("on") or {}):
            c = n["on"]["items"]
            if c != (doc.get("item") or {}).get("name") and c not in (doc.get("collections") or {}):
                errs.append(f"{where}: loops over {c!r} — the chart's item or a declared collection")
        if k == "end" and n.get("link") and not isinstance(n["link"], str):
            errs.append(f"{where}: link must be a chart id")
        if k in ROBOT and re.search(r"\bTBD\b", n.get("label", "")):
            warns.append(f"{where}: TBD in the step — needs a number before it runs")

    starts = [nid for nid, n in nodes.items() if n.get("kind") == "start"]
    if len(starts) != 1:
        errs.append(f"a chart has one start ({len(starts)} found)")
    else:
        seen, todo = set(), [starts[0]]
        while todo:
            v = todo.pop()
            if v in seen:
                continue
            seen.add(v)
            todo += [e["to"] for e in out[v]]
            n = nodes[v]
            if n.get("kind") == "decision" and n.get("exhausted") in nodes:
                todo.append(n["exhausted"])
        lost = [nid for nid, n in nodes.items() if nid not in seen and n.get("kind") != "note"]
        if lost:
            errs.append("not reachable from the start: " + ", ".join(lost))

    # a robot loop is bounded (max_attempts) or it is the item loop;
    # operator-screen loops are free
    for cyc in _cycles(nodes, out):
        if not {nodes[v].get("kind") for v in cyc} & ROBOT:
            continue
        gates = [nodes[v] for v in cyc if nodes[v].get("kind") == "decision"]
        if any("items" in (g.get("on") or {}) or g.get("max_attempts") for g in gates):
            continue
        errs.append("unbounded robot loop " + " → ".join(cyc + [cyc[0]]) +
                    ": give its decision max_attempts and an exhausted node (Workspace unrolls bounded loops)")
    return {"ok": not errs, "errors": errs, "warnings": warns}
