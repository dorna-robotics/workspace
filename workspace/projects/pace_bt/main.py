"""pace_bt entry point. All config lives in launch.j2.

Framework reference: ../../../docs/bt-framework-guide.md §2
"""

import argparse
import importlib
import os
from pathlib import Path

from workspace.workspace import Workspace
from workspace.runtime_server import RuntimeServer
from workspace.bt.launcher import (
    load_checks, load_recipes, read_yaml_or_j2, run_protocol,
)


LAUNCH_FILE   = "launch.j2"        # j2 takes priority — falls back to launch.yaml
PORT_ENV_VAR  = "PORT"

_BASE_DIR = Path(__file__).parent
LAUNCH = read_yaml_or_j2(_BASE_DIR / LAUNCH_FILE)


def _import_module(rel_path: str):
    """'actions.py' → 'actions';  'protocol/actions.py' → 'protocol.actions'."""
    name = rel_path.removesuffix(".py").replace("/", ".")
    return importlib.import_module(name)


actions = _import_module(LAUNCH.get("actions", "actions.py"))
checks  = _import_module(LAUNCH.get("checks",  "checks.py"))


def workflow_fn(*, workspace, core, **kwargs):
    # Inject project-wide tunables from launch.j2 (e.g. speed_factor)
    # into recipes.j2 rendering. recipes.j2 references {{ speed_factor }}.
    render_vars = {"speed_factor": LAUNCH.get("speed_factor", 50)}
    recipes = load_recipes(
        workspace, core, _BASE_DIR / LAUNCH["recipes"], **render_vars,
    )
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
