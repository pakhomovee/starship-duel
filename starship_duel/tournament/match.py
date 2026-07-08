"""Play one tournament match and record it.

Reuses :class:`starship_duel.web.session.GameSession` verbatim, so all the
replay-recording and crash-forfeit logic (a ``BotError`` from a dead/timed-out
subprocess -> automatic loss) is inherited.  The two competitor bots are injected
via the ``bot_overrides`` seam; competitor ``a_id`` plays ship seat 0, ``b_id``
seat 1, and ``first_mover`` sets which seat moves first this game.

The finished game's replay is persisted through the same
:class:`~starship_duel.web.history.GameStore` the web UI uses, and its record id
is written back onto the ``matches`` row as ``replay_rid`` so the existing viewer
can play it back with no changes.
"""

from __future__ import annotations

from typing import Optional

from ..game import GameConfig
from .registry import BotRegistry
from .store import TournamentStore

# Hard ceiling on plies so a pair of pathological bots can never wedge a worker.
_MAX_PLIES = 4000


def run_match(match: dict, *, tstore: TournamentStore, registry: BotRegistry,
              game_store, config: Optional[GameConfig] = None) -> Optional[int]:
    """Play the given claimed ``match`` row to completion and record the result.

    Returns the winning seat (0/1) or ``None`` for a draw.  Raises only on an
    *orchestration* failure (e.g. a bot subprocess that won't even launch); the
    caller marks such matches ``error``.  In-game bot misbehaviour is handled by
    GameSession as a forfeit, which is a normal ``done`` result.
    """
    from ..web.session import GameSession

    a_id, b_id = match["a_id"], match["b_id"]
    seed = match.get("seed")
    bot_a = registry.build(a_id, seed=None if seed is None else seed)
    bot_b = registry.build(b_id, seed=None if seed is None else seed + 1)

    session = GameSession(
        {0: a_id, 1: b_id},
        config=config or GameConfig(),
        seed=seed,
        store=game_store,
        first_mover=match["first_mover"],
        bot_overrides={0: bot_a, 1: bot_b},
    )
    try:
        plies = 0
        while session.can_step_bot() and plies < _MAX_PLIES:
            session.step_bot()
            plies += 1
        st = session.env.engine.state
        result = st.winner  # 0 / 1 / None
        tstore.finish_match(match["id"], result, st.end_reason, session.record_id)
        return result
    finally:
        session.close()  # reap subprocess bots
