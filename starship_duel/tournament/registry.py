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
#: The map-universal ``uppo`` tiers play the current game on any map; the legacy
#: ``ppo-*`` tiers are retained for continuity.
BASELINES = ("random", "heuristic", "hunter", "uppo-easy", "uppo")

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
    """Loaded once; builds a fresh :class:`Bot` for a competitor on demand.

    Launch specs come from two trusted sources, merged in :meth:`reload`:
      * the static JSON allowlist (``path`` / ``$STARSHIP_TOURNEY_BOTS``), and
      * validated **active submissions** in an :class:`AccountStore`, materialized
        to ``submissions_dir`` -- so a user's uploaded bot competes under their
        username with no manual config.
    """

    def __init__(self, path: Optional[str] = None, *, account_store=None,
                 submissions_dir: Optional[str] = None):
        self.path = path
        self.account_store = account_store
        self.submissions_dir = submissions_dir
        self.specs: Dict[str, dict] = {}
        self.reload()

    def reload(self) -> None:
        specs = _load_specs(self.path)
        if self.account_store is not None:
            from .accounts import materialize_active
            # Submissions win over any same-named JSON entry.
            specs.update(materialize_active(self.account_store, self.submissions_dir))
        self.specs = specs

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
