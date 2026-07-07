"""Server-side registry of arena (external subprocess) bots for the web UI.

Security: the web client selects an arena bot by *name* only — it never sends a
command. The command for each name comes from this server-side allowlist, so
enabling arena bots in the UI can't run arbitrary commands from web requests.

Populated from:
  * the bundled example bot (so there's always one to play against), plus
  * an optional JSON file of your own bots — `$STARSHIP_ARENA_BOTS` if set, else
    `arena_bots.json` in the working directory, e.g.::

        {
          "my-cpp-bot": {"command": "./mybot", "timeout": 1.0},
          "py-hunter":  "python bots/hunter.py"
        }
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

from ..arena import SubprocessBot

_PKG = Path(__file__).resolve().parent.parent  # .../starship_duel
_EXAMPLE_PY = _PKG / "arena" / "sdk" / "python" / "example_bot.py"
# The C++ example ships as source; it only appears here once you've built it:
#   cd starship_duel/arena/sdk/cpp
#   g++ -std=c++17 -O2 -I. example_bot.cpp -o example_bot   # needs nlohmann/json
_EXAMPLE_CPP_BIN = _PKG / "arena" / "sdk" / "cpp" / "example_bot"

#: Prefix that marks a controller name as an arena bot (vs a built-in bot).
PREFIX = "arena:"


def _defaults() -> Dict[str, dict]:
    bots: Dict[str, dict] = {}
    if _EXAMPLE_PY.exists():
        bots["example-py"] = {"command": [sys.executable, str(_EXAMPLE_PY)], "timeout": 2.0}
    # Auto-register the compiled C++ example once it's been built.
    if _EXAMPLE_CPP_BIN.exists() and os.access(_EXAMPLE_CPP_BIN, os.X_OK):
        bots["example-cpp"] = {"command": [str(_EXAMPLE_CPP_BIN)], "timeout": 2.0}
    return bots


def load_arena_bots() -> Dict[str, dict]:
    """Return ``{name: {"command", "timeout"?, "cwd"?}}`` for all arena bots."""
    bots = _defaults()
    path = os.environ.get("STARSHIP_ARENA_BOTS") or str(Path.cwd() / "arena_bots.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            for name, spec in data.items():
                if isinstance(spec, str):
                    spec = {"command": spec}
                if "command" in spec:
                    bots[name] = spec
        except (json.JSONDecodeError, OSError, AttributeError):
            pass  # a bad config file must not take the server down
    return bots


class ArenaBots:
    """Loaded once at server start; builds :class:`SubprocessBot` on demand."""

    def __init__(self):
        self.specs = load_arena_bots()

    def reload(self):
        """Re-read the config so newly-added bots appear without a restart."""
        self.specs = load_arena_bots()

    def names(self):
        return sorted(self.specs)

    def make(self, name: str) -> SubprocessBot:
        spec = self.specs[name]
        return SubprocessBot(
            spec["command"],
            name=PREFIX + name,
            timeout=float(spec.get("timeout", 2.0)),
            cwd=spec.get("cwd"),
        )
