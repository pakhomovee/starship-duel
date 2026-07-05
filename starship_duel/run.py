"""Match orchestration + CLI for player-vs-bot, bot-vs-bot, human-vs-human.

    python -m starship_duel.run --bot0 heuristic --bot1 random --render
    python -m starship_duel.run --bot0 human --bot1 heuristic
    python -m starship_duel.run --bot0 random --bot1 random --games 200 --quiet
"""

from __future__ import annotations

import argparse
from typing import Dict, List, Optional

from .bots import Bot, make_bot
from .env import StarshipDuelEnv, agent_id
from .game import GameConfig


def play_skirmish(
    bot0: Bot,
    bot1: Bot,
    *,
    config: Optional[GameConfig] = None,
    seed: Optional[int] = None,
    map_id: Optional[str] = None,
    first_ship: Optional[int] = None,
    render: bool = False,
    action_guard: Optional[int] = None,
) -> Dict:
    """Run one skirmish to completion and return a result summary."""
    config = config or GameConfig()
    env = StarshipDuelEnv(config=config, seed=seed)
    env.reset(map_id=map_id, first_ship=first_ship)
    bots = {0: bot0, 1: bot1}
    bot0.reset()
    bot1.reset()

    if render:
        st = env.engine.state
        print(f"# map={st.map_id} | first_ship={st.turn_ship} | "
              f"spawns: ship0={st.ships[0].position}, ship1={st.ships[1].position}")

    guard = action_guard if action_guard is not None else config.turn_cap * 50
    steps = 0
    last_ship = env.engine.current_ship
    while not env.done:
        agent = env.agent_selection
        sid = agent_id(agent)
        obs = env.observe(agent)
        action = bots[sid].act(obs)
        env.step(action)
        if render:
            for ev in env.last_events:
                print(f"  {ev}")
        steps += 1
        if steps > guard:
            raise RuntimeError("action guard tripped -- a bot may never end its turn")
        last_ship = sid

    st = env.engine.state
    result = {
        "winner": st.winner,
        "end_reason": st.end_reason,
        "turns": st.turn_number,
        "actions": steps,
        "campaign_score": list(st.campaign_score),
        "map_id": st.map_id,
    }
    if render:
        print(f"# result: winner={result['winner']} ({result['end_reason']}) "
              f"in {result['turns']} turns")
    return result


def play_campaign(
    bot0: Bot,
    bot1: Bot,
    games: int,
    *,
    config: Optional[GameConfig] = None,
    seed: Optional[int] = None,
    render: bool = False,
) -> Dict:
    """Best-of-N style aggregate over ``games`` skirmishes (spec 6 meta-layer)."""
    wins = [0, 0]
    draws = 0
    reasons: Dict[str, int] = {}
    for g in range(games):
        gseed = None if seed is None else seed + g
        # Alternate who moves first for fairness.
        res = play_skirmish(
            bot0, bot1, config=config, seed=gseed,
            first_ship=g % 2, render=render,
        )
        if res["winner"] is None:
            draws += 1
        else:
            wins[res["winner"]] += 1
        reasons[res["end_reason"]] = reasons.get(res["end_reason"], 0) + 1
    return {
        "games": games,
        "wins_ship0": wins[0],
        "wins_ship1": wins[1],
        "draws": draws,
        "end_reasons": reasons,
    }


def main(argv: Optional[List[str]] = None) -> None:
    p = argparse.ArgumentParser(description="Run Starship Duel skirmishes.")
    p.add_argument("--bot0", default="heuristic", help="bot name for ship 0")
    p.add_argument("--bot1", default="random", help="bot name for ship 1")
    p.add_argument("--games", type=int, default=1, help="number of skirmishes")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--map", dest="map_id", default=None, help="force a map id")
    p.add_argument("--render", action="store_true", help="print per-action log")
    p.add_argument("--quiet", action="store_true", help="summary only")
    args = p.parse_args(argv)

    bot0 = make_bot(args.bot0, seed=args.seed)
    bot1 = make_bot(args.bot1, seed=None if args.seed is None else args.seed + 1)
    render = args.render and not args.quiet

    if args.games == 1:
        res = play_skirmish(bot0, bot1, seed=args.seed, map_id=args.map_id, render=render)
        if not render:
            print(res)
    else:
        summary = play_campaign(bot0, bot1, args.games, seed=args.seed, render=render)
        print(f"{args.bot0}(0) vs {args.bot1}(1) over {summary['games']} games: "
              f"{summary['wins_ship0']}-{summary['wins_ship1']} "
              f"(draws {summary['draws']}) | end reasons: {summary['end_reasons']}")


if __name__ == "__main__":
    main()
