"""Bot interface.

A bot is anything with an ``act(observation) -> Action`` method.  It is called
repeatedly during its own turn (once per action) and should return
``Action.end_turn()`` to pass.  ``observation.legal_actions`` lists exactly the
moves it is allowed to make right now, so the simplest possible valid bot is::

    class MyBot(Bot):
        def act(self, obs):
            return obs.legal_actions[0]

Bots see only the partial-information :class:`Observation` (spec 3) -- never the
rival's true position -- so the same bot works for player-vs-bot and bot-vs-bot.
"""

from __future__ import annotations

import random
from typing import Optional

from ..game import Action, Observation


class Bot:
    #: Human-readable name; override or pass to ``__init__``.
    name: str = "bot"

    def __init__(self, name: Optional[str] = None, seed: Optional[int] = None):
        if name is not None:
            self.name = name
        self.rng = random.Random(seed)

    def reset(self) -> None:
        """Called at the start of each skirmish.  Override to clear per-game
        memory (e.g. a rival model).  Default: no-op."""

    def act(self, obs: Observation) -> Action:  # pragma: no cover - abstract
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} {self.name!r}>"
