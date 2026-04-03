# workspace/rl/base_env.py
# Generic Gymnasium environment for LAOS RL training.
#
# Reads everything from two YAML files:
#   - protocol/protocol.yaml      → states, dependencies, goals
#   - 4_constraints/constraints.yaml → rewards, constraints, reward rules
#
# Constraint types (defined in YAML, no code changes needed):
#   bool — true_on / false_on / block_on_true / block_on_false
#   int  — add_on / sub_on / block / max
#   enum — options / map / penalty
#
# Auto-skip: forced steps (only 1 valid action) are executed automatically.
#   The model only sees steps where it has a real choice (2+ valid actions).

from pathlib import Path

import numpy as np
import gymnasium as gym
from gymnasium import spaces
import yaml


class BaseLabEnv(gym.Env):

    metadata = {"render_modes": []}

    def __init__(self, protocol_path: Path, constraints_path: Path = None,
                 batch_size: int = 4, max_items: int = None, max_steps: int = 200):
        super().__init__()

        self.max_items      = max_items or batch_size
        self.batch_size        = self.max_items
        self._default_items = batch_size
        self._active_items  = batch_size
        self.max_steps      = max_steps

        # ── Load protocol ───────────────────────────────────────────────
        with open(protocol_path) as f:
            protocol = yaml.safe_load(f)

        self._states: list[dict] = protocol["states"]
        self._goal: set[str]     = set(protocol["goal"])
        self._state_names        = [s["name"] for s in self._states]
        self._n_states           = len(self._state_names)
        self._idx                = {name: i for i, name in enumerate(self._state_names)}

        self._requires: dict[str, list[int]] = {
            s["name"]: [self._idx[r] for r in (s.get("requires") or [])]
            for s in self._states
        }
        self._background = {s["name"] for s in self._states if s.get("background")}
        self._optional   = {s["name"] for s in self._states if s.get("optional")}

        # ── Load constraints (optional) ─────────────────────────────────
        cfg = {}
        if constraints_path is None:
            constraints_path = protocol_path.parent.parent / "4_constraints" / "constraints.yaml"
        if constraints_path.exists():
            with open(constraints_path) as f:
                cfg = yaml.safe_load(f) or {}

        # rewards
        rw = cfg.get("rewards", {})
        self._rw_per_step   = rw.get("per_step",   -1.0)
        self._rw_goal_state = rw.get("goal_state",  50.0)
        self._rw_completion = rw.get("completion", 200.0)
        self._rw_background = rw.get("background",   5.0)

        # reward rules
        self._reward_rules = cfg.get("reward_rules", [])

        # ── Dynamic constraints ─────────────────────────────────────────
        self._bools: list[dict] = []
        self._ints:  list[dict] = []
        self._enums: list[dict] = []

        for c in cfg.get("constraints", []):
            ctype   = c.get("type")
            observe = c.get("observe", False)

            if ctype == "bool":
                self._bools.append({
                    "name":           c["name"],
                    "observe":        observe,
                    "true_on":        set(c.get("true_on", [])),
                    "false_on":       set(c.get("false_on", [])),
                    "block_on_true":  set(c.get("block_on_true") or c.get("block_when_true") or c.get("block") or []),
                    "block_on_false": set(c.get("block_on_false") or c.get("block_when_false") or []),
                    "value":          False,
                })

            elif ctype == "int":
                self._ints.append({
                    "name":    c["name"],
                    "observe": observe,
                    "max":     c.get("max", 1),
                    "add_on":  set(c.get("add_on", [])),
                    "sub_on":  set(c.get("sub_on", [])),
                    "block":   set(c.get("block", [])),
                    "value":   0,
                })

            elif ctype == "enum":
                values    = c.get("options", c.get("values", []))
                value_idx = {v: i + 1 for i, v in enumerate(values)}  # 0 = none
                self._enums.append({
                    "name":      c["name"],
                    "observe":   observe,
                    "values":    values,
                    "n_values":  len(values) + 1,  # +1 for none
                    "value_idx": value_idx,
                    "map":       c.get("map", {}),
                    "penalty":   c.get("penalty", 0.0),
                    "value":     0,  # 0 = none
                })

        # ── Observation & action spaces ─────────────────────────────────
        self._n_actions = self._n_states * self.max_items
        self.action_space = spaces.Discrete(self._n_actions)

        # observation:
        #   active_items (1 float, normalized by max_items)
        #   completed matrix (max_items × n_states)
        #   bool constraints (1 float each)
        #   int constraints (1 float each, normalized)
        #   enum constraints (one-hot each)
        obs_size = (1  # active_items count
                    + self.max_items * self._n_states
                    + sum(1 for b in self._bools if b["observe"])
                    + sum(1 for i in self._ints  if i["observe"])
                    + sum(e["n_values"] for e in self._enums if e["observe"]))
        self.observation_space = spaces.Box(
            low=0, high=1, shape=(obs_size,), dtype=np.float32
        )

    # ── Gymnasium interface ─────────────────────────────────────────────

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)

        # randomize active items per episode (1 to max_items)
        if self.max_items > 1:
            self._active_items = self.np_random.integers(1, self.max_items + 1)
        else:
            self._active_items = 1

        if options and "batch_size" in options:
            self._active_items = options["batch_size"]

        self._completed = np.zeros((self.max_items, self._n_states), dtype=np.float32)
        for t in range(self._active_items, self.max_items):
            self._completed[t, :] = 1.0

        self._step_count = 0

        for b in self._bools:
            b["value"] = False
        for i in self._ints:
            i["value"] = 0
        for e in self._enums:
            e["value"] = 0

        return self._obs(), {}

    def _execute_one(self, action: int) -> float:
        """Execute a single action, update state, return reward delta."""
        state_i    = action // self.batch_size
        item_i     = action % self.batch_size
        state_name = self._state_names[state_i]

        reward = self._rw_per_step

        # mark state complete
        if state_name in self._background:
            for t in range(self._active_items):
                self._completed[t, state_i] = 1.0
            reward += self._rw_background
        else:
            self._completed[item_i, state_i] = 1.0

        if state_name in self._goal:
            reward += self._rw_goal_state

        # update bool constraints
        for b in self._bools:
            if state_name in b["true_on"]:
                b["value"] = True
            elif state_name in b["false_on"]:
                b["value"] = False

        # update int constraints
        for i in self._ints:
            if state_name in i["add_on"]:
                i["value"] = min(i["value"] + 1, i["max"])
            elif state_name in i["sub_on"]:
                i["value"] = max(i["value"] - 1, 0)

        # update enum constraints
        for e in self._enums:
            mapped = e["map"].get(state_name)
            if mapped is not None:
                if isinstance(mapped, list):
                    acceptable = {e["value_idx"].get(v, 0) for v in mapped}
                    if e["value"] not in acceptable:
                        new_val = e["value_idx"].get(mapped[0], 0)
                        if e["value"] != 0:
                            reward += e["penalty"]
                        e["value"] = new_val
                else:
                    new_val = e["value_idx"].get(mapped, 0)
                    if new_val != e["value"] and e["value"] != 0:
                        reward += e["penalty"]
                    e["value"] = new_val

        # YAML reward rules
        reward += self._eval_reward_rules(state_name)

        # subclass hook
        reward += self.reward_shaping(state_name, item_i)

        self._step_count += 1
        return reward

    def _execute_forced(self) -> float:
        """Auto-execute forced steps (only 1 valid action).
        Returns goal/background bonuses only. Other rewards absorbed."""
        bonus = 0.0
        while self._step_count < self.max_steps:
            mask = self.action_masks()
            valid = np.where(mask)[0]
            if len(valid) != 1:
                break
            action     = valid[0]
            state_i    = action // self.batch_size
            state_name = self._state_names[state_i]
            is_goal    = state_name in self._goal
            is_bg      = state_name in self._background
            self._execute_one(action)
            if is_goal:
                bonus += self._rw_goal_state
            if is_bg:
                bonus += self._rw_background
        return bonus

    def step(self, action: int):
        reward     = self._execute_one(action)
        terminated = False

        # auto-execute forced steps, keep goal/bg bonuses
        reward += self._execute_forced()

        goal_done = all(
            np.all(self._completed[:, self._idx[g]] == 1.0)
            for g in self._goal if g in self._idx
        )
        truncated = self._step_count >= self.max_steps

        if goal_done:
            reward    += self._rw_completion
            terminated = True

        return self._obs(), reward, terminated, truncated, {}

    def action_masks(self) -> np.ndarray:
        mask = np.zeros(self._n_actions, dtype=bool)

        blocked: set[str] = set()
        for b in self._bools:
            if b["value"]:
                blocked |= b["block_on_true"]
            else:
                blocked |= b["block_on_false"]
        for i in self._ints:
            if i["value"] >= i["max"]:
                blocked |= i["block"]

        for si, state in enumerate(self._states):
            name = state["name"]
            reqs = self._requires[name]

            if name in blocked:
                continue

            if name in self._background:
                already_done = self._completed[0, si] == 1.0
                requires_met = all(
                    self._completed[t, r] == 1.0
                    for t in range(self._active_items) for r in reqs
                )
                if not already_done and requires_met:
                    mask[si * self.batch_size] = True
            else:
                for ii in range(self._active_items):
                    action       = si * self.batch_size + ii
                    already_done = self._completed[ii, si] == 1.0
                    requires_met = all(self._completed[ii, r] == 1.0 for r in reqs)
                    if not already_done and requires_met:
                        mask[action] = True
        return mask

    # ── Override in subclass for complex logic ────────────────────────

    def reward_shaping(self, state_name: str, item_i: int) -> float:
        return 0.0

    # ── Internals ─────────────────────────────────────────────────────

    def _eval_reward_rules(self, state_name: str) -> float:
        total = 0.0
        for rule in self._reward_rules:
            if rule.get("when") == state_name:
                incomplete = rule.get("while_incomplete")
                if incomplete:
                    idx = self._idx.get(incomplete)
                    if idx is not None and (self._completed[:, idx] < 1.0).any():
                        total += rule.get("reward", 0.0)
                else:
                    total += rule.get("reward", 0.0)
        return total

    def _obs(self) -> np.ndarray:
        parts = [np.array([self._active_items / self.max_items], dtype=np.float32)]
        parts.append(self._completed.flatten())

        for b in self._bools:
            if b["observe"]:
                parts.append(np.array([float(b["value"])], dtype=np.float32))

        for i in self._ints:
            if i["observe"]:
                parts.append(np.array([i["value"] / max(i["max"], 1)], dtype=np.float32))

        for e in self._enums:
            if e["observe"]:
                onehot = np.zeros(e["n_values"], dtype=np.float32)
                onehot[e["value"]] = 1.0
                parts.append(onehot)

        return np.concatenate(parts)
