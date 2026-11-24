"""A project's calibration plan, for the Calibration page — subprocess tool.

Run BY the GUI server (never imported into it), like
``scene_builder/ref_solve.py``: it builds the project's Workspace in
simulation, exactly what ``main.py`` would load, runs its recipes, and
prints one JSON object to stdout — the stations to probe, in order, with
where each point is on the bench (plate, hole, height above the plate),
what stands on each station and must come off, and how many points
``core/calibrate.json`` already holds.

The plan is derived, so every project has one with nothing written for
it — builder projects included: every recipe whose component carries
calibration points (``clb_0``, ``clb_1`` …) is a station. Plain stations
are probed in simple mode with the probe rod; tool racks in precise mode
with the vertical probe and the three-pocket jig; points that face
sideways (a hotel's) in precise mode with the horizontal probe. Stations
sharing one ``calibration_name`` (same component, same approach
settings) are probed once.

A project that wants something else adds ``calibration.py`` next to its
``main.py`` (or next to its recipes file, when projects share one) with

    def plan(p, workspace, rcp):
        ...                     # reorder p["order"], drop or edit p["stations"]
        return p

called with the derived plan before it is printed.

Usage: python3 plan.py <path/to/main.py>
"""
import importlib.util
import json
import math
import os
import pkgutil
import re
import sys
import tempfile
import traceback

import yaml
from workspace.j2 import render_text

MAX_POINTS = 8          # a station with more (an SBS adapter has 24) gets its corners and middle
PROBE = {"simple": "probe_rod", "precise": "probe_vertical", "sideways": "probe_horizontal"}


def _merged(paths):
    """The scene the way Workspace merges it: j2 rendered, later files override."""
    cfgs = {}
    for p in paths:
        text = open(p).read()
        if p.endswith(".j2") or "{%" in text or "{{" in text:
            text = render_text(text, os.path.dirname(os.path.abspath(p)))
        cfgs.update(yaml.safe_load(text) or {})
    return cfgs


def _kids(ws, name):
    """The components standing on ``name`` (one level)."""
    out = []
    for solid in (getattr(ws.components.get(name), "assembly", None) or {}).values():
        for ks in (getattr(solid, "children", None) or {}).values():
            out += [k["child_solid"].component for k in ks if k.get("child_solid") is not None]
    return [k for k in dict.fromkeys(out) if k != name]


def _tree(ws, name):
    """Everything standing on ``name``, all the way up."""
    out = []
    for k in _kids(ws, name):
        out += [k] + _tree(ws, k)
    return out


def _nice(ctype):
    return (ctype or "").replace("_", " ")


def _spread(xy):
    """The four corners and the middle of a big point set, then the rest."""
    names = list(xy)
    if len(names) <= MAX_POINTS:
        return names, []
    xs, ys = [v[0] for v in xy.values()], [v[1] for v in xy.values()]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    pick = []
    for tx, ty in ((min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys)), (cx, cy)):
        a = min(xy, key=lambda k: (xy[k][0] - tx) ** 2 + (xy[k][1] - ty) ** 2)
        if a not in pick:
            pick.append(a)
    pick.sort(key=lambda a: int(a.split("_")[1]))
    return pick, [a for a in names if a not in pick]


