"""HunterBot -- an aggressive archetype that wins by the *kill*, not by points.

With the lives/hunt rules a landed FIRE strips a rival life and scatters it
hidden, so hunting is: locate -> close -> hit -> re-locate.  This bot leans into
that loop -- it buys Long-Range Scanners early (ranged raid + passive tracking),
Scans for free whenever it has lost the rival, pounces the instant the rival is
pinned, and only claims territory to fund the chase.  It's a foil to a
territorial/domination bot and a useful tournament baseline.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional

from ..game import Action, ActionType, Observation
from .base import Bot
from .belief import BotBelief

_DOOM_HORIZON = 2
_SPECULATIVE_FIRE_MAX = 3   # hunter is happy to gamble a cheap miss to force a hit


def _next_hop(adjacency: Dict[str, List[str]], src: str, targets, blocked=()) -> Optional[str]:
    targets, blocked = set(targets), set(blocked)
    if not targets or src in targets:
        return None
    frontier = deque((n, n) for n in adjacency[src] if n not in blocked)
    seen = {src, *adjacency[src]}
    while frontier:
        node, first = frontier.popleft()
        if node in targets:
            return first
        for nxt in adjacency[node]:
            if nxt not in seen and nxt not in blocked:
                seen.add(nxt)
                frontier.append((nxt, first))
    # nothing reachable avoiding blocked -> retry ignoring blocked
    if blocked:
        return _next_hop(adjacency, src, targets)
    return None


class HunterBot(Bot):
    name = "hunter"

    def __init__(self, name=None, seed=None):
        super().__init__(name=name, seed=seed)
        self.belief = BotBelief()

    def reset(self) -> None:
        self.belief.reset()

    def act(self, obs: Observation) -> Action:
        legal = obs.legal_actions
        types = {a.type for a in legal}
        candidates = self.belief.observe(obs)
        known = obs.rival_position

        def find(atype: ActionType) -> Optional[Action]:
            return next((a for a in legal if a.type is atype), None)

        def collapse_in(sys: str) -> float:
            c = obs.system_collapse_in.get(sys)
            return float("inf") if c is None else float(c)

        doomed = lambda sys: collapse_in(sys) <= _DOOM_HORIZON
        jumps = [a.dest for a in legal if a.type is ActionType.JUMP]

        # 1. Rival in the crosshairs -> fire (co-located, or adjacent with LRS).
        if ActionType.FIRE in types:
            adj = obs.adjacency[obs.position]
            if known == obs.position or candidates == {obs.position}:
                return Action.fire()
            if obs.unlocked["long_range_scanners"] and known is not None and known in adj:
                return Action.fire()   # ranged raid

        # 2. Pounce: rival pinned to one system we can reach -- jump on to shoot.
        pin = known if known is not None else (
            next(iter(candidates)) if len(candidates) == 1 else None)
        if pin is not None and obs.actions_remaining >= 2:
            if pin in jumps:
                return Action.jump(pin)

        # 3. Don't die to the collapse.
        if doomed(obs.position) and jumps:
            safe = [d for d in jumps if not doomed(d)] or jumps
            safe.sort(key=lambda d: -collapse_in(d))
            return Action.jump(safe[0])

        # 4. Speculative shot when the rival is nearly cornered onto us.
        if (ActionType.FIRE in types and obs.position in candidates
                and 1 <= len(candidates) <= _SPECULATIVE_FIRE_MAX):
            return Action.fire()

        # 5. Buy the hunt kit: Long-Range Scanners first (ranged raid + tracking).
        lrs = find(ActionType.UNLOCK_LONG_RANGE_SCANNERS)
        if lrs is not None:
            return lrs

        # 6. Lost the rival (e.g. it just respawned)?  Scan is free -> relocate.
        if known is None or len(candidates) > _SPECULATIVE_FIRE_MAX:
            scan = find(ActionType.SCAN)
            if scan is not None:
                return scan

        # 7. Fund the chase: grab the star we're on if it's free and safe.
        if (ActionType.CLAIM in types and not doomed(obs.position)
                and obs.system_owner[obs.position] is None):
            return Action.claim()

        # 8. Close the distance toward the rival's most likely systems.
        if jumps:
            targets = {known} if known is not None else candidates
            blocked = {d for d in jumps if doomed(d)}
            hop = _next_hop(obs.adjacency, obs.position, targets or set(jumps), blocked)
            if hop in jumps:
                return Action.jump(hop)
            live = [d for d in jumps if not doomed(d)] or jumps
            return Action.jump(self.rng.choice(live))

        # 9. Nothing better -- re-cloak or pass.
        if ActionType.HOLD in types:
            return Action.hold()
        return Action.end_turn()
