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
from .belief import BotBelief


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

    def __init__(self, name=None, seed=None):
        super().__init__(name=name, seed=seed)
        self.belief = BotBelief()

    def reset(self) -> None:
        self.belief.reset()

    def act(self, obs: Observation) -> Action:
        legal = obs.legal_actions
        types = {a.type for a in legal}
        # We're only handed the rival's exact system when it's certain; for
        # anything fuzzier we track our own belief.
        self.belief.observe(obs)
        known_rival = obs.rival_position

        def find(atype: ActionType) -> Optional[Action]:
            for a in legal:
                if a.type is atype:
                    return a
            return None

        # 1. Confirmed co-location -> fire.
        if known_rival == obs.position and ActionType.FIRE in types:
            return Action.fire()

        # 2. Rival known and adjacent -> jump on, but only if we can also fire.
        if known_rival is not None and obs.actions_remaining >= 2:
            jump = next((a for a in legal if a.type is ActionType.JUMP and a.dest == known_rival), None)
            if jump is not None:
                return jump

        # 3. Exposed with nothing better to do -> re-cloak.
        if not obs.cloaked and ActionType.HOLD in types:
            return Action.hold()

        # Claiming exposes us, so only grab high-value binaries, and never while
        # a *known* rival is adjacent (they'd shoot us next turn).
        rival_adjacent = known_rival is not None and known_rival in obs.adjacency[obs.position]
        if ActionType.CLAIM in types and not rival_adjacent:
            owner = obs.system_owner[obs.position]
            if owner is None and obs.position in obs.binary_systems:
                return Action.claim()

        # Buy one high-value unlock when comfortably rich.
        if obs.energy >= 40 and not obs.unlocked["long_range_scanners"]:
            u = find(ActionType.UNLOCK_LONG_RANGE_SCANNERS)
            if u is not None:
                return u

        # Rival unknown but we can afford it -> Scan to pin them down.
        if known_rival is None:
            scan = find(ActionType.SCAN)
            if scan is not None:
                return scan

        # Movement. Jumping into a system that hides the cloaked rival exposes
        # us there and gets us shot next turn, so we avoid *suspected* enemy
        # systems, and we stay unpredictable (a deterministic path is easy prey).
        danger = self.belief.candidates
        neighbors = [d for d in obs.adjacency[obs.position]
                     if any(a.type is ActionType.JUMP and a.dest == d for a in legal)]
        safe = [n for n in neighbors if n not in danger]
        bin_targets = [b for b in obs.binary_systems if obs.system_owner.get(b) != obs.ship_id]

        ideal = _next_hop(obs.adjacency, obs.position, bin_targets or obs.binary_systems)
        dest = None
        if ideal in safe:
            dest = ideal                       # safe progress toward a binary
        elif safe:
            dest = self.rng.choice(safe)       # unpredictable, ambush-avoiding hop
        if dest is not None:
            return Action.jump(dest)

        # No safe move: sit tight, cloaked, collecting income.
        if ActionType.HOLD in types:
            return Action.hold()
        return Action.end_turn()
