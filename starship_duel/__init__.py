"""Starship Duel -- a gym-like backend for a 1v1 hidden-information duel.

Quick start::

    from starship_duel.bots import make_bot
    from starship_duel.run import play_skirmish

    result = play_skirmish(make_bot("heuristic"), make_bot("random"), seed=0)

See ``starship_duel/README.md`` for the full API and design notes.
"""

from .env import StarshipDuelEnv
from .game import Engine, GameConfig, build_observation

__all__ = ["StarshipDuelEnv", "Engine", "GameConfig", "build_observation"]
__version__ = "0.1.0"
