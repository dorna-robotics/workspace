# workspace/rl/train.py
# Generic training script — no project-specific code needed.
# Network size, learning rate, and batch size scale automatically
# based on the action/observation space.
#
# Usage:
#   sudo python3 workspace/rl/train.py --project pace_atomic --count 4 --steps 200000

import argparse
from pathlib import Path

from stable_baselines3.common.callbacks import BaseCallback
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker

from workspace.rl.base_env import BaseLabEnv

_PROJECTS_DIR = Path(__file__).parent.parent.parent / "projects"


def _make_env(project: str, n_items: int) -> BaseLabEnv:
    """Construct env directly from project YAML files."""
    base = _PROJECTS_DIR / project
    protocol    = base / "3_protocol" / "protocol.yaml"
    constraints = base / "4_constraints" / "constraints.yaml"
    if not protocol.exists():
        raise FileNotFoundError(f"No protocol.yaml at {protocol}")
    # scale max_steps with problem size
    n_states = len(__import__("yaml").safe_load(open(protocol))["states"])
    max_steps = n_states * n_items * 3
    return BaseLabEnv(protocol, constraints, n_items=n_items, max_steps=max_steps)


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
                  f"avg_reward={avg_r:+.1f}  avg_len={avg_l:.0f}")

            if avg_r > self._best_avg + self.min_delta:
                self._best_avg   = avg_r
                self._flat_count = 0
            else:
                self._flat_count += 1
                if self._flat_count >= self.patience:
                    print(f"\nEarly stop — no improvement for {self.patience} intervals "
                          f"(best={self._best_avg:+.1f})")
                    return False
        return True


def train(project: str, n_items: int, total_steps: int, out: Path, resume: bool = False):
    raw_env = _make_env(project, n_items)
    cfg     = _auto_config(raw_env)
    env     = ActionMasker(raw_env, _mask_fn)

    print(f"  actions={raw_env.action_space.n}  obs={raw_env.observation_space.shape[0]}  "
          f"net={cfg['net_arch']}  lr={cfg['learning_rate']}  batch={cfg['batch_size']}")

    if resume and out.exists():
        model = MaskablePPO.load(str(out), env=env)
        print(f"Resuming from {out}  |  +{total_steps:,} steps")
    else:
        model = MaskablePPO(
            "MlpPolicy", env,
            verbose=0,
            n_steps=cfg["n_steps"],
            batch_size=cfg["batch_size"],
            n_epochs=15,
            learning_rate=cfg["learning_rate"],
            policy_kwargs=dict(net_arch=cfg["net_arch"]),
        )
        print(f"Training for {total_steps:,} steps  |  items={n_items}  |  saving to {out}")

    model.learn(
        total_timesteps=total_steps,
        callback=LogCallback(cfg["n_steps"], total_steps, patience=25),
        reset_num_timesteps=not resume,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(out))
    print(f"\nDone — model saved to {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True,
                        help="Project folder name under workspace/projects/")
    parser.add_argument("--count", type=int,  default=4)
    parser.add_argument("--steps", type=int,  default=200_000)
    parser.add_argument("--out",    type=Path, default=None)
    parser.add_argument("--resume", action="store_true",
                        help="Resume training from existing model")
    args = parser.parse_args()

    out = args.out or (_PROJECTS_DIR / args.project / "5_rl" / "models" / "policy.zip")
    train(args.project, n_items=args.count, total_steps=args.steps, out=out, resume=args.resume)
