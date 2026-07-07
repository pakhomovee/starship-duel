"""CLI entrypoint for PPO training.

Phase 1 (validate PPO)::

    python -m starship_duel.rl.ppo.train --opponent heuristic --total-updates 300

Phase 2 (self-play league)::

    python -m starship_duel.rl.ppo.train --self-play --total-updates 3000 \
        --log-dir runs/ppo_league
"""

from __future__ import annotations

import argparse
from typing import List, Optional

from .config import PPOConfig
from .trainer import PPOTrainer


def _parse(argv: Optional[List[str]]) -> PPOConfig:
    cfg = PPOConfig()
    p = argparse.ArgumentParser(description="Train a Starship Duel policy with PPO.")
    p.add_argument("--map-id", default=cfg.map_id)
    p.add_argument("--seed", type=int, default=cfg.seed)
    p.add_argument("--total-updates", type=int, default=cfg.total_updates)
    p.add_argument("--episodes-per-update", type=int, default=cfg.episodes_per_update)
    p.add_argument("--num-workers", type=int, default=cfg.num_workers,
                   help="rollout worker processes (0/1 = in-process; ~#cores on a VM)")
    p.add_argument("--mp-start-method", default=cfg.mp_start_method,
                   choices=["fork", "spawn", "forkserver"],
                   help="override worker start method (auto: fork on Linux)")
    p.add_argument("--hidden", type=int, default=cfg.hidden)
    p.add_argument("--depth", type=int, default=cfg.depth)
    p.add_argument("--lr", type=float, default=cfg.lr)
    p.add_argument("--gamma", type=float, default=cfg.gamma)
    p.add_argument("--ent-coef", type=float, default=cfg.ent_coef)
    p.add_argument("--opponent", default=cfg.opponent, help="scripted anchor opponent")
    p.add_argument("--self-play", action="store_true", help="enable the snapshot league")
    p.add_argument("--snapshot-every", type=int, default=cfg.snapshot_every)
    p.add_argument("--eval-every", type=int, default=cfg.eval_every)
    p.add_argument("--eval-games", type=int, default=cfg.eval_games)
    p.add_argument("--log-dir", default=cfg.log_dir)
    p.add_argument("--device", default=cfg.device)
    a = p.parse_args(argv)

    cfg.map_id = a.map_id
    cfg.seed = a.seed
    cfg.total_updates = a.total_updates
    cfg.episodes_per_update = a.episodes_per_update
    cfg.num_workers = a.num_workers
    cfg.mp_start_method = a.mp_start_method
    cfg.hidden = a.hidden
    cfg.depth = a.depth
    cfg.lr = a.lr
    cfg.gamma = a.gamma
    cfg.ent_coef = a.ent_coef
    cfg.opponent = a.opponent
    cfg.self_play = a.self_play
    cfg.snapshot_every = a.snapshot_every
    cfg.eval_every = a.eval_every
    cfg.eval_games = a.eval_games
    cfg.log_dir = a.log_dir
    cfg.device = a.device
    return cfg


def main(argv: Optional[List[str]] = None) -> None:
    cfg = _parse(argv)
    print(f"[ppo] map={cfg.map_id} self_play={cfg.self_play} "
          f"opponent={cfg.opponent} updates={cfg.total_updates} "
          f"episodes/update={cfg.episodes_per_update} workers={cfg.num_workers} "
          f"device={cfg.device}")
    PPOTrainer(cfg).train()


if __name__ == "__main__":
    main()
