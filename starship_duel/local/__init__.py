"""Local test app: a single-user desktop companion for competition participants.

    python -m starship_duel.local            # starts the app + opens a browser

A trimmed-down cousin of :mod:`starship_duel.web` with no accounts, tournament,
tokens, or docker sandbox — just the pieces a participant needs on their own
machine (Linux / macOS / Windows):

  * register their solution files as "my bots" (run as plain subprocesses over
    the arena JSON-line protocol, same as on the server);
  * play or watch games against the built-in baselines (random / heuristic /
    hunter / PPO tiers) and against previous versions of their own bot;
  * batch test runs (N headless games) with win-rate stats and optional replays;
  * browse and replay game history.

State lives under ``~/.starship_duel`` (override with ``$STARSHIP_LOCAL_DIR``
or ``--data-dir``), so the app works no matter where it is launched from.
"""

# NB: deliberately no `from .app import app` here — that would shadow the
# `starship_duel.local.app` submodule with the FastAPI instance of the same
# name. Use `python -m starship_duel.local` or import starship_duel.local.app.
from .app import main

__all__ = ["main"]
