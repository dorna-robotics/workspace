"""Recipe parameter solver — step 3 of the bootstrap-project pipeline.

    sudo python3 -m workspace.recipes.solve <project_dir> [--skeleton FILE]

Given a finished scene, this answers the two questions every recipe
needs answered, using CHEAP checks only (closed-form IK + box
arithmetic — no OMPL, no motion, seconds for a whole bench):

  KINEMATICS  does each station's recipe boot (reference IK) with its
              declared left_approach / base_distance — and if not,
              which (la, bd) at rail_span 1 does?  Total failures are
              diagnosed geometrically (rail-frame x/y vs rail range).

  GEOMETRY    per station target, march along the anchor's APPROACH
              RAY (its local +z — world-vertical only for upright
              anchors; tilted for feeders/presenters) and find where
              the ray exits every inflated collision box (+10 mm per
              face). Boxes owned by the payload stack itself (the tube
              being entered/picked, its cap, its own boxes) are
              excluded by componentName. Two numbers per station, both
              including a hard 20 mm margin (the retract knife-edge
              lesson — sim passes boundary-exact endpoints, real
              joints do not):
                min pad   what any pick/place/immerse hover needs
                min end   how far above the payload ANY motion must
                          END (retracts, exits) — a stranded arm
                          inside a box poisons the next plan's start.
              This layer is robot-agnostic — pure scene arithmetic.

Modes:
  default            solve the project's existing recipes.j2 entries
                     and REPORT (no files touched).
  --skeleton FILE    solve a skeleton (name -> {class, component, +any
                     kwargs}) and print a ready recipes.j2 body to
                     stdout for review.

The tool never writes project files — the operator (or the AI running
the pipeline) reviews the report and applies values deliberately,
matching the explicit-parameters rule.

Why endpoint checks suffice: measured across real projects, every
recipe-layer failure was an ENDPOINT failure — hover goal inside an
inflated box, exit left inside a box, reference IK unreachable. Valid
endpoints with no connecting path is rare enough to leave for the
operator's eyes on the bench (step 5).
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import os
import pkgutil
import sys

import yaml
from jinja2 import Template


# The planner's collision inflation — one padding per box face
# (workspace.compute_collision_boxes). Kept in lockstep with
# Core.motion_plan's default.
PLANNER_PADDING = 10.0

BD_CANDIDATES = (50, 75, 100, 125, 150, 175, 200, 250, 300, 350)


def _load_yaml_j2(path):
    text = open(path).read()
    if path.endswith(".j2") or "{%" in text or "{{" in text:
        text = Template(text).render()
    return yaml.safe_load(text) or {}


def _import_class(dotted):
    mod, cls = dotted.rsplit(".", 1)
    return getattr(importlib.import_module(mod), cls)


def _try_boot(cls, ws, core, comp, kwargs):
    """Instantiate the recipe; return the instance or None. The instance
    is REUSED for the geometry probe — booting twice with identical
    kwargs is both wasteful and, for tool racks, order-dependent."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            return cls(workspace=ws, core=core, component=comp, **kwargs)
    except Exception:
        return None


def _rail_frame_xy(core, solid, anchor):
    p = solid.pose(anchor=anchor, in_frame=core.rail_base)
    c0 = core.rail_base.pose(anchor="carriage")
    return p[0] - c0[0], p[1] - c0[1]


# Hard clearance margin on every reported number — endpoints that sit
# exactly on an inflated box surface pass in sim and fail on real
# joints (the retract knife edge, measured on the bna bench).
MARGIN = 20.0
RAY_HORIZON = 400.0
RAY_STEP = 2.0


def _anchor_ray(solid, anchor):
    """World (origin, unit direction) of the anchor's local +z — the
    axis recipe paddings actually extend along (a_pad is an offset in
    the ANCHOR frame, not world-vertical: tilted for the capfeeder's
    -48 deg place, vertical for rack slots)."""
    import numpy as np
    from dorna2 import pose as dpose
    T = np.array(dpose.xyzabc_to_T(list(solid.pose(anchor))))
    origin, direction = T[:3, 3], T[:3, 2]
    # The corridor leaves the station AWAY from the bench. Some anchors
    # (tool racks — tools hang mouth-down) have their local +z pointing
    # INTO the plate; marching that way reports the bench itself as an
    # obstacle. Sign the axis upward.
    if direction[2] < 0:
        direction = -direction
    return origin, direction


