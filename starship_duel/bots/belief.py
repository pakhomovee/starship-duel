"""A bot-side belief tracker.

The engine no longer hands bots a ready-made "could be here" set — an
:class:`~starship_duel.game.observation.Observation` reveals the rival's *exact*
system only when it's known for certain (``obs.rival_position``).  But it also
gives you the pieces to redo the reachability BFS yourself: ``rival_last_seen``
(the last confirmed system) and ``rival_moves_since_seen`` (an upper bound on how
far it could have travelled since).  This helper turns those into a candidate
set; a custom bot can of course track something sharper.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from ..game import Observation, System


class BotBelief:
    def __init__(self):
        self.candidates: Set[System] = set()
        self._adj: Dict[System, List[System]] = {}

    def reset(self) -> None:
        self.candidates = set()

    def observe(self, obs: Observation) -> Set[System]:
        """Rebuild the estimate from this observation; returns the candidate set.

        Same reachability BFS the engine used to expose: everything within
        ``rival_moves_since_seen`` hops of the last confirmed sighting.
        """
        self._adj = obs.adjacency
        all_systems = set(obs.adjacency)

        if obs.rival_position is not None:
            self.candidates = {obs.rival_position}
        elif obs.rival_last_seen is not None:
            self.candidates = self._reachable_within(
                obs.rival_last_seen, obs.rival_moves_since_seen) or all_systems
        else:
            self.candidates = all_systems - {obs.position}
        return self.candidates

    def known_position(self) -> Optional[System]:
        """The rival's system if we've narrowed it to exactly one, else None."""
        return next(iter(self.candidates)) if len(self.candidates) == 1 else None

    # -- helpers -------------------------------------------------------------
    def _reachable_within(self, start: System, hops: int) -> Set[System]:
        seen = {start}
        frontier = {start}
        for _ in range(max(hops, 0)):
            nxt: Set[System] = set()
            for c in frontier:
                nxt.update(self._adj.get(c, ()))
            frontier = nxt - seen
            seen |= nxt
        return seen
