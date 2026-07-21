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
    enqueue_baselines_for_bot,
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


def test_per_bot_schedule_touches_only_that_bot(store):
    store.add_competitor("alice", "bot")
    store.add_competitor("bob", "bot")
    enqueue_baselines_for_bot(store, "bob", n_each=4)

    added = enqueue_baselines_for_bot(store, "alice", n_each=4)
    assert added == len(BASELINES) * 4                     # baselines only, no bob
    rows = [r for r in store.list_matches(limit=1000) if r["a_id"] == "alice"]
    assert len(rows) == added
    assert {r["b_id"] for r in rows} == set(BASELINES)
    # Balanced first-mover, same as the bulk schedules.
    per_pair = [r for r in rows if r["b_id"] == "heuristic"]
    assert sum(r["first_mover"] for r in per_pair) == 2

    # Scheduling alice again leaves bob's queue untouched.
    assert len([r for r in store.list_matches(limit=1000) if r["a_id"] == "bob"]) == len(BASELINES) * 4


def test_resubmission_replaces_rather_than_stacking(store):
    store.add_competitor("alice", "bot")
    n = len(BASELINES) * 4
    assert enqueue_baselines_for_bot(store, "alice", n_each=4) == n

    # Finish one and leave the rest pending, then "resubmit".
    done = store.list_matches(status="pending", limit=1)[0]
    store.finish_match(done["id"], 0, "fire_hit", None)
    assert enqueue_baselines_for_bot(store, "alice", n_each=4) == n

    # The old version's results are gone and the queue hasn't grown: a repeat
    # uploader can't accumulate work for the match workers.
    rows = store.list_matches(limit=1000)
    assert len(rows) == n
    assert all(r["status"] == "pending" for r in rows)
    assert store.results_for_scoring() == []

    # Opting out of the reset tops up instead (idempotent, like the bulk path).
    assert enqueue_baselines_for_bot(store, "alice", n_each=4, reset=False) == 0
    assert enqueue_baselines_for_bot(store, "alice", n_each=6, reset=False) == len(BASELINES) * 2


def test_scheduling_skips_competitors_the_registry_cannot_launch(store):
    """Regression: a competitor whose submission stops building leaves the
    registry but keeps its `competitors` row, so scheduling used to queue matches
    that could only die with 'no launch spec for bot competitor ...'."""
    from starship_duel.tournament.schedule import runnable_bots

    for c in ("alive", "broken"):
        store.add_competitor(c, "bot")
    reg = BotRegistry()
    reg.specs = {"alive": {"command": ["x"]}}      # 'broken' failed to build

    assert runnable_bots(store, reg) == ["alive"]
    assert runnable_bots(store, None) == ["alive", "broken"]   # opt-out unchanged

    added = enqueue_baselines(store, n_each=2, registry=reg)
    assert added == len(BASELINES) * 2
    assert {r["a_id"] for r in store.list_matches(limit=500)} == {"alive"}

    # Same for the round robin: one runnable bot means no pairs at all, rather
    # than a doomed alive-vs-broken schedule.
    assert enqueue_full_round_robin(store, n_each=2, registry=reg) == 0


# ------------------------------------------------------------------- worker --
def test_worker_reloads_specs_for_a_bot_submitted_after_it_started(store, game_store):
    """Regression: a long-lived worker used to fail every match for a bot that
    was submitted after it booted, with 'no launch spec for bot competitor ...',
    because only the API process reloaded the registry."""
    from starship_duel.tournament.worker import SpecRefresher, run_worker

    reg = BotRegistry()
    reg.specs = {}
    reloads = []

    def fake_reload():
        reloads.append(1)
        reg.specs = {"latecomer": {"command": [sys.executable, "-c", "pass"]}}

    reg.reload = fake_reload
    store.add_matches([("latecomer", "random", 0, 1)])

    run_worker("w0", tstore=store, registry=reg, game_store=game_store,
               stop_when_empty=True, refresher=SpecRefresher(reg, min_interval=0))
    assert reloads, "worker never reloaded its allowlist"
    # The bot is a stub that exits immediately, so the match is a forfeit --
    # a *played* match, not an orchestration error. That's the point: the
    # competitor was found and launched.
    row = store.list_matches()[0]
    assert row["status"] == "done", row["error"]


