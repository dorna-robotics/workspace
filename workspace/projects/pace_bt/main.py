"""Orchestrator entry point for pace_bt.

The orchestrator launches a project by spawning ``python3 -u main.py
--port <port>`` with ``cwd=<project_dir>``. ``main.py`` is responsible
for:

  1. Reading ``launch.yaml`` (scene path + kwargs schema).
  2. Instantiating the :class:`Workspace`.
  3. Defining a ``workflow_fn(*, workspace, core, **kwargs)`` that the
     :class:`RuntimeServer` will call when the operator clicks Start.
  4. Starting the RuntimeServer.

Project-local modules (``workflow``, ``actions``) are imported with
short names because the project directory is on ``sys.path``
automatically when Python runs a script (Python adds the script's
own directory to ``sys.path``).
"""

import argparse
import os
from pathlib import Path

import yaml

from workspace.workspace import Workspace
from workspace.runtime_server import RuntimeServer

from workflow import run as _workflow_run


_BASE_DIR = Path(__file__).parent


def workflow_fn(*, workspace, core, **kwargs):
    """Entry called by the orchestrator on every Start click.

    Forwards to :func:`workflow.run`, which sets up the BT engine,
    PDDL planner, and OR-style scheduler.
    """
    _workflow_run(workspace, core, **kwargs)


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
