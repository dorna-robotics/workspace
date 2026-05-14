"""Orchestrator entry point for pace_bt.

Explicit — every wire is visible:

  * ``launch.yaml`` is opened and its ``scene`` field is passed to
    :class:`Workspace`.
  * ``recipes.yaml`` is loaded into a name → recipe-instance dict via
    :func:`workspace.bt.launcher.load_recipes`.
  * ``checks`` is imported and turned into ``{name: callable}`` via
    :func:`workspace.bt.launcher.load_checks`.
  * ``actions`` is imported (registers Action subclasses on import).
  * ``workflow_fn`` calls :func:`workspace.bt.launcher.run_protocol`
    with all four explicit inputs.
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
from workspace.bt.launcher import load_checks, load_recipes, run_protocol

import actions  # registers Inspect, Decap, … into the ActionRegistry on import
import checks   # Checks class — gives the framework pre_check / post_check callables


_BASE_DIR = Path(__file__).parent


def workflow_fn(*, workspace, core, **kwargs):
    """Called by RuntimeServer on every operator Start click.

    Loads ``recipes.yaml`` + ``checks.py`` (so ``ctx.recipes["gripper"]``
    etc. are populated for real-mode runs and pre_check/post_check
    names resolve), then hands actions + recipes + checks + kwargs
    to the BT framework's default protocol runner.
    """
    recipes  = load_recipes(workspace, core, _BASE_DIR / "recipes.yaml")
    check_fns = load_checks(workspace, core, recipes, checks_module=checks, **kwargs)
    return run_protocol(
        workspace, core, actions,
        recipes=recipes,
        checks=check_fns,
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
