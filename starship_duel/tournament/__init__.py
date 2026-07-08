"""Tournament platform (Phase 1): match runner, SQLite job queue, and
Bradley-Terry scoring for a bot competition.

The trusted game engine runs on the host (the referee); competitor bots are
driven as subprocesses over the existing ``starship_duel.arena`` protocol.  This
package only orchestrates: it schedules matches, hands them to workers that reuse
:class:`starship_duel.web.session.GameSession` to play + record a replay, stores
results in SQLite, and ranks competitors with a regularized Bradley-Terry model.

See ``registry`` (who can play), ``store`` (queue + results), ``match`` (play one
game), ``schedule`` (fill the queue), ``scoring`` (BT + bootstrap CIs), and the
``worker`` / ``tick`` entry points.
"""

from __future__ import annotations

from .registry import BASELINES, BotRegistry
from .store import TournamentStore

__all__ = ["BASELINES", "BotRegistry", "TournamentStore"]
