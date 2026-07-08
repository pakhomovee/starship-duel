"""Recompute and publish the Bradley-Terry standings.

Wire this to cron every 6h for live "current standings" during the contest::

    0 */6 * * *  cd /srv/starship && python -m starship_duel.tournament.tick

The admin ``POST /api/tournament/recompute`` endpoint calls the same
:func:`starship_duel.tournament.scoring.compute_bt`.  ``--scope full`` is the
post-deadline all-pairs ranking (identical math, just more finished matches).
"""

from __future__ import annotations

import argparse

from .scoring import compute_bt
from .store import TournamentStore


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Recompute tournament Bradley-Terry standings")
    ap.add_argument("--db", default=None, help="tournament sqlite path ($STARSHIP_TOURNEY_DB)")
    ap.add_argument("--scope", choices=["quick", "full"], default="quick")
    ap.add_argument("--n-boot", type=int, default=1000, help="bootstrap resamples for CIs")
    args = ap.parse_args(argv)

    store = TournamentStore(args.db)
    rows = compute_bt(store, args.scope, n_boot=args.n_boot)
    print(f"[{args.scope}] standings over {store.status_counts().get('done', 0)} finished matches:")
    for r in rows:
        print(f"  #{r['rank']:>2}  {r['id']:<16} score={r['score']:+.3f} "
              f"[{r['ci_low']:+.3f}, {r['ci_high']:+.3f}]  "
              f"{r['wins']}-{r['losses']} ({r['n_games']} games)")
    if not rows:
        print("  (no decisive matches yet)")


if __name__ == "__main__":
    main()
