# workspace/rl/infer.py
# RLRunner — generic inference runner.
# Constructs env from project YAML files, no env.py needed.

from pathlib import Path
from typing import Callable

import numpy as np
import yaml
from sb3_contrib import MaskablePPO

from workspace.rl.base_env import BaseLabEnv


class RLRunner:

    def __init__(self, rt, protocol_path: Path, constraints_path: Path,
                 n_items: int, model_path: Path):
        self.rt       = rt
        self._env     = BaseLabEnv(protocol_path, constraints_path, n_items=n_items)
        self._model   = MaskablePPO.load(str(model_path))
        self._handlers: dict[str, Callable] = {}

        with open(protocol_path) as f:
            data = yaml.safe_load(f)

        self._states      = data["states"]
        self._goal        = set(data["goal"])
        self._state_names = [s["name"] for s in self._states]
        self._background  = {s["name"] for s in self._states if s.get("background")}

    def register(self, state_name: str, handler: Callable):
        self._handlers[state_name] = handler

    def run(self, n_items: int):
        env = self._env
        obs, _ = env.reset()
        completed: dict[str, set[int]] = {n: set() for n in self._state_names}
        max_steps = n_items * len(self._state_names) * 3

        for _ in range(max_steps):
            mask = env.action_masks()
            if not np.any(mask):
                break

            action, _ = self._model.predict(obs, action_masks=mask, deterministic=True)
            state_i    = int(action) // n_items
            item_i     = int(action) % n_items
            state_name = self._state_names[state_i]
            handler    = self._handlers.get(state_name)

            if state_name in self._background:
                self.rt.step(f"RL → {state_name} [background]")
                if handler:
                    handler()
                for t in range(n_items):
                    completed[state_name].add(t)
            else:
                self.rt.step(f"RL → {state_name} [{item_i + 1}/{n_items}]")
                if handler:
                    handler(item_i)
                completed[state_name].add(item_i)

            obs, _, terminated, truncated, _ = env.step(int(action))
            if terminated or truncated:
                break

        missing = [g for g in self._goal if len(completed.get(g, set())) < n_items]
        if missing:
            raise RuntimeError(f"RL runner did not reach goal states: {missing}")

        self.rt.step("Protocol complete — all goal states reached", level="success")
