"""Who is allowed to play, and how to launch them.

Two kinds of competitor:
  * **baselines** -- the bundled reference bots (``random``, ``heuristic``,
    ``ppo-easy``, ``ppo-medium``), built in-process via ``bots.make_bot``.  These
    are the opponents used for the during-contest "partial standings".
  * **bots** -- participant entries, each launched as a subprocess over the
    ``starship_duel.arena`` stdin/stdout protocol (:class:`SubprocessBot`, which
    already enforces per-move timeout -> strike -> forfeit).

Security mirrors :mod:`starship_duel.web.arena_registry`: the *command* for a bot
is read only from a trusted server-side allowlist (``$STARSHIP_TOURNEY_BOTS``
JSON, or a path passed in), never from the DB or a web request.  A competitor id
is just a key into this allowlist.

Example ``tourney_bots.json``::

    {
      "alice":  {"command": ["python", "bots/alice.py"], "timeout": 1.0},
      "bob-cpp": {"command": ["./bots/bob"], "timeout": 0.5, "cwd": "/srv/bots"}
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from ..arena import SubprocessBot
from ..bots import Bot, make_bot

#: Reference bots every tournament includes; also the partial-standings opponents.
BASELINES = ("random", "heuristic", "ppo-easy", "ppo-medium")

_DEFAULT_TIMEOUT = 1.0


def _load_specs(path: Optional[str]) -> Dict[str, dict]:
    path = path or os.environ.get("STARSHIP_TOURNEY_BOTS")
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    specs: Dict[str, dict] = {}
    for cid, spec in data.items():
        if isinstance(spec, str):
            spec = {"command": spec}
        if isinstance(spec, dict) and "command" in spec:
            specs[cid] = spec
    return specs


class BotRegistry:
    """Loaded once; builds a fresh :class:`Bot` for a competitor on demand."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.specs = _load_specs(path)

    def reload(self) -> None:
        self.specs = _load_specs(self.path)

    def bot_ids(self) -> List[str]:
        """Participant (non-baseline) competitor ids available in the allowlist."""
        return sorted(self.specs)

    def all_ids(self) -> List[str]:
        return sorted(self.specs) + list(BASELINES)

    def kind(self, competitor_id: str) -> str:
        return "baseline" if competitor_id in BASELINES else "bot"

    def build(self, competitor_id: str, *, kind: Optional[str] = None,
              seed: Optional[int] = None) -> Bot:
        kind = kind or self.kind(competitor_id)
        if kind == "baseline":
            return make_bot(competitor_id, seed=seed)
        spec = self.specs.get(competitor_id)
        if spec is None:
            raise KeyError(f"no launch spec for bot competitor {competitor_id!r} "
                           f"(known: {self.bot_ids()})")
        cmd = spec["command"]
        return SubprocessBot(
            cmd,
            name=competitor_id,
            timeout=float(spec.get("timeout", _DEFAULT_TIMEOUT)),
            cwd=spec.get("cwd"),
        )
