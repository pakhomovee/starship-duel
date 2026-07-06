"""Partial-information observation for one ship (spec 3).

Everything here is public or self-private for the observing ship; the rival's
energy, banked overcharge, and hidden position are never included.  The rival's
exact system is given (as :attr:`Observation.rival_position`) only when it is
known for certain; the fuzzy "could be here" set is intentionally omitted so
bots infer it themselves.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

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
    # Early-warning countdown: plies until each system goes supernova (0 =
    # collapsing now, None = not scheduled).  A system also shows DESTABILIZING
    # in ``system_status`` for the final ``shrink_warning`` plies before it goes.
    system_collapse_in: Dict[System, Optional[int]]
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

    # -- hidden-info layer --------------------------------------------------
    # The rival's EXACT system when it is currently known for certain (they are
    # exposed, or a Scan/reveal has pinned them and they haven't moved since),
    # else ``None``.  The fuzzy "could be here" set is deliberately NOT provided
    # -- a bot must infer the rival's possible whereabouts itself (see
    # ``starship_duel.bots.belief.BotBelief`` for a ready-made helper).
    rival_position: Optional[System]

    # Enough to reconstruct the reachability BFS yourself: the last system the
    # rival was confirmed in (``None`` if never), and an upper bound on how many
    # single-hop moves it could have made since (0 while currently known).  The
    # candidate set is roughly ``systems within rival_moves_since_seen hops of
    # rival_last_seen``.
    rival_last_seen: Optional[System]
    rival_moves_since_seen: int

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

    # Hard reveal only: the exact rival system when it is known for certain
    # (exposed, or pinned by a Scan/reveal that is still valid), else None.
    # The engine's belief tracker is kept for the UI/spectator overlay, but is
    # not surfaced to the observer -- bots infer their own fuzzy belief.
    if not rival.cloaked:
        rival_position: Optional[System] = rival.position
    else:
        bt = engine.belief[ship]
        cands = bt.candidates
        rival_position = next(iter(cands)) if (bt.is_pinned and len(cands) == 1) else None

    # Materials for the observer's own reachability BFS.
    last_seen = engine._last_seen_pos[ship]
    if rival_position is not None:
        last_seen = rival_position
        moves_since_seen = 0
    else:
        moves_since_seen = engine._rival_turns_unseen[ship] * engine.config.base_actions

    cache_view: Dict[System, Optional[dict]] = {}
    for s, c in st.system_cache.items():
        cache_view[s] = None if c is None else {"kind": c.kind.value, "value": c.value}

    collapse_in = {s: engine.collapse_in(s) for s in st.system_status}

    return Observation(
        ship_id=ship,
        map_id=st.map_id,
        adjacency={s: list(n) for s, n in engine.map.adjacency.items()},
        binary_systems=sorted(st.binary_systems),
        system_owner=dict(st.system_owner),
        system_status={s: v.value for s, v in st.system_status.items()},
        system_cache=cache_view,
        system_collapse_in=collapse_in,
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
        rival_position=rival_position,
        rival_last_seen=last_seen,
        rival_moves_since_seen=moves_since_seen,
        legal_actions=engine.legal_actions(ship) if st.turn_ship == ship else [],
    )
