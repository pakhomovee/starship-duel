"""Balance analysis from a trained policy: first-mover advantage + action usage.

Plays mirror self-play (the same policy in both seats, sampling so games vary)
and reports, per map:

  * **first-mover win rate** with a Wilson 95% interval -- the number the per-map
    ``komi`` in :mod:`starship_duel.game.maps` is calibrated against (target ~50%);
  * **action usage** -- the share of decisions spent on each action, which surfaces
    dead abilities (e.g. SCAN / PROXIMITY / JAMMING going unused);
  * **end-reason** mix and mean game length.

Because self-play reward is ~0 by construction, this is *not* a strength signal --
it is a **meta probe** for balancing.  Sweep komi without touching ``maps.py`` via
``--komi <map>=<dom>,<energy>`` (mutates the in-memory map for the run only).

    python -m starship_duel.rl.balance --checkpoint ckpt_final.pt --games 150
    python -m starship_duel.rl.balance -c ckpt_final.pt --komi reference=4,8 --komi map2=6,11

Works with a universal (GNN) checkpoint (plays every map) or a single-map one
(that map only).
"""

from __future__ import annotations

import argparse
import collections
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..game import Engine, GameConfig, build_observation
from ..game.maps import MAPS, get_map


def wilson(wins: int, n: int, z: float = 1.96) -> Tuple[float, float, float]:
    """Return ``(p_hat, lo, hi)`` -- the point estimate and Wilson 95% interval."""
    if n == 0:
        return 0.0, 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return p, max(0.0, center - half), min(1.0, center + half)


@dataclass
class MapReport:
    map_id: str
    games: int = 0
    first_wins: int = 0        # games won by whoever moved first
    draws: int = 0
    actions: collections.Counter = field(default_factory=collections.Counter)
    end_reasons: collections.Counter = field(default_factory=collections.Counter)
    total_plies: int = 0

    @property
    def first_win_rate(self) -> float:
        decided = self.games - self.draws
        return self.first_wins / decided if decided else 0.0

    def komi_suggestion(self, gmap) -> Optional[str]:
        """A directional komi nudge toward a 50% first-mover rate.

        Heuristic, not a solve: move ``komi_domination`` by roughly the point gap
        implied by the win-rate error, in the direction that helps the *disadvantaged*
        mover.  Re-measure after applying; a couple of iterations converge.
        """
        decided = self.games - self.draws
        p, lo, hi = wilson(self.first_wins, decided)
        if lo <= 0.5 <= hi:
            return None  # 50% is inside the CI -> balanced within noise
        cur = gmap.komi_domination if gmap.komi_domination is not None else "cfg"
        # ~16 domination points swing the race end-to-end; scale the gap by that.
        delta = round((p - 0.5) * 16)
        if delta == 0:
            delta = 1 if p > 0.5 else -1
        direction = "raise" if delta > 0 else "lower"
        return (f"first-mover {'over' if p > 0.5 else 'under'}-favored "
                f"({p*100:.0f}%); {direction} second-mover komi_domination "
                f"by ~{abs(delta)} (now {cur})")


def _make_policy(checkpoint: str, deterministic: bool, seed: int):
    """Load a checkpoint as a Bot (universal if possible, else single-map)."""
    from ..bots.ppo_bot import PpoBot, UniversalPpoBot
    try:
        return UniversalPpoBot.from_checkpoint(checkpoint, deterministic=deterministic, seed=seed)
    except (ValueError, KeyError):
        return PpoBot.from_checkpoint(checkpoint, deterministic=deterministic, seed=seed)


def _play(eng: Engine, p0, p1, rep: "MapReport", first: int, map_id: str,
          seed: int, max_plies: int) -> Optional[int]:
    """Play one game to completion, tallying actions/end-reason into ``rep``.
    Returns the winning seat (0/1) or None for a draw."""
    eng.reset(map_id=map_id, first_ship=first)
    p0.reset(); p1.reset()
    plies = 0
    while not eng.is_terminal() and plies < max_plies:
        s = eng.current_ship
        obs = build_observation(eng, s)
        a = (p0 if s == 0 else p1).act(obs)
        rep.actions[a.type.name] += 1
        eng.apply_action(a)
        plies += 1
    st = eng.state
    rep.games += 1
    rep.total_plies += plies
    rep.end_reasons[st.end_reason or "draw"] += 1
    return st.winner


