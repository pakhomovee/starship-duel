"""A small hand-written strategy -- a readable template for custom bots.

Priorities, highest first:
  1. If we can confirm the rival is right here, FIRE (instant win).
  2. If the rival's exact system is known and adjacent, and we have enough
     actions to jump-and-fire this turn, pounce.  (Jumping onto the rival and
     *not* firing loses to the end-of-turn forced fire, spec 5.)
  3. If we've been exposed, HOLD to slip back under cloak.
  4. Economy: claim valuable systems, buy a key unlock when rich, Scan to
     locate a lost rival.
  5. Otherwise drift toward the nearest binary system and bank leftover actions.

None of this is tuned for strength -- it's here to exercise every mechanic and
show how to read an :class:`Observation`.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional

from ..game import Action, ActionType, Observation
from .base import Bot


def _next_hop(adjacency: Dict[str, List[str]], src: str, targets) -> Optional[str]:
    """First step of a shortest path from ``src`` to the nearest target."""
    targets = set(targets)
    if not targets or src in targets:
        return None
    # BFS remembering the first move taken out of ``src``.
    frontier = deque((n, n) for n in adjacency[src])
    seen = {src, *adjacency[src]}
    while frontier:
        node, first = frontier.popleft()
        if node in targets:
            return first
        for nxt in adjacency[node]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append((nxt, first))
    return None


class HeuristicBot(Bot):
    name = "heuristic"

    def act(self, obs: Observation) -> Action:
        legal = obs.legal_actions
        types = {a.type for a in legal}
        known_rival = obs.candidate_systems[0] if len(obs.candidate_systems) == 1 else None

        def find(atype: ActionType) -> Optional[Action]:
            for a in legal:
                if a.type is atype:
                    return a
            return None

        # 1. Confirmed co-location -> fire.
        if known_rival == obs.position and ActionType.FIRE in types:
            return Action.fire()

        # 2. Rival pinned and adjacent -> jump on, but only if we can also fire.
        if known_rival is not None and obs.actions_remaining >= 2:
            jump = next((a for a in legal if a.type is ActionType.JUMP and a.dest == known_rival), None)
            if jump is not None:
                return jump

        # 3. Exposed with nothing better to do -> re-cloak.
        if not obs.cloaked and ActionType.HOLD in types:
            return Action.hold()

        # 4a. Claim an unclaimed system we're standing on (prefer binaries),
        #     but not while a known rival sits adjacent (claiming exposes us).
        rival_adjacent = known_rival is not None and known_rival in obs.adjacency[obs.position]
        if ActionType.CLAIM in types and not rival_adjacent:
            owner = obs.system_owner[obs.position]
            valuable = obs.position in obs.binary_systems
            if owner is None and (valuable or obs.energy < 4):
                return Action.claim()

        # 4b. Buy one high-value unlock when comfortably rich.
        if obs.energy >= 40 and not obs.unlocked["long_range_scanners"]:
            u = find(ActionType.UNLOCK_LONG_RANGE_SCANNERS)
            if u is not None:
                return u

        # 4c. Lost the rival -> Scan to relocate them.
        if known_rival is None and len(obs.candidate_systems) > 3:
            scan = find(ActionType.SCAN)
            if scan is not None:
                return scan

        # 5. Drift toward the rival if known, else toward the nearest binary.
        targets = [known_rival] if known_rival else [
            b for b in obs.binary_systems if obs.system_owner.get(b) != obs.ship_id
        ]
        hop = _next_hop(obs.adjacency, obs.position, targets or obs.binary_systems)
        if hop is not None:
            jump = next((a for a in legal if a.type is ActionType.JUMP and a.dest == hop), None)
            if jump is not None:
                return jump

        # Nothing worthwhile: bank the remaining actions.
        return Action.end_turn()
