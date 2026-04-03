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
from pathlib import Path
from typing import Callable

import yaml
from jinja2 import Environment, FileSystemLoader

from workspace.ortools.runner import ORRunner


def _load_yaml(base_dir: Path, folder: str, name: str) -> dict:
    """Load a YAML file, rendering as Jinja2 template first if .j2 exists."""
    j2_path = base_dir / folder / (name + ".j2")
    yaml_path = base_dir / folder / (name + ".yaml")
    if j2_path.is_file():
        env = Environment(loader=FileSystemLoader(str(base_dir / folder)))
        rendered = env.get_template(name + ".j2").render()
        return yaml.safe_load(rendered)
    with open(yaml_path) as f:
        return yaml.safe_load(f)


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
            def __init__(self, workspace, core, batch_size=4):
                super().__init__(workspace, core, _BASE_DIR, batch_size=batch_size)

            def _register_all(self):
                r   = self.runner.register_state
                rcp = self.rcp
                cfg = self.cfg
                ...

        def workflow_fn(*, workspace, core):
            wf = Workflow(workspace, core)
            wf.run()

    Dynamic batch_size:
        Pass batch_size at construction time.  To get it from a camera, do:

            n = camera.count_tubes("source_rack")
            wf = Workflow(workspace, core, batch_size=n)

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
        states_cls,
        checks_cls,
        batch_size: int = 1,
        horizon: int = 20,
        **kwargs,
    ):
        self._base_dir = base_dir
        self.rt  = workspace.rt
        self.rcp = self._load_recipes(workspace, core)
        self.batch_size = batch_size
        self.kwargs = kwargs

        # Enum constraint state (tool enforcement)
        self._enum_state:    dict[str, str | None] = {}
        self._enum_handlers: dict[str, Callable]   = {}

        # Support both protocol.yaml and protocol.j2
        protocol_j2 = base_dir / "protocol" / "protocol.j2"
        protocol = base_dir / "protocol" / "protocol.yaml"
        if protocol_j2.is_file():
            env = Environment(loader=FileSystemLoader(str(base_dir / "protocol")))
            rendered = env.get_template("protocol.j2").render()
            protocol.write_text(rendered)

        self.runner = ORRunner(
            self.rt, protocol, batch_size,
            horizon=horizon,
        )
        self._register_all(states_cls, checks_cls)
        self._apply_tool_enforcement()

    # ── Loading ──────────────────────────────────────────────────────────────

    def _load_recipes(self, workspace, core) -> dict:
        defs = _load_yaml(self._base_dir, "recipes", "recipes")
        rcp   = {}
        for alias, defn in defs.items():
            cls    = _import_class(defn["class"])
            kwargs = dict(defn.get("kwargs") or {})
            comp   = workspace.components[kwargs.pop("component")]
            rcp[alias] = cls(workspace, core, comp, **kwargs)
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
        self.runner.run(self.batch_size)
        self._release_all()

    def _apply_tool_enforcement(self):
        """
        Reads tool: field from protocol.yaml and auto-wraps each registered
        handler with tool pickup/place logic. Called after _register_all().
        No tool: field = no wrapping, no tool swap.
        """
        with open(self._base_dir / "protocol" / "protocol.yaml") as f:
            data = yaml.safe_load(f)

        tool_map = {s["name"]: s["tool"] for s in data["states"] if s.get("tool")}
        if not tool_map:
            return

        rcp = self.rcp

        def swap_tool(old, new):
            if old:
                rcp[old].place()
            if new:
                rcp[new].pick()

        self.on_enum_change("tool", handler=swap_tool)

        for state_name, tool_name in tool_map.items():
            handler = self.runner._handlers.get(state_name)
            if handler:
                self.runner._handlers[state_name] = self._with("tool", tool_name, handler)

    def _register_all(self, states_cls, checks_cls):
        for name, fn in states_cls(self.rcp, self.rt, self.batch_size, **self.kwargs).make().items():
            self.runner.register_state(name, fn)
        for name, fn in checks_cls().make().items():
            self.runner.register_check(name, fn)
