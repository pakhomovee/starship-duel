"""Per-observer belief about where the *rival* ship is (spec 3 / 3a).

This is the hidden-information layer.  The tracker is fed only *public* signals
(action categories + reveal events), never the rival's true position, so it can
be handed to a policy without leaking ground truth.

Because action categories are public (Jump/Hold/Claim/Fire are always
distinguishable, spec 3), we know exactly how many JUMPs the rival made, which
yields a tighter belief than a raw "BFS by total action budget" would: only
JUMPs expand the candidate set.
"""

from __future__ import annotations

from typing import Optional, Set

from .maps import GameMap
from .types import System


class BeliefTracker:
    """Tracks the candidate-system set for one observer's view of the rival.

    Lifecycle:
      * ``reset(candidates)`` at skirmish start (e.g. all systems consistent
        with the spawn constraint).
      * ``pin(system)`` on any reveal event -> collapse to a single system and
        stay pinned while the rival remains exposed.
      * ``on_rival_jump()`` when the (cloaked) rival is observed to JUMP ->
        expand by one hop.
      * ``on_rival_hold_or_op()`` for any non-moving observed action -> no
        change (the rival did not move).
    """

    def __init__(self, gmap: GameMap):
        self._map = gmap
        self._candidates: Set[System] = set(gmap.systems)
        self._pinned: bool = False

    # -- queries -------------------------------------------------------------
    @property
    def candidates(self) -> Set[System]:
        return set(self._candidates)

    @property
    def is_pinned(self) -> bool:
        return self._pinned

    # -- updates -------------------------------------------------------------
    def reset(self, candidates: Set[System]) -> None:
        self._candidates = set(candidates)
        self._pinned = False

    def pin(self, system: System) -> None:
        """Collapse to an exact system (a reveal happened)."""
        self._candidates = {system}
        self._pinned = True

    def unpin(self) -> None:
        """Rival re-cloaked; keep current position as the seed but allow the
        set to expand again on future JUMPs."""
        self._pinned = False

    def on_rival_jump(self) -> None:
        # A cloaked JUMP always widens the belief by one hop.  (An *exposed*
        # ship's jump never reaches this method -- the engine pins it to the
        # exact new system instead -- so unconditional expansion here is what
        # keeps the belief sound: a one-shot reveal from Scan/LRS collapses the
        # set, and this re-expands it as the still-cloaked rival moves on.)
        widened: Set[System] = set()
        for c in self._candidates:
            widened.update(self._map.neighbors(c))
        # A JUMP always changes system, so the pre-jump systems drop out unless
        # reachable as a neighbour of some other candidate.
        self._candidates = widened
        self._pinned = False  # lock released; the rival is on the move again

    def on_rival_hold_or_op(self) -> None:
        # Non-moving action -> candidate set unchanged.
        return

    def prune(self, excluded: Set[System]) -> None:
        """Remove systems ruled out by public constraints (never empties the
        set -- a contradictory prune is ignored rather than trusted)."""
        if self._pinned:
            return
        remaining = self._candidates - excluded
        if remaining:
            self._candidates = remaining

    def restrict_to_valid(self, valid: Set[System]) -> None:
        remaining = self._candidates & valid
        if remaining:
            self._candidates = remaining
