"""Serialize a :class:`GameSession` to a JSON-friendly view for the frontend.

Two perspectives:
  * a **player** view (``perspective`` = ship id) uses that ship's partial-info
    :class:`Observation` -- the rival is only shown when legitimately revealed,
    otherwise it appears as a candidate-system overlay.
  * the **truth** view (bot-vs-bot spectating) shows both ships exactly, plus
    each side's belief set for debugging the hidden-info layer.
"""

from __future__ import annotations

from typing import List, Optional, Union

from ..game import ActionType, build_observation
from ..game.observation import Observation
from .layout import compute_layout
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


def _action_cost(session: GameSession, atype: ActionType) -> Optional[int]:
    # FIRE is priced only in territory-control mode; in assassination mode
    # (enable_instakill) it's a free instant-kill, so there's no cost to show.
    if atype is ActionType.FIRE:
        return None if session.config.enable_instakill else session.config.cost_fire
    attr = _COST_ATTR.get(atype)
    return getattr(session.config, attr) if attr else None


def serialize(session: GameSession, perspective: Union[str, int] = "truth") -> dict:
    eng = session.env.engine
    st = eng.state
    gmap = eng.map
    layout = compute_layout(gmap)

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
            "collapse_in": eng.collapse_in(name),  # plies to supernova (early warning)
            "fogged": False,  # overridden per-observer below (fog of war)
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
        "domination": list(st.domination),
        "domination_target": session.config.domination_target,
        "lives": list(st.lives),
        "lives_max": session.config.lives,
        "systems": systems,
        "edges": edges,
        "events": session.events_for(perspective),
        "awaiting_human": session.is_human_turn(),
        "can_step": (not st.done) and session.controllers[st.turn_ship] != "human",
    }

    if perspective == "truth":
        view["ships"] = [
            {"id": s, "position": st.ships[s].position, "cloaked": st.ships[s].cloaked,
             "known": True}
            for s in (0, 1)
        ]
        # Belief overlays: read the engine's tracker directly (it is kept for
        # visualization even though bots no longer receive it).
        view["candidates"] = {
            "0": sorted(eng.belief[0].candidates),
            "1": sorted(eng.belief[1].candidates),
        }
        view["hud"] = [_ship_hud(st, s) for s in (0, 1)]
        view["legal_actions"] = []
    else:
        me = int(perspective)
        obs = build_observation(eng, me)
        rival = eng.state.ships[1 - me]
        rival_known = not rival.cloaked
        view["self_id"] = me
        # Fog of war: this observer only sees ownership it has actually sensed;
        # unknown systems are shown fogged (rival claims under cloak stay hidden).
        known = eng.owner_known[me]
        for sysd in systems:
            if sysd["name"] in known:
                sysd["owner"] = obs.system_owner.get(sysd["name"])
            else:
                sysd["owner"] = None
                sysd["fogged"] = True
        view["ships"] = [
            {"id": me, "position": obs.position, "cloaked": obs.cloaked, "known": True, "is_self": True},
            {"id": 1 - me,
             "position": rival.position if rival_known else None,
             "cloaked": rival.cloaked, "known": rival_known, "is_self": False},
        ]
        view["candidates"] = {str(1 - me): sorted(eng.belief[me].candidates)}
        view["hud"] = [_obs_hud(obs, me)]
        view["rival_unlocked"] = obs.rival_unlocked
        view["rival_last_action"] = obs.rival_last_action
        view["rival_last_turn_actions"] = obs.rival_last_turn_actions
        view["legal_actions"] = (
            _legal_actions(session, obs) if view["awaiting_human"] else []
        )
        # Full catalogue (affordable + not) so the UI can show every option and
        # dim the ones currently unavailable.
        view["action_menu"] = (
            _action_menu(session, obs) if view["awaiting_human"] else []
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


_UNLOCK_FLAG = {
    ActionType.UNLOCK_PROXIMITY_ALERT: "proximity_alert",
    ActionType.UNLOCK_LONG_RANGE_SCANNERS: "long_range_scanners",
    ActionType.UNLOCK_JAMMING: "jamming",
}


def _action_menu(session: GameSession, obs: Observation) -> List[dict]:
    """Every action the ship could ever take from here, each flagged
    ``enabled`` (currently legal/affordable) with a short ``reason`` when not.
    The engine remains the sole authority -- disabled entries are never
    accepted server-side."""
    from ..game import Action  # local import to keep module import-light

    legal = set(obs.legal_actions)  # Action is a frozen dataclass -> hashable

    # A FIRE while the rival is a hop away (and known) is a "snipe": with
    # Long-Range Scanners, the same FIRE action reaches an adjacent rival instead
    # of the co-located one (engine ``_do_fire`` auto-targets, so the submitted
    # action stays a plain ``Action.fire()`` -- adding a dest would be illegal).
    # Surface it as a distinct, discoverable target rather than a bare "Fire".
    snipe_target = None
    rp = obs.rival_position
    if (obs.unlocked.get("long_range_scanners") and rp is not None
            and rp != obs.position and rp in obs.adjacency.get(obs.position, [])):
        snipe_target = rp

    catalogue: List[Action] = [Action.jump(d) for d in sorted(obs.adjacency.get(obs.position, []))]
    catalogue += [
        Action.hold(), Action.claim(), Action.fire(),
        Action.scan(), Action.deep_cloak(), Action.overcharge(),
        Action(ActionType.UNLOCK_PROXIMITY_ALERT),
        Action(ActionType.UNLOCK_LONG_RANGE_SCANNERS),
        Action(ActionType.UNLOCK_JAMMING),
        Action.end_turn(),
    ]

    out = []
    for a in catalogue:
        enabled = a in legal
        entry = {
            "type": a.type.name,
            "dest": a.dest,
            "label": _LABELS[a.type] + (f" → {a.dest}" if a.dest else ""),
            "cost": _action_cost(session, a.type),
            "enabled": enabled,
            "reason": None if enabled else _disabled_reason(a, obs, session),
        }
        # Relabel the FIRE entry as a snipe when a known rival sits one hop away.
        # The submission stays FIRE with no dest (the engine targets the rival);
        # ``snipe_target`` is UI-only, for the label and the map highlight/click.
        if a.type is ActionType.FIRE and snipe_target is not None:
            entry["label"] = f"Snipe → {snipe_target}"
            entry["snipe_target"] = snipe_target
        out.append(entry)
    return out


def _disabled_reason(action, obs: Observation, session: GameSession) -> str:
    t = action.type
    cost = _action_cost(session, t)
    if t in _UNLOCK_FLAG:
        if obs.unlocked[_UNLOCK_FLAG[t]]:
            return "already unlocked"
        if cost is not None and obs.energy < cost:
            return f"needs {cost}⚡ (have {obs.energy})"
        return "unavailable"
    if cost is not None and obs.energy < cost:  # SCAN / DEEP_CLOAK / OVERCHARGE
        return f"needs {cost}⚡ (have {obs.energy})"
    if t is ActionType.CLAIM:
        return "already yours" if obs.system_owner.get(obs.position) == obs.ship_id else "can't claim here"
    if t is ActionType.HOLD:
        owner = obs.system_owner.get(obs.position)
        return "enemy-held system" if owner not in (None, obs.ship_id) else "must move (unstable)"
    if t is ActionType.JUMP:
        return "route unstable"
    return "unavailable"
