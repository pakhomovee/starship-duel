"""Opponent pool for self-play.

Before self-play is enabled the learner trains against a single scripted anchor
(Phase 1).  With ``self_play=True`` we accumulate frozen snapshots of the policy
and sample opponents from a mix of {latest snapshot, an older snapshot, a
scripted anchor}.  Keeping scripted anchors in the mix prevents the classic
self-play failure of forgetting how to beat simple, non-adaptive play.
"""

from __future__ import annotations

import copy
import random
from typing import List, Optional

import numpy as np

from ...bots import make_bot
from ...game import Action, Observation
from ..action_coding import ActionCodec
from ..encoders import ObservationEncoder
from ..model import MaskedActorCritic
from .config import PPOConfig


class PolicyOpponent:
    """A frozen policy snapshot wrapped as an :class:`Opponent`.

    Samples from the (masked) policy so the pool provides varied, non-brittle
    opposition rather than a single deterministic line.
    """

    def __init__(
        self,
        model: MaskedActorCritic,
        codec: ActionCodec,
        encoder: ObservationEncoder,
        deterministic: bool = False,
    ):
        self.model = model
        self.codec = codec
        self.encoder = encoder
        self.deterministic = deterministic

    def reset(self) -> None:  # frozen: no per-game state
        pass

    def act(self, obs: Observation) -> Action:
        enc = self.encoder.encode(obs)
        mask = self.codec.mask(obs)
        a_idx, _, _ = self.model.act_numpy(enc, mask, deterministic=self.deterministic)
        return self.codec.decode(int(a_idx))


class League:
    def __init__(self, cfg: PPOConfig, codec: ActionCodec, encoder: ObservationEncoder):
        self.cfg = cfg
        self.codec = codec
        self.encoder = encoder
        self._snapshots: List[MaskedActorCritic] = []

    # -- pool management ----------------------------------------------------
    def add_snapshot(self, model: MaskedActorCritic) -> None:
        """Freeze a deep copy of ``model`` and add it to the pool (FIFO capped)."""
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

    # -- sampling -----------------------------------------------------------
    def sample(self, rng: random.Random, seed: Optional[int] = None):
        """Return an opponent for one game.

        Phase 1 (no self-play or empty pool): the configured scripted anchor.
        Phase 2: weighted choice of latest / past snapshot / scripted anchor.
        """
        if not self.cfg.self_play or not self._snapshots:
            return make_bot(self.cfg.opponent, seed=seed)

        r = rng.random()
        p_latest, p_past = self.cfg.p_latest, self.cfg.p_past
        if r < p_latest:
            model = self._snapshots[-1]
        elif r < p_latest + p_past:
            model = rng.choice(self._snapshots)
        else:
            anchor = rng.choice(self.cfg.scripted_anchors)
            return make_bot(anchor, seed=seed)
        return PolicyOpponent(model, self.codec, self.encoder)
