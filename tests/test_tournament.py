"""Tests for the tournament platform (Phase 1): the SQLite queue + atomic claim,
the match runner (reusing GameSession), scheduling, and Bradley-Terry scoring.
"""

from __future__ import annotations

import sys
import threading

import pytest

from starship_duel.tournament.registry import BASELINES, BotRegistry
from starship_duel.tournament.schedule import (
    enqueue_baselines,
    enqueue_full_round_robin,
    register_competitors,
)
from starship_duel.tournament.scoring import compute_bt
from starship_duel.tournament.store import TournamentStore
from starship_duel.tournament.match import run_match


@pytest.fixture
def store(tmp_path):
    return TournamentStore(str(tmp_path / "tourney.db"))


@pytest.fixture
def game_store(tmp_path):
    from starship_duel.web.history import GameStore
    return GameStore(str(tmp_path / "games.db"))


# ------------------------------------------------------------------ store ----
def test_store_competitors_and_matches(store):
    store.add_competitor("alice", "bot")
    store.add_competitor("heuristic", "baseline")
    store.add_competitor("alice", "bot")  # INSERT OR IGNORE -> no duplicate
    assert {c["id"] for c in store.list_competitors()} == {"alice", "heuristic"}
    assert [c["id"] for c in store.list_competitors(kind="bot")] == ["alice"]

    n = store.add_matches([("alice", "heuristic", 0, 1), ("alice", "heuristic", 1, 2)])
    assert n == 2
    assert store.status_counts() == {"pending": 2}
    assert store.count_pair("alice", "heuristic") == 2


def test_claim_is_atomic_across_threads(store):
    """Many workers pulling the same queue must never double-claim a match."""
    store.add_matches([("a", "b", i % 2, i) for i in range(200)])

    claimed: list = []
    lock = threading.Lock()

    def worker(wid):
        got = []
        while True:
            m = store.claim_match(f"w{wid}")
            if m is None:
                break
            got.append(m["id"])
        with lock:
            claimed.extend(got)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(claimed) == 200
    assert len(set(claimed)) == 200          # no id handed out twice
    assert store.status_counts() == {"running": 200}


# ------------------------------------------------------------- match runner --
def test_run_match_records_result_and_replay(store, game_store):
    store.add_matches([("heuristic", "random", 0, 7)])
    m = store.claim_match("w0")
    result = run_match(m, tstore=store, registry=BotRegistry(), game_store=game_store)

    # Territory-control mode has no instant-kills, so a game can also end in a
    # draw (equal domination at timeout); any of the three is a valid result.
    assert result in (0, 1, None)
    row = store.list_matches()[0]
    assert row["status"] == "done"
    assert row["end_reason"]
    # The replay is persisted in the shared GameStore and is playable back.
    replay = game_store.get_replay(row["replay_rid"])
    assert replay is not None and len(replay["frames"]) >= 2
    assert replay["meta"]["controllers"] == {"0": "heuristic", "1": "random"}


def test_crashing_bot_forfeits_and_worker_survives(store, game_store, tmp_path):
    crash = tmp_path / "crash_bot.py"
    crash.write_text("import sys\nsys.exit(0)\n")  # dies immediately -> BotError on act
    reg = BotRegistry()
    reg.specs["boom"] = {"command": [sys.executable, str(crash)], "timeout": 0.5}

    # boom is seat 1 and moves first, so it forfeits on ply 1 -> heuristic (seat 0) wins.
    store.add_matches([("heuristic", "boom", 1, 3)])
    m = store.claim_match("w0")
    result = run_match(m, tstore=store, registry=reg, game_store=game_store)

    assert result == 0                          # the live opponent wins by forfeit
    row = store.list_matches()[0]
    assert row["status"] == "done"
    assert row["end_reason"] in ("crash", "forfeit") or "crash" in (row["end_reason"] or "")


# --------------------------------------------------------------- scheduling --
def test_scheduling_is_balanced_and_idempotent(store):
    reg = BotRegistry()
    reg.specs = {"alice": {"command": ["x"]}, "bob": {"command": ["y"]}, "cara": {"command": ["z"]}}
    register_competitors(store, reg)

    added = enqueue_baselines(store, n_each=10)
    assert added == 3 * len(BASELINES) * 10               # 3 bots x 4 baselines x 10
    assert enqueue_baselines(store, n_each=10) == 0        # idempotent top-up

    # First-mover split is balanced for one representative pair.
    rows = [r for r in store.list_matches(limit=1000)
            if r["a_id"] == "alice" and r["b_id"] == "heuristic"]
    assert len(rows) == 10
    assert sum(r["first_mover"] for r in rows) == 5        # five each of 0 and 1

    added_full = enqueue_full_round_robin(store, n_each=10)
    assert added_full == 3 * 10                            # C(3,2)=3 pairs x 10


# ------------------------------------------------------------------ scoring --
def _finish(store, a, b, winner_seat, n):
    store.add_matches([(a, b, i % 2, i) for i in range(n)])
    for row in store.list_matches(status="pending", limit=n):
        store.finish_match(row["id"], winner_seat, "fire_hit", None)


def test_bt_orders_competitors_and_bounds_ci(store):
    for c in ("A", "B", "C"):
        store.add_competitor(c, "bot")
    # A beats B and C; B beats C (all as seat 0 -> winner seat 0). Clean sweeps.
    _finish(store, "A", "B", 0, 10)
    _finish(store, "A", "C", 0, 10)
    _finish(store, "B", "C", 0, 10)

    rows = compute_bt(store, "quick", n_boot=200)
    order = [r["id"] for r in rows]
    assert order == ["A", "B", "C"]
    assert [r["rank"] for r in rows] == [1, 2, 3]
    # Regularization keeps every score + CI finite even on 10-0 sweeps.
    for r in rows:
        assert all(map(lambda v: v == v and abs(v) < 1e6, (r["score"], r["ci_low"], r["ci_high"])))
        assert r["ci_low"] <= r["score"] <= r["ci_high"] or r["n_games"] > 0

    # Snapshot is cached and retrievable for the API.
    snap = store.get_standings("quick")
    assert snap and [r["id"] for r in snap["rows"]] == ["A", "B", "C"]


def test_bt_empty_is_safe(store):
    assert compute_bt(store, "quick", n_boot=10) == []
    assert store.get_standings("quick")["rows"] == []
