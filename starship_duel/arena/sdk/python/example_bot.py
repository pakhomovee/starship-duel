#!/usr/bin/env python3
"""Example Starship Duel bot (Python). Run it against the engine with:

    python -m starship_duel.run --bot0 heuristic \
        --bot1 "cmd:python starship_duel/arena/sdk/python/example_bot.py"

It keeps a tiny belief of its own across turns and plays a simple hunt-and-hide
strategy — a starting point to build on.
"""

from starship_sdk import run

# Per-game memory (the process persists for the whole game).
_last_known = None


def decide(req: dict) -> dict:
    global _last_known
    me = req["you"]
    rival = req["rival"]
    legal = req["legal_actions"]
    types = {a["action"] for a in legal}

    known = rival["known_position"]
    if known:
        _last_known = known

    # 1) Kill if the rival is confirmed in our system.
    if known == me["position"] and "FIRE" in types:
        return {"action": "FIRE"}

    # 2) If the rival is known adjacent and we can jump+fire this turn, pounce.
    if known and me["actions_remaining"] >= 2:
        for a in legal:
            if a["action"] == "JUMP" and a.get("target") == known:
                return a

    # 3) If exposed, slip back under cloak.
    if not me["cloaked"] and "HOLD" in types:
        return {"action": "HOLD"}

    # 4) Grab an unclaimed binary we're standing on for income.
    here = req["systems"].get(me["position"], {})
    if "CLAIM" in types and here.get("binary") and here.get("owner") is None:
        return {"action": "CLAIM"}

    # 5) Otherwise move toward a binary, avoiding collapsing systems.
    jumps = [a for a in legal if a["action"] == "JUMP"
             and req["systems"].get(a["target"], {}).get("status") == "STABLE"]
    if jumps:
        # prefer a binary destination if one is adjacent
        for a in jumps:
            if req["systems"].get(a["target"], {}).get("binary"):
                return a
        return jumps[0]

    return {"action": "HOLD" if "HOLD" in types else "END_TURN"}


if __name__ == "__main__":
    run(decide)
