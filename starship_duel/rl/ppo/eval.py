"""Win-rate benchmarks against fixed opponents.

Self-play reward is ~0 by construction (a policy tied against copies of itself),
so it is *not* a usable progress signal.  These external benchmarks are how we
actually tell whether the policy is getting stronger.  We alternate both the
learner's seat and who moves first so results aren't confounded by first-move
advantage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from ...bots import make_bot
from ..single_agent_view import EncodedGame
from ..model import MaskedActorCritic


@dataclass
class EvalResult:
    opponent: str
    games: int
    wins: int
    losses: int
    draws: int

    @property
    def win_rate(self) -> float:
        return self.wins / self.games if self.games else 0.0

    @property
    def score(self) -> float:
        """Zero-sum score in [-1, 1]: (wins - losses) / games."""
        return (self.wins - self.losses) / self.games if self.games else 0.0


def evaluate(
    model: MaskedActorCritic,
    game: EncodedGame,
    opponent_names: List[str],
    games: int,
    *,
    base_seed: int = 1_000_000,
) -> Dict[str, EvalResult]:
    model.eval()
    results: Dict[str, EvalResult] = {}
    for name in opponent_names:
        wins = losses = draws = 0
        for g in range(games):
            opp = make_bot(name, seed=base_seed + g)
            ep = game.collect(
                model,
                opp,
                learner_seat=g % 2,          # alternate seat
                first_ship=(g // 2) % 2,     # alternate who moves first
                seed=base_seed + g,
            )
            if ep.draw:
                draws += 1
            elif ep.win:
                wins += 1
            else:
                losses += 1
        results[name] = EvalResult(name, games, wins, losses, draws)
    model.train()
    return results
