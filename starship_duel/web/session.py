"""In-memory game sessions driving the web UI.

A session owns one :class:`StarshipDuelEnv` plus a *controller* for each ship —
either ``"human"`` or the name of a registered bot.  It knows how to apply a
human action, auto-play bot turns, and single-step a bot-vs-bot game so the UI
can watch it unfold.
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from typing import Dict, List, Optional

from ..bots import Bot, make_bot
from ..bots.base import BotError
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
        arena=None,
        store=None,
        first_mover: Optional[int] = None,
        bot_overrides: Optional[Dict[int, Bot]] = None,
    ):
        self.id = f"g{next(_ids)}"
        self.controllers = controllers  # {0: "human"|botname|"arena:<name>", 1: ...}
        self.config = config or GameConfig()
        self.seed = seed
        self.map_id = map_id
        # Which ship moves first this skirmish (None -> engine default/random).
        self.first_mover = first_mover
        self.arena = arena  # web.arena_registry.ArenaBots (or None)
        self.store = store  # web.history.GameStore (or None) -> persist replays
        self.lock = threading.Lock()

        # Replay recording: one record per skirmish (a new record_id per reset).
        self.record_id: str = ""
        self.frames: List[dict] = []
        self.started: float = 0.0
        self._saved: bool = False

        self.env = StarshipDuelEnv(config=self.config, seed=seed)
        # Callers (e.g. the tournament match runner) may inject already-built bot
        # instances directly; otherwise controllers are resolved by name through
        # the bot/arena registries.
        if bot_overrides is not None:
            self.bots: Dict[int, Bot] = dict(bot_overrides)
        else:
            self.bots = {
                s: self._build_controller(name, None if seed is None else seed + s)
                for s, name in controllers.items()
                if name != "human"
            }
        self.events: List[str] = []
        self.reset()

    def _build_controller(self, name: str, seed):
        from .arena_registry import PREFIX
        if self.arena is not None and name.startswith(PREFIX):
            return self.arena.make(name[len(PREFIX):])
        return make_bot(name, seed=seed)

    def close(self) -> None:
        """Tear down any external subprocess bots this session owns."""
        for b in self.bots.values():
            if hasattr(b, "close"):
                b.close()

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
        self.env.reset(map_id=self.map_id, first_ship=self.first_mover)
        for b in self.bots.values():
            b.reset()
        self.events = []
        st = self.env.engine.state
        self.events.append(
            f"skirmish start on {st.map_id}; ship_{st.turn_ship} moves first"
        )
        # Start a fresh replay recording for this skirmish.
        self.record_id = uuid.uuid4().hex[:12]
        self.frames = []
        self.started = time.time()
        self._saved = False
        self._record()
        # No auto-play: bot turns are advanced explicitly (Step / Auto) so the
        # UI can watch every bot action unfold, even in human-vs-bot.

    # -- replay recording ----------------------------------------------------
    def _record(self) -> None:
        """Capture the current truth frame; persist the game once it ends."""
        from .serialize import serialize  # local import avoids an import cycle

        self.frames.append(serialize(self, "truth"))
        if self.store is not None and not self._saved and self.env.done:
            self._saved = True
            st = self.env.engine.state
            meta = {
                "created": self.started,
                "mode": self.mode,
                "map_id": st.map_id,
                "seed": self.seed,
                "controllers": {str(k): v for k, v in self.controllers.items()},
                "winner": st.winner,
                "end_reason": st.end_reason,
                "plies": len(self.frames),
            }
            try:
                self.store.save(self.record_id, meta, self.frames)
            except Exception:  # never let persistence break live play
                pass

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
        return self._step(action)

    def _step(self, action: Action) -> List[str]:
        self.env.step(action)
        evs = list(self.env.last_events)
        self.events.extend(evs)
        self._record()
        return evs

    # -- bot stepping --------------------------------------------------------
    def can_step_bot(self) -> bool:
        return not self.env.done and not self.is_human_turn()

    def step_bot(self) -> List[str]:
        """Advance exactly one bot action (used by Step / Auto in any mode)."""
        if not self.can_step_bot():
            return []
        ship = self.current_ship
        bot = self.bots[ship]
        obs = build_observation(self.env.engine, ship)
        try:
            action = bot.act(obs)
        except BotError as e:
            # A crashing bot automatically loses.
            self.env.engine.forfeit(ship, reason="crash")
            self.events.append(f"ship{ship} crashed ({e}) — forfeits")
            self._record()
            return list(self.events[-1:])
        return self._step(action)

    def recent_events(self, limit: int = 40) -> List[str]:
        return self.events[-limit:]
