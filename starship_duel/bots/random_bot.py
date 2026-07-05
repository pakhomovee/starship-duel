"""A uniform-random legal-move bot -- the simplest sane baseline."""

from __future__ import annotations

from ..game import Action, ActionType, Observation
from .base import Bot


class RandomBot(Bot):
    name = "random"

    def __init__(self, name=None, seed=None, end_turn_bias: float = 0.15):
        super().__init__(name=name, seed=seed)
        # Chance to voluntarily end the turn on any given action, so games do
        # not always spend every action every turn.
        self.end_turn_bias = end_turn_bias

    def act(self, obs: Observation) -> Action:
        legal = obs.legal_actions
        # Always take a guaranteed kill if the rival is known to be right here.
        if obs.rival_position == obs.position:
            for a in legal:
                if a.type is ActionType.FIRE:
                    return a
        if self.rng.random() < self.end_turn_bias:
            return Action.end_turn()
        # Prefer not to end the turn purely at random most of the time.
        non_pass = [a for a in legal if a.type is not ActionType.END_TURN]
        return self.rng.choice(non_pass or legal)
