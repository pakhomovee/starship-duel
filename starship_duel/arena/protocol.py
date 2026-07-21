"""The arena wire protocol: one JSON object per line, both directions.

Engine -> bot (a request to decide one action)::

    {"type":"act","turn":12,"your_id":1,"map_id":"reference","domination_target":80,
     "you":{...},"rival":{...},"map":{...},"systems":{...},
     "campaign_score":[0,1],"skirmish":1,
     "legal_actions":[{"action":"JUMP","target":"Halcyon Binary"},
                      {"action":"HOLD"}, ...]}

Both win races are public, so ``you``/``rival`` each carry ``lives`` and
``domination`` (the map-control score, raced to ``domination_target``).

Bot -> engine (the chosen action; just echo one legal entry)::

    {"action":"JUMP","target":"Halcyon Binary"}

``legal_actions`` is precomputed to lower the barrier to entry (no need to
re-derive legality), but the engine independently validates every reply anyway.
The bot may reply by name (as above) or by ``{"index": k}`` into
``legal_actions``.
"""

from __future__ import annotations

from typing import List, Optional

from ..game import Action, ActionType, Observation


def action_to_wire(a: Action) -> dict:
    d = {"action": a.type.name}
    if a.dest is not None:
        d["target"] = a.dest
    return d


def encode_request(obs: Observation) -> dict:
    """Serialize one partial-information observation into a request object."""
    systems = {}
    for name in obs.adjacency:
        cache = obs.system_cache.get(name)
        systems[name] = {
            "owner": obs.system_owner.get(name),
            # False when this ship has never sensed the system: ``owner`` is then
            # unknown rather than proven unowned (a deep-cloaked rival may hold it).
            "owner_known": obs.owner_known is None or name in obs.owner_known,
            "status": obs.system_status.get(name, "STABLE"),
            "binary": name in obs.binary_systems,
            "cache": cache,  # {"kind","value"} or None
            # plies until this system goes supernova (0 = now, null = safe)
            "collapse_in": obs.system_collapse_in.get(name),
        }
    return {
        "type": "act",
        "turn": obs.turn_number,
        "your_id": obs.ship_id,
        "map_id": obs.map_id,
        # Both win races are PUBLIC to both ships (see the UI's rival card).
        "domination_target": obs.domination_target,
        "you": {
            "position": obs.position,
            "cloaked": obs.cloaked,
            # Turns of Deep Cloak protection left; 0 when not deep-cloaked.
            "deep_cloak_turns_left": obs.deep_cloak_turns_left,
            "energy": obs.energy,
            "banked_overcharge": obs.banked_overcharge,
            "actions_remaining": obs.actions_remaining,
            "unlocked": obs.unlocked,
            "lives": obs.lives,
            "domination": obs.domination[obs.ship_id],
        },
        "rival": {
            # exact system only when known for certain, else nulls + the BFS seed
            "known_position": obs.rival_position,
            "last_seen": obs.rival_last_seen,
            "moves_since_seen": obs.rival_moves_since_seen,
            "unlocked": obs.rival_unlocked,
            "last_action": obs.rival_last_action,
            "lives": obs.rival_lives,
            "domination": obs.domination[1 - obs.ship_id],
        },
        "map": {
            "adjacency": obs.adjacency,
            "binary_systems": obs.binary_systems,
        },
        "systems": systems,
        "campaign_score": obs.campaign_score,
        "skirmish": obs.skirmish_number,
        "legal_actions": [action_to_wire(a) for a in obs.legal_actions],
    }


def parse_reply(reply: dict, legal: List[Action]) -> Optional[Action]:
    """Turn a bot's reply object into a legal :class:`Action`, or ``None``.

    Accepts ``{"action": "...","target": "..."}`` or ``{"index": k}``.  Returns
    ``None`` for anything malformed, unknown, or not currently legal (the caller
    substitutes a safe default and records a strike)."""
    if not isinstance(reply, dict):
        return None

    if "index" in reply:
        try:
            i = int(reply["index"])
        except (TypeError, ValueError):
            return None
        return legal[i] if 0 <= i < len(legal) else None

    name = reply.get("action")
    if not isinstance(name, str):
        return None
    try:
        atype = ActionType[name.strip().upper()]
    except KeyError:
        return None
    target = reply.get("target")
    candidate = Action(atype, target if atype is ActionType.JUMP else None)
    return candidate if candidate in legal else None
