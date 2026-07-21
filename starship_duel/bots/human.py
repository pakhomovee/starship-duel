"""Interactive console controller -- lets a person play a ship at the prompt."""

from __future__ import annotations

from ..game import Action, Observation
from .base import Bot


def render_observation(obs: Observation) -> str:
    lines = []
    lines.append(f"=== ship_{obs.ship_id}'s turn | skirmish {obs.skirmish_number} "
                 f"| turn {obs.turn_number} | score {obs.campaign_score} ===")
    lines.append(f"You are at {obs.position} | cloaked={obs.cloaked} | "
                 f"energy={obs.energy} | actions_left={obs.actions_remaining} | "
                 f"banked_overcharge={obs.banked_overcharge}")
    lines.append(f"Your unlocks: {', '.join(k for k, v in obs.unlocked.items() if v) or 'none'}")
    lines.append(f"Rival unlocks: {', '.join(k for k, v in obs.rival_unlocked.items() if v) or 'none'}")
    lines.append("Rival last turn: "
                 f"{', '.join(obs.rival_last_turn_actions) or '-'}")
    if obs.rival_position is not None:
        lines.append(f"Rival KNOWN at: {obs.rival_position}")
    else:
        lines.append("Rival position: unknown (infer from the action log)")
    owned = [s for s, o in obs.system_owner.items() if o == obs.ship_id]
    rival_owned = [s for s, o in obs.system_owner.items() if o not in (None, obs.ship_id)]
    lines.append(f"You own: {', '.join(owned) or 'none'}")
    lines.append(f"Rival owns: {', '.join(rival_owned) or 'none'}")
    caches = [f"{s}({c['kind']}:{c['value']})" for s, c in obs.system_cache.items() if c]
    lines.append(f"Caches: {', '.join(caches) or 'none'}")
    lines.append(f"Neighbors of {obs.position}: {', '.join(obs.adjacency[obs.position])}")
    return "\n".join(lines)


class HumanBot(Bot):
    name = "human"

    def act(self, obs: Observation) -> Action:
        print(render_observation(obs))
        legal = obs.legal_actions
        print("\nLegal actions:")
        for i, a in enumerate(legal):
            print(f"  [{i}] {a}")
        while True:
            raw = input("Choose action # (or 'q' to resign): ").strip()
            if raw.lower() in ("q", "quit", "resign"):
                # Firing a guaranteed miss is the closest in-game 'give up';
                # simplest is to just end the turn. Resignation handled by runner.
                raise KeyboardInterrupt
            try:
                idx = int(raw)
                if 0 <= idx < len(legal):
                    return legal[idx]
            except ValueError:
                pass
            print("  invalid choice, try again")
