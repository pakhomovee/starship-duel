"""Match worker: pull claimed jobs from the queue and play them.

Run one or more of these next to the API (``python -m starship_duel.tournament.worker
--workers 4``).  The atomic claim in :meth:`TournamentStore.claim_match` makes
several worker *threads* -- and several worker *processes* -- safe to run at once;
matches are never double-played.
"""

from __future__ import annotations

import argparse
import os
import socket
import threading
import time
from typing import Optional

from ..game import GameConfig
from ..web.history import GameStore
from .match import run_match
from .registry import BotRegistry
from .store import TournamentStore


def run_worker(worker_id: str, *, tstore: TournamentStore, registry: BotRegistry,
               game_store: GameStore, config: Optional[GameConfig] = None,
               max_matches: Optional[int] = None, idle_sleep: float = 0.5,
               stop_when_empty: bool = False) -> int:
    """Claim-and-play loop. Returns the number of matches this worker handled."""
    handled = 0
    while max_matches is None or handled < max_matches:
        match = tstore.claim_match(worker_id)
        if match is None:
            if stop_when_empty:
                break
            time.sleep(idle_sleep)
            continue
        try:
            run_match(match, tstore=tstore, registry=registry,
                      game_store=game_store, config=config)
        except Exception as e:  # orchestration failure -> mark error, keep going
            tstore.fail_match(match["id"], f"{type(e).__name__}: {e}")
        handled += 1
    return handled


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Starship Duel tournament match worker")
    ap.add_argument("--db", default=None, help="tournament sqlite path ($STARSHIP_TOURNEY_DB)")
    ap.add_argument("--games-db", default=None, help="replay sqlite path ($STARSHIP_GAMES_DB)")
    ap.add_argument("--bots", default=None, help="bot allowlist json ($STARSHIP_TOURNEY_BOTS)")
    ap.add_argument("--workers", type=int, default=1, help="concurrent worker threads")
    ap.add_argument("--max", type=int, default=None, help="stop after this many matches (per worker)")
    ap.add_argument("--once", action="store_true", help="exit when the queue is empty")
    args = ap.parse_args(argv)

    tstore = TournamentStore(args.db)
    game_store = GameStore(args.games_db)
    registry = BotRegistry(args.bots)
    base = f"{socket.gethostname()}:{os.getpid()}"

    threads = []
    counts = {}
    for i in range(max(1, args.workers)):
        wid = f"{base}:{i}"
        def _run(wid=wid):
            counts[wid] = run_worker(
                wid, tstore=tstore, registry=registry, game_store=game_store,
                max_matches=args.max, stop_when_empty=args.once,
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
