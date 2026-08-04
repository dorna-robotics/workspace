"""Solve every recipe's reference joints for a project — subprocess tool.

Run BY the builder server (never imported into it): the builder patches
dorna2/camera/path_planning with preview stubs, and the solve must see
the REAL platform. This script builds the project's Workspace in
simulation (exactly what ``main.py`` would load, with every component
forced to ``simulation: true``), runs ``load_recipes`` — so ref joints
come from the SAME ``Recipe.__init__`` path the project runs at boot:
pinned ``ref_joints`` pass through, unpinned ones get the real IK
sweep — and prints one JSON object to stdout:

    {"ok": true, "recipes": {"anode": {"ref_joints": [...], "pinned": true,
                                       "class": "workspace.recipes.scale.Scale"},
                             "robot": {"ref_joints": null, "pinned": false, ...}}}

Recipes with no component have no reference pose → ``ref_joints: null``.
A recipe whose init raises reports {"error": "..."} instead of killing
the whole solve.

Usage: python3 ref_solve.py <project_dir>
"""
import importlib
import json
import os
import pkgutil
import sys
import tempfile

import yaml
from jinja2 import Template


def main(project_dir):
    project_dir = os.path.abspath(project_dir)
    sys.path.insert(0, project_dir)

    # project-local components — same registration main.py does
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
    scene = [os.path.join(project_dir, p) for p in scene]

    # Merge the scene the way Workspace does (render j2, later files
    # override), then force EVERY component into simulation so nothing
    # touches hardware from a builder click.
    cfgs = {}
    for p in scene:
        text = open(p).read()
        if p.endswith(".j2") or "{%" in text or "{{" in text:
            text = Template(text).render()
        cfgs.update(yaml.safe_load(text) or {})
    for name, cfg in cfgs.items():
        if isinstance(cfg, dict):
            cfg["simulation"] = True

    fd, tmp = tempfile.mkstemp(prefix="builder_ref_", suffix=".yaml")
    os.close(fd)
    try:
        with open(tmp, "w") as f:
            yaml.safe_dump(cfgs, f, sort_keys=False)

        from workspace.workspace import Workspace
        from workspace.bt.launcher import load_recipes

        ws = Workspace(config_path=[tmp], port=5998)
        core = ws.components["core"]

        recipes_path = os.path.join(project_dir, launch.get("recipes", "recipes.j2"))
        out = {}
        # load_recipes instantiates everything in one shot; a failing
        # recipe raises out of it — so instantiate per-recipe instead,
        # reusing its parsing by loading the rendered yaml ourselves.
        text = open(recipes_path).read()
        rendered = Template(text).render()
        defs = yaml.safe_load(rendered) or {}
        for name, spec in defs.items():
            cls_path = (spec or {}).get("class", "")
            kwargs = dict((spec or {}).get("kwargs") or {})
            row = {"class": cls_path, "pinned": kwargs.get("ref_joints") is not None}
            try:
                mod_name, cls_name = cls_path.rsplit(".", 1)
                cls = getattr(importlib.import_module(mod_name), cls_name)
                comp = kwargs.pop("component", None)
                if comp is not None:
                    comp = ws.components[comp]
                r = cls(workspace=ws, core=core, component=comp, **kwargs)
                rj = getattr(r, "ref_joints", None)
                row["ref_joints"] = [float(v) for v in rj] if rj is not None else None
            except Exception as ex:
                row["error"] = f"{type(ex).__name__}: {ex}"
            out[name] = row
        print(json.dumps({"ok": True, "recipes": out}))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: ref_solve.py <project_dir>"}))
        sys.exit(1)
    try:
        main(sys.argv[1])
    except Exception as ex:
        print(json.dumps({"ok": False, "error": f"{type(ex).__name__}: {ex}"}))
        sys.exit(0)
