"""Orchestrator entry point for pace_bt.

Explicit — every wire is visible:

  * ``launch.yaml`` is opened and its ``scene`` field is passed to
    :class:`Workspace`.
  * ``recipes.yaml`` is loaded into a name → recipe-instance dict via
    :func:`workspace.bt.launcher.load_recipes`.
  * ``actions`` is imported (registers Action subclasses on import).
  * ``workflow_fn`` calls :func:`workspace.bt.launcher.run_protocol`
    with all three explicit inputs.
  * ``--port`` (and ``PORT`` env var) wire through to
    :class:`Workspace` and :class:`RuntimeServer`.

Same shape as pace_or's main.py — just calls into the BT framework's
``run_protocol`` instead of pace_or's ``BaseWorkflow``.
"""

import argparse
import os
from pathlib import Path

import yaml

from workspace.workspace import Workspace
from workspace.runtime_server import RuntimeServer
from workspace.bt.launcher import load_recipes, run_protocol

import actions  # registers Inspect, Decap, … into the ActionRegistry on import


_BASE_DIR = Path(__file__).parent


def workflow_fn(*, workspace, core, **kwargs):
    """Called by RuntimeServer on every operator Start click.

    Loads ``recipes.yaml`` (so ``ctx.recipes["gripper"]`` etc. are
    populated for real-mode runs) and hands the actions module +
    recipes + kwargs to the BT framework's default protocol runner.
    """
    recipes = load_recipes(workspace, core, _BASE_DIR / "recipes.yaml")
    return run_protocol(
        workspace, core, actions,
        recipes=recipes,
        project_name="pace_bt",
        **kwargs,
    )


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
