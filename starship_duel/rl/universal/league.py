"""Opponent pool for universal self-play.

A frozen snapshot is a :class:`GraphActorCritic`; wrapped as an opponent it plays
*any* map because it builds its graph adapters straight from each observation's
adjacency (so it also handles maps outside the training set).
"""

from __future__ import annotations

import copy
import random
from typing import Dict, List, Tuple

from ...bots import make_bot
from ...game import Action, Observation
from ..ppo.config import PPOConfig
from .graph_action import UniversalActionCodec
from .graph_encoder import GraphObsEncoder
from .model import GraphActorCritic


class UniversalPolicyOpponent:
    """Play any map with a frozen universal policy (adapters built per-map)."""

    def __init__(self, model: GraphActorCritic, deterministic: bool = False):
        self.model = model
        self.deterministic = deterministic
        self._cache: Dict[Tuple[str, ...], Tuple[UniversalActionCodec, GraphObsEncoder]] = {}

    def reset(self) -> None:
        pass

    def _adapters(self, obs: Observation):
        systems = tuple(sorted(obs.adjacency.keys()))
        if systems not in self._cache:
            self._cache[systems] = (
                UniversalActionCodec(list(systems)), GraphObsEncoder(list(systems))
            )
        return self._cache[systems]

    def act(self, obs: Observation) -> Action:
        codec, encoder = self._adapters(obs)
        a_idx, _, _ = self.model.act_numpy(
            encoder.encode(obs), codec.mask(obs), deterministic=self.deterministic
        )
        return codec.decode(int(a_idx))


class UniversalLeague:
    def __init__(self, cfg: PPOConfig):
        self.cfg = cfg
        self._snapshots: List[GraphActorCritic] = []

    def add_snapshot(self, model: GraphActorCritic) -> None:
        snap = copy.deepcopy(model)
        snap.eval()
        for p in snap.parameters():
            p.requires_grad_(False)
        self._snapshots.append(snap)
        if len(self._snapshots) > self.cfg.max_snapshots:
            self._snapshots.pop(0)

    @property
    def n_snapshots(self) -> int:
        return len(self._snapshots)

    def sample(self, rng: random.Random, seed=None):
        if not self.cfg.self_play or not self._snapshots:
            return make_bot(self.cfg.opponent, seed=seed)
        r = rng.random()
        if r < self.cfg.p_latest:
            model = self._snapshots[-1]
        elif r < self.cfg.p_latest + self.cfg.p_past:
            model = rng.choice(self._snapshots)
        else:
            return make_bot(rng.choice(self.cfg.scripted_anchors), seed=seed)
        return UniversalPolicyOpponent(model)
