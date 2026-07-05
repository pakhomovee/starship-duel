"""In-memory game sessions driving the web UI.

A session owns one :class:`StarshipDuelEnv` plus a *controller* for each ship —
either ``"human"`` or the name of a registered bot.  It knows how to apply a
human action, auto-play bot turns, and single-step a bot-vs-bot game so the UI
can watch it unfold.
"""

from __future__ import annotations

import itertools
import threading
from typing import Dict, List, Optional

from ..bots import Bot, make_bot
from ..env import StarshipDuelEnv
from ..game import Action, ActionType, GameConfig, build_observation

_ids = itertools.count(1)


class GameSession:
    def __init__(
        self,
        controllers: Dict[int, str],
        *,
        config: Optional[GameConfig] = None,
        seed: Optional[int] = None,
        map_id: Optional[str] = None,
    ):
        self.id = f"g{next(_ids)}"
        self.controllers = controllers  # {0: "human"|botname, 1: ...}
        self.config = config or GameConfig()
        self.seed = seed
        self.map_id = map_id
        self.lock = threading.Lock()

        self.env = StarshipDuelEnv(config=self.config, seed=seed)
        self.bots: Dict[int, Bot] = {
            s: make_bot(name, seed=None if seed is None else seed + s)
            for s, name in controllers.items()
            if name != "human"
        }
        self.events: List[str] = []
        self.reset()

    # -- derived -------------------------------------------------------------
    @property
    def mode(self) -> str:
        humans = sum(1 for c in self.controllers.values() if c == "human")
        return {2: "human_vs_human", 1: "human_vs_bot", 0: "bot_vs_bot"}[humans]

    @property
    def current_ship(self) -> int:
        return self.env.engine.current_ship

    def is_human_turn(self) -> bool:
        return not self.env.done and self.controllers[self.current_ship] == "human"

    # -- lifecycle -----------------------------------------------------------
    def reset(self) -> None:
        self.env.reset(map_id=self.map_id, first_ship=None)
        for b in self.bots.values():
            b.reset()
        self.events = []
        st = self.env.engine.state
        self.events.append(
            f"skirmish start on {st.map_id}; ship_{st.turn_ship} moves first"
        )
        # In human-vs-bot / bot-vs-bot, auto-advance until a human is needed
        # (bot-vs-bot stops immediately so the UI can step it).
        if self.mode == "human_vs_bot":
            self._auto_play_until_human()

    # -- human input ---------------------------------------------------------
    def apply_human_action(self, action_type: str, dest: Optional[str]) -> List[str]:
        if self.env.done:
            return []
        if not self.is_human_turn():
            raise ValueError("not the human's turn")
        atype = ActionType[action_type]
        action = Action(atype, dest)
        if not self.env.engine.is_legal(action, self.current_ship):
            raise ValueError(f"illegal action {action_type} {dest or ''}".strip())

        new_events = self._step(action)
        # After the human's action, let any bot turns play out (hotseat
        # human-vs-human never auto-plays -- the other human takes over).
        if self.mode == "human_vs_bot":
            new_events += self._auto_play_until_human()
        return new_events

    def _step(self, action: Action) -> List[str]:
        self.env.step(action)
        evs = list(self.env.last_events)
        self.events.extend(evs)
        return evs

    # -- bot stepping --------------------------------------------------------
    def step_bot(self) -> List[str]:
        """Advance exactly one bot action (for watching bot-vs-bot)."""
        if self.env.done or self.is_human_turn():
            return []
        ship = self.current_ship
        bot = self.bots[ship]
        obs = build_observation(self.env.engine, ship)
        action = bot.act(obs)
        return self._step(action)

    def _auto_play_until_human(self, guard: int = 10000) -> List[str]:
        collected: List[str] = []
        n = 0
        while not self.env.done and not self.is_human_turn():
            collected += self.step_bot()
            n += 1
            if n > guard:
                raise RuntimeError("auto-play guard tripped")
        return collected

    def recent_events(self, limit: int = 40) -> List[str]:
        return self.events[-limit:]
