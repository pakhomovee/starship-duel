"""Partial-information observation for one ship (spec 3).

Everything here is public or self-private for the observing ship; the rival's
exact position, energy, cloak and banked overcharge are never included.  The
rival's "could be here" set is exposed as :attr:`Observation.candidate_systems`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .engine import Engine
from .types import Action, ShipId, System, other


@dataclass
class Observation:
    # -- identity ------------------------------------------------------------
    ship_id: ShipId
    map_id: str

    # -- fully public --------------------------------------------------------
    adjacency: Dict[System, List[System]]
    binary_systems: List[System]
    system_owner: Dict[System, Optional[ShipId]]
    system_status: Dict[System, str]
    system_cache: Dict[System, Optional[dict]]  # {"kind":..., "value":...} or None
    campaign_score: List[int]
    skirmish_number: int
    turn_ship: ShipId
    turn_number: int
    turn_clock: float
    rival_unlocked: Dict[str, bool]
    rival_last_action: Optional[str]

    # -- self-private --------------------------------------------------------
    position: System
    cloaked: bool
    energy: int
    banked_overcharge: int
    actions_remaining: int
    unlocked: Dict[str, bool]

    # -- inferred hidden-info layer -----------------------------------------
    candidate_systems: List[System]

    # -- convenience for policies / bots ------------------------------------
    legal_actions: List[Action] = field(default_factory=list)

    @property
    def is_my_turn(self) -> bool:
        return self.turn_ship == self.ship_id

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["legal_actions"] = [str(a) for a in self.legal_actions]
        return d


def build_observation(engine: Engine, ship: ShipId) -> Observation:
    st = engine.state
    me = st.ships[ship]
    rival_id = other(ship)
    rival = st.ships[rival_id]

    # Candidate set: authoritative when the rival is currently exposed,
    # otherwise the belief set, pruned by public constraints (spec 3a).
    if not rival.cloaked:
        candidates: Set[System] = {rival.position}
    else:
        bt = engine.belief[ship]
        candidates = bt.candidates
        if engine.config.belief_prune_owned:
            owned_by_me = {s for s, o in st.system_owner.items() if o == ship}
            candidates = candidates - owned_by_me or candidates

    cache_view: Dict[System, Optional[dict]] = {}
    for s, c in st.system_cache.items():
        cache_view[s] = None if c is None else {"kind": c.kind.value, "value": c.value}

    return Observation(
        ship_id=ship,
        map_id=st.map_id,
        adjacency={s: list(n) for s, n in engine.map.adjacency.items()},
        binary_systems=sorted(st.binary_systems),
        system_owner=dict(st.system_owner),
        system_status={s: v.value for s, v in st.system_status.items()},
        system_cache=cache_view,
        campaign_score=list(st.campaign_score),
        skirmish_number=st.skirmish_number,
        turn_ship=st.turn_ship,
        turn_number=st.turn_number,
        turn_clock=st.turn_clock,
        rival_unlocked=dict(rival.unlocked),
        rival_last_action=rival.last_public_action,
        position=me.position,
        cloaked=me.cloaked,
        energy=me.energy,
        banked_overcharge=me.banked_overcharge,
        actions_remaining=me.actions_remaining,
        unlocked=dict(me.unlocked),
        candidate_systems=sorted(candidates),
        legal_actions=engine.legal_actions(ship) if st.turn_ship == ship else [],
    )
