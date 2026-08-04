"""Persistent IK worker for the builder's TCP drag — one per project.

Run BY the builder server as a subprocess (never imported: the builder
patches dorna2 with preview stubs). Builds the project's sim Workspace
ONCE, solves every recipe's reference (same as ref_solve), then answers
drag requests over stdin/stdout as JSON lines using THE recipe's own
``core.IK`` kwargs — rail sweep, approach side, reference branch, all
verbatim from recipes.j2.

Handshake (stdout, first line):
    {"ready": true, "recipes": {name: {"target_anchor": ..., "target_offset": [...],
        "target_solid_name": ..., "anchor_world": [...], "ref_joints": [...] | null}}}

Request  (stdin):  {"recipe": "anode", "offset": [x,y,z,a,b,c]}
Response (stdout): {"ok": true, "status": 2, "joints": [...], "solids": {name: world_pose6}}
"""
import importlib
import json
import os
import pkgutil
import sys
import tempfile
from copy import deepcopy

import yaml
from jinja2 import Template


def _pose6(solid, anchor=None):
    p = solid.pose(anchor=anchor) if anchor else solid.pose()
    return [float(v) for v in list(p)[:6]]


def main(project_dir):
    # stdout is the PROTOCOL channel — everything the platform prints
    # during Workspace build / recipe init / IK goes to stderr.
    proto = sys.stdout
    sys.stdout = sys.stderr
    project_dir = os.path.abspath(project_dir)
    sys.path.insert(0, project_dir)
    comp_dir = os.path.join(project_dir, "components")
    if os.path.isdir(comp_dir):
        for mod in pkgutil.iter_modules([comp_dir]):
            if not mod.name.startswith("_"):
                importlib.import_module(f"components.{mod.name}")

    with open(os.path.join(project_dir, "launch.yaml")) as f:
        launch = yaml.safe_load(f) or {}
    scene = launch.get("scene") or []
    if isinstance(scene, str):
        scene = [scene]
    cfgs = {}
    for rel in scene:
        text = open(os.path.join(project_dir, rel)).read()
        if rel.endswith(".j2") or "{%" in text or "{{" in text:
            text = Template(text).render()
        cfgs.update(yaml.safe_load(text) or {})
    for cfg in cfgs.values():
        if isinstance(cfg, dict):
            cfg["simulation"] = True

    fd, tmp = tempfile.mkstemp(prefix="builder_ik_", suffix=".yaml")
    os.close(fd)
    try:
        with open(tmp, "w") as f:
            yaml.safe_dump(cfgs, f, sort_keys=False)
        from workspace.workspace import Workspace
        ws = Workspace(config_path=[tmp], port=5997)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    core = ws.components["core"]

    # recipe defs: class DEFAULTS merged with recipes.j2 kwargs — the
    # exact prm a Recipe.__init__ would see. Instantiate each (solves /
    # passes through ref_joints) so the drag seeds from the recipe's
    # own reference branch.
    rp = os.path.join(project_dir, launch.get("recipes", "recipes.j2"))
    defs = yaml.safe_load(Template(open(rp).read()).render()) or {}
    recipes, info = {}, {}
    for name, spec in defs.items():
        try:
            mod_name, cls_name = (spec or {}).get("class", "").rsplit(".", 1)
            cls = getattr(importlib.import_module(mod_name), cls_name)
            kwargs = dict((spec or {}).get("kwargs") or {})
            prm = deepcopy(getattr(cls, "DEFAULTS", {}))
            prm.update(kwargs)
            comp_name = kwargs.pop("component", None)
            comp = ws.components[comp_name] if comp_name else None
            r = cls(workspace=ws, core=core, component=comp, **kwargs)
            rj = getattr(r, "ref_joints", None)
            row = {
                "component": comp_name,
                "target_solid_name": prm.get("target_solid_name", "body"),
                "target_anchor": prm.get("target_anchor", "center"),
                "target_offset": prm.get("target_offset", [0, 0, 50, 0, 180, 0]),
                "base_distance": prm.get("base_distance", 350),
                "rail_step": prm.get("rail_step", 0),
                "rail_span": prm.get("rail_span", 0),
                "left_approach": prm.get("left_approach", True),
                "ref_joints": [float(v) for v in rj] if rj is not None else None,
            }
            if comp is not None:
                solid = comp.assembly[row["target_solid_name"]]
                row["anchor_world"] = _pose6(solid, row["target_anchor"])
                recipes[name] = (row, solid)
            info[name] = row
        except Exception as ex:
            info[name] = {"error": f"{type(ex).__name__}: {ex}"}

    print(json.dumps({"ready": True, "recipes": info}), file=proto, flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            name = req["recipe"]
            row, solid = recipes[name]
            J, C = core.IK(
                target_solid=solid,
                target_anchor=row["target_anchor"],
                target_offset=[float(v) for v in req["offset"]],
                base_distance=row["base_distance"],
                rail_step=row["rail_step"],
                rail_span=row["rail_span"],
                ref_joints=row["ref_joints"],
                left_approach=row["left_approach"],
            )
            out = {"ok": C == 2, "status": int(C)}
            if C == 2 and J is not None:
                out["joints"] = [float(v) for v in J]
                core.robot_api.joints = list(out["joints"])
                core.update_pose()
                out["solids"] = {sn: _pose6(s) for sn, s in core.assembly.items()}
            print(json.dumps(out), file=proto, flush=True)
        except Exception as ex:
            print(json.dumps({"ok": False, "error": f"{type(ex).__name__}: {ex}"}),
                  file=proto, flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