def analyze(checkpoint: str, *, games: int = 150, maps: Optional[List[str]] = None,
            config: Optional[GameConfig] = None, base_seed: int = 1000,
            deterministic: bool = False, max_plies: int = 4000) -> Dict[str, MapReport]:
    # Reproducibility: the policy samples from torch's *global* RNG, so seed it (and
    # numpy) or two runs of the same command disagree. Spawn/cache luck dominates
    # the first-mover signal, so each game seed is also played in BOTH orderings
    # (antithetic pairing) -- a far lower-variance estimate of who the turn order
    # actually favours than independent games.
    import numpy as np
    import torch
    torch.manual_seed(base_seed)
    np.random.seed(base_seed & 0xFFFFFFFF)

    cfg = config or GameConfig()
    p0 = _make_policy(checkpoint, deterministic, seed=1)
    p1 = _make_policy(checkpoint, deterministic, seed=2)
    map_ids = maps or [m.id for m in MAPS]
    reports: Dict[str, MapReport] = {}
    eng = Engine(config=cfg, seed=base_seed)
    for map_id in map_ids:
        rep = MapReport(map_id=map_id)
        pairs = max(1, games // 2)
        for i in range(pairs):
            seed = base_seed + i
            for first in (0, 1):  # same spawn/cache seed, both turn orders
                eng.rng.seed(seed)
                winner = _play(eng, p0, p1, rep, first, map_id, seed, max_plies)
                if winner is None:
                    rep.draws += 1
                elif winner == first:
                    rep.first_wins += 1
        reports[map_id] = rep
    return reports


def _print_report(reports: Dict[str, MapReport]) -> None:
    total_actions: collections.Counter = collections.Counter()
    total_ends: collections.Counter = collections.Counter()
    tot_first = tot_decided = tot_games = tot_plies = 0

    print(f"\n{'map':10} {'games':>6} {'first-win%':>11} {'95% CI':>15} {'draws':>6} "
          f"{'komi(d,e)':>10}")
    print("-" * 70)
    for map_id, rep in reports.items():
        gmap = get_map(map_id)
        decided = rep.games - rep.draws
        p, lo, hi = wilson(rep.first_wins, decided)
        ci = f"[{lo*100:.0f},{hi*100:.0f}]"
        komi = f"{gmap.komi_domination},{gmap.komi_energy}"
        print(f"{map_id:10} {rep.games:6d} {p*100:11.1f} {ci:>15} {rep.draws:6d} {komi:>10}")
        tot_first += rep.first_wins; tot_decided += decided
        tot_games += rep.games; tot_plies += rep.total_plies
        total_actions.update(rep.actions); total_ends.update(rep.end_reasons)

    p, lo, hi = wilson(tot_first, tot_decided)
    print("-" * 70)
    print(f"{'OVERALL':10} {tot_games:6d} {p*100:11.1f} {f'[{lo*100:.0f},{hi*100:.0f}]':>15}")

    print("\nkomi suggestions (toward 50% first-mover):")
    any_sug = False
    for map_id, rep in reports.items():
        sug = rep.komi_suggestion(get_map(map_id))
        if sug:
            any_sug = True
            print(f"  {map_id:10} {sug}")
    if not any_sug:
        print("  all maps within the 50% CI -- balanced.")

    print("\naction usage (share of all decisions):")
    tot = sum(total_actions.values()) or 1
    for name, c in total_actions.most_common():
        flag = "   <-- ~unused" if c / tot < 0.01 else ""
        print(f"  {name:28} {100*c/tot:6.2f}%  ({c}){flag}")

    print("\nend reasons:")
    for r, c in total_ends.most_common():
        print(f"  {r:14} {c}")
    print(f"\nmean game length: {tot_plies/max(tot_games,1):.1f} plies\n")


def _apply_komi_overrides(overrides: List[str]) -> None:
    for ov in overrides:
        try:
            map_id, vals = ov.split("=", 1)
            dom, energy = (int(x) for x in vals.split(","))
        except ValueError:
            raise SystemExit(f"bad --komi {ov!r}; expected <map>=<dom>,<energy>")
        gmap = get_map(map_id.strip())
        # GameMap is a frozen dataclass; override in place for this run only.
        object.__setattr__(gmap, "komi_domination", dom)
        object.__setattr__(gmap, "komi_energy", energy)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Policy-driven balance analysis")
    ap.add_argument("-c", "--checkpoint", default="ckpt_final.pt")
    ap.add_argument("-n", "--games", type=int, default=150, help="games per map")
    ap.add_argument("--maps", nargs="*", default=None, help="subset of map ids")
    ap.add_argument("--komi", action="append", default=[],
                    help="override a map's komi for this run: <map>=<dom>,<energy>")
    ap.add_argument("--deterministic", action="store_true",
                    help="greedy policy (identical games; usually leave off)")
    ap.add_argument("--seed", type=int, default=1000)
    args = ap.parse_args(argv)

    _apply_komi_overrides(args.komi)
    reports = analyze(args.checkpoint, games=args.games, maps=args.maps,
                      base_seed=args.seed, deterministic=args.deterministic)
    _print_report(reports)


if __name__ == "__main__":
    main()