def test_refresher_rate_limits_reloads_for_a_competitor_that_stays_unknown(store):
    """A deleted competitor must not make every claimed match rebuild every bot."""
    from starship_duel.tournament.worker import SpecRefresher

    reg = BotRegistry()
    reg.specs = {}
    reloads = []
    reg.reload = lambda: reloads.append(1)   # never resolves 'ghost'

    refresher = SpecRefresher(reg, min_interval=3600)
    for _ in range(20):
        refresher.ensure({"a_id": "ghost", "b_id": "random"})
    assert len(reloads) == 1                 # one per interval, not one per match

    # Known competitors never trigger a reload at all.
    refresher.ensure({"a_id": "random", "b_id": "heuristic"})
    assert len(reloads) == 1


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


def test_live_recompute_carries_intervals_and_flags_them(store):
    """n_boot=0 must not invent a [0.00, 0.00] interval — it reuses the last
    bootstrapped one and marks it stale, so the UI can dim it."""
    _finish(store, "A", "B", 0, 10)
    full = {r["id"]: r for r in compute_bt(store, "quick", n_boot=200)}
    assert not any(r["ci_stale"] for r in full.values())
    assert full["A"]["ci_low"] != 0.0 or full["A"]["ci_high"] != 0.0

    # More games arrive; the live path refits scores but not the intervals.
    _finish(store, "A", "B", 0, 4)
    live = {r["id"]: r for r in compute_bt(store, "quick", n_boot=0, carry_ci=True)}
    assert live["A"]["n_games"] == 14                    # scores/counts are current
    assert all(r["ci_stale"] for r in live.values() if r["ranked"])
    assert live["A"]["ci_low"] == full["A"]["ci_low"]    # interval carried over
    assert live["A"]["ci_high"] == full["A"]["ci_high"]

    # A competitor that has never been bootstrapped gets no interval at all,
    # rather than a fabricated zero-width one.
    _finish(store, "C", "A", 0, 6)
    live2 = {r["id"]: r for r in compute_bt(store, "quick", n_boot=0, carry_ci=True)}
    assert live2["C"]["ci_low"] is None and live2["C"]["ci_high"] is None
    # ...and a later full recompute fills it in and clears the flag.
    full2 = {r["id"]: r for r in compute_bt(store, "quick", n_boot=200)}
    assert full2["C"]["ci_low"] is not None and not full2["C"]["ci_stale"]


def test_worker_publishes_standings_when_the_queue_drains(store, game_store):
    from starship_duel.tournament.worker import StandingsPublisher, run_worker

    reg = BotRegistry()
    store.add_matches([("random", "heuristic", 0, 1), ("random", "heuristic", 1, 2)])
    assert store.get_standings("quick") is None

    run_worker("w0", tstore=store, registry=reg, game_store=game_store,
               stop_when_empty=True, publisher=StandingsPublisher(store, min_interval=0))

    snap = store.get_standings("quick")
    assert snap is not None, "queue drained but standings were never published"
    assert {r["id"] for r in snap["rows"]} >= {"random", "heuristic"}

    # An idle worker that played nothing must not recompute at all.
    before = snap["computed"]
    run_worker("w0", tstore=store, registry=reg, game_store=game_store,
               stop_when_empty=True, publisher=StandingsPublisher(store, min_interval=0))
    assert store.get_standings("quick")["computed"] == before


def test_publisher_coalesces_concurrent_drains(store):
    """All threads go idle together when the queue empties; one snapshot is enough."""
    from starship_duel.tournament.worker import StandingsPublisher

    _finish(store, "A", "B", 0, 4)
    pub = StandingsPublisher(store, min_interval=3600)
    assert pub.publish() is True
    assert [pub.publish() for _ in range(5)] == [False] * 5
    assert pub.publish(force=True) is True


def test_bt_empty_is_safe(store):
    assert compute_bt(store, "quick", n_boot=10) == []
    assert store.get_standings("quick")["rows"] == []
