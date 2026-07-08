# workspace/rl/train.py
# Generic training script — no project-specific code needed.
# Network size, learning rate, and batch size scale automatically
# based on the action/observation space.
#
# Usage:
#   sudo python3 workspace/rl/train.py --project pace_atomic --count 4 --steps 200000
#   sudo python3 workspace/rl/train.py --project pace_atomic --count 4 --steps 1000000 --n_envs 8

import argparse
from pathlib import Path

import torch
from stable_baselines3.common.callbacks import BaseCallback
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker

from workspace.rl.base_env import BaseLabEnv

# Example projects live at the repo root (…/workspace/examples); real
# projects are standalone repos outside this one — pass those by path.
_PROJECTS_DIR = Path(__file__).parents[3] / "examples"


def _make_env(project: str, batch_size: int, max_items: int = None) -> BaseLabEnv:
    """Construct env directly from project YAML files."""
    base = _PROJECTS_DIR / project
    protocol    = base / "protocol" / "protocol.yaml"
    constraints = base / "4_constraints" / "constraints.yaml"
    if not protocol.exists():
        raise FileNotFoundError(f"No protocol.yaml at {protocol}")
    # scale max_steps with problem size
    n_states = len(__import__("yaml").safe_load(open(protocol))["states"])
    effective_max = max_items or batch_size
    max_steps = n_states * effective_max * 3
    return BaseLabEnv(protocol, constraints, batch_size=batch_size,
                      max_items=max_items, max_steps=max_steps)


def _auto_config(env: BaseLabEnv) -> dict:
    """Pick network arch, learning rate, batch size based on env complexity."""
    n_actions = env.action_space.n
    obs_size  = env.observation_space.shape[0]

    if n_actions <= 50:
        # small (e.g. pace_laos: 36 actions)
        net_arch = [64, 64]
        lr       = 3e-4
        batch    = 64
        n_steps  = 2048
    elif n_actions <= 200:
        # medium
        net_arch = [128, 128]
        lr       = 1e-4
        batch    = 128
        n_steps  = 2048
    else:
        # large (e.g. pace_atomic: 216 actions)
        net_arch = [256, 256, 128]
        lr       = 5e-5
        batch    = 256
        n_steps  = 4096

    return {
        "net_arch":      net_arch,
        "learning_rate": lr,
        "batch_size":    batch,
        "n_steps":       n_steps,
    }


def _mask_fn(env: BaseLabEnv):
    return env.action_masks()


class LogCallback(BaseCallback):
    """Print progress and stop early if reward stops improving."""
    def __init__(self, log_every: int, total_steps: int,
                 patience: int = 25, min_delta: float = 2.0):
        super().__init__()
        self.log_every   = log_every
        self.total_steps = total_steps
        self.patience    = patience
        self.min_delta   = min_delta
        self._ep_rewards: list[float] = []
        self._ep_lens:    list[int]   = []
        self._best_avg   = float("-inf")
        self._flat_count = 0

    def _on_step(self) -> bool:
        for info in self.locals.get("infos", []):
            if "episode" in info:
                self._ep_rewards.append(info["episode"]["r"])
                self._ep_lens.append(info["episode"]["l"])

        if self.num_timesteps % self.log_every == 0 and self._ep_rewards:
            pct   = min(100.0 * self.num_timesteps / self.total_steps, 100.0)
            avg_r = sum(self._ep_rewards[-20:]) / len(self._ep_rewards[-20:])
            avg_l = sum(self._ep_lens[-20:])    / len(self._ep_lens[-20:])
            print(f"[{pct:5.1f}%] step={self.num_timesteps:>7}  "
                  f"avg_reward={avg_r:+.1f}  avg_len={avg_l:.0f}", flush=True)

            if avg_r > self._best_avg + self.min_delta:
                self._best_avg   = avg_r
                self._flat_count = 0
            else:
                self._flat_count += 1
                if self.patience > 0 and self._flat_count >= self.patience:
                    print(f"\nEarly stop — no improvement for {self.patience} intervals "
                          f"(best={self._best_avg:+.1f})", flush=True)
                    return False
        return True


def _make_vec_env(project: str, batch_size: int, max_items: int, n_envs: int):
    """Create vectorized environment for parallel training."""
    from stable_baselines3.common.vec_env import DummyVecEnv

    def make_fn():
        def _init():
            env = _make_env(project, batch_size, max_items)
            return ActionMasker(env, _mask_fn)
        return _init

    return DummyVecEnv([make_fn() for _ in range(n_envs)])


def train(project: str, batch_size: int, total_steps: int, out: Path,
          resume: bool = False, n_envs: int = 1, no_early_stop: bool = False,
          max_items: int = None):
    raw_env = _make_env(project, batch_size, max_items)
    cfg     = _auto_config(raw_env)
    device  = "cuda" if torch.cuda.is_available() else "cpu"

    if n_envs > 1:
        env = _make_vec_env(project, batch_size, max_items, n_envs)
    else:
        env = ActionMasker(raw_env, _mask_fn)

    effective_max = max_items or batch_size
    print(f"  actions={raw_env.action_space.n}  obs={raw_env.observation_space.shape[0]}  "
          f"net={cfg['net_arch']}  lr={cfg['learning_rate']}  batch={cfg['batch_size']}  "
          f"device={device}  envs={n_envs}  items=1-{effective_max}", flush=True)

    if resume and out.exists():
        model = MaskablePPO.load(str(out), env=env, device=device)
        start_steps = model.num_timesteps
        target_steps = start_steps + total_steps
        print(f"Resuming from {out}  |  at {start_steps:,}  |  +{total_steps:,} steps", flush=True)
    else:
        model = MaskablePPO(
            "MlpPolicy", env,
            verbose=0,
            device=device,
            n_steps=cfg["n_steps"],
            batch_size=cfg["batch_size"],
            n_epochs=15,
            learning_rate=cfg["learning_rate"],
            policy_kwargs=dict(net_arch=cfg["net_arch"]),
        )
        start_steps = 0
        target_steps = total_steps
        print(f"Training for {total_steps:,} steps  |  count={batch_size}  |  saving to {out}", flush=True)

    model.learn(
        total_timesteps=target_steps,
        callback=LogCallback(2048, target_steps, patience=0 if no_early_stop else 25),
        reset_num_timesteps=False,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(out))
    print(f"\nDone — model saved to {out}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True,
                        help="Project folder name under examples/ (repo root)")
    parser.add_argument("--count", type=int,  default=4,
                        help="Default item count (also min when --max_count is set)")
    parser.add_argument("--max_count", type=int, default=None,
                        help="Max items — randomizes 1 to max_count per episode")
    parser.add_argument("--steps", type=int,  default=200_000)
    parser.add_argument("--out",    type=Path, default=None)
    parser.add_argument("--resume", action="store_true",
                        help="Resume training from existing model")
    parser.add_argument("--n_envs", type=int, default=1,
                        help="Number of parallel environments (use 8-16 on Colab)")
    parser.add_argument("--no_early_stop", action="store_true",
                        help="Disable early stopping — run full steps")
    args = parser.parse_args()

    out = args.out or (_PROJECTS_DIR / args.project / "5_rl" / "models" / "policy.zip")
    train(args.project, batch_size=args.count, total_steps=args.steps, out=out,
          resume=args.resume, n_envs=args.n_envs, no_early_stop=args.no_early_stop,
          max_items=args.max_count)
