"""The Starship Duel simulator (spec 4 & 5).

``Engine`` owns the true :class:`GameState`, applies one action at a time,
drives the start/end-of-turn lifecycle, and maintains the public belief
trackers used to build partial-information observations.

All hidden-information bookkeeping is derived purely from public signals, so an
observation built from ``engine`` never leaks the rival's true position.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional, Set

from .belief import BeliefTracker
from .config import GameConfig
from .maps import GameMap, sample_map, spawn_positions
from .types import (
    Action,
    ActionType,
    Cache,
    CacheKind,
    ENERGY_ACTIONS,
    GameState,
    ShipId,
    ShipState,
    System,
    SystemStatus,
    UNLOCK_ACTIONS,
    other,
)


class IllegalActionError(Exception):
    """Raised when an action violates the rules (bot bug or bad input)."""


class Engine:
    def __init__(self, config: Optional[GameConfig] = None, seed: Optional[int] = None):
        self.config = config or GameConfig()
        self.rng = random.Random(seed)
        self.map: GameMap = None  # type: ignore[assignment]
        self.state: GameState = None  # type: ignore[assignment]
        # belief[o] = observer ``o``'s belief about the rival other(o).
        self.belief: List[BeliefTracker] = []

    # ------------------------------------------------------------------ setup
    def reset(
        self,
        *,
        skirmish_number: int = 1,
        campaign_score: Optional[List[int]] = None,
        map_id: Optional[str] = None,
        first_ship: Optional[ShipId] = None,
    ) -> GameState:
        gmap = self._pick_map(map_id)
        self.map = gmap
        p0, p1 = spawn_positions(gmap, self.rng)

        ships = [ShipState(position=p0), ShipState(position=p1)]
        first = self.rng.randint(0, 1) if first_ship is None else first_ship

        self.state = GameState(
            skirmish_number=skirmish_number,
            campaign_score=list(campaign_score or [0, 0]),
            map_id=gmap.id,
            binary_systems=gmap.binary_systems,
            system_owner={s: None for s in gmap.systems},
            system_status={s: SystemStatus.STABLE for s in gmap.systems},
            system_cache={s: None for s in gmap.systems},
            turn_ship=first,
            turn_number=0,
            turn_clock=self.config.turn_clock_start,
            ships=ships,
        )

        # Per-observer "last confirmed sighting" of the rival: the system it was
        # last seen in, and how many of its turns have passed since.  Surfaced in
        # the observation so a bot can re-run the reachability BFS itself.
        self._last_seen_pos: List[Optional[System]] = [None, None]
        self._rival_turns_unseen: List[int] = [0, 0]

        # Precompute the shrinking-field collapse schedule for this skirmish.
        self._plan_collapse(gmap)

        # Seed each observer's belief.  By default both initial positions are
        # revealed, so belief starts pinned to the rival's exact spawn and
        # re-expands as they move under cloak (spec's "last confirmed position"
        # model); otherwise seed the whole spawn-consistent set.
        self.belief = [BeliefTracker(gmap), BeliefTracker(gmap)]
        for o in (0, 1):
            rival_pos = ships[other(o)].position
            if self.config.reveal_initial_positions:
                self._see(o, rival_pos)  # a hard reveal (pinned) at spawn
            else:
                self.belief[o].reset(self._spawn_consistent_set(ships[o].position))

        self._start_turn(first)
        return self.state

    def _pick_map(self, map_id: Optional[str]) -> GameMap:
        if map_id is None:
            return sample_map(self.rng)
        from .maps import get_map

        return get_map(map_id)

    def _spawn_consistent_set(self, own_pos: System) -> Set[System]:
        candidates = {
            s
            for s in self.map.systems
            if self.map.hop_distance(own_pos, s) >= 2 and s != own_pos
        }
        return candidates or {s for s in self.map.systems if s != own_pos}

    def _plan_collapse(self, gmap: GameMap) -> None:
        """Assign each system the ply it goes SUPERNOVA (and, ``shrink_warning``
        plies earlier, DESTABILIZING).  Systems collapse outside-in toward a
        random surviving "eye", one every ``shrink_interval`` plies.

        Collapsing strictly farthest-from-eye first keeps the surviving field
        **connected** at every step: the survivors are always the full ball of
        systems within some radius r of the eye, plus a partial next ring; the
        ball is connected, and every partial-ring survivor still connects to a
        radius-(r-1) system that also survives.  So a ship can always reach the
        shrinking core (and never gets stranded by disconnection)."""
        cfg = self.config
        self._supernova_turn: Dict[System, int] = {}
        self._destabilize_turn: Dict[System, int] = {}
        self._shrink_center: Optional[System] = None
        if not cfg.shrink_enabled:
            return
        systems = gmap.systems
        center = self.rng.choice(systems)
        self._shrink_center = center
        # Farthest-from-eye collapses first; random tiebreak within a ring so the
        # order (and thus the endgame arena) varies between games.
        order = sorted(systems, key=lambda s: (-gmap.hop_distance(center, s), self.rng.random()))
        for k, sysname in enumerate(order):
            t = cfg.shrink_start_turn + k * cfg.shrink_interval
            self._supernova_turn[sysname] = t
            self._destabilize_turn[sysname] = t - cfg.shrink_warning

    def collapse_in(self, system: System) -> Optional[int]:
        """Plies until ``system`` goes supernova (0 = collapsing now); ``None``
        if it is not scheduled or the shrink is disabled.  This is the public
        early-warning countdown surfaced to players and bots."""
        if not self.config.shrink_enabled or system not in self._supernova_turn:
            return None
        remaining = self._supernova_turn[system] - self.state.turn_number
        return max(remaining, 0)

    def forfeit(self, loser: ShipId, reason: str = "forfeit") -> None:
        """End the skirmish with ``loser`` losing (e.g. its bot process crashed)."""
        if not self.state.done:
            self._win(other(loser), reason, [])

    # -------------------------------------------------------------- accessors
    @property
    def current_ship(self) -> ShipId:
        return self.state.turn_ship

    def is_terminal(self) -> bool:
        return self.state.done

    # ---------------------------------------------------------- legal actions
    def legal_actions(self, ship: Optional[ShipId] = None) -> List[Action]:
        st = self.state
        s = self.current_ship if ship is None else ship
        ship_state = st.ships[s]
        pos = ship_state.position
        cfg = self.config
        owner = st.system_owner
        status = st.system_status

        actions: List[Action] = []

        # JUMP to each neighbour (never into a collapsed/supernova system).
        for dest in self.map.neighbors(pos):
            if status[dest] is SystemStatus.SUPERNOVA:
                continue
            actions.append(Action.jump(dest))

        pos_supernova = status[pos] is SystemStatus.SUPERNOVA

        # HOLD: current system not rival-claimed, and not forced to flee.
        if owner[pos] != other(s) and not pos_supernova:
            actions.append(Action.hold())

        # CLAIM: must not already be ours, not on a collapsing star.
        if owner[pos] != s and not pos_supernova:
            actions.append(Action.claim())

        # FIRE: always available.
        actions.append(Action.fire())

        # Energy operations.
        e = ship_state.energy
        if e >= cfg.cost_scan:
            actions.append(Action.scan())
        if e >= cfg.cost_deep_cloak:
            actions.append(Action.deep_cloak())
        if e >= cfg.cost_overcharge:
            actions.append(Action.overcharge())
        for atype, flag in UNLOCK_ACTIONS.items():
            if not ship_state.unlocked[flag] and e >= self._cost(atype):
                actions.append(Action(atype))

        # END_TURN is always available (enables action banking, spec 5a).
        actions.append(Action.end_turn())
        return actions

    def is_legal(self, action: Action, ship: Optional[ShipId] = None) -> bool:
        return action in self.legal_actions(ship)

    def _cost(self, atype: ActionType) -> int:
        cfg = self.config
        return {
            ActionType.SCAN: cfg.cost_scan,
            ActionType.DEEP_CLOAK: cfg.cost_deep_cloak,
            ActionType.OVERCHARGE: cfg.cost_overcharge,
            ActionType.UNLOCK_PROXIMITY_ALERT: cfg.cost_unlock_proximity_alert,
            ActionType.UNLOCK_LONG_RANGE_SCANNERS: cfg.cost_unlock_long_range_scanners,
            ActionType.UNLOCK_JAMMING: cfg.cost_unlock_jamming,
        }[atype]

    # ------------------------------------------------------------ apply action
    def apply_action(self, action: Action) -> List[str]:
        """Apply one action for the current ship.  Returns public log events.

        Automatically runs end-of-turn (and the next ship's start-of-turn) when
        the ship runs out of actions or plays :attr:`ActionType.END_TURN`.
        """
        st = self.state
        if st.done:
            raise IllegalActionError("skirmish already finished")

        s = st.turn_ship
        if not self.is_legal(action, s):
            raise IllegalActionError(f"illegal action {action} for ship {s}")

        events: List[str] = []

        if action.type is ActionType.END_TURN:
            self._end_turn(s, events)
            return events

        # Dispatch.
        handler = {
            ActionType.JUMP: self._do_jump,
            ActionType.HOLD: self._do_hold,
            ActionType.CLAIM: self._do_claim,
            ActionType.FIRE: self._do_fire,
            ActionType.SCAN: self._do_scan,
            ActionType.DEEP_CLOAK: self._do_deep_cloak,
            ActionType.OVERCHARGE: self._do_overcharge,
            ActionType.UNLOCK_PROXIMITY_ALERT: self._do_unlock,
            ActionType.UNLOCK_LONG_RANGE_SCANNERS: self._do_unlock,
            ActionType.UNLOCK_JAMMING: self._do_unlock,
        }[action.type]
        handler(s, action, events)

        # Public action log (jam-aware) for the rival's observation (spec 3).
        st.ships[s].last_public_action = self._public_category(s, action)

        st.ships[s].actions_remaining -= 1

        if st.done:
            return events

        if st.ships[s].actions_remaining <= 0:
            self._end_turn(s, events)
        return events

    def _public_category(self, s: ShipId, action: Action) -> str:
        if action.type in ENERGY_ACTIONS:
            if self.state.ships[s].unlocked["jamming"]:
                return "spent Energy"
            return action.type.value
        # Jump / Hold / Claim / Fire are always distinguishable.
        return action.type.value

    # ---------------------------------------------------------- action effects
    def _do_jump(self, s: ShipId, action: Action, events: List[str]) -> None:
        st = self.state
        ship = st.ships[s]
        rival_id = other(s)
        rival = st.ships[rival_id]
        dest = action.dest
        ship.position = dest
        events.append(f"ship{s} jumps to {dest}")

        entered_rival_claimed = st.system_owner[dest] == rival_id
        entered_rival_system = dest == rival.position

        if entered_rival_claimed:
            self._expose(s, events, reason="entered rival-claimed system")

        if entered_rival_system:
            if self.config.reveal_both_on_colocation_entry:
                self._expose(s, events, reason="co-location")
                self._expose(rival_id, events, reason="co-location")
            else:
                # Mover trips the defender's alarm; the defender stays hidden
                # unless the mover has Long-Range Scanners (documented choice).
                self._expose(s, events, reason="entered rival's system")
                if ship.unlocked["long_range_scanners"]:
                    self._reveal_rival_to(s, events, "long-range scanners")

        # Belief update for the observer that watches this ship.
        if ship.cloaked:
            self.belief[rival_id].on_rival_jump()
        else:
            self._see(rival_id, ship.position)  # still exposed -> tracked exactly

    def _do_hold(self, s: ShipId, action: Action, events: List[str]) -> None:
        ship = self.state.ships[s]
        was_exposed = not ship.cloaked
        ship.cloaked = True
        events.append(f"ship{s} holds")
        if was_exposed:
            # Seen at this system, now re-cloaks in place: still known here, but
            # free to expand again on the next observed jump.
            self._see(other(s), ship.position)
            self.belief[other(s)].unpin()
        # else: cloaked hold -> candidate set unchanged.

    def _do_claim(self, s: ShipId, action: Action, events: List[str]) -> None:
        st = self.state
        ship = st.ships[s]
        st.system_owner[ship.position] = s
        events.append(f"ship{s} claims {ship.position}")
        self._expose(s, events, reason="claim")

    def _do_fire(self, s: ShipId, action: Action, events: List[str]) -> None:
        st = self.state
        ship = st.ships[s]
        rival_id = other(s)
        rival = st.ships[rival_id]
        if ship.position == rival.position:
            events.append(f"ship{s} FIRES and hits at {ship.position}")
            self._win(s, "fire_hit", events)
        else:
            events.append(f"ship{s} fires and misses")
            if rival.unlocked["proximity_alert"]:
                self._expose(s, events, reason="failed fire vs proximity alert")

    def _do_scan(self, s: ShipId, action: Action, events: List[str]) -> None:
        st = self.state
        ship = st.ships[s]
        rival_id = other(s)
        rival = st.ships[rival_id]
        ship.energy -= self.config.cost_scan
        if not rival.deep_cloak_active:
            self._see(s, rival.position)  # scanner learns exact system
            events.append(f"ship{s} scans: rival at {rival.position}")
        else:
            events.append(f"ship{s} scans: rival deep-cloaked, no reading")

    def _do_deep_cloak(self, s: ShipId, action: Action, events: List[str]) -> None:
        ship = self.state.ships[s]
        ship.energy -= self.config.cost_deep_cloak
        was_exposed = not ship.cloaked
        ship.cloaked = True
        ship.deep_cloak_turns_left = self.config.deep_cloak_duration
        events.append(f"ship{s} engages deep cloak ({ship.deep_cloak_turns_left} turns)")
        if was_exposed:
            self._see(other(s), ship.position)
            self.belief[other(s)].unpin()

    def _do_overcharge(self, s: ShipId, action: Action, events: List[str]) -> None:
        ship = self.state.ships[s]
        ship.energy -= self.config.cost_overcharge
        ship.banked_overcharge += 1
        events.append(f"ship{s} overcharges (+1 banked action)")

    def _do_unlock(self, s: ShipId, action: Action, events: List[str]) -> None:
        ship = self.state.ships[s]
        flag = UNLOCK_ACTIONS[action.type]
        ship.energy -= self._cost(action.type)
        ship.unlocked[flag] = True
        events.append(f"ship{s} unlocks {flag}")

    # --------------------------------------------------------- exposure/reveal
    def _see(self, observer: ShipId, system: System) -> None:
        """Record a hard sighting: ``observer`` now knows the rival is at
        ``system``.  Pins belief and resets the last-seen bookkeeping."""
        self.belief[observer].pin(system)
        self._last_seen_pos[observer] = system
        self._rival_turns_unseen[observer] = 0

    def _expose(self, s: ShipId, events: List[str], reason: str = "") -> None:
        """Reveal ship ``s``'s exact position to its rival (unless deep-cloaked)."""
        ship = self.state.ships[s]
        if ship.deep_cloak_active:
            return  # immune to exposure triggers
        ship.cloaked = False
        self._see(other(s), ship.position)
        if reason:
            events.append(f"ship{s} exposed ({reason}) at {ship.position}")

    def _reveal_rival_to(self, observer: ShipId, events: List[str], reason: str) -> None:
        """``observer`` learns the rival's exact system (Scan / LRS)."""
        rival = self.state.ships[other(observer)]
        if rival.deep_cloak_active:
            return
        self._see(observer, rival.position)
        events.append(f"ship{observer} detects rival at {rival.position} ({reason})")

    # ----------------------------------------------------------- turn lifecycle
    def _start_turn(self, s: ShipId, events: Optional[List[str]] = None) -> None:
        st = self.state
        cfg = self.config
        ship = st.ships[s]
        if events is None:
            events = []

        # The field collapses at the start of the turn; a ship stranded on a
        # system that just went supernova is destroyed (game over).
        self._advance_system_status()
        if self._check_collapse_deaths(events):
            return

        # Income from owned (living) systems -- also the per-turn domination gain.
        income = self._controlled_income(s)
        ship.energy += income

        # Domination victory: banking this turn's income toward the map-control
        # target is a second way to win, rewarding held territory (spec: FIRE is
        # the knockout; domination is the decision on points).
        if cfg.domination_enabled:
            st.domination[s] += income
            if st.domination[s] >= cfg.domination_target:
                self._win(s, "domination", events)
                return

        ship.actions_remaining = cfg.base_actions + ship.banked_overcharge
        ship.banked_overcharge = 0
        # Deep Cloak counts down one of the ship's own turns each start.
        if ship.deep_cloak_turns_left > 0:
            ship.deep_cloak_turns_left -= 1
        st.turn_clock = cfg.turn_clock_start

        # Cache collection: only when *starting* a turn on a cache (spec 5b).
        cache = st.system_cache.get(ship.position)
        if cache is not None:
            self._collect_cache(s, cache)
            st.system_cache[ship.position] = None
            self._expose(s, [], reason="cache collection")

    def _controlled_income(self, s: ShipId) -> int:
        """Income (and domination gain) from the systems ``s`` owns.  A star that
        has gone supernova is gone -- it pays nothing."""
        st = self.state
        cfg = self.config
        total = 0
        for sysname, own in st.system_owner.items():
            if own == s and st.system_status[sysname] is not SystemStatus.SUPERNOVA:
                total += cfg.income_binary if sysname in st.binary_systems else cfg.income_single
        return total

    def _collect_cache(self, s: ShipId, cache: Cache) -> None:
        ship = self.state.ships[s]
        if cache.kind is CacheKind.ENERGY:
            ship.energy += cache.value
        else:  # OVERCHARGE -> +1 banked action for next turn
            ship.banked_overcharge += 1

    def _end_turn(self, s: ShipId, events: List[str]) -> None:
        st = self.state
        cfg = self.config
        ship = st.ships[s]
        rival_id = other(s)
        rival = st.ships[rival_id]

        # Action banking (spec 5a): only the excess beyond the base carries.
        rollover = max(0, ship.actions_remaining - cfg.base_actions)
        ship.banked_overcharge += rollover
        ship.actions_remaining = 0

        # This ship (``s``) just finished a turn; from the rival's viewpoint it is
        # one more of ``s``'s turns since the rival last had it in sight.
        if not self.belief[rival_id].is_pinned:
            self._rival_turns_unseen[rival_id] += 1

        # Optional forced fire on end-of-turn co-location (spec 5): the mover who
        # did not fire is the one fired upon -- unless deep-cloaked.  Disabled by
        # default (a kill requires actively choosing FIRE).
        if (cfg.enable_forced_fire and ship.position == rival.position
                and not ship.deep_cloak_active):
            events.append(f"ship{s} ends co-located; ship{rival_id} force-fires")
            self._win(rival_id, "forced_fire", events)
            return

        st.turn_number += 1
        if st.turn_number >= cfg.turn_cap:
            self._resolve_timeout(events)
            return

        st.turn_ship = rival_id
        self._start_turn(rival_id, events)

    def _win(self, winner: ShipId, reason: str, events: List[str]) -> None:
        st = self.state
        st.done = True
        st.winner = winner
        st.end_reason = reason
        st.campaign_score[winner] += 1
        events.append(f"ship{winner} wins skirmish ({reason})")

    def _resolve_timeout(self, events: List[str]) -> None:
        st = self.state
        cfg = self.config
        st.done = True
        st.end_reason = "timeout"
        # With domination on, a timeout is decided on the map-control race first.
        if cfg.domination_enabled and st.domination[0] != st.domination[1]:
            st.winner = 0 if st.domination[0] > st.domination[1] else 1
            events.append(f"skirmish timeout (winner={st.winner})")
            return
        if self.config.timeout_resolution == "systems":
            counts = [0, 0]
            for own in st.system_owner.values():
                if own is not None:
                    counts[own] += 1
            if counts[0] != counts[1]:
                st.winner = 0 if counts[0] > counts[1] else 1
            elif st.ships[0].energy != st.ships[1].energy:
                st.winner = 0 if st.ships[0].energy > st.ships[1].energy else 1
            else:
                st.winner = None  # draw
        else:
            st.winner = None  # draw
        events.append(f"skirmish timeout (winner={st.winner})")

    # ------------------------------------------------------- world dynamics
    def _advance_system_status(self) -> None:
        """The collapse (deterministic schedule), cache escalation, and spawns.

        Rather than filling every empty system (spec 5b's naive pseudocode),
        we keep a small contested pool: existing caches escalate in value, and
        at most ``max_active_caches`` exist at once, replenished on a cadence.
        """
        st = self.state
        cfg = self.config

        # 1) Shrinking field: set each system's status from the fixed schedule
        #    (a collapsing star clears any cache on it).
        if cfg.shrink_enabled:
            turn = st.turn_number
            for sysname in self.map.systems:
                if turn >= self._supernova_turn[sysname]:
                    st.system_status[sysname] = SystemStatus.SUPERNOVA
                    st.system_cache[sysname] = None
                elif turn >= self._destabilize_turn[sysname]:
                    st.system_status[sysname] = SystemStatus.DESTABILIZING

        occupied = {st.ships[0].position, st.ships[1].position}

        # 2) Escalate existing (unoccupied) caches.
        for sysname in self.map.systems:
            if sysname in occupied:
                continue  # placement restriction: no upgrade under a ship
            cache = st.system_cache.get(sysname)
            if cache is not None and st.turn_number >= cache.next_upgrade_turn:
                self._upgrade_cache(sysname, cache)

        # 3) Spawn a new cache on cadence, capped, at a random eligible system.
        self._maybe_spawn_cache(occupied)

    def _maybe_spawn_cache(self, occupied: set) -> None:
        st = self.state
        cfg = self.config
        if cfg.cache_spawn_period <= 0 or cfg.max_active_caches <= 0:
            return
        if st.turn_number % cfg.cache_spawn_period != 0:
            return
        active = sum(1 for c in st.system_cache.values() if c is not None)
        if active >= cfg.max_active_caches:
            return

        eligible = [
            s for s in self.map.systems
            if s not in occupied
            and st.system_cache.get(s) is None
            and st.system_status[s] is SystemStatus.STABLE
            and (cfg.cache_spawn_in_binaries or s not in st.binary_systems)
        ]
        if eligible:
            target = self.rng.choice(eligible)
            st.system_cache[target] = self._spawn_cache(target)

    def _spawn_cache(self, sysname: System) -> Cache:
        cfg = self.config
        base = cfg.cache_base_binary if sysname in self.state.binary_systems else cfg.cache_base_single
        return Cache(
            kind=CacheKind.ENERGY,
            value=base,
            next_upgrade_turn=self.state.turn_number + cfg.cache_upgrade_period,
        )

    def _upgrade_cache(self, sysname: System, cache: Cache) -> None:
        cfg = self.config
        if cache.kind is CacheKind.ENERGY and self.rng.random() < cfg.cache_overcharge_transform_prob:
            cache.kind = CacheKind.OVERCHARGE
            cache.value = 0
        elif cache.kind is CacheKind.ENERGY:
            cache.value += cfg.cache_upgrade_energy_step
        # OVERCHARGE caches are terminal (no further upgrade).
        cache.next_upgrade_turn = self.state.turn_number + cfg.cache_upgrade_period

    def _check_collapse_deaths(self, events: List[str]) -> bool:
        """A ship standing on a system that has gone SUPERNOVA is destroyed.
        Returns True (and ends the skirmish) if anyone died."""
        st = self.state
        dead = [i for i in (0, 1)
                if st.system_status[st.ships[i].position] is SystemStatus.SUPERNOVA]
        if not dead:
            return False
        if len(dead) == 2:
            st.done = True
            st.winner = None
            st.end_reason = "collapse"
            events.append("both ships caught in the collapse — draw")
        else:
            loser = dead[0]
            self._win(other(loser), "supernova", events)
        return True
