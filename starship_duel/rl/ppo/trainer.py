"""The PPO update loop: rollout collection, GAE, clipped-surrogate optimization,
self-play league management, periodic evaluation, and checkpointing.

Single-process rollouts for now (the pure-Python sim is fast on CPU); the loop is
structured so ``collect_rollout`` can later be swapped for a multiprocessing pool
without touching the optimization code.
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

from ..model import MaskedActorCritic
from ..single_agent_view import EncodedGame, Episode
from .buffer import RolloutBatch
from .config import PPOConfig
from .eval import evaluate
from .league import League


class PPOTrainer:
    def __init__(self, cfg: PPOConfig):
        self.cfg = cfg
        random.seed(cfg.seed)
        np.random.seed(cfg.seed)
        torch.manual_seed(cfg.seed)
        self.rng = random.Random(cfg.seed)

        self.game = EncodedGame(map_id=cfg.map_id, seed=cfg.seed)
        self.device = torch.device(cfg.device)
        self.model = MaskedActorCritic(
            self.game.obs_size, self.game.n_actions, hidden=cfg.hidden, depth=cfg.depth
        ).to(self.device)
        if cfg.init_from:
            ckpt = torch.load(cfg.init_from, map_location=self.device, weights_only=False)
            self.model.load_state_dict(ckpt["model_state"])
            print(f"[ppo] warm-started policy from {cfg.init_from}")
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=cfg.lr, eps=1e-5)

        # Rollouts: in-process League, or a pool of worker processes.  Either way
        # the object exposes add_snapshot() / n_snapshots so the loop is uniform.
        if cfg.num_workers and cfg.num_workers > 1:
            from .parallel import ParallelRolloutCollector
            self.parallel = ParallelRolloutCollector(cfg, cfg.num_workers)
            self.league = None
        else:
            self.parallel = None
            self.league = League(cfg, self.game.codec, self.game.encoder)
        # Whichever collector owns the snapshot pool (uniform add_snapshot/n_snapshots).
        self._pool = self.parallel if self.parallel is not None else self.league

        os.makedirs(cfg.log_dir, exist_ok=True)
        self._metrics_path = os.path.join(cfg.log_dir, "metrics.jsonl")
        self._writer = self._maybe_tensorboard()
        self.global_step = 0

    def _maybe_tensorboard(self):
        try:
            from torch.utils.tensorboard import SummaryWriter
        except Exception:
            print("[ppo] tensorboard not installed; logging to metrics.jsonl only")
            return None
        # ``max_queue`` bounds the in-RAM event buffer; ``flush_secs`` bounds how
        # long anything sits unwritten -- keeps the memory footprint tiny.
        writer = SummaryWriter(self.cfg.log_dir, max_queue=100, flush_secs=30)
        import dataclasses
        writer.add_text("config", "```\n" + json.dumps(dataclasses.asdict(self.cfg), indent=2) + "\n```")
        return writer

    @staticmethod
    def _tb_tag(key: str) -> str:
        """Group flat metric keys into TensorBoard sections for a readable UI."""
        if key.startswith("eval/"):
            return key
        if key in ("policy_loss", "value_loss", "entropy", "approx_kl",
                   "clipfrac", "explained_variance"):
            return f"losses/{key}"
        if key in ("rollout_win_rate", "rollout_draw_rate", "mean_ep_len",
                   "mean_reward", "illegal_samples", "n_snapshots"):
            return f"charts/{key}"
        return f"perf/{key}"

    # -- rollout ------------------------------------------------------------
    def collect_rollout(self) -> List[Episode]:
        if self.parallel is not None:
            return self.parallel.collect(self.model)
        episodes: List[Episode] = []
        for _ in range(self.cfg.episodes_per_update):
            seed = self.rng.randrange(2**31)
            opponent = self.league.sample(self.rng, seed=seed)
            episodes.append(
                self.game.collect(
                    self.model,
                    opponent,
                    learner_seat=self.rng.randint(0, 1),
                    first_ship=self.rng.randint(0, 1),
                    seed=seed,
                )
            )
        return episodes

    # -- PPO update ---------------------------------------------------------
    def update(self, batch: RolloutBatch) -> dict:
        cfg = self.cfg
        n = len(batch)
        idx = np.arange(n)
        minibatch_size = max(1, n // cfg.num_minibatches)

        clipfracs: List[float] = []
        approx_kl = 0.0
        pg_loss = v_loss = ent = torch.tensor(0.0)
        for _ in range(cfg.update_epochs):
            np.random.shuffle(idx)
            stop = False
            for start in range(0, n, minibatch_size):
                mb = idx[start : start + minibatch_size]
                mb_obs = batch.obs[mb]
                mb_mask = batch.masks[mb]
                mb_act = batch.actions[mb]

                _, newlogp, entropy, newvalue = self.model.get_action_and_value(
                    mb_obs, mb_mask, mb_act
                )
                logratio = newlogp - batch.logprobs[mb]
                ratio = logratio.exp()

                with torch.no_grad():
                    approx_kl = ((ratio - 1) - logratio).mean().item()
                    clipfracs.append(((ratio - 1.0).abs() > cfg.clip_coef).float().mean().item())

                adv = batch.advantages[mb]
                if cfg.norm_adv and adv.numel() > 1:
                    adv = (adv - adv.mean()) / (adv.std() + 1e-8)

                # Clipped policy loss.
                pg1 = -adv * ratio
                pg2 = -adv * torch.clamp(ratio, 1 - cfg.clip_coef, 1 + cfg.clip_coef)
                pg_loss = torch.max(pg1, pg2).mean()

                # (Optionally clipped) value loss.
                newvalue = newvalue.view(-1)
                if cfg.clip_vloss:
                    v_unclipped = (newvalue - batch.returns[mb]) ** 2
                    v_clipped = batch.values[mb] + torch.clamp(
                        newvalue - batch.values[mb], -cfg.clip_coef, cfg.clip_coef
                    )
                    v_clipped = (v_clipped - batch.returns[mb]) ** 2
                    v_loss = 0.5 * torch.max(v_unclipped, v_clipped).mean()
                else:
                    v_loss = 0.5 * ((newvalue - batch.returns[mb]) ** 2).mean()

                ent = entropy.mean()
                loss = pg_loss - cfg.ent_coef * ent + cfg.vf_coef * v_loss

                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), cfg.max_grad_norm)
                self.optimizer.step()

            if cfg.target_kl is not None and approx_kl > cfg.target_kl:
                stop = True
            if stop:
                break

        # explained variance of the value function
        y_pred = batch.values.cpu().numpy()
        y_true = batch.returns.cpu().numpy()
        var_y = np.var(y_true)
        explained_var = np.nan if var_y == 0 else 1 - np.var(y_true - y_pred) / var_y

        return {
            "policy_loss": float(pg_loss.item()),
            "value_loss": float(v_loss.item()),
            "entropy": float(ent.item()),
            "approx_kl": float(approx_kl),
            "clipfrac": float(np.mean(clipfracs)) if clipfracs else 0.0,
            "explained_variance": float(explained_var),
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

            batch = RolloutBatch(episodes, cfg.gamma, cfg.gae_lambda, cfg.device)
            stats = self.update(batch)

            wins = sum(ep.win for ep in episodes)
            draws = sum(ep.draw for ep in episodes)
            illegal = sum(ep.illegal_samples for ep in episodes)
            rollout_stats = {
                "update": update,
                "global_step": self.global_step,
                "rollout_win_rate": wins / len(episodes),
                "rollout_draw_rate": draws / len(episodes),
                "mean_ep_len": float(np.mean([ep.length for ep in episodes])),
                "mean_reward": float(np.mean([ep.reward for ep in episodes])),
                "illegal_samples": illegal,
                "n_snapshots": self._pool.n_snapshots,
                "sps": int(self.global_step / (time.time() - start + 1e-9)),
                **stats,
            }

            if illegal:
                print(f"  !! WARNING: {illegal} illegal policy actions this update "
                      f"(action masking is broken)")

            # self-play snapshotting
            if cfg.self_play and update % cfg.snapshot_every == 0:
                self._pool.add_snapshot(self.model)

            # evaluation
            if update % cfg.eval_every == 0 or update == 1:
                evals = evaluate(self.model, self.game, cfg.eval_opponents, cfg.eval_games)
                for name, res in evals.items():
                    rollout_stats[f"eval/{name}_winrate"] = res.win_rate
                    rollout_stats[f"eval/{name}_score"] = res.score
                self._log(rollout_stats, evals)
            else:
                self._log(rollout_stats, None)

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
            step = stats["update"]
            for k, v in stats.items():
                if isinstance(v, (int, float)) and k not in ("update", "global_step"):
                    self._writer.add_scalar(self._tb_tag(k), v, step)

        msg = (f"upd {stats['update']:>4} | wr {stats['rollout_win_rate']:.2f} "
               f"draw {stats['rollout_draw_rate']:.2f} | len {stats['mean_ep_len']:.1f} "
               f"| ent {stats['entropy']:.3f} kl {stats['approx_kl']:.3f} "
               f"ev {stats['explained_variance']:.2f} | sps {stats['sps']}")
        if evals is not None:
            evtxt = "  ".join(f"{n}:{r.win_rate:.2f}" for n, r in evals.items())
            msg += f"\n         eval[{evtxt}]"
        print(msg)

    def save(self, path: str) -> None:
        torch.save(
            {
                "model_state": self.model.state_dict(),
                "obs_size": self.game.obs_size,
                "n_actions": self.game.n_actions,
                "systems": self.game.codec.systems,
                "map_id": self.cfg.map_id,
                "hidden": self.cfg.hidden,
                "depth": self.cfg.depth,
            },
            path,
        )
