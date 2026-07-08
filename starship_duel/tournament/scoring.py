"""Bradley-Terry ranking with bootstrap confidence intervals.

We fit a regularized BT model over the pairwise win records using
:func:`choix.opt_pairwise` (the ``alpha`` prior keeps scores finite even on a
clean 10-0 sweep).  A nonparametric bootstrap -- resample the individual match
outcomes with replacement, refit ``n_boot`` times -- gives a per-competitor CI.

``compute_bt`` is scope-agnostic: ``quick`` (during the contest) and ``full``
(post-deadline) run the *same* code over whatever finished matches exist; only
the set of scheduled matches differs.
"""

from __future__ import annotations

import random
from typing import List, Optional, Tuple

import choix
import numpy as np

from .store import TournamentStore

Comparison = Tuple[int, int]  # (winner_index, loser_index)


def _decisive(results: List[Tuple[str, str, Optional[int]]]) -> List[Tuple[str, str]]:
    """Reduce (a_id, b_id, seat_result) rows to (winner_id, loser_id), dropping draws."""
    out = []
    for a_id, b_id, res in results:
        if res is None:
            continue
        out.append((a_id, b_id) if res == 0 else (b_id, a_id))
    return out


def _fit(n: int, comparisons: List[Comparison], alpha: float) -> np.ndarray:
    if not comparisons:
        return np.zeros(n)
    return choix.opt_pairwise(n, comparisons, alpha=alpha)


def compute_bt(store: TournamentStore, scope: str, *, alpha: float = 0.05,
               n_boot: int = 1000, ci: float = 0.90,
               seed: Optional[int] = 0) -> List[dict]:
    """Fit BT over all finished matches and return ranked standings rows.

    Each row: ``{id, score, rank, ci_low, ci_high, n_games, wins, losses}``.
    Also persisted to the ``standings`` table under ``scope``.
    """
    results = store.results_for_scoring()
    wins_by = _decisive(results)

    # Index the competitors that actually appear in decisive games.
    ids = sorted({c for pair in wins_by for c in pair})
    idx = {c: i for i, c in enumerate(ids)}
    n = len(ids)

    # Per-competitor bookkeeping over ALL finished games (incl. draws) for display.
    n_games = {c: 0 for c in ids}
    wins = {c: 0 for c in ids}
    losses = {c: 0 for c in ids}
    for a_id, b_id, res in results:
        for c in (a_id, b_id):
            if c in n_games:
                n_games[c] += 1
        if res is None:
            continue
        w, l = (a_id, b_id) if res == 0 else (b_id, a_id)
        if w in wins:
            wins[w] += 1
        if l in losses:
            losses[l] += 1

    if n == 0:
        store.save_standings(scope, [])
        return []

    comparisons = [(idx[w], idx[l]) for (w, l) in wins_by]
    params = _fit(n, comparisons, alpha)

    # Bootstrap CIs: resample decisive games with replacement, refit.
    lo_q, hi_q = (1 - ci) / 2 * 100, (1 + ci) / 2 * 100
    ci_low = dict.fromkeys(ids, 0.0)
    ci_high = dict.fromkeys(ids, 0.0)
    if comparisons and n_boot > 0:
        rng = random.Random(seed)
        m = len(comparisons)
        samples = np.empty((n_boot, n))
        for b in range(n_boot):
            resampled = [comparisons[rng.randrange(m)] for _ in range(m)]
            samples[b] = _fit(n, resampled, alpha)
        lows = np.percentile(samples, lo_q, axis=0)
        highs = np.percentile(samples, hi_q, axis=0)
        ci_low = {c: float(lows[i]) for c, i in idx.items()}
        ci_high = {c: float(highs[i]) for c, i in idx.items()}

    rows = [{
        "id": c,
        "score": float(params[idx[c]]),
        "ci_low": ci_low[c],
        "ci_high": ci_high[c],
        "n_games": n_games[c],
        "wins": wins[c],
        "losses": losses[c],
    } for c in ids]
    rows.sort(key=lambda r: r["score"], reverse=True)
    for rank, r in enumerate(rows, start=1):
        r["rank"] = rank

    store.save_standings(scope, rows)
    return rows