def _stack_members(solid, anchor):
    """(component names, top points) of everything stacked at ``anchor``
    — the payload the operation carries/enters. Their collision boxes
    are NOT obstacles for this station's approach ray."""
    import numpy as np
    names, pts = set(), []

    def walk(s):
        ch_map = s.children if isinstance(s.children, dict) else {}
        for lst in ch_map.values():
            for ch in lst:
                c = ch["child_solid"]
                comp = getattr(c, "component", None)
                if comp:
                    names.add(comp)
                try:
                    pts.append(np.array(list(c.pose("top"))[:3]))
                except Exception:
                    pass
                walk(c)

    try:
        anchor_children = (solid.children.get(anchor, [])
                           if isinstance(solid.children, dict) else solid.children[anchor])
    except Exception:
        anchor_children = []
    for ch in anchor_children:
        c = ch["child_solid"]
        comp = getattr(c, "component", None)
        if comp:
            names.add(comp)
        try:
            pts.append(np.array(list(c.pose("top"))[:3]))
        except Exception:
            pass
        walk(c)
    return names, pts


def _ray_clearance(ws, solid, anchor):
    """March the approach ray; return (d_in, label, h_stack, h_container).

    d_in: the largest distance along the ray still inside ANY inflated
    obstacle box (payload-stack boxes excluded by componentName) —
    every endpoint of every motion at this station must sit beyond
    d_in + MARGIN along the ray.
    """
    import numpy as np
    from dorna2 import pose as dpose
    origin, direction = _anchor_ray(solid, anchor)
    stack_names, stack_pts = _stack_members(solid, anchor)

    h_stack = 0.0
    for pt in stack_pts:
        h_stack = max(h_stack, float(np.dot(pt - origin, direction)))
    h_container = 0.0
    try:
        top_pt = np.array(list(solid.pose("top"))[:3])
        h_container = max(0.0, float(np.dot(top_pt - origin, direction)))
    except Exception:
        pass

    world, _tool = ws.compute_collision_boxes(PLANNER_PADDING)
    ts = np.arange(0.0, RAY_HORIZON, RAY_STEP)
    pts = origin[None, :] + ts[:, None] * direction[None, :]
    pts_h = np.hstack([pts, np.ones((len(ts), 1))])

    d_in, label = 0.0, None
    for b in world:
        if b.get("componentName") in stack_names:
            continue
        T_inv = np.linalg.inv(np.array(dpose.xyzabc_to_T(list(b["pose"]))))
        local = (T_inv @ pts_h.T).T[:, :3]
        half = np.array(b["scale"]) / 2.0
        inside = np.all(np.abs(local) <= half, axis=1)
        if inside.any():
            t_max = float(ts[inside][-1]) + RAY_STEP
            if t_max > d_in:
                lx, ly, lz = b["scale"]
                d_in, label = t_max, f"[{lx:.0f}x{ly:.0f}x{lz:.0f}]({b.get('componentName', '?')})"
    return d_in, label, h_stack, h_container


