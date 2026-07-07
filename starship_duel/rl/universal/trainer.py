"""PPO trainer for the map-universal GNN policy.

Mirrors the legacy :class:`~starship_duel.rl.ppo.trainer.PPOTrainer` (same clipped
surrogate, GAE, league self-play, checkpointing) but operates on graph batches
and samples a map per episode.  Evaluation is reported per map.
"""

from __future__ import annotations

import json
import os
import random
import time
from typing import List

import numpy as np
import torch
import torch.nn as nn

from ..ppo.config import PPOConfig
from .buffer import GraphRolloutBatch
from .eval import evaluate
from .game import GraphEpisode, UniversalGame
from .league import UniversalLeague
from .model import GraphActorCritic


class GraphPPOTrainer:
    def __init__(self, cfg: PPOConfig):
        self.cfg = cfg
        random.seed(cfg.seed); np.random.seed(cfg.seed); torch.manual_seed(cfg.seed)
        self.rng = random.Random(cfg.seed)
        self.device = torch.device(cfg.device)

        self.game = UniversalGame(train_maps=cfg.train_maps, seed=cfg.seed)
        self.model = GraphActorCritic(
            self.game.node_dim, self.game.global_dim,
            hidden=cfg.hidden, gnn_layers=cfg.gnn_layers,
        ).to(self.device)
        if cfg.init_from:
            ckpt = torch.load(cfg.init_from, map_location=self.device, weights_only=False)
            self.model.load_state_dict(ckpt["model_state"])
            print(f"[uppo] warm-started from {cfg.init_from}")
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=cfg.lr, eps=1e-5)

        if cfg.num_workers and cfg.num_workers > 1:
            from .parallel import UniversalParallelCollector
            self.parallel = UniversalParallelCollector(cfg, cfg.num_workers)
            self.league = None
        else:
            self.parallel = None
            self.league = UniversalLeague(cfg)
        self._pool = self.parallel if self.parallel is not None else self.league

        os.makedirs(cfg.log_dir, exist_ok=True)
        self._metrics_path = os.path.join(cfg.log_dir, "metrics.jsonl")
        self._writer = self._maybe_tensorboard()
        self.global_step = 0

    def _maybe_tensorboard(self):
        try:
            from torch.utils.tensorboard import SummaryWriter
        except Exception:
            print("[uppo] tensorboard not installed; metrics.jsonl only")
            return None
        import dataclasses
        w = SummaryWriter(self.cfg.log_dir, max_queue=100, flush_secs=30)
        w.add_text("config", "```\n" + json.dumps(dataclasses.asdict(self.cfg), indent=2) + "\n```")
        return w

    # -- rollout ------------------------------------------------------------
    def collect_rollout(self) -> List[GraphEpisode]:
        if self.parallel is not None:
            return self.parallel.collect(self.model)
        episodes = []
        for _ in range(self.cfg.episodes_per_update):
            seed = self.rng.randrange(2**31)
            episodes.append(self.game.collect(
                self.model, self.league.sample(self.rng, seed=seed),
                map_id=self.rng.choice(self.cfg.train_maps),
                learner_seat=self.rng.randint(0, 1),
                first_ship=self.rng.randint(0, 1), seed=seed,
            ))
        return episodes

    # -- PPO update ---------------------------------------------------------
    def update(self, batch: GraphRolloutBatch) -> dict:
        cfg = self.cfg
        n = len(batch)
        idx = np.arange(n)
        minibatch_size = max(1, n // cfg.num_minibatches)
        clipfracs: List[float] = []
        approx_kl = 0.0
        pg_loss = v_loss = ent = torch.tensor(0.0)

        for _ in range(cfg.update_epochs):
            np.random.shuffle(idx)
            for start in range(0, n, minibatch_size):
                mb = idx[start:start + minibatch_size]
                _, newlogp, entropy, newvalue = self.model.get_action_and_value(
                    batch.minibatch(mb), batch.actions[mb]
                )
                logratio = newlogp - batch.logprobs[mb]
                ratio = logratio.exp()
                with torch.no_grad():
                    approx_kl = ((ratio - 1) - logratio).mean().item()
                    clipfracs.append(((ratio - 1.0).abs() > cfg.clip_coef).float().mean().item())

                adv = batch.advantages[mb]
                if cfg.norm_adv and adv.numel() > 1:
                    adv = (adv - adv.mean()) / (adv.std() + 1e-8)
                pg_loss = torch.max(
                    -adv * ratio,
                    -adv * torch.clamp(ratio, 1 - cfg.clip_coef, 1 + cfg.clip_coef),
                ).mean()

                newvalue = newvalue.view(-1)
                if cfg.clip_vloss:
                    v_unc = (newvalue - batch.returns[mb]) ** 2
                    v_cl = batch.values[mb] + torch.clamp(
                        newvalue - batch.values[mb], -cfg.clip_coef, cfg.clip_coef)
                    v_loss = 0.5 * torch.max(v_unc, (v_cl - batch.returns[mb]) ** 2).mean()
                else:
                    v_loss = 0.5 * ((newvalue - batch.returns[mb]) ** 2).mean()

                ent = entropy.mean()
                loss = pg_loss - cfg.ent_coef * ent + cfg.vf_coef * v_loss
                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), cfg.max_grad_norm)
                self.optimizer.step()

            if cfg.target_kl is not None and approx_kl > cfg.target_kl:
                break

        y_pred, y_true = batch.values.cpu().numpy(), batch.returns.cpu().numpy()
        var_y = np.var(y_true)
        ev = np.nan if var_y == 0 else 1 - np.var(y_true - y_pred) / var_y
        return {
            "policy_loss": float(pg_loss.item()), "value_loss": float(v_loss.item()),
            "entropy": float(ent.item()), "approx_kl": float(approx_kl),
            "clipfrac": float(np.mean(clipfracs)) if clipfracs else 0.0,
            "explained_variance": float(ev),
        }

    # -- main loop ----------------------------------------------------------
    def train(self) -> None:
        cfg = self.cfg
        start = time.time()
        for update in range(1, cfg.total_updates + 1):
            if cfg.anneal_lr:
                frac = 1.0 - (update - 1) / cfg.total_updates
                for g in self.optimizer.param_groups:
                    g["lr"] = frac * cfg.lr

            episodes = self.collect_rollout()
            self.global_step += sum(ep.length for ep in episodes)
            batch = GraphRolloutBatch(episodes, cfg.gamma, cfg.gae_lambda, cfg.device)
            stats = self.update(batch)

            illegal = sum(ep.illegal_samples for ep in episodes)
            row = {
                "update": update, "global_step": self.global_step,
                "rollout_win_rate": sum(ep.win for ep in episodes) / len(episodes),
                "rollout_draw_rate": sum(ep.draw for ep in episodes) / len(episodes),
                "mean_ep_len": float(np.mean([ep.length for ep in episodes])),
                "illegal_samples": illegal, "n_snapshots": self._pool.n_snapshots,
                "sps": int(self.global_step / (time.time() - start + 1e-9)), **stats,
            }
            if illegal:
                print(f"  !! WARNING: {illegal} illegal policy actions (masking broken)")
            if cfg.self_play and update % cfg.snapshot_every == 0:
                self._pool.add_snapshot(self.model)

            evals = None
            if update % cfg.eval_every == 0 or update == 1:
                evals = evaluate(self.model, self.game, cfg.train_maps,
                                 cfg.eval_opponents, cfg.eval_games)
                per_opp = {o: [] for o in cfg.eval_opponents}
                for map_id, res in evals.items():
                    for o, r in res.items():
                        row[f"eval/{map_id}/{o}_winrate"] = r.win_rate
                        per_opp[o].append(r.win_rate)
                for o, xs in per_opp.items():
                    row[f"eval/mean/{o}_winrate"] = float(np.mean(xs))
            self._log(row, evals)
            if update % cfg.save_every == 0:
                self.save(os.path.join(cfg.log_dir, f"ckpt_{update}.pt"))

        self.save(os.path.join(cfg.log_dir, "ckpt_final.pt"))
        if self._writer is not None:
            self._writer.close()
        if self.parallel is not None:
            self.parallel.close()

    # -- logging / io -------------------------------------------------------
    def _log(self, stats: dict, evals) -> None:
        with open(self._metrics_path, "a") as f:
            f.write(json.dumps(stats) + "\n")
        if self._writer is not None:
            try:
                step = stats["update"]
                for k, v in stats.items():
                    if isinstance(v, (int, float)) and k not in ("update", "global_step"):
                        tag = k if k.startswith("eval/") else (
                            f"losses/{k}" if k in ("policy_loss", "value_loss", "entropy",
                                                   "approx_kl", "clipfrac", "explained_variance")
                            else f"charts/{k}")
                        self._writer.add_scalar(tag, v, step)
            except Exception as e:
                print(f"[uppo] tensorboard disabled after error: {e}")
                self._writer = None
        msg = (f"upd {stats['update']:>4} | wr {stats['rollout_win_rate']:.2f} "
               f"len {stats['mean_ep_len']:.1f} | ent {stats['entropy']:.3f} "
               f"kl {stats['approx_kl']:.3f} ev {stats['explained_variance']:.2f} "
               f"| sps {stats['sps']}")
        if evals is not None:
            parts = []
            for o in self.cfg.eval_opponents:
                mean = np.mean([evals[m][o].win_rate for m in evals])
                parts.append(f"{o}:{mean:.2f}")
            msg += "\n         eval[mean " + "  ".join(parts) + "] per-map: " + \
                   " ".join(f"{m}({evals[m][self.cfg.eval_opponents[-1]].win_rate:.2f})" for m in evals)
        print(msg)

    def save(self, path: str) -> None:
        torch.save({
            "universal": True,
            "model_state": self.model.state_dict(),
            "node_dim": self.game.node_dim, "global_dim": self.game.global_dim,
            "hidden": self.cfg.hidden, "gnn_layers": self.cfg.gnn_layers,
            "train_maps": self.cfg.train_maps,
        }, path)
