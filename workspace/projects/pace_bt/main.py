"""pace_bt entry point. All config lives in launch.yaml.

Framework reference: ../../../docs/bt-framework-guide.md §2
"""

import argparse
import os
from pathlib import Path

import yaml

from workspace.workspace import Workspace
from workspace.runtime_server import RuntimeServer
from workspace.bt.launcher import load_checks, load_recipes, run_protocol

import actions
import checks


LAUNCH_FILE   = "launch.yaml"
PORT_ENV_VAR  = "PORT"

_BASE_DIR = Path(__file__).parent
with open(_BASE_DIR / LAUNCH_FILE) as f:
    LAUNCH = yaml.safe_load(f)


def workflow_fn(*, workspace, core, **kwargs):
    recipes   = load_recipes(workspace, core, _BASE_DIR / LAUNCH["recipes"])
    check_fns = load_checks(workspace, core, recipes, checks_module=checks, **kwargs)
    return run_protocol(
        workspace, core, actions,
        recipes=recipes,
        checks=check_fns,
        project_name=LAUNCH["project_name"],
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
