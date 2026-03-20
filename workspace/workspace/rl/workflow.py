# workspace/rl/workflow.py
# Base Workflow class — shared by all LAOS projects.
# Handles: loading params, loading recipes, runner setup.
# Assumes nothing about tools, item counts, or project structure.

import importlib
import types
from pathlib import Path

import yaml

from workspace.rl.infer import RLRunner


def _import_class(path: str):
    """Import a class from a dotted path like 'workspace.recipes.rack.Rack'."""
    module_path, class_name = path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class BaseWorkflow:

    def __init__(self, workspace, core, base_dir: Path, n_items: int):
        self._base_dir = base_dir
        self.rt  = workspace.rt
        self.cfg = self._load_params()
        self.rcp = self._load_recipes(workspace, core)
        self.n   = n_items

        # enum state tracking: {constraint_name: current_value}
        self._enum_state = {}
        # enum handlers: {constraint_name: {"enter": fn, "exit": fn}}
        self._enum_handlers = {}

        protocol    = base_dir / "3_protocol" / "protocol.yaml"
        constraints = base_dir / "4_constraints" / "constraints.yaml"
        model       = base_dir / "5_rl" / "models" / "policy.zip"

        self.runner = RLRunner(self.rt, protocol, constraints, n_items, model_path=model, cfg=self.cfg)
        self._register_all()

    # ── Loading ─────────────────────────────────────────────────────────────

    def _load_params(self) -> types.SimpleNamespace:
        with open(self._base_dir / "2_params" / "params.yaml") as f:
            data = yaml.safe_load(f)
        return types.SimpleNamespace(**data)

    def _load_recipes(self, workspace, core) -> dict:
        with open(self._base_dir / "2_params" / "recipes.yaml") as f:
            defs = yaml.safe_load(f)
        speed = getattr(self.cfg, "speed_factor", 10)
        rcp = {}
        for alias, defn in defs.items():
            cls = _import_class(defn["class"])
            kwargs = dict(defn.get("kwargs") or {})
            comp = workspace.components[kwargs.pop("component")]
            rcp[alias] = cls(workspace, core, comp, speed_factor=speed, **kwargs)
        return rcp

    # ── Generic enum enforcement ──────────────────────────────────────────

    def on_enum_change(self, constraint_name: str, handler=None):
        """Register a handler for an enum constraint.

        Args:
            constraint_name: must match the constraint name in constraints.yaml
            handler: callable(old_value, new_value) — called when value changes.
                     old_value is None on first use.
        """
        self._enum_handlers[constraint_name] = handler
        self._enum_state[constraint_name] = None

    def _ensure(self, constraint_name: str, value: str):
        """Ensure an enum constraint is set to the given value.
        Calls handler(old, new) if changed."""
        current = self._enum_state.get(constraint_name)
        if current == value:
            return
        handler = self._enum_handlers.get(constraint_name)
        if handler:
            handler(current, value)
        self._enum_state[constraint_name] = value

    def _release(self, constraint_name: str):
        """Release an enum constraint — calls handler(current, None) and resets."""
        current = self._enum_state.get(constraint_name)
        if current:
            handler = self._enum_handlers.get(constraint_name)
            if handler:
                handler(current, None)
            self._enum_state[constraint_name] = None

    def _release_all(self):
        """Release all enum constraints."""
        for name in list(self._enum_state.keys()):
            self._release(name)

    def _with(self, constraint_name: str, value: str, fn):
        """Wrap a handler with automatic enum enforcement.

        Usage in workflow.py:
            r("source_rack.pick", self._with("current_tool", "gripper", lambda i: ...))
        """
        def handler(i):
            self._ensure(constraint_name, value)
            return fn(i)
        return handler

    # ── Execution ─────────────────────────────────────────────────────────

    def run(self):
        self.runner.run(self.n)
        self._release_all()

    # ── Override in subclass ──────────────────────────────────────────────

    def _register_all(self):
        raise NotImplementedError
