"""Multiprocessing rollout collection.

The pure-Python simulator is CPU-bound and the GIL makes threads useless here, so
we fan rollouts out across **persistent worker processes**.  Each worker owns its
own :class:`EncodedGame` + a local copy of the learner network + a cache of
frozen opponent snapshots; every update the main process **broadcasts** the fresh
learner weights (and any newly-added snapshot) to every worker, then each worker
plays its share of games and ships back the resulting :class:`Episode` objects
(numpy arrays -- cheap to pickle).

Design notes:
  - Persistent workers + per-worker task queues (a broadcast to all), not a
    ``Pool`` -- a Pool can't guarantee each worker sees each new snapshot exactly
    once, which is what lets us cache snapshots by id instead of resending them.
  - ``torch.set_num_threads(1)`` in every worker: 32 processes each spinning up
    BLAS threads would oversubscribe the box and run *slower*.
  - Opponent sampling (league logic) runs worker-side with the same cfg weights,
    mirroring :class:`~starship_duel.rl.ppo.league.League`.
"""

from __future__ import annotations

import multiprocessing as mp
import random
import sys
from typing import Dict, List, Optional, Tuple

from ..model import MaskedActorCritic
from ..single_agent_view import EncodedGame
from .config import PPOConfig
from .league import PolicyOpponent

# A snapshot ships as (id, state_dict); the learner ships as a bare state_dict.
StateDict = dict


def _sample_opponent(rng: random.Random, cfg: PPOConfig, snaps: Dict[int, MaskedActorCritic],
                     game: EncodedGame, seed: int, self_play: bool):
    """Worker-side mirror of League.sample (latest snapshot == highest id)."""
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
    return PolicyOpponent(model, game.codec, game.encoder)


def _worker_main(worker_id: int, cfg: PPOConfig, task_q: mp.Queue, result_q: mp.Queue) -> None:
    import torch

    torch.set_num_threads(1)
    game = EncodedGame(map_id=cfg.map_id, seed=cfg.seed + 1000 + worker_id)
    model = MaskedActorCritic(game.obs_size, game.n_actions, hidden=cfg.hidden, depth=cfg.depth)
    model.eval()
    snaps: Dict[int, MaskedActorCritic] = {}
    rng = random.Random(cfg.seed * 7919 + worker_id)

    while True:
        task = task_q.get()
        if task is None:
            break

        model.load_state_dict(task["learner_sd"])
        for sid, sd in task["new_snaps"]:
            m = MaskedActorCritic(game.obs_size, game.n_actions, hidden=cfg.hidden, depth=cfg.depth)
            m.load_state_dict(sd)
            m.eval()
            for p in m.parameters():
                p.requires_grad_(False)
            snaps[sid] = m
        if task["active_ids"] is not None:
            for k in [k for k in snaps if k not in task["active_ids"]]:
                del snaps[k]

        episodes = []
        for seed in task["seeds"]:
            opp = _sample_opponent(rng, cfg, snaps, game, seed, task["self_play"])
            episodes.append(
                game.collect(
                    model, opp,
                    learner_seat=rng.randint(0, 1),
                    first_ship=rng.randint(0, 1),
                    seed=seed,
                )
            )
        result_q.put((worker_id, episodes))


class ParallelRolloutCollector:
    """Owns the worker pool and the snapshot registry shipped to workers."""

    def __init__(self, cfg: PPOConfig, num_workers: int):
        self.cfg = cfg
        self.num_workers = num_workers
        # fork on Linux keeps RAM low (COW-shared torch); spawn on macOS/Windows.
        method = cfg.mp_start_method or ("fork" if sys.platform.startswith("linux") else "spawn")
        self._ctx = mp.get_context(method)
        self._task_qs: List[mp.Queue] = [self._ctx.Queue() for _ in range(num_workers)]
        self._result_q: mp.Queue = self._ctx.Queue()
        self._procs: List[mp.Process] = []
        for i in range(num_workers):
            p = self._ctx.Process(
                target=_worker_main, args=(i, cfg, self._task_qs[i], self._result_q), daemon=True
            )
            p.start()
            self._procs.append(p)

        # Snapshot registry (mirrors League but stores CPU state_dicts + ids).
        self._snaps: List[Tuple[int, StateDict]] = []
        self._next_id = 0
        self._sent_ids: set = set()
        self._seed_counter = cfg.seed * 100003 + 1

    # -- snapshot pool ------------------------------------------------------
    def add_snapshot(self, model: MaskedActorCritic) -> None:
        sd = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        self._snaps.append((self._next_id, sd))
        self._next_id += 1
        if len(self._snaps) > self.cfg.max_snapshots:
            self._snaps.pop(0)

    @property
    def n_snapshots(self) -> int:
        return len(self._snaps)

    # -- collection ---------------------------------------------------------
    def collect(self, model: MaskedActorCritic) -> List:
        cfg = self.cfg
        learner_sd = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        active_ids = [sid for sid, _ in self._snaps]
        new_snaps = [(sid, sd) for sid, sd in self._snaps if sid not in self._sent_ids]

        # Split episodes across workers (spread the remainder over the first few).
        n = cfg.episodes_per_update
        base, rem = divmod(n, self.num_workers)
        counts = [base + (1 if i < rem else 0) for i in range(self.num_workers)]

        for i, count in enumerate(counts):
            seeds = [self._seed_counter + j for j in range(count)]
            self._seed_counter += count
            self._task_qs[i].put({
                "learner_sd": learner_sd,
                "new_snaps": new_snaps,          # every worker caches the same new ids
                "active_ids": active_ids,
                "seeds": seeds,
                "self_play": cfg.self_play,
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