def _hook(dirs):
    """The project's ``calibration.py`` plan(), if it has one."""
    for d in dirs:
        f = os.path.join(d, "calibration.py")
        if os.path.isfile(f):
            spec = importlib.util.spec_from_file_location("project_calibration", f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if callable(getattr(mod, "plan", None)):
                return f, mod.plan
    return None, None


def build(main_py, port=5997):
    project_dir = os.path.dirname(os.path.abspath(main_py))
    sys.path.insert(0, project_dir)
    comp_dir = os.path.join(project_dir, "components")
    if os.path.isdir(comp_dir):                     # project-local components, as main.py registers them
        for mod in pkgutil.walk_packages([comp_dir], prefix="components."):
            if not mod.name.rsplit(".", 1)[-1].startswith("_"):
                importlib.import_module(mod.name)

    with open(os.path.join(project_dir, "launch.yaml")) as f:
        launch = yaml.safe_load(f) or {}
    scene = launch.get("scene") or []
    scene = [os.path.normpath(os.path.join(project_dir, p)) for p in ([scene] if isinstance(scene, str) else scene)]
    recipes_path = os.path.normpath(os.path.join(project_dir, launch.get("recipes", "recipes.j2")))

    cfgs = _merged(scene)
    for cfg in cfgs.values():                       # nothing touches hardware from a page load
        if isinstance(cfg, dict):
            cfg["simulation"] = True
    fd, tmp = tempfile.mkstemp(prefix="calibration_plan_", suffix=".yaml")
    os.close(fd)
    try:
        with open(tmp, "w") as f:
            yaml.safe_dump(cfgs, f, sort_keys=False)
        from workspace.workspace import Workspace
        ws = Workspace(config_path=[tmp], port=port, project_dir=project_dir)
    finally:
        os.unlink(tmp)
    core = ws.components["core"]

    # recipes one by one, so one that fails to build doesn't hide the rest
    defs = yaml.safe_load(render_text(open(recipes_path).read(), os.path.dirname(recipes_path))) or {}
    rcp, errors = {}, {}
    for name, spec in defs.items():
        kwargs = dict((spec or {}).get("kwargs") or {})
        try:
            mod_name, cls_name = (spec or {}).get("class", "").rsplit(".", 1)
            cls = getattr(importlib.import_module(mod_name), cls_name)
            comp = kwargs.pop("component", None)
            rcp[name] = cls(workspace=ws, core=core, component=ws.components[comp] if comp else None, **kwargs)
        except Exception as ex:
            errors[name] = f"{type(ex).__name__}: {ex}"

    # fixture plates: footprint, top and hole grid, in world
    world, _, _ = ws.compute_collision_boxes(0)
    foot = {}
    for b in world:
        x, y, z = b["pose"][:3]
        sx, sy, sz = b["scale"]
        foot.setdefault(b.get("componentName"), []).append(
            [round(x - sx / 2, 1), round(y - sy / 2, 1), round(sx, 1), round(sy, 1), round(z + sz / 2, 1)])
    plates = {}
    for n, c in ws.components.items():
        if not str(getattr(c, "type", n)).startswith("fixture_plate") and not n.startswith("fixture_plate"):
            continue
        body = c.assembly.get("body")
        if body is None or n not in foot:
            continue
        holes = {a: [round(v, 2) for v in body.pose(anchor=a)[:2]] for a in body.anchors if re.fullmatch(r"[A-Z]\d+", a)}
        plates[n] = dict(box=foot[n][0], top=foot[n][0][4], holes=holes)

    def over(p):
        for n, pl in plates.items():
            for h, q in pl["holes"].items():
                if abs(q[0] - p[0]) < 0.5 and abs(q[1] - p[1]) < 0.5:
                    return n, h
        return None, None

    def under(xy):
        for n, pl in plates.items():
            x0, y0, sx, sy, _ = pl["box"]
            if x0 <= xy[0] <= x0 + sx and y0 <= xy[1] <= y0 + sy:
                return n
        return None

    stored = {}
    cal_file = getattr(getattr(core, "calibration", None), "file_path", None)
    if cal_file and os.path.isfile(cal_file):
        try:
            stored = (json.load(open(cal_file)) or {}).get("data") or {}
        except Exception:
            stored = {}

    stations, seen, shares = {}, {}, {}
    for alias, r in rcp.items():
        comp = getattr(r, "component", None)
        if comp is None:
            continue
        clb = {}
        for sol in comp.assembly.values():
            for a in sol.anchors:
                if re.fullmatch(r"clb_\d+", a) and a not in clb:
                    clb[a] = sol
        if not clb:
            continue
        cname = getattr(r, "calibration_name", None) or comp.name
        if cname in seen:                           # same points as a station already planned
            shares.setdefault(seen[cname], []).append(alias)
            continue
        seen[cname] = alias
        cls = type(r).__name__
        names = sorted(clb, key=lambda a: int(a.split("_")[1]))
        side = [a for a in names if len(clb[a].anchors[a]) >= 5
                and (abs(clb[a].anchors[a][3]) > 1 or abs(clb[a].anchors[a][4]) > 1)]
        up = [a for a in names if a not in side]
        world_xyz = {a: clb[a].pose(anchor=a) for a in names}
        probe, extra = _spread({a: world_xyz[a][:2] for a in up})
        if cls == "ToolRack":
            mode, probe, extra = "precise", [a for a in up if a == "clb_0"] or up[:1], []
        else:
            mode = "simple"
        points = []
        for a in probe + side:
            p = world_xyz[a]
            pl, hole = (None, None) if a in side else over(p)   # a plate hole, or the station itself (a device top, a shelf)
            points.append(dict(clb=a, plate=pl or under(p[:2]), hole=hole, xyz=[round(v, 1) for v in p[:3]],
                               height=round(p[2] - plates[pl]["top"], 1) if pl else None,
                               mode="precise" if a in side else mode,
                               probe=PROBE["sideways"] if a in side else PROBE[mode]))
        on = [k for k in _tree(ws, comp.name)            # real things only: a collision_box is a keep-out
              if getattr(ws.components[k], "type", None) != "collision_box"]
        counts = {}
        for k in on:
            t = _nice(getattr(ws.components[k], "type", k))
            counts[t] = counts.get(t, 0) + 1
        fp = foot.get(comp.name) or []
        if not fp:                                  # an adapter without a collision box: around its points
            xs, ys = [p["xyz"][0] for p in points], [p["xyz"][1] for p in points]
            fp = [[min(xs) - 25, min(ys) - 25, max(xs) - min(xs) + 50, max(ys) - min(ys) + 50, 0]]
        stations[alias] = dict(
            handle=alias, title=alias.replace("_", " "), recipe=cls, component=comp.name,
            component_type=getattr(comp, "type", None), mode=mode,
            probe=points[0]["probe"] if points else PROBE[mode],
            calibration_name=cname, stored=len(stored.get(cname) or []),
            points=points, more=extra, footprint=fp,
            remove=[f"{n} × {t}" if n > 1 else t for t, n in counts.items()], remove_components=on,
            plate=under(comp.assembly["body"].pose(anchor="center")[:2]) if "body" in comp.assembly else None,
            lift_after=cls == "Decapper" or "decapper" in comp.name,
            left_approach=getattr(r, "left_approach", None), base_distance=getattr(r, "base_distance", None),
            rail_step=getattr(r, "rail_step", None), rail_span=getattr(r, "rail_span", None))
    for alias, also in shares.items():
        stations[alias]["shares"] = also

    # simple stations first (one probe rod), then the tool racks (the vertical probe), then sideways points
    rank = {"probe_rod": 0, "probe_vertical": 1, "probe_horizontal": 2}
    order = sorted(stations, key=lambda h: rank.get(stations[h]["probe"], 3))
    rail = getattr(core, "rail_cfg", None) or {}
    out = dict(ok=True, project=launch.get("project_name") or os.path.basename(project_dir),
               main=os.path.abspath(main_py), scene=[os.path.relpath(p, project_dir) for p in scene],
               recipes=os.path.relpath(recipes_path, project_dir),
               calibration_file=str(cal_file) if cal_file else None,
               rail_axis=rail.get("axis"), plates=plates, stations=stations, order=order,
               recipe_errors=errors, source="derived")

    hook_file, hook = _hook(dict.fromkeys([project_dir, os.path.dirname(recipes_path)]))
    if hook:
        out = hook(out, ws, rcp) or out
        out["source"] = os.path.relpath(hook_file, project_dir)
    return out


def _exit(code=0):
    """Leave now — a normal exit joins dorna2's non-daemon sim-api
    thread and hangs with the answer already on stdout."""
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: plan.py <path/to/main.py>"}))
        _exit(2)
    real_stdout, sys.stdout = sys.stdout, sys.stderr  # platform prints go to stderr; stdout is the answer
    try:
        res = build(sys.argv[1])
    except Exception as ex:
        traceback.print_exc()
        res = {"ok": False, "error": f"{type(ex).__name__}: {ex}"}
    sys.stdout = real_stdout
    print(json.dumps(res))
    _exit(0 if res.get("ok") else 1)
