"""Bots and a small name-based registry used by the runner/CLI.

Register a custom bot by adding it to :data:`REGISTRY` (or call
``register("name", factory)``).  A factory is a zero-arg callable returning a
fresh :class:`Bot`.
"""

from __future__ import annotations

from typing import Callable, Dict

from .base import Bot
from .belief import BotBelief
from .deepseek_bot import DeepSeekBot
from .heuristic_bot import HeuristicBot
from .human import HumanBot, render_observation
from .hunter_bot import HunterBot
from .random_bot import RandomBot

#: A factory takes an optional ``seed`` and returns a fresh :class:`Bot`.
BotFactory = Callable[..., Bot]

REGISTRY: Dict[str, BotFactory] = {
    "random": lambda seed=None: RandomBot(seed=seed),
    "heuristic": lambda seed=None: HeuristicBot(seed=seed),
    "hunter": lambda seed=None: HunterBot(seed=seed),
    "deepseek": lambda seed=None: DeepSeekBot(seed=seed),
    "human": lambda seed=None: HumanBot(),
}


def register(name: str, factory: BotFactory) -> None:
    REGISTRY[name] = factory


def make_bot(name: str, seed=None) -> Bot:
    if name not in REGISTRY:
        raise KeyError(f"unknown bot {name!r}; available: {sorted(REGISTRY)}")
    return REGISTRY[name](seed=seed)


__all__ = [
    "Bot",
    "BotBelief",
    "RandomBot",
    "HeuristicBot",
    "HunterBot",
    "DeepSeekBot",
    "HumanBot",
    "render_observation",
    "REGISTRY",
    "register",
    "make_bot",
]
