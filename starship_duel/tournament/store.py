"""SQLite-backed tournament state: competitors, the match queue, and cached
Bradley-Terry standings.

Same lightweight style as :mod:`starship_duel.web.history` (stdlib sqlite3, a
fresh connection per operation, lazy schema), but in **WAL mode** and with an
atomic ``UPDATE ... RETURNING`` job-claim so several *worker processes* can pull
matches concurrently without a double-claim -- the SQLite equivalent of
Postgres' ``SELECT ... FOR UPDATE SKIP LOCKED`` at this scale.

Match rows carry ``a_id`` (ship seat 0) and ``b_id`` (ship seat 1); ``result``
is the winning seat (0 or 1) or NULL for a draw.  ``replay_rid`` links to a row
in the ``web.history`` ``games`` table so the existing replay viewer can play the
game back unchanged.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS competitors (
    id      TEXT PRIMARY KEY,
    kind    TEXT NOT NULL,           -- 'bot' | 'baseline'
    active  INTEGER NOT NULL DEFAULT 1,
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    a_id        TEXT NOT NULL,       -- competitor at ship seat 0
    b_id        TEXT NOT NULL,       -- competitor at ship seat 1
    first_mover INTEGER NOT NULL,    -- which seat (0/1) moves first
    seed        INTEGER,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|error
    worker      TEXT,
    result      INTEGER,             -- winning seat 0/1, NULL = draw
    end_reason  TEXT,
    replay_rid  TEXT,
    created     REAL NOT NULL,
    started     REAL,
    finished    REAL,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS matches_status ON matches (status);
CREATE INDEX IF NOT EXISTS matches_pair   ON matches (a_id, b_id);
CREATE TABLE IF NOT EXISTS standings (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    computed REAL NOT NULL,
    scope    TEXT NOT NULL,          -- 'quick' | 'full'
    payload  TEXT NOT NULL           -- json: [{id, score, rank, ci_low, ci_high, n_games}]
);
CREATE INDEX IF NOT EXISTS standings_scope ON standings (scope, computed DESC);
"""

# How long a busy connection waits for a lock before erroring (ms).
_BUSY_TIMEOUT_MS = 8000


def default_db_path() -> str:
    return os.environ.get("STARSHIP_TOURNEY_DB") or "starship_tournament.db"


class TournamentStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or default_db_path()
        self._lock = threading.Lock()  # guards same-process writers; DB locks guard cross-process

    def _connect(self) -> sqlite3.Connection:
        # autocommit (isolation_level=None) so we control transactions explicitly
        # for the claim; WAL + a busy timeout let concurrent workers coexist.
        conn = sqlite3.connect(self.path, timeout=_BUSY_TIMEOUT_MS / 1000.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        conn.executescript(_SCHEMA)
        return conn

    # -- competitors ---------------------------------------------------------
    def add_competitor(self, cid: str, kind: str, active: bool = True) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO competitors (id, kind, active, created) VALUES (?,?,?,?)",
                (cid, kind, int(active), time.time()),
            )

    def set_active(self, cid: str, active: bool) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE competitors SET active=? WHERE id=?", (int(active), cid))

    def get_competitor(self, cid: str) -> Optional[dict]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM competitors WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None

    def list_competitors(self, *, kind: Optional[str] = None,
                         active_only: bool = False) -> List[dict]:
        q = "SELECT * FROM competitors"
        clauses, args = [], []
        if kind is not None:
            clauses.append("kind=?"); args.append(kind)
        if active_only:
            clauses.append("active=1")
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY id"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(q, args).fetchall()]

    # -- match queue ---------------------------------------------------------
    def add_matches(self, rows: Sequence[Tuple[str, str, int, Optional[int]]]) -> int:
        """Bulk-insert pending matches. Each row: (a_id, b_id, first_mover, seed)."""
        now = time.time()
        payload = [(a, b, int(fm), (None if s is None else int(s)), now) for (a, b, fm, s) in rows]
        if not payload:
            return 0
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO matches (a_id, b_id, first_mover, seed, created) VALUES (?,?,?,?,?)",
                payload,
            )
        return len(payload)

    def count_pair(self, a_id: str, b_id: str) -> int:
        """Matches already scheduled for this *ordered* pair (any status)."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM matches WHERE a_id=? AND b_id=?", (a_id, b_id)
            ).fetchone()[0]

    def claim_match(self, worker: str, *, retries: int = 100) -> Optional[dict]:
        """Atomically take the oldest pending match and mark it running.

        Concurrency-safe across processes: ``BEGIN IMMEDIATE`` serializes writers,
        so two workers never grab the same row.  Returns the claimed row (dict) or
        ``None`` when the queue is empty."""
        for attempt in range(retries):
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "UPDATE matches SET status='running', worker=?, started=? "
                    "WHERE id = (SELECT id FROM matches WHERE status='pending' "
                    "            ORDER BY id LIMIT 1) "
                    "RETURNING *",
                    (worker, time.time()),
                ).fetchone()
                conn.execute("COMMIT")
                return dict(row) if row is not None else None
            except sqlite3.OperationalError as e:
                conn.execute("ROLLBACK") if conn.in_transaction else None
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    time.sleep(0.01 * (attempt + 1))
                    continue
                raise
            finally:
                conn.close()
        return None

    def finish_match(self, mid: int, result: Optional[int], end_reason: Optional[str],
                     replay_rid: Optional[str]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE matches SET status='done', result=?, end_reason=?, replay_rid=?, finished=? "
                "WHERE id=?",
                (None if result is None else int(result), end_reason, replay_rid, time.time(), mid),
            )

    def fail_match(self, mid: int, error: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE matches SET status='error', error=?, finished=? WHERE id=?",
                (str(error)[:2000], time.time(), mid),
            )

    def status_counts(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) c FROM matches GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    def list_matches(self, *, status: Optional[str] = None, limit: int = 500) -> List[dict]:
        q = "SELECT * FROM matches"
        args: list = []
        if status is not None:
            q += " WHERE status=?"; args.append(status)
        q += " ORDER BY id DESC LIMIT ?"; args.append(int(limit))
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(q, args).fetchall()]

    def results_for_scoring(self) -> List[Tuple[str, str, Optional[int]]]:
        """(a_id, b_id, result) for every finished match (result: 0/1 seat, or None)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT a_id, b_id, result FROM matches WHERE status='done'"
            ).fetchall()
        return [(r["a_id"], r["b_id"], r["result"]) for r in rows]

    # -- standings snapshots -------------------------------------------------
    def save_standings(self, scope: str, payload: list) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO standings (computed, scope, payload) VALUES (?,?,?)",
                (time.time(), scope, json.dumps(payload, separators=(",", ":"))),
            )

    def get_standings(self, scope: str) -> Optional[dict]:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT computed, scope, payload FROM standings WHERE scope=? "
                "ORDER BY computed DESC LIMIT 1",
                (scope,),
            ).fetchone()
        if r is None:
            return None
        return {"computed": r["computed"], "scope": r["scope"], "rows": json.loads(r["payload"])}
