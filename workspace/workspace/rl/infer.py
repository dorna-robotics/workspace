# workspace/rl/infer.py
# RLRunner — generic inference runner.
# Constructs env from project YAML files, no env.py needed.
# The RL model decides action ORDER.
# This runner enforces real-world TIMING for background states.

import time
from pathlib import Path
from typing import Callable

import numpy as np
import yaml
from sb3_contrib import MaskablePPO

from workspace.rl.base_env import BaseLabEnv


class RLRunner:

    def __init__(self, rt, protocol_path: Path, constraints_path: Path,
                 n_items: int, model_path: Path, cfg=None):
        self.rt       = rt
        self._env     = BaseLabEnv(protocol_path, constraints_path, n_items=n_items)
        self._model   = MaskablePPO.load(str(model_path))
        self._handlers: dict[str, Callable] = {}
        self._cfg     = cfg

        with open(protocol_path) as f:
            data = yaml.safe_load(f)

        self._states      = data["states"]
        self._goal        = set(data["goal"])
        self._state_names = [s["name"] for s in self._states]
        self._background  = {}
        for s in self._states:
            if s.get("background"):
                d = s.get("duration")
                duration_sec = 0
                if isinstance(d, str) and cfg:
                    # string reference to params key (e.g. "shake_duration")
                    duration_sec = getattr(cfg, d, 0)
                elif isinstance(d, list):
                    # [min, max] range — use cfg value if available, else midpoint
                    if cfg and isinstance(d, list):
                        # check if there's a matching params key
                        duration_sec = (d[0] + d[1]) / 2
                    else:
                        duration_sec = (d[0] + d[1]) / 2
                elif isinstance(d, (int, float)):
                    duration_sec = d
                self._background[s["name"]] = duration_sec

    def register(self, state_name: str, handler: Callable):
        self._handlers[state_name] = handler

    def run(self, n_items: int):
        env = self._env
        obs, _ = env.reset()
        completed: dict[str, set[int]] = {n: set() for n in self._state_names}
        max_steps = n_items * len(self._state_names) * 3

        # track real-time background tasks: {state_name: (start_time, duration_sec)}
        bg_active: dict[str, tuple[float, float]] = {}

        for _ in range(max_steps):
            mask = env.action_masks()
            if not np.any(mask):
                if bg_active:
                    # wait for all active background tasks
                    for bg_name, (start, dur) in list(bg_active.items()):
                        remaining = dur - (time.time() - start)
                        if remaining > 0:
                            self.rt.step(f"Waiting {remaining:.0f}s for {bg_name}")
                            self.rt.delay(remaining)
                    bg_active.clear()
                    # step env to tick timers forward (use first valid action after wait)
                    continue
                break

            action, _ = self._model.predict(obs, action_masks=mask, deterministic=True)
            state_i    = int(action) // n_items
            item_i     = int(action) % n_items
            state_name = self._state_names[state_i]
            handler    = self._handlers.get(state_name)

            if state_name in self._background:
                duration_sec = self._background[state_name]
                self.rt.step(f"RL → {state_name} [background, {duration_sec:.0f}s]")
                if handler:
                    handler(0)
                bg_active[state_name] = (time.time(), duration_sec)
                for t in range(n_items):
                    completed[state_name].add(t)
            else:
                # check if this state depends on a background state that's still running
                state_reqs = self._env._requires.get(state_name, [])
                for bg_name, (start, dur) in list(bg_active.items()):
                    bg_idx = self._state_names.index(bg_name) if bg_name in self._state_names else -1
                    if bg_idx in state_reqs:
                        remaining = dur - (time.time() - start)
                        if remaining > 0:
                            self.rt.step(f"Waiting {remaining:.0f}s for {bg_name}")
                            self.rt.delay(remaining)
                        bg_active.pop(bg_name)

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
