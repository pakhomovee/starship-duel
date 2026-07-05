"""Flat discrete action space for a fixed map.

A policy emits a single integer; :class:`ActionCodec` maps it to/from a game
:class:`~starship_duel.game.types.Action`, and produces the legal-action mask.

Layout for a map with ``N`` systems (sorted by name)::

    [0 .. N-1]  JUMP to system i         (legal only if adjacent to us)
    N+0         HOLD
    N+1         CLAIM
    N+2         FIRE
    N+3         SCAN
    N+4         DEEP_CLOAK
    N+5         OVERCHARGE
    N+6         UNLOCK_PROXIMITY_ALERT
    N+7         UNLOCK_LONG_RANGE_SCANNERS
    N+8         UNLOCK_JAMMING
    N+9         END_TURN

Encoding JUMP by *absolute destination index* (not "the k-th neighbour") keeps
the action meaning stable across positions and across maps of equal size.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..game import Action, ActionType, Observation
from ..game.maps import GameMap

_NON_JUMP_ORDER: List[ActionType] = [
    ActionType.HOLD,
    ActionType.CLAIM,
    ActionType.FIRE,
    ActionType.SCAN,
    ActionType.DEEP_CLOAK,
    ActionType.OVERCHARGE,
    ActionType.UNLOCK_PROXIMITY_ALERT,
    ActionType.UNLOCK_LONG_RANGE_SCANNERS,
    ActionType.UNLOCK_JAMMING,
    ActionType.END_TURN,
]


class ActionCodec:
    def __init__(self, systems: List[str]):
        self.systems: List[str] = list(systems)
        self.n_systems = len(self.systems)
        self._sys_index = {s: i for i, s in enumerate(self.systems)}
        self.n_actions = self.n_systems + len(_NON_JUMP_ORDER)
        self._nonjump_base = self.n_systems

    @classmethod
    def from_map(cls, gmap: GameMap) -> "ActionCodec":
        return cls(sorted(gmap.systems))

    # -- encode: Action -> index --------------------------------------------
    def encode(self, action: Action) -> int:
        if action.type is ActionType.JUMP:
            return self._sys_index[action.dest]
        return self._nonjump_base + _NON_JUMP_ORDER.index(action.type)

    # -- decode: index -> Action --------------------------------------------
    def decode(self, index: int) -> Action:
        if index < 0 or index >= self.n_actions:
            raise IndexError(f"action index {index} out of range [0,{self.n_actions})")
        if index < self.n_systems:
            return Action.jump(self.systems[index])
        atype = _NON_JUMP_ORDER[index - self._nonjump_base]
        return Action(atype)

    # -- legal-action mask ---------------------------------------------------
    def mask(self, obs: Observation) -> np.ndarray:
        """Binary mask (``int8``) over the flat action space for ``obs``.

        ``int8`` is what gymnasium's ``Discrete.sample(mask)`` and
        ``MultiBinary`` require.  Uses ``obs.legal_actions`` (already
        rules-filtered by the engine), so it is exactly the applicable set.
        """
        m = np.zeros(self.n_actions, dtype=np.int8)
        for a in obs.legal_actions:
            m[self.encode(a)] = 1
        return m

    def label(self, index: int) -> str:
        a = self.decode(index)
        return str(a)
