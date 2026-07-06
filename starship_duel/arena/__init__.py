"""Arena: run external bots as isolated subprocesses over a JSON-line protocol.

This is the competition/ladder layer (Battlesnake / Halite / CodinGame style),
kept deliberately separate from the in-process RL loop.  Each bot is its own OS
process talking one line of JSON per turn over stdin/stdout, so:

  * a bot can be written in ANY language that can read a line and parse JSON;
  * a bot keeps its own memory across turns (its own belief/opponent model);
  * a crash, hang, or illegal move in one bot cannot take the match runner down
    — the engine stays authoritative, validates every reply, and substitutes a
    safe default (plus a logged strike) on timeout/garbage/illegal input.

See :mod:`starship_duel.arena.protocol` for the wire format and
:class:`starship_duel.arena.subprocess_bot.SubprocessBot` for the engine side.
Thin, copy-pasteable SDKs live under ``arena/sdk/``.
"""

from .subprocess_bot import SubprocessBot

__all__ = ["SubprocessBot"]
