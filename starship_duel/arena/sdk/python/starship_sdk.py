"""Starship Duel — Python bot SDK (standalone, ~30 lines of real code).

Copy this file next to your bot. Write a ``decide(request) -> dict`` function
and call ``run(decide)``. The harness reads one JSON request per line from
stdin, calls your function, and writes one JSON action per line to stdout.

    from starship_sdk import run

    def decide(req):
        me = req["you"]
        # fire if we know the rival is right here
        if req["rival"]["known_position"] == me["position"]:
            return {"action": "FIRE"}
        # otherwise take the first legal action offered
        return req["legal_actions"][0]

    run(decide)

`request` fields: turn, your_id, you{position,cloaked,energy,banked_overcharge,
actions_remaining,unlocked}, rival{known_position,last_seen,moves_since_seen,
unlocked,last_action}, map{adjacency,binary_systems}, systems{name:{owner,status,
binary,cache}}, legal_actions[{action,target?}]. Reply with one legal action
object, e.g. {"action":"JUMP","target":"Veyra"} or {"action":"HOLD"} — or
{"index": k} to pick legal_actions[k].

Keep any per-game memory (belief tracking, opponent model) in your own module
globals; the process persists for the whole game.
"""

from __future__ import annotations

import json
import sys
from typing import Callable


def run(decide: Callable[[dict], dict]) -> None:
    # NOTE: an unhandled exception in your decide() is a runtime error — the
    # process exits and the engine scores it as an automatic LOSS. Catch and
    # handle your own errors (returning a valid action) if you want to survive
    # them; otherwise fix the bug.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        action = decide(request)
        sys.stdout.write(json.dumps(action) + "\n")
        sys.stdout.flush()
