"""Multiprocessing rollout collection for the universal policy.

Same broadcast-to-persistent-workers design as the legacy
:mod:`starship_duel.rl.ppo.parallel` (fork on Linux, ``torch.set_num_threads(1)``,
snapshots cached by id), but each worker owns a :class:`UniversalGame` +
:class:`GraphActorCritic` and samples a map per episode.
"""

from __future__ import annotations

import multiprocessing as mp
import random
import sys
from typing import Dict, List, Tuple

from ..ppo.config import PPOConfig
from .game import UniversalGame
from .league import UniversalPolicyOpponent
from .model import GraphActorCritic

StateDict = dict


def _sample_opponent(rng, cfg, snaps, self_play, seed):
    from ...bots import make_bot
    if not self_play or not snaps:
        return make_bot(cfg.opponent, seed=seed)
    r = rng.random()
    if r < cfg.p_latest:
        model = snaps[max(snaps)]
    elif r < cfg.p_latest + cfg.p_past:
        model = snaps[rng.choice(list(snaps))]
    else:
        return make_bot(rng.choice(cfg.scripted_anchors), seed=seed)
    return UniversalPolicyOpponent(model)


def _new_model(game: UniversalGame, cfg: PPOConfig) -> GraphActorCritic:
    m = GraphActorCritic(game.node_dim, game.global_dim,
                         hidden=cfg.hidden, gnn_layers=cfg.gnn_layers)
    m.eval()
    return m


def _worker_main(worker_id: int, cfg: PPOConfig, task_q: mp.Queue, result_q: mp.Queue) -> None:
    import torch
    torch.set_num_threads(1)
    game = UniversalGame(train_maps=cfg.train_maps, seed=cfg.seed + 1000 + worker_id)
    model = _new_model(game, cfg)
    snaps: Dict[int, GraphActorCritic] = {}
    rng = random.Random(cfg.seed * 7919 + worker_id)

    while True:
        task = task_q.get()
        if task is None:
            break
        model.load_state_dict(task["learner_sd"])
        for sid, sd in task["new_snaps"]:
            m = _new_model(game, cfg)
            m.load_state_dict(sd)
            for p in m.parameters():
                p.requires_grad_(False)
            snaps[sid] = m
        if task["active_ids"] is not None:
            for k in [k for k in snaps if k not in task["active_ids"]]:
                del snaps[k]

        episodes = []
        for seed in task["seeds"]:
            opp = _sample_opponent(rng, cfg, snaps, task["self_play"], seed)
            episodes.append(game.collect(
                model, opp, map_id=rng.choice(cfg.train_maps),
                learner_seat=rng.randint(0, 1), first_ship=rng.randint(0, 1), seed=seed,
            ))
        result_q.put((worker_id, episodes))


class UniversalParallelCollector:
    def __init__(self, cfg: PPOConfig, num_workers: int):
        self.cfg = cfg
        self.num_workers = num_workers
        method = cfg.mp_start_method or ("fork" if sys.platform.startswith("linux") else "spawn")
        self._ctx = mp.get_context(method)
        self._task_qs = [self._ctx.Queue() for _ in range(num_workers)]
        self._result_q = self._ctx.Queue()
        self._procs = []
        for i in range(num_workers):
            p = self._ctx.Process(target=_worker_main,
                                  args=(i, cfg, self._task_qs[i], self._result_q), daemon=True)
            p.start()
            self._procs.append(p)
        self._snaps: List[Tuple[int, StateDict]] = []
        self._next_id = 0
        self._sent_ids: set = set()
        self._seed_counter = cfg.seed * 100003 + 1

    def add_snapshot(self, model: GraphActorCritic) -> None:
        sd = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        self._snaps.append((self._next_id, sd))
        self._next_id += 1
        if len(self._snaps) > self.cfg.max_snapshots:
            self._snaps.pop(0)

    @property
    def n_snapshots(self) -> int:
        return len(self._snaps)

    def collect(self, model: GraphActorCritic) -> List:
        cfg = self.cfg
        learner_sd = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        active_ids = [sid for sid, _ in self._snaps]
        new_snaps = [(sid, sd) for sid, sd in self._snaps if sid not in self._sent_ids]

        base, rem = divmod(cfg.episodes_per_update, self.num_workers)
        counts = [base + (1 if i < rem else 0) for i in range(self.num_workers)]
        for i, count in enumerate(counts):
            seeds = [self._seed_counter + j for j in range(count)]
            self._seed_counter += count
            self._task_qs[i].put({
                "learner_sd": learner_sd, "new_snaps": new_snaps,
                "active_ids": active_ids, "seeds": seeds, "self_play": cfg.self_play,
            })
        self._sent_ids.update(sid for sid, _ in new_snaps)

        episodes: List = []
        for _ in range(self.num_workers):
            _, worker_eps = self._result_q.get()
            episodes.extend(worker_eps)
        return episodes

    def close(self) -> None:
        for q in self._task_qs:
            q.put(None)
        for p in self._procs:
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()
