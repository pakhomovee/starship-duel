"""Serialize a :class:`GameSession` to a JSON-friendly view for the frontend.

Two perspectives:
  * a **player** view (``perspective`` = ship id) uses that ship's partial-info
    :class:`Observation` -- the rival is only shown when legitimately revealed,
    otherwise it appears as a candidate-system overlay.
  * the **truth** view (bot-vs-bot spectating) shows both ships exactly, plus
    each side's belief set for debugging the hidden-info layer.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Union

from ..game import ActionType, build_observation
from ..game.observation import Observation
from .session import GameSession

# Costs mirrored for UI labels (engine is source of truth for enforcement).
_COST_ATTR = {
    ActionType.SCAN: "cost_scan",
    ActionType.DEEP_CLOAK: "cost_deep_cloak",
    ActionType.OVERCHARGE: "cost_overcharge",
    ActionType.UNLOCK_PROXIMITY_ALERT: "cost_unlock_proximity_alert",
    ActionType.UNLOCK_LONG_RANGE_SCANNERS: "cost_unlock_long_range_scanners",
    ActionType.UNLOCK_JAMMING: "cost_unlock_jamming",
}

_LABELS = {
    ActionType.JUMP: "Jump",
    ActionType.HOLD: "Hold",
    ActionType.CLAIM: "Claim",
    ActionType.FIRE: "Fire",
    ActionType.SCAN: "Scan",
    ActionType.DEEP_CLOAK: "Deep Cloak",
    ActionType.OVERCHARGE: "Overcharge",
    ActionType.UNLOCK_PROXIMITY_ALERT: "Unlock: Proximity Alert",
    ActionType.UNLOCK_LONG_RANGE_SCANNERS: "Unlock: Long-Range Scanners",
    ActionType.UNLOCK_JAMMING: "Unlock: Jamming",
    ActionType.END_TURN: "End Turn",
}


def _fallback_layout(systems: List[str]) -> Dict[str, tuple]:
    n = len(systems)
    cx, cy, r = 500.0, 360.0, 300.0
    return {
        s: (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
        for i, s in enumerate(systems)
    }


def _action_cost(session: GameSession, atype: ActionType) -> Optional[int]:
    attr = _COST_ATTR.get(atype)
    return getattr(session.config, attr) if attr else None


def serialize(session: GameSession, perspective: Union[str, int] = "truth") -> dict:
    eng = session.env.engine
    st = eng.state
    gmap = eng.map
    layout = gmap.layout or _fallback_layout(sorted(gmap.systems))

    systems = []
    for name in gmap.systems:
        x, y = layout[name]
        cache = st.system_cache.get(name)
        systems.append({
            "name": name,
            "x": x, "y": y,
            "binary": name in st.binary_systems,
            "degree": gmap.degree(name),
            "owner": st.system_owner.get(name),
            "status": st.system_status[name].value,
            "cache": None if cache is None else {"kind": cache.kind.value, "value": cache.value},
        })

    edges = []
    seen = set()
    for a, nbrs in gmap.adjacency.items():
        for b in nbrs:
            key = tuple(sorted((a, b)))
            if key not in seen:
                seen.add(key)
                edges.append(list(key))

    view: dict = {
        "game_id": session.id,
        "mode": session.mode,
        "controllers": {str(k): v for k, v in session.controllers.items()},
        "map_id": st.map_id,
        "perspective": perspective,
        "done": st.done,
        "winner": st.winner,
        "end_reason": st.end_reason,
        "turn_ship": st.turn_ship,
        "turn_number": st.turn_number,
        "campaign_score": list(st.campaign_score),
        "systems": systems,
        "edges": edges,
        "events": session.recent_events(),
        "awaiting_human": session.is_human_turn(),
        "can_step": (not st.done) and session.controllers[st.turn_ship] != "human",
    }

    if perspective == "truth":
        view["ships"] = [
            {"id": s, "position": st.ships[s].position, "cloaked": st.ships[s].cloaked,
             "known": True}
            for s in (0, 1)
        ]
        # Belief overlays: what each observer thinks about the other.
        view["candidates"] = {
            "0": sorted(build_observation(eng, 0).candidate_systems),
            "1": sorted(build_observation(eng, 1).candidate_systems),
        }
        view["hud"] = [_ship_hud(st, s) for s in (0, 1)]
        view["legal_actions"] = []
    else:
        me = int(perspective)
        obs = build_observation(eng, me)
        rival = eng.state.ships[1 - me]
        rival_known = not rival.cloaked
        view["self_id"] = me
        view["ships"] = [
            {"id": me, "position": obs.position, "cloaked": obs.cloaked, "known": True, "is_self": True},
            {"id": 1 - me,
             "position": rival.position if rival_known else None,
             "cloaked": rival.cloaked, "known": rival_known, "is_self": False},
        ]
        view["candidates"] = {str(1 - me): sorted(obs.candidate_systems)}
        view["hud"] = [_obs_hud(obs, me)]
        view["rival_unlocked"] = obs.rival_unlocked
        view["rival_last_action"] = obs.rival_last_action
        view["legal_actions"] = (
            _legal_actions(session, obs) if view["awaiting_human"] else []
        )
    return view


def _ship_hud(st, s: int) -> dict:
    ship = st.ships[s]
    return {
        "id": s,
        "position": ship.position,
        "cloaked": ship.cloaked,
        "energy": ship.energy,
        "banked_overcharge": ship.banked_overcharge,
        "actions_remaining": ship.actions_remaining if st.turn_ship == s else 0,
        "unlocked": dict(ship.unlocked),
    }


def _obs_hud(obs: Observation, s: int) -> dict:
    return {
        "id": s,
        "position": obs.position,
        "cloaked": obs.cloaked,
        "energy": obs.energy,
        "banked_overcharge": obs.banked_overcharge,
        "actions_remaining": obs.actions_remaining,
        "unlocked": dict(obs.unlocked),
    }


def _legal_actions(session: GameSession, obs: Observation) -> List[dict]:
    out = []
    for a in obs.legal_actions:
        out.append({
            "type": a.type.name,
            "dest": a.dest,
            "label": _LABELS[a.type] + (f" → {a.dest}" if a.dest else ""),
            "cost": _action_cost(session, a.type),
        })
    return out
