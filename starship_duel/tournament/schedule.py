"""Fill the match queue.

Three schedules:
  * :func:`enqueue_baselines` -- every active participant bot vs each of the 4
    baselines, N games each with balanced first-mover.  Cheap and continuous;
    drives the during-contest partial standings.
  * :func:`enqueue_baselines_for_bot` -- the same, for a *single* competitor.
    Fired automatically when a submission validates, so authors get a placing
    without an admin pressing anything.
  * :func:`enqueue_full_round_robin` -- every active-bot *pair*, N games each,
    balanced first-mover.  ~C(bots, 2) x N matches; the post-deadline full eval.

The two bulk schedules are **idempotent**: they only top a pair up to N games, so
re-running after adding a competitor (or bumping N) schedules just the missing
matches.  The per-bot one instead *replaces* that bot's matches by default,
because its trigger is the arrival of new code under an existing name.

Nothing here plays a game -- these only append rows to the queue.  Throughput
(and therefore CPU) is set by how many :mod:`starship_duel.tournament.worker`
processes are draining it, not by how fast matches are scheduled.
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


def runnable_bots(store: TournamentStore, registry: Optional[BotRegistry]) -> List[str]:
    """Active bot competitors the registry can actually launch.

    The two sides drift apart: ``competitors`` rows are permanent, while a bot
    leaves the registry the moment its submission stops building (see
    :func:`~starship_duel.tournament.accounts.materialize_active`) or is
    deactivated.  Scheduling from ``competitors`` alone therefore queues matches
    that can only ever fail with "no launch spec for bot competitor ...".  Pass a
    registry to schedule only what can run.
    """
    bots = [c["id"] for c in store.list_competitors(kind="bot", active_only=True)]
    if registry is None:
        return bots
    live = set(registry.bot_ids())
    return [b for b in bots if b in live]


def enqueue_baselines(store: TournamentStore, *, n_each: int = 10,
                      registry: Optional[BotRegistry] = None) -> int:
    """Schedule participant-vs-baseline matches. Returns rows added."""
    bots = runnable_bots(store, registry)
    rows: List[Tuple[str, str, int, int]] = []
    for bot in bots:
        for base in BASELINES:
            already = store.count_pair(bot, base)
            rows += _balanced_rows(bot, base, n_each, already, seed_base=hash((bot, base)) % 1_000_000)
    return store.add_matches(rows)


def enqueue_baselines_for_bot(store: TournamentStore, bot_id: str, *, n_each: int = 10,
                              reset: bool = True) -> int:
    """Schedule one bot's baseline evaluation. Returns rows added.

    This is the auto-evaluation a fresh submission triggers, so it stays
    deliberately small: *one* competitor against the baselines only, never the
    all-pairs round robin.  With ``reset`` (the default) the bot's existing
    matches are purged first -- a resubmission supersedes the previous version
    rather than topping up its row count, which both keeps the standings honest
    and stops repeat uploads from stacking queue depth.
    """
    rows: List[Tuple[str, str, int, int]] = []
    if reset:
        store.purge_matches(bot_id)
    for base in BASELINES:
        already = 0 if reset else store.count_pair(bot_id, base)
        rows += _balanced_rows(bot_id, base, n_each, already,
                               seed_base=hash((bot_id, base)) % 1_000_000)
    return store.add_matches(rows)


def enqueue_full_round_robin(store: TournamentStore, *, n_each: int = 10,
                             registry: Optional[BotRegistry] = None) -> int:
    """Schedule the all-pairs round robin over active participant bots."""
    bots = sorted(runnable_bots(store, registry))
    rows: List[Tuple[str, str, int, int]] = []
    for a_id, b_id in itertools.combinations(bots, 2):
        already = store.count_pair(a_id, b_id)
        rows += _balanced_rows(a_id, b_id, n_each, already, seed_base=hash((a_id, b_id)) % 1_000_000)
    return store.add_matches(rows)
