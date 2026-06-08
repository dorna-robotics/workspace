"""feeder example entry point. All config lives in launch.yaml.

A minimal BT project that demonstrates the cap-feeder + suction
tool + cap-holder triple:

  Start            — picks the suction tool from the tool rack
  FeedCap(cap)     — for each cap, hover over the feeder, pick the
                      cap currently at the feeder's ``place`` anchor,
                      drop it in the next cap-holder slot, advance
                      the feeder by one step
  Park             — places the suction tool back, parks the robot

``cap_count`` (operator kwarg) sets how many caps the run transfers.

Framework reference: ../../../../docs/bt-framework-guide.md §2
"""

import argparse
import importlib
import os
from pathlib import Path

import yaml

from workspace.workspace import Workspace
from workspace.runtime_server import RuntimeServer
from workspace.bt.launcher import load_checks, load_recipes, run_protocol


LAUNCH_FILE   = "launch.yaml"
PORT_ENV_VAR  = "PORT"

_BASE_DIR = Path(__file__).parent
with open(_BASE_DIR / LAUNCH_FILE) as f:
    LAUNCH = yaml.safe_load(f)


def _import_module(rel_path: str):
    name = rel_path.removesuffix(".py").replace("/", ".")
    return importlib.import_module(name)


actions = _import_module(LAUNCH.get("actions", "actions.py"))
checks  = _import_module(LAUNCH.get("checks",  "checks.py"))


def workflow_fn(*, workspace, core, **kwargs):
    recipes   = load_recipes(workspace, core, _BASE_DIR / LAUNCH["recipes"])
    check_fns = load_checks(workspace, core, recipes, checks_module=checks, **kwargs)
    return run_protocol(
        workspace, core, actions,
        recipes=recipes,
        checks=check_fns,
        project_name=LAUNCH["project_name"],
        plan_window=int(LAUNCH.get("plan_window", 4)),
        scheduler=str(LAUNCH.get("scheduler", "cpsat")),
        **kwargs,
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--port", type=int,
        default=int(os.getenv(PORT_ENV_VAR, str(LAUNCH["port"]))),
    )
    args = p.parse_args()

    ws = Workspace(config_path=LAUNCH["scene"], port=args.port)
    RuntimeServer(runtime=ws.rt, workflow_fn=workflow_fn, workspace=ws).run()


if __name__ == "__main__":
    main()
