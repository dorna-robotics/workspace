"""Protocol dryrun — the last software gate before the bench, as a command.

    sudo python3 -m workspace.bt.dryrun <project_dir> [--batch 2] [--kw k=v ...]

Boots the project's scene in a throwaway SIM workspace (hardware never
touched — simulation forced on every device), loads the recipes, and
runs the REAL protocol through the REAL engine: PDDL planning, CP-SAT
scheduling, checks, the BT leaf engine, and — the part nothing cheaper
covers — REAL motion planning for every hop. Only the motion PLAYBACK
is stubbed (moves complete instantly), so a full batch runs in minutes
instead of hours.

This is the gate that catches what endpoint arithmetic cannot: recipe
machinery against live scene state, IK corridors with the actual tools
mounted, and residual planner failures. Green here means the bench run
is judging path SHAPE, not hunting logic bugs.

Same kwarg handling as workspace.bt.replay. Exit code 0 on
Status.SUCCESS only.
"""

from __future__ import annotations

import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser(description="Run the real protocol in sim with real planning, stubbed playback.")
    ap.add_argument("project", help="project directory (holds launch.yaml)")
    ap.add_argument("--batch", type=int, default=2, help="batch size (default 2 — exercises interleaving)")
    ap.add_argument("--kw", action="append", default=[], help="kwarg override name=value (repeatable)")
    ap.add_argument("--port", type=int, default=5998, help="viewer port for the throwaway workspace")
    args = ap.parse_args()
    project = os.path.abspath(args.project)

    from workspace.recipes.solve import load_launch, merged_sim_scene
    from workspace.bt.replay import resolve_kwargs
    launch = load_launch(project)
    kwargs = resolve_kwargs(launch, batch=args.batch, overrides=args.kw)

    # Stub PLAYBACK only: moves land at their targets instantly. Planning,
    # IK, collision, attach/detach and device sims all stay real.
    from workspace.components.core.core import SimulationAPI

    def _jump(self, joint, **kw):
        self.joints = [float(v) for v in joint]; return 2

    def _jump_chain(self, joints, vajs, corners, **kw):
        self.joints = [float(v) for v in joints[-1]]; return 2

    def _jump_smove(self, points, **kw):
        self.joints = [float(v) for v in points[-1]]; return 2

    SimulationAPI.jmove = _jump
    SimulationAPI.lmove = _jump
    SimulationAPI.cjmove = _jump_chain
    SimulationAPI.clmove = _jump_chain
    SimulationAPI.smove = _jump_smove

    from workspace.workspace import Workspace
    from workspace.bt.launcher import load_recipes, run_protocol

    ws = Workspace(config_path=merged_sim_scene(project, launch), port=args.port)
    core = ws.components["core"]
    rcp = load_recipes(ws, core, os.path.join(project, launch.get("recipes", "recipes.j2")))
    core.robot_api.jmove(joint=[0, 45, -90, 0, -45, 0, 100, 0])
    ws.rt.start()

    sys.path.insert(0, project)
    sys.modules.pop("actions", None)
    import actions as A

    status = run_protocol(workspace=ws, core=core, recipes=rcp, actions_module=A,
                          project_name=launch.get("project_name", os.path.basename(project)),
                          kwargs=kwargs)
    print(f"\ndryrun: {status} (batch {args.batch}, kwargs {kwargs})")
    sys.exit(0 if getattr(status, "name", str(status)) == "SUCCESS" else 1)


if __name__ == "__main__":
    main()
