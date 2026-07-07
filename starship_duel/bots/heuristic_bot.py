"""A small hand-written strategy -- a readable template for custom bots.

Priorities, highest first:
  1. If the rival is (near-)certainly on our system, FIRE.
  2. If the rival's exact system is known and adjacent, pounce (jump on, so we
     can fire it this turn).
  3. Survival: if our star is about to go supernova, evacuate toward the core.
  4. Take a good speculative shot when the rival is cornered to a couple of
     systems and one of them is ours.
  5. Re-cloak if exposed; grab a binary; buy a key unlock; Scan to relocate.
  6. Hunt: close in on the rival's suspected systems (or drift to a binary),
     always keeping off soon-to-collapse stars.

None of this is tuned for strength -- it's here to exercise every mechanic and
show how to read an :class:`Observation`, including the shrinking field.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, List, Optional

from ..game import Action, ActionType, Observation
from .base import Bot
from .belief import BotBelief

# A star this many plies (or fewer) from going supernova will detonate before
# our next turn -- we must not be standing on it when our turn ends.
_DOOM_HORIZON = 2
# Fire speculatively when the rival is pinned to at most this many systems and
# one of them is ours (>=1/N chance to hit, and a miss is cheap).
_SPECULATIVE_FIRE_MAX = 2
# Actively converge on the rival once we've narrowed them to a handful of stars.
_HUNT_MAX_CANDIDATES = 5


def _next_hop(adjacency: Dict[str, List[str]], src: str, targets, blocked=()) -> Optional[str]:
    """First step of a shortest path from ``src`` to the nearest target,
    routing around ``blocked`` systems where possible."""
    targets = set(targets)
    blocked = set(blocked)
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
        candidates = self.belief.observe(obs)
        known_rival = obs.rival_position

        def find(atype: ActionType) -> Optional[Action]:
            return next((a for a in legal if a.type is atype), None)

        def jump_dests() -> List[str]:
            return [a.dest for a in legal if a.type is ActionType.JUMP]

        # -- shrinking-field awareness ---------------------------------------
        def collapse_in(sys: str) -> float:
            c = obs.system_collapse_in.get(sys)
            return float("inf") if c is None else float(c)

        def doomed(sys: str) -> bool:
            return collapse_in(sys) <= _DOOM_HORIZON

        # 1. The rival is (near-)certainly right here -> shoot.
        if ActionType.FIRE in types and (
            known_rival == obs.position
            or candidates == {obs.position}
        ):
            return Action.fire()

        # 2. Rival pinned to one adjacent system and we have the actions to jump
        #    on AND fire this turn -> pounce (priority 1 fires on the next
        #    action).  Never step onto them without the follow-up shot: entering
        #    their system exposes us and hands them the kill.
        pin = known_rival if known_rival is not None else (
            next(iter(candidates)) if len(candidates) == 1 else None)
        if pin is not None and obs.actions_remaining >= 2:
            pounce = next((a for a in legal
                           if a.type is ActionType.JUMP and a.dest == pin), None)
            if pounce is not None:
                return pounce

        # 3. Survival first: our star is about to detonate -> flee to the
        #    safest reachable neighbour (longest-lived, avoiding suspected
        #    ambush systems when we have a choice).
        if doomed(obs.position) and jump_dests():
            return Action.jump(self._safest_hop(obs, jump_dests(), candidates, collapse_in))

        # 4. Good speculative shot: rival cornered to a couple of stars and one
        #    is ours. Worth an action even on a miss -- this is what turns the
        #    endgame into a shoot-out instead of a stalemate.
        if (ActionType.FIRE in types and obs.position in candidates
                and 1 <= len(candidates) <= _SPECULATIVE_FIRE_MAX):
            return Action.fire()

        # 5a. Exposed with nothing lethal to do -> slip back under cloak.
        if not obs.cloaked and ActionType.HOLD in types and not doomed(obs.position):
            return Action.hold()

        # 5b. Economy is how you win on points now: claim the star you're on to
        #     build domination -- any unowned system, plus stealing a rival's
        #     binary (denies their income and grabs the richest tiles).
        immune = obs.deep_cloak_turns_left > 0
        rival_adjacent = known_rival is not None and known_rival in obs.adjacency[obs.position]
        here_owner = obs.system_owner[obs.position]  # CLAIM is legal => owner != us
        worth_claiming = (ActionType.CLAIM in types and not doomed(obs.position)
                          and (here_owner is None or obs.position in obs.binary_systems))
        if worth_claiming:
            # Claiming exposes us, so do it when no known rival is adjacent -- or
            # when we're deep-cloaked and therefore immune to that exposure.
            if not rival_adjacent or immune:
                return Action.claim()
            # Contested but valuable: spend Energy on Deep Cloak so we can take it
            # without handing the adjacent rival our position (claim next action).
            if (obs.position in obs.binary_systems and obs.actions_remaining >= 2
                    and ActionType.DEEP_CLOAK in types):
                return Action.deep_cloak()

        # 5c. Invest surplus Energy in abilities. An ability is in the legal set
        #     only when we can afford it, so we let affordability gate the buy
        #     (no hard-coded prices).  Long-Range Scanners first -- it turns any
        #     jump-onto-rival into a confirmed kill -- then Overcharge to snowball
        #     claims once we already hold ground and face no adjacent threat.
        lrs = find(ActionType.UNLOCK_LONG_RANGE_SCANNERS)
        if lrs is not None:
            return lrs
        if (ActionType.OVERCHARGE in types and not rival_adjacent
                and obs.banked_overcharge == 0
                and any(o == obs.ship_id for o in obs.system_owner.values())):
            return Action.overcharge()

        # 5d. Lost the rival? Scan to pin them down and set up a pounce.
        if known_rival is None:
            scan = find(ActionType.SCAN)
            if scan is not None:
                return scan

        # 6. Movement. Stay off soon-to-collapse stars and out of suspected
        #    ambush systems (entering the rival's star exposes us); within that,
        #    close in on the rival if we have a fix, else drift toward a binary.
        dests = jump_dests()
        if not dests:
            if ActionType.HOLD in types and not doomed(obs.position):
                return Action.hold()
            return Action.end_turn()

        ambush = candidates  # systems the (cloaked) rival might be sitting in
        # Prefer hops that are both safe (not collapsing) and not an ambush; fall
        # back to merely-safe, then anything, so we always keep moving off a
        # doomed star.
        staging = [d for d in dests if not doomed(d) and d not in ambush]
        pool = staging or [d for d in dests if not doomed(d)] or dests
        hunting = 1 <= len(candidates) <= _HUNT_MAX_CANDIDATES

        if hunting:
            # Approach the rival's suspected region but stage *next to* it -- get
            # adjacent with actions to spare so a Scan-and-pounce (or the
            # collapse) can finish them, instead of walking into the ambush.
            toward = min(
                pool,
                key=lambda d: min(self._hops(obs.adjacency, d, c) for c in candidates),
            )
            return Action.jump(toward)

        # No fix on the rival: roam to grab territory. Head for the nearest
        # claimable star -- unowned or a rival binary worth stealing -- favouring
        # binaries (4x income), along safe, non-ambush stars.
        mine = obs.ship_id
        claim_bins = [b for b in obs.binary_systems if obs.system_owner.get(b) != mine]
        claim_any = [s for s in obs.adjacency if obs.system_owner.get(s) != mine]
        drift_targets = claim_bins or claim_any or list(obs.binary_systems)
        blocked = [d for d in dests if doomed(d) or d in ambush]
        ideal = _next_hop(obs.adjacency, obs.position, drift_targets, blocked=blocked)
        if ideal in pool:
            return Action.jump(ideal)
        # Else a safe, longest-lived hop; keep it unpredictable.
        best_life = max(collapse_in(d) for d in pool)
        pick = [d for d in pool if collapse_in(d) == best_life]
        return Action.jump(self.rng.choice(pick))

    # -- helpers -------------------------------------------------------------
    def _safest_hop(self, obs, dests, danger, collapse_in) -> str:
        """Evacuation target: longest-lived star, avoiding suspected ambush
        systems when there's an equally safe alternative."""
        best_life = max(collapse_in(d) for d in dests)
        safest = [d for d in dests if collapse_in(d) == best_life]
        clear = [d for d in safest if d not in danger]
        return self.rng.choice(clear or safest)

    @staticmethod
    def _hops(adjacency: Dict[str, List[str]], a: str, b: str) -> int:
        if a == b:
            return 0
        seen = {a}
        frontier = deque([(a, 0)])
        while frontier:
            node, d = frontier.popleft()
            for nxt in adjacency[node]:
                if nxt == b:
                    return d + 1
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append((nxt, d + 1))
        return 10**9
