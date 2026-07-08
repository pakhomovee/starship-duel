"""Fill the match queue.

Two schedules:
  * :func:`enqueue_baselines` -- every active participant bot vs each of the 4
    baselines, N games each with balanced first-mover.  Cheap and continuous;
    drives the during-contest partial standings.
  * :func:`enqueue_full_round_robin` -- every active-bot *pair*, N games each,
    balanced first-mover.  ~C(bots, 2) x N matches; the post-deadline full eval.

Both are **idempotent**: they only top a pair up to N games, so re-running after
adding a competitor (or bumping N) schedules just the missing matches.
"""

from __future__ import annotations

import itertools
from typing import List, Optional, Tuple

from .registry import BASELINES, BotRegistry
from .store import TournamentStore


def register_competitors(store: TournamentStore, registry: BotRegistry) -> None:
    """Make sure every baseline and every allowlisted bot exists as a competitor."""
    for name in BASELINES:
        store.add_competitor(name, "baseline")
    for cid in registry.bot_ids():
        store.add_competitor(cid, "bot")


def _balanced_rows(a_id: str, b_id: str, n_each: int, already: int,
                   seed_base: int) -> List[Tuple[str, str, int, int]]:
    """Rows to top the ordered (a_id, b_id) pair up to ``n_each`` games, with the
    first half moving seat 0 first and the second half seat 1 first."""
    rows = []
    for k in range(already, n_each):
        first_mover = 0 if k < n_each // 2 else 1
        rows.append((a_id, b_id, first_mover, seed_base + k))
    return rows


def enqueue_baselines(store: TournamentStore, *, n_each: int = 10) -> int:
    """Schedule participant-vs-baseline matches. Returns rows added."""
    bots = [c["id"] for c in store.list_competitors(kind="bot", active_only=True)]
    rows: List[Tuple[str, str, int, int]] = []
    for bot in bots:
        for base in BASELINES:
            already = store.count_pair(bot, base)
            rows += _balanced_rows(bot, base, n_each, already, seed_base=hash((bot, base)) % 1_000_000)
    return store.add_matches(rows)


def enqueue_full_round_robin(store: TournamentStore, *, n_each: int = 10) -> int:
    """Schedule the all-pairs round robin over active participant bots."""
    bots = sorted(c["id"] for c in store.list_competitors(kind="bot", active_only=True))
    rows: List[Tuple[str, str, int, int]] = []
    for a_id, b_id in itertools.combinations(bots, 2):
        already = store.count_pair(a_id, b_id)
        rows += _balanced_rows(a_id, b_id, n_each, already, seed_base=hash((a_id, b_id)) % 1_000_000)
    return store.add_matches(rows)
