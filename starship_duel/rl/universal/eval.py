"""Per-map win-rate benchmarks for the universal policy.

Evaluates each map separately (and against each fixed opponent) so you can see
both overall strength and whether it holds up on every topology -- including a
held-out map if you pass one that isn't in ``train_maps``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from ...bots import make_bot
from .game import UniversalGame
from .model import GraphActorCritic


@dataclass
class EvalResult:
    games: int
    wins: int
    losses: int
    draws: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0


def evaluate(
    model: GraphActorCritic,
    game: UniversalGame,
    map_ids: List[str],
    opponent_names: List[str],
    games: int,
    *,
    base_seed: int = 1_000_000,
) -> Dict[str, Dict[str, EvalResult]]:
    model.eval()
    out: Dict[str, Dict[str, EvalResult]] = {}
    for map_id in map_ids:
        out[map_id] = {}
        for name in opponent_names:
            wins = losses = draws = 0
            for g in range(games):
                ep = game.collect(
                    model, make_bot(name, seed=base_seed + g),
                    map_id=map_id,
                    learner_seat=g % 2, first_ship=(g // 2) % 2,
                    seed=base_seed + g,
                )
                if ep.draw:
                    draws += 1
                elif ep.win:
                    wins += 1
                else:
                    losses += 1
            out[map_id][name] = EvalResult(games, wins, losses, draws)
    model.train()
    return out