# Per-class probe: which anchor(s) represent the station's target.
def _probe_anchors(recipe_obj, comp):
    try:
        attached, _sn = recipe_obj._resolve_attached_component()
        slots = attached.slot["body"]
        return attached.assembly["body"], [slots[0], slots[len(slots) // 2], slots[-1]]
    except Exception:
        pass
    # the recipe's declared target solid (e.g. the shaker's "rotating")
    tsn = getattr(recipe_obj, "component", None) and getattr(recipe_obj, "ref_joints", None)
    solid = comp.assembly.get("body") or next(iter(comp.assembly.values()))
    for sn, sol in comp.assembly.items():
        slots = [a for a in sol.anchors
                 if len(a) in (2, 3) and a[0].isalpha() and a[1:].isdigit()]
        if slots:
            return sol, sorted(slots)[:1] + sorted(slots)[-1:]
    for a in ("place", "top"):
        if a in solid.anchors:
            return solid, [a]
    return solid, [next(iter(solid.anchors))]


def load_launch(project_dir):
    """Read launch.yaml AND register the project's local components —
    every tool that boots a project scene needs both, and a scene
    referencing a project-local ``type:`` dies with "Unknown component
    type" otherwise (mirrors main.py's _register_project_components)."""
    comp_dir = os.path.join(project_dir, "components")
    if os.path.isdir(comp_dir):
        if project_dir not in sys.path:
            sys.path.insert(0, project_dir)
        for mod in pkgutil.iter_modules([comp_dir]):
            if not mod.name.startswith("_"):
                importlib.import_module(f"components.{mod.name}")
    return yaml.safe_load(open(os.path.join(project_dir, "launch.yaml")))


def merged_sim_scene(project_dir, launch=None):
    """Render + merge the project's scene files with every device forced
    to sim, into one temp yaml. Validation tooling must NEVER grab (or
    fail to grab) real hardware — a missing joint source poisons every
    IK. Returns the temp file path."""
    launch = launch or load_launch(project_dir)
    merged = {}
    for rel in launch["scene"]:
        merged.update(_load_yaml_j2(os.path.join(project_dir, rel)))
    for cfg in merged.values():
        if isinstance(cfg, dict) and cfg.get("simulation") is False:
            cfg["simulation"] = True
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(merged, f, sort_keys=False)
        return f.name


def solve(project_dir, skeleton_path=None, port=5999):
    from workspace.workspace import Workspace
    from workspace.bt.launcher import load_recipes

    launch = load_launch(project_dir)
    ws = Workspace(config_path=merged_sim_scene(project_dir, launch), port=port)
    core = ws.components["core"]

    if skeleton_path:
        entries = yaml.safe_load(open(skeleton_path)) or {}
        entries = {
            name: {"class": e["class"] if "." in e["class"]
                   else _DEFAULT_CLASS_PATHS[e["class"]],
                   "kwargs": {k: v for k, v in e.items() if k not in ("class",)}}
            for name, e in entries.items()
        }
    else:
        rec = _load_yaml_j2(os.path.join(project_dir, launch.get("recipes", "recipes.j2")))
        entries = {n: {"class": e["class"], "kwargs": dict(e.get("kwargs", {}))}
                   for n, e in rec.items()}

    print(f"\n=== recipe solve: {project_dir}  ({len(entries)} entries) ===\n")
    solved = {}
    for name, e in entries.items():
        kw = dict(e["kwargs"])
        comp_name = kw.pop("component", None)
        if comp_name is None:
            print(f"{name:22s} (no component — robot-level handle)          OK")
            solved[name] = {"class": e["class"], "kwargs": kw}
            continue
        if comp_name not in ws.components:
            print(f"{name:22s} component {comp_name!r} NOT IN SCENE          ** FIX SCENE **")
            continue
        comp = ws.components[comp_name]
        cls = _import_class(e["class"])

        # ── kinematics: declared values first, then the sweep ──
        la0 = kw.get("left_approach", True)
        bd0 = kw.get("base_distance")
        hit, robj = None, _try_boot(cls, ws, core, comp, kw)
        if robj is not None:
            hit = (la0, bd0, "declared")
        else:
            probe = dict(kw)
            probe.setdefault("rail_step", 25)
            probe["rail_span"] = kw.get("rail_span", 1)
            for la in (la0, not la0):
                for bd in BD_CANDIDATES:
                    probe["left_approach"], probe["base_distance"] = la, bd
                    robj = _try_boot(cls, ws, core, comp, probe)
                    if robj is not None:
                        hit = (la, bd, "swept")
                        break
                if hit:
                    break
        if hit is None:
            sol = comp.assembly.get("body") or next(iter(comp.assembly.values()))
            anchor = "place" if "place" in sol.anchors else "center"
            dx, dy = _rail_frame_xy(core, sol, anchor)
            print(f"{name:22s} UNREACHABLE — rail-frame x={dx:.0f} y={dy:.0f}, "
                  f"rail [{core.rail_min}, {core.rail_max}]              ** FIX SCENE **")
            continue

        la, bd, how = hit
        kw["left_approach"], kw["base_distance"] = la, bd if bd is not None else kw.get("base_distance")
        if how == "swept":
            # the sweep succeeded WITH these rail params — they are part
            # of the answer, not incidental
            kw["rail_step"] = kw.get("rail_step", 25)
            kw["rail_span"] = kw.get("rail_span", 1)
        kw["component"] = comp_name

        # ── geometry: ray clearance with the hard margin ──
        try:
            solid, anchors = _probe_anchors(robj, comp)
        except Exception as ex:
            print(f"{name:22s} la={str(la):5s} bd={kw['base_distance']!s:>4s} ({how})   "
                  f"geometry probe failed: {type(ex).__name__}")
            solved[name] = {"class": e["class"], "kwargs": kw}
            continue
        need_pad, need_end, note = 0.0, 0.0, ""
        for a in anchors:
            try:
                d_in, blabel, h_stack, h_cont = _ray_clearance(ws, solid, a)
            except Exception:
                continue
            if d_in <= 0:
                continue
            h_base = max(h_stack, h_cont)
            np_ = max(0.0, d_in + MARGIN - h_base)
            ne_ = max(0.0, d_in + MARGIN - h_stack)
            if np_ > need_pad or ne_ > need_end:
                need_pad, need_end = max(need_pad, np_), max(need_end, ne_)
                note = f"{blabel} holds the ray to {d_in:.0f} @ {a}"
        if need_pad > 0 or need_end > 0:
            geom = (f"min pad {need_pad:.0f} / min end {need_end:.0f} above load "
                    f"({note}; incl {MARGIN:.0f} margin)")
        else:
            geom = f"ray clear (incl {MARGIN:.0f} margin)"
        print(f"{name:22s} la={str(la):5s} bd={kw['base_distance']!s:>4s} ({how})   {geom}")
        solved[name] = {"class": e["class"], "kwargs": kw}

    print(f"\nGeometry notes (all numbers include the {MARGIN:.0f} mm margin):")
    print("  min pad — what any pick/place/immerse hover padding at that station")
    print("            must reach (pick/place default 50, immerse default 10).")
    print("  min end — how far above the payload ANY motion must END there")
    print("            (retract distances, exit heights): an arm stranded")
    print("            inside an inflated box poisons the next plan's start.")
    print("  Measured along the anchor's approach ray, tilted stations included.")
    return solved


_DEFAULT_CLASS_PATHS = {
    "Recipe":         "workspace.recipes.recipe.Recipe",
    "Rack":           "workspace.recipes.rack.Rack",
    "ToolRack":       "workspace.recipes.tool_rack.ToolRack",
    "Decapper":       "workspace.recipes.decapper.Decapper",
    "DosingSite":  "workspace.recipes.doser.DosingSite",
    "Shaker":         "workspace.recipes.shaker.Shaker",
    "Scale":          "workspace.recipes.scale.Scale",
    "Feeder":         "workspace.recipes.feeder.Feeder",
    "Inspector": "workspace.recipes.inspector.Inspector",
    "BarcodeReader":  "workspace.recipes.barcode_reader.BarcodeReader",
    "Printer":        "workspace.recipes.printer.Printer",
}


def main():
    ap = argparse.ArgumentParser(description="Solve recipe parameters against a scene (cheap checks, no motion).")
    ap.add_argument("project", help="project directory (holds launch.yaml)")
    ap.add_argument("--skeleton", help="skeleton yaml: name -> {class, component, ...} — prints a recipes body")
    ap.add_argument("--port", type=int, default=5999, help="viewer port for the throwaway workspace (default 5999)")
    args = ap.parse_args()
    solved = solve(os.path.abspath(args.project), args.skeleton, port=args.port)
    if args.skeleton:
        print("\n# ── solved recipes body (review, then paste) ─────────────────")
        for name, e in solved.items():
            print(f"{name}:")
            print(f"  class: {e['class']}")
            print(f"  kwargs: {e['kwargs']}")


if __name__ == "__main__":
    main()
