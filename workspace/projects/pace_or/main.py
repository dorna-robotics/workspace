import os
import argparse
from pathlib import Path

import yaml

from workspace.workspace import Workspace
from workspace.ortools.workflow import BaseWorkflow
from workspace.runtime_server import RuntimeServer
from states import States
from protocol.checks import Checks

_BASE_DIR = Path(__file__).parent


def workflow_fn(*, workspace, core, **kwargs):
    BaseWorkflow(workspace, core, _BASE_DIR, States, Checks, **kwargs).run()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "5010")))
    args = p.parse_args()

    with open(_BASE_DIR / "launch.yaml") as f:
        launch = yaml.safe_load(f)

    ws = Workspace(config_path=launch["scene"], port=args.port)
    RuntimeServer(runtime=ws.rt, workflow_fn=workflow_fn, workspace=ws).run()


if __name__ == "__main__":
    main()
