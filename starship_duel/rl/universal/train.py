"""CLI entrypoint for map-universal PPO training.

Phase 1 (validate vs heuristic across all maps)::

    python -m starship_duel.rl.universal.train --num-workers 32 \
        --episodes-per-update 512 --total-updates 400 --log-dir runs/uni-ppo-phase1

Phase 2 (self-play league)::

    python -m starship_duel.rl.universal.train --self-play --num-workers 32 \
        --episodes-per-update 512 --total-updates 4000 \
        --init-from runs/uni-ppo-phase1/ckpt_final.pt --log-dir runs/uni-ppo-league
"""

from __future__ import annotations

import argparse
from typing import List, Optional

from ..ppo.config import PPOConfig
from .trainer import GraphPPOTrainer


def _parse(argv: Optional[List[str]]) -> PPOConfig:
    cfg = PPOConfig(log_dir="runs/uni-ppo")
    p = argparse.ArgumentParser(description="Train a map-universal Starship Duel policy (GNN PPO).")
    p.add_argument("--train-maps", nargs="+", default=cfg.train_maps,
                   help="maps sampled per episode (default: all four)")
    p.add_argument("--seed", type=int, default=cfg.seed)
    p.add_argument("--total-updates", type=int, default=cfg.total_updates)
    p.add_argument("--episodes-per-update", type=int, default=cfg.episodes_per_update)
    p.add_argument("--num-workers", type=int, default=cfg.num_workers)
    p.add_argument("--mp-start-method", default=cfg.mp_start_method,
                   choices=["fork", "spawn", "forkserver"])
    p.add_argument("--hidden", type=int, default=cfg.hidden)
    p.add_argument("--gnn-layers", type=int, default=cfg.gnn_layers)
    p.add_argument("--lr", type=float, default=cfg.lr)
    p.add_argument("--ent-coef", type=float, default=cfg.ent_coef)
    p.add_argument("--opponent", default=cfg.opponent)
    p.add_argument("--self-play", action="store_true")
    p.add_argument("--init-from", default=cfg.init_from)
    p.add_argument("--snapshot-every", type=int, default=cfg.snapshot_every)
    p.add_argument("--eval-every", type=int, default=cfg.eval_every)
    p.add_argument("--eval-games", type=int, default=cfg.eval_games)
    p.add_argument("--log-dir", default=cfg.log_dir)
    p.add_argument("--device", default=cfg.device)
    a = p.parse_args(argv)

    cfg.train_maps = a.train_maps
    cfg.seed = a.seed
    cfg.total_updates = a.total_updates
    cfg.episodes_per_update = a.episodes_per_update
    cfg.num_workers = a.num_workers
    cfg.mp_start_method = a.mp_start_method
    cfg.hidden = a.hidden
    cfg.gnn_layers = a.gnn_layers
    cfg.lr = a.lr
    cfg.ent_coef = a.ent_coef
    cfg.opponent = a.opponent
    cfg.self_play = a.self_play
    cfg.init_from = a.init_from
    cfg.snapshot_every = a.snapshot_every
    cfg.eval_every = a.eval_every
    cfg.eval_games = a.eval_games
    cfg.log_dir = a.log_dir
    cfg.device = a.device
    return cfg


def main(argv: Optional[List[str]] = None) -> None:
    cfg = _parse(argv)
    print(f"[uppo] universal GNN | maps={cfg.train_maps} self_play={cfg.self_play} "
          f"updates={cfg.total_updates} eps/upd={cfg.episodes_per_update} "
          f"workers={cfg.num_workers} device={cfg.device}")
    GraphPPOTrainer(cfg).train()


if __name__ == "__main__":
    main()
