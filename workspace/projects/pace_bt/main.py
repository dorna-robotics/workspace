"""Orchestrator entry point for pace_bt.

Explicit — every wire is visible:

  * ``launch.yaml`` is the single source of truth for project config
    (name, port, scene, recipes file, GUI form). Parsed once at
    import time into ``LAUNCH``.
  * ``actions`` is imported (registers Action subclasses on import).
  * ``checks`` is imported and turned into ``{name: callable}`` via
    :func:`workspace.bt.launcher.load_checks`.
  * ``workflow_fn`` calls :func:`workspace.bt.launcher.run_protocol`
    with all four explicit inputs.
  * ``--port`` (and ``PORT`` env var) wire through to
    :class:`Workspace` and :class:`RuntimeServer`, defaulting to the
    port declared in ``launch.yaml``.

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


# ─── Framework conventions (rarely changed) ────────────────────────────
# The launch file's own name is a bootstrap constant — main.py must
# know it to read everything else. The env var name follows the
# orchestrator's universal convention.
LAUNCH_FILE   = "launch.yaml"
PORT_ENV_VAR  = "PORT"
# ────────────────────────────────────────────────────────────────────────


_BASE_DIR = Path(__file__).parent

# Parse launch.yaml once — both workflow_fn and main() consume it.
with open(_BASE_DIR / LAUNCH_FILE) as f:
    LAUNCH = yaml.safe_load(f)


def workflow_fn(*, workspace, core, **kwargs):
    """Called by RuntimeServer on every operator Start click.

    Loads recipes + checks (so ``ctx.recipes["gripper"]`` etc. are
    populated for real-mode runs and pre_check/post_check names
    resolve), then hands actions + recipes + checks + kwargs to the
    BT framework's default protocol runner.
    """
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
