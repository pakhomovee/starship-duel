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
               seed: Optional[int] = 0, carry_ci: bool = False) -> List[dict]:
    """Fit BT over all finished matches and return standings rows.

    EVERY active competitor gets a row, even one whose games all drew or errored:
    such a bot is ``ranked=False`` (no decisive result to score) and carries its
    ``errored``/``pending`` counts and ``last_error`` so an author can see *why*
    it isn't competing, instead of silently vanishing from the ladder.

    Each row: ``{id, score, rank, ranked, ci_low, ci_high, ci_stale, n_games,
    wins, losses, draws, errored, pending, last_error}``.  Also persisted under
    ``scope``.

    The fit itself is cheap; the ``n_boot`` bootstrap refits are what cost real
    time (seconds at 5 competitors, minutes at 50), so the live path after each
    batch of matches runs with ``n_boot=0`` and ``carry_ci=True``: scores and
    ranks are current, while each row's interval is carried over from the last
    snapshot that actually bootstrapped and flagged ``ci_stale``.  A competitor
    with no previous interval gets ``ci_low``/``ci_high`` of ``None`` rather than
    a fabricated ``[0.00, 0.00]``.
    """
    results = store.results_for_scoring()
    wins_by = _decisive(results)
    match_stats = store.competitor_match_stats()

    # The row set is every active competitor UNION anyone who has actually played
    # a finished game (keeps historical results visible even if since deactivated).
    active_ids = {c["id"] for c in store.list_competitors(active_only=True)}
    played_ids = {c for (a, b, _) in results for c in (a, b)}
    ids = sorted(active_ids | played_ids)

    # BT is fit only over competitors that appear in a DECISIVE game; the rest get
    # a neutral score and are flagged unranked below.
    decisive_ids = sorted({c for pair in wins_by for c in pair})
    idx = {c: i for i, c in enumerate(decisive_ids)}
    n = len(decisive_ids)

    # Per-competitor bookkeeping over all finished games (incl. draws) for display.
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

    comparisons = [(idx[w], idx[l]) for (w, l) in wins_by]
    params = _fit(n, comparisons, alpha) if n else np.zeros(0)

    # Bootstrap CIs: resample decisive games with replacement, refit.
    lo_q, hi_q = (1 - ci) / 2 * 100, (1 + ci) / 2 * 100
    ci_low = dict.fromkeys(decisive_ids, 0.0)
    ci_high = dict.fromkeys(decisive_ids, 0.0)
    ci_stale = False
    if carry_ci and n_boot <= 0:
        # Reuse the last bootstrapped interval per competitor; anyone new simply
        # has none yet.  Marked stale so the UI doesn't present it as current.
        ci_stale = True
        prev = store.get_standings(scope) or {}
        prev_rows = {r["id"]: r for r in prev.get("rows", []) if not r.get("ci_stale")}
        ci_low = {c: prev_rows.get(c, {}).get("ci_low") for c in decisive_ids}
        ci_high = {c: prev_rows.get(c, {}).get("ci_high") for c in decisive_ids}
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

    rows = []
    for c in ids:
        ranked = c in idx
        ms = match_stats.get(c, {})
        rows.append({
            "id": c,
            "ranked": ranked,
            "score": float(params[idx[c]]) if ranked else 0.0,
            "ci_low": ci_low[c] if ranked else 0.0,
            "ci_high": ci_high[c] if ranked else 0.0,
            "ci_stale": ci_stale and ranked,
            "n_games": n_games[c],
            "wins": wins[c],
            "losses": losses[c],
            "draws": n_games[c] - wins[c] - losses[c],
            "errored": ms.get("error", 0),
            "pending": ms.get("pending", 0) + ms.get("running", 0),
            "last_error": ms.get("last_error"),
        })

    # Ranked competitors first (by score); unranked trail, ordered by games played.
    rows.sort(key=lambda r: (r["ranked"], r["score"], r["n_games"]), reverse=True)
    rank = 0
    for r in rows:
        if r["ranked"]:
            rank += 1
            r["rank"] = rank
        else:
            r["rank"] = None

    store.save_standings(scope, rows)
    return rows
