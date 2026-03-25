# workspace/or/workflow.py
# Base Workflow for OR-Tools projects.
#
# API-compatible with workspace.rl.workflow.BaseWorkflow — swap the import
# in any project workflow.py and it works.  No model file needed.
#
# Differences from the RL version:
# - Uses ORRunner instead of RLRunner (no policy.zip required)
# - Accepts an optional `horizon` parameter for rolling-window replanning
# - Exposes register_bg_cleanup() for background state teardown

from __future__ import annotations

import importlib
import types
from pathlib import Path
from typing import Callable

import yaml

from workspace.ortools.runner import ORRunner


def _import_class(dotted_path: str):
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class BaseWorkflow:
    """
    Base class for OR-Tools lab automation workflows.

    Usage in a project's workflow.py:

        from workspace.or.workflow import BaseWorkflow

        _BASE_DIR = Path(__file__).parent

        class Workflow(BaseWorkflow):
            def __init__(self, workspace, core, n_items=4):
                super().__init__(workspace, core, _BASE_DIR, n_items=n_items)

            def _register_all(self):
                r   = self.runner.register
                rcp = self.rcp
                cfg = self.cfg
                ...

        def workflow_fn(*, workspace, core):
            wf = Workflow(workspace, core)
            wf.run()

    Dynamic n_items:
        Pass n_items at construction time.  To get it from a camera, do:

            n = camera.count_tubes("source_rack")
            wf = Workflow(workspace, core, n_items=n)

    Rolling horizon:
        Pass horizon=4 to replan every 4 tasks.  The scheduler recomputes
        an optimal order after each window, so failures and physical changes
        are handled automatically.
    """

    def __init__(
        self,
        workspace,
        core,
        base_dir: Path,
        n_items: int,
        horizon: int | None = None,
    ):
        self._base_dir = base_dir
        self.rt  = workspace.rt
        self.cfg = self._load_params()
        self.rcp = self._load_recipes(workspace, core)
        self.n   = n_items

        # Enum constraint state (tool enforcement)
        self._enum_state:    dict[str, str | None] = {}
        self._enum_handlers: dict[str, Callable]   = {}

        protocol    = base_dir / "3_protocol" / "protocol.yaml"
        constraints = base_dir / "4_constraints" / "constraints.yaml"

        self.runner = ORRunner(
            self.rt, protocol, constraints, n_items,
            cfg=self.cfg, horizon=horizon,
        )
        self._register_all()

    # ── Loading ──────────────────────────────────────────────────────────────

    def _load_params(self) -> types.SimpleNamespace:
        with open(self._base_dir / "2_params" / "params.yaml") as f:
            data = yaml.safe_load(f)
        return types.SimpleNamespace(**data)

    def _load_recipes(self, workspace, core) -> dict:
        with open(self._base_dir / "2_params" / "recipes.yaml") as f:
            defs = yaml.safe_load(f)
        speed = getattr(self.cfg, "speed_factor", 10)
        rcp   = {}
        for alias, defn in defs.items():
            cls    = _import_class(defn["class"])
            kwargs = dict(defn.get("kwargs") or {})
            comp   = workspace.components[kwargs.pop("component")]
            rcp[alias] = cls(workspace, core, comp, speed_factor=speed, **kwargs)
        return rcp

    # ── Enum constraint helpers (tool enforcement) ────────────────────────────

    def on_enum_change(self, constraint_name: str, handler: Callable = None):
        """Register a handler called when a tool/mode enum changes value."""
        self._enum_handlers[constraint_name] = handler
        self._enum_state[constraint_name]    = None

    def _ensure(self, constraint_name: str, value: str):
        current = self._enum_state.get(constraint_name)
        if current == value:
            return
        handler = self._enum_handlers.get(constraint_name)
        if handler:
            handler(current, value)
        self._enum_state[constraint_name] = value

    def _release(self, constraint_name: str):
        current = self._enum_state.get(constraint_name)
        if current:
            handler = self._enum_handlers.get(constraint_name)
            if handler:
                handler(current, None)
            self._enum_state[constraint_name] = None

    def _release_all(self):
        for name in list(self._enum_state.keys()):
            self._release(name)

    def _with(self, constraint_name: str, value: str, fn: Callable) -> Callable:
        """Wrap a handler with automatic enum enforcement (e.g. tool pickup)."""
        def handler(i):
            self._ensure(constraint_name, value)
            return fn(i)
        return handler

    # ── Execution ────────────────────────────────────────────────────────────

    def run(self):
        self.runner.run(self.n)
        self._release_all()

    def _register_all(self):
        raise NotImplementedError
