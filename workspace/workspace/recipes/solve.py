"""Recipe parameter solver — step 3 of the bootstrap-project pipeline.

    sudo python3 -m workspace.recipes.solve <project_dir> [--skeleton FILE]

Given a finished scene, this answers the two questions every recipe
needs answered, using CHEAP checks only (closed-form IK + box
arithmetic — no OMPL, no motion, seconds for a whole bench):

  KINEMATICS  does each station's recipe boot (reference IK) with its
              declared left_approach / base_distance — and if not,
              which (la, bd) at rail_span 1 does?  Total failures are
              diagnosed geometrically (rail-frame x/y vs rail range).

  GEOMETRY    per station target, where is the payload top, where is
              the governing collision box top AFTER the planner's
              inflation (+10 mm per face), and therefore what hover
              padding does any pick/place/immerse need to clear it?
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


def _payload_top_z(ws, solid, anchor):
    """World z of the top of whatever stack sits at ``anchor`` —
    the anchor itself when empty."""
    top = solid.pose(anchor)[2]
    try:
        children = solid.children.get(anchor, []) if isinstance(solid.children, dict) else solid.children[anchor]
    except Exception:
        children = []
    for ch in children:
        child = ch["child_solid"]
        try:
            top = max(top, _payload_top_z(ws, child, "top"))
        except Exception:
            pass
    return top


def _governing_box(ws, x, y, above_z):
    """The tallest INFLATED box whose footprint contains (x, y) —
    the box any vertical entry at that spot must clear. Returns
    (inflated_top_z, label) or (None, None)."""
    world, _tool = ws.compute_collision_boxes(PLANNER_PADDING)
    best, label = None, None
    for b in world:
        px, py, pz = b["pose"][0:3]
        lx, ly, lz = b["scale"]
        # world boxes from compute_collision_boxes come axis-aligned in
        # pose+scale form; treat the footprint as axis-aligned (true
        # for every 0/90/180/270-degree bench layout).
        if abs(x - px) <= lx / 2 and abs(y - py) <= ly / 2:
            top = pz + lz / 2
            if top > above_z - 200 and (best is None or top > best):
                best, label = top, f"[{lx:.0f}x{ly:.0f}x{lz:.0f}]@z{top:.0f}"
    return best, label


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


def solve(project_dir, skeleton_path=None, port=5999):
    from workspace.workspace import Workspace
    from workspace.bt.launcher import load_recipes

    launch = yaml.safe_load(open(os.path.join(project_dir, "launch.yaml")))
    scene = [os.path.join(project_dir, s) for s in launch["scene"]]
    # The solve is pure geometry — ALWAYS run the throwaway workspace in
    # sim, whatever the scene says. Booting a simulation:false scene here
    # would grab (or fail to grab) the real hardware and poison every IK
    # with a missing joint source.
    merged = {}
    for path in scene:
        merged.update(_load_yaml_j2(path))
    for cfg in merged.values():
        if isinstance(cfg, dict) and cfg.get("simulation") is False:
            cfg["simulation"] = True
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(merged, f, sort_keys=False)
        merged_path = f.name
    ws = Workspace(config_path=merged_path, port=port)
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

        # ── geometry: hover clearance over the governing inflated box ──
        try:
            solid, anchors = _probe_anchors(robj, comp)
        except Exception as ex:
            print(f"{name:22s} la={str(la):5s} bd={kw['base_distance']!s:>4s} ({how})   "
                  f"geometry probe failed: {type(ex).__name__}")
            solved[name] = {"class": e["class"], "kwargs": kw}
            continue
        need = 0.0
        note = ""
        for a in anchors:
            ax, ay = solid.pose(a)[0], solid.pose(a)[1]
            ptop = _payload_top_z(ws, solid, a)
            btop, blabel = _governing_box(ws, ax, ay, ptop)
            if btop is not None and btop > ptop:
                if btop - ptop > need:
                    need = btop - ptop
                    note = f"box {blabel} over payload top z{ptop:.0f} @ {a}"
        geom = f"min hover padding {need:.0f} ({note})" if need > 0 else "hover clear at any padding"
        print(f"{name:22s} la={str(la):5s} bd={kw['base_distance']!s:>4s} ({how})   {geom}")
        solved[name] = {"class": e["class"], "kwargs": kw}

    print("\nGeometry notes: 'min hover padding' is what ANY pick/place/immerse")
    print("at that station must exceed to keep its planned hover goal outside")
    print(f"the inflated boxes (planner padding {PLANNER_PADDING:.0f}/face). pick/place default")
    print("is 50, immerse default is 10 — raise per call where the minimum is higher.")
    return solved


_DEFAULT_CLASS_PATHS = {
    "Recipe":         "workspace.recipes.recipe.Recipe",
    "Rack":           "workspace.recipes.rack.Rack",
    "ToolRack":       "workspace.recipes.tool_rack.ToolRack",
    "Decapper":       "workspace.recipes.decapper.Decapper",
    "DosingSite":     "workspace.recipes.doser.DosingSite",
    "PipettingSite":  "workspace.recipes.pipetting.PipettingSite",
    "Shaker":         "workspace.recipes.shaker.Shaker",
    "Scale":          "workspace.recipes.scale.Scale",
    "Feeder":         "workspace.recipes.feeder.Feeder",
    "FixedInspector": "workspace.recipes.inspector.FixedInspector",
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
