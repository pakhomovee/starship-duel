"""Fixed-width action codec for the map-universal policy.

The action space is a **fixed** ``MAX_SYSTEMS + 10`` layout so a single network
output width works across every map::

    [0 .. MAX_SYSTEMS-1]   JUMP to the i-th system (sorted order); legal only if
                           that system exists on this map and is adjacent to us
    MAX_SYSTEMS + 0..9     the 10 non-JUMP verbs (HOLD, CLAIM, FIRE, ...)

JUMP index ``i`` maps to node ``i`` in :class:`GraphObsEncoder`'s sorted order,
so the GNN's per-node pointer logits align 1:1 with JUMP action indices.  A map
with ``N < MAX_SYSTEMS`` systems simply leaves JUMP slots ``N..MAX_SYSTEMS-1``
permanently masked out.
"""

from __future__ import annotations

from typing import List

import numpy as np

from ...game import Action, ActionType, Observation
from ..action_coding import _NON_JUMP_ORDER  # reuse the canonical verb order
from .graph_encoder import MAX_SYSTEMS


class UniversalActionCodec:
    def __init__(self, systems: List[str], max_systems: int = MAX_SYSTEMS):
        self.systems = list(systems)  # sorted; same order as the graph encoder
        self.n_systems = len(self.systems)
        self.max_systems = max_systems
        self._sys_index = {s: i for i, s in enumerate(self.systems)}
        self.verb_base = max_systems
        self.n_actions = max_systems + len(_NON_JUMP_ORDER)

    # -- encode: Action -> index --------------------------------------------
    def encode(self, action: Action) -> int:
        if action.type is ActionType.JUMP:
            return self._sys_index[action.dest]
        return self.verb_base + _NON_JUMP_ORDER.index(action.type)

    # -- decode: index -> Action --------------------------------------------
    def decode(self, index: int) -> Action:
        if index < 0 or index >= self.n_actions:
            raise IndexError(f"action index {index} out of range [0,{self.n_actions})")
        if index < self.max_systems:
            if index >= self.n_systems:
                raise IndexError(f"JUMP index {index} has no system on this map")
            return Action.jump(self.systems[index])
        return Action(_NON_JUMP_ORDER[index - self.verb_base])

    # -- legal-action mask ---------------------------------------------------
    def mask(self, obs: Observation) -> np.ndarray:
        m = np.zeros(self.n_actions, dtype=np.int8)
        for a in obs.legal_actions:
            m[self.encode(a)] = 1
        return m

    def label(self, index: int) -> str:
        return str(self.decode(index))
