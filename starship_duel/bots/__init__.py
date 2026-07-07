"""Bots and a small name-based registry used by the runner/CLI.

Register a custom bot by adding it to :data:`REGISTRY` (or call
``register("name", factory)``).  A factory is a zero-arg callable returning a
fresh :class:`Bot`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict

from .base import Bot
from .belief import BotBelief
from .deepseek_bot import DeepSeekBot
from .heuristic_bot import HeuristicBot
from .human import HumanBot, render_observation
from .random_bot import RandomBot

#: A factory takes an optional ``seed`` and returns a fresh :class:`Bot`.
BotFactory = Callable[..., Bot]

#: Bundled trained-PPO checkpoints, exposed as ready-to-play difficulty tiers.
#: The checkpoints ship in ``bots/ppo/``; the factory is lazy so importing this
#: package never pulls in torch/numpy unless a PPO bot is actually built.
_PPO_DIR = Path(__file__).resolve().parent / "ppo"
_PPO_TIERS = {
    "ppo-easy": "ckpt_500.pt",
    "ppo-medium": "ckpt_2000.pt",
}


def _make_ppo(ckpt: str, display: str) -> BotFactory:
    def factory(seed=None) -> Bot:
        from .ppo_bot import PpoBot
        return PpoBot.from_checkpoint(str(_PPO_DIR / ckpt), name=display, seed=seed)
    return factory


REGISTRY: Dict[str, BotFactory] = {
    "random": lambda seed=None: RandomBot(seed=seed),
    "heuristic": lambda seed=None: HeuristicBot(seed=seed),
    "deepseek": lambda seed=None: DeepSeekBot(seed=seed),
    "human": lambda seed=None: HumanBot(),
}

# Register bundled PPO tiers only if their checkpoint file is present.
for _tier, _ckpt in _PPO_TIERS.items():
    if (_PPO_DIR / _ckpt).exists():
        REGISTRY[_tier] = _make_ppo(_ckpt, _tier)


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
    "DeepSeekBot",
    "HumanBot",
    "render_observation",
    "REGISTRY",
    "register",
    "make_bot",
]
