"""Persistent store of played games, for browsing and replay.

Each finished skirmish is saved as a compact row: a little metadata plus a
zlib-compressed JSON array of per-ply *truth* frames (the same shape the web UI
already renders for spectating).  Replaying a game is then just stepping through
its stored frames — no re-simulation, so it works for every mode, seed, human,
or external arena bot exactly as it was played.

Storage is stdlib SQLite; frames compress heavily (the board geometry repeats
each ply), so even long games are only a few KB on disk.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import zlib
from typing import Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    rid         TEXT PRIMARY KEY,
    created     REAL NOT NULL,
    mode        TEXT,
    map_id      TEXT,
    seed        INTEGER,
    controllers TEXT,          -- json {"0": name, "1": name}
    winner      INTEGER,       -- 0/1, or NULL for a draw
    end_reason  TEXT,
    plies       INTEGER,
    frames      BLOB           -- zlib(json([...truth frames...]))
);
CREATE INDEX IF NOT EXISTS games_created ON games (created DESC);
"""


def default_db_path() -> str:
    return os.environ.get("STARSHIP_GAMES_DB") or "starship_games.db"


class GameStore:
    """Thin thread-safe wrapper over a SQLite games table.

    A fresh connection is opened per operation so the store is safe to call from
    the request threads and the websocket executor alike.
    """

    def __init__(self, path: Optional[str] = None):
        self.path = path or default_db_path()
        self._lock = threading.Lock()
        # Schema is created lazily on first use, so merely constructing a store
        # (e.g. importing the server module) never touches the filesystem.

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)  # idempotent (IF NOT EXISTS)
        return conn

    # -- write ---------------------------------------------------------------
    def save(self, rid: str, meta: dict, frames: List[dict]) -> None:
        blob = zlib.compress(json.dumps(frames, separators=(",", ":")).encode("utf-8"))
        row = (
            rid,
            float(meta.get("created", time.time())),
            meta.get("mode"),
            meta.get("map_id"),
            meta.get("seed"),
            json.dumps(meta.get("controllers", {})),
            meta.get("winner"),
            meta.get("end_reason"),
            int(meta.get("plies", len(frames))),
            blob,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO games "
                "(rid, created, mode, map_id, seed, controllers, winner, end_reason, plies, frames) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            conn.commit()

    # -- read ----------------------------------------------------------------
    @staticmethod
    def _summary(r: sqlite3.Row) -> dict:
        return {
            "rid": r["rid"],
            "created": r["created"],
            "mode": r["mode"],
            "map_id": r["map_id"],
            "seed": r["seed"],
            "controllers": json.loads(r["controllers"] or "{}"),
            "winner": r["winner"],
            "end_reason": r["end_reason"],
            "plies": r["plies"],
        }

    def list_games(self, limit: int = 200) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT rid, created, mode, map_id, seed, controllers, winner, end_reason, plies "
                "FROM games ORDER BY created DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [self._summary(r) for r in rows]

    def get_meta(self, rid: str) -> Optional[dict]:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT rid, created, mode, map_id, seed, controllers, winner, end_reason, plies "
                "FROM games WHERE rid = ?",
                (rid,),
            ).fetchone()
        return self._summary(r) if r else None

    def get_replay(self, rid: str) -> Optional[dict]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM games WHERE rid = ?", (rid,)).fetchone()
        if r is None:
            return None
        frames = json.loads(zlib.decompress(r["frames"]).decode("utf-8"))
        meta = self._summary(r)
        return {"meta": meta, "frames": frames}

    def delete(self, rid: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM games WHERE rid = ?", (rid,))
            conn.commit()
            return cur.rowcount > 0

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
