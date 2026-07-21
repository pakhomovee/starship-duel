"""Match worker: pull claimed jobs from the queue and play them.

Run one or more of these next to the API (``python -m starship_duel.tournament.worker
--workers 4``).  The atomic claim in :meth:`TournamentStore.claim_match` makes
several worker *threads* -- and several worker *processes* -- safe to run at once;
matches are never double-played.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import threading
import time
from typing import Optional

from ..game import GameConfig
from ..web.history import GameStore
from .accounts import AccountStore
from .match import run_match
from .registry import BotRegistry
from .store import TournamentStore

_log = logging.getLogger(__name__)


class SpecRefresher:
    """Reload the launch allowlist when a claimed match names a competitor this
    worker has never heard of.

    Workers are long-lived, and the API materializes a newly validated
    submission in *its own* process -- so without this, every match for a bot
    submitted after the worker booted dies on ``no launch spec for bot
    competitor ...``.  That is not hypothetical: it is exactly what a queue
    filled by submission-time auto-evaluation looks like.

    Reloads are rate-limited and shared by all worker threads, because
    :func:`~starship_duel.tournament.accounts.materialize_active` rewrites every
    bot directory (and may recompile C++).  A competitor that stays unknown --
    a deleted account, say -- therefore costs one reload per interval, not one
    per match, and its matches still fail fast afterwards.
    """

    def __init__(self, registry: BotRegistry, *, min_interval: float = 5.0):
        self.registry = registry
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def _known(self, cid: str) -> bool:
        return self.registry.kind(cid) == "baseline" or cid in self.registry.specs

    def ensure(self, match: dict) -> None:
        if all(self._known(cid) for cid in (match["a_id"], match["b_id"])):
            return
        with self._lock:
            # Re-check inside the lock: a sibling thread may have just reloaded
            # and brought this competitor in.
            if all(self._known(cid) for cid in (match["a_id"], match["b_id"])):
                return
            now = time.time()
            if now - self._last < self.min_interval:
                return
            self._last = now
            self.registry.reload()


class StandingsPublisher:
    """Refresh the live standings snapshot when the queue drains.

    A submission's placing then appears as soon as its matches finish, instead
    of at the next cron tick.  Deliberately ``n_boot=0``: the fit is ~0.2s even
    at 50 competitors, while bootstrapping the intervals takes *minutes* and
    would cost far more CPU than playing the matches did.  Intervals are carried
    from the last full recompute (the cron ``tick`` / the admin endpoint) and
    flagged stale until it runs again.

    Shared by every thread in the process: when the queue empties they all go
    idle at once, and one snapshot for that drain is enough.
    """

    def __init__(self, tstore: TournamentStore, *, scope: str = "quick",
                 min_interval: float = 2.0):
        self.tstore = tstore
        self.scope = scope
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def publish(self, *, force: bool = False) -> bool:
        """Recompute unless a sibling thread just did. Returns True if it ran."""
        from .scoring import compute_bt

        with self._lock:
            now = time.time()
            if not force and now - self._last < self.min_interval:
                return False
            self._last = now
            try:
                compute_bt(self.tstore, self.scope, n_boot=0, carry_ci=True)
                return True
            except Exception:
                # Never let a scoring failure kill a worker: matches are already
                # recorded, and the next drain (or the cron tick) republishes.
                _log.exception("live standings recompute failed")
                return False


def run_worker(worker_id: str, *, tstore: TournamentStore, registry: BotRegistry,
               game_store: GameStore, config: Optional[GameConfig] = None,
               max_matches: Optional[int] = None, idle_sleep: float = 0.5,
               stop_when_empty: bool = False,
               refresher: Optional[SpecRefresher] = None,
               publisher: Optional["StandingsPublisher"] = None,
               publish: bool = True) -> int:
    """Claim-and-play loop. Returns the number of matches this worker handled."""
    handled = 0
    if refresher is None:
        refresher = SpecRefresher(registry)
    if publisher is None:
        publisher = StandingsPublisher(tstore)
    # Matches played since the standings were last published. Recomputing on
    # *drain* rather than per match coalesces a whole submission's batch (and
    # any concurrent ones) into a single fit.
    unpublished = 0
    while max_matches is None or handled < max_matches:
        match = tstore.claim_match(worker_id)
        if match is None:
            if publish and unpublished:
                publisher.publish()
                unpublished = 0
            if stop_when_empty:
                break
            time.sleep(idle_sleep)
            continue
        try:
            refresher.ensure(match)  # pick up bots submitted since we started
            run_match(match, tstore=tstore, registry=registry,
                      game_store=game_store, config=config)
        except Exception as e:  # orchestration failure -> mark error, keep going
            tstore.fail_match(match["id"], f"{type(e).__name__}: {e}")
        handled += 1
        unpublished += 1
    if publish and unpublished:
        publisher.publish(force=True)  # stopped mid-queue: publish what we played
    return handled


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Starship Duel tournament match worker")
    ap.add_argument("--db", default=None, help="tournament sqlite path ($STARSHIP_TOURNEY_DB)")
    ap.add_argument("--games-db", default=None, help="replay sqlite path ($STARSHIP_GAMES_DB)")
    ap.add_argument("--accounts-db", default=None, help="accounts sqlite path ($STARSHIP_ACCOUNTS_DB)")
    ap.add_argument("--bots", default=None, help="bot allowlist json ($STARSHIP_TOURNEY_BOTS)")
    ap.add_argument("--submissions-dir", default=None, help="where to materialize submission bots")
    ap.add_argument("--workers", type=int, default=1, help="concurrent worker threads")
    ap.add_argument("--max", type=int, default=None, help="stop after this many matches (per worker)")
    ap.add_argument("--once", action="store_true", help="exit when the queue is empty")
    ap.add_argument("--no-publish", action="store_true",
                    help="don't refresh live standings when the queue drains")
    args = ap.parse_args(argv)

    tstore = TournamentStore(args.db)
    game_store = GameStore(args.games_db)
    # Wire the AccountStore so validated submissions resolve to runnable bots
    # (the same competitors the API scheduled).
    registry = BotRegistry(args.bots, account_store=AccountStore(args.accounts_db),
                           submissions_dir=args.submissions_dir)
    base = f"{socket.gethostname()}:{os.getpid()}"
    # One refresher for the whole process: the threads share a registry, so they
    # should share the reload rate limit too.
    refresher = SpecRefresher(registry)
    publisher = StandingsPublisher(tstore)

    threads = []
    counts = {}
    for i in range(max(1, args.workers)):
        wid = f"{base}:{i}"
        def _run(wid=wid):
            counts[wid] = run_worker(
                wid, tstore=tstore, registry=registry, game_store=game_store,
                max_matches=args.max, stop_when_empty=args.once,
                refresher=refresher, publisher=publisher,
                publish=not args.no_publish,
            )
        t = threading.Thread(target=_run, name=wid, daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    total = sum(counts.values())
    print(f"workers done: {counts}  (total {total} matches)")
    print(f"queue status: {tstore.status_counts()}")


if __name__ == "__main__":
    main()
