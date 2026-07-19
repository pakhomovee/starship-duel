"""Headless batch runs: play N games between two bots and aggregate stats.

Runs in a background thread so the UI can poll progress.  Each game goes
through a :class:`GameSession` (rather than the bare env loop) so that when
recording is on, every game lands in the history store as a watchable replay.
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from typing import Dict, List, Optional

from ..bots import Bot, make_bot
from ..game import GameConfig
from ..web.session import GameSession
from .mybots import PREFIX, MyBots

_ids = itertools.count(1)


def _build_bot(name: str, seed: Optional[int], mybots: MyBots) -> Bot:
    if name.startswith(PREFIX):
        return mybots.make(name[len(PREFIX):])
    return make_bot(name, seed=seed)


class BatchRun:
    def __init__(self, controllers: Dict[int, str], games: int, *,
                 seed: Optional[int], map_id: Optional[str],
                 alternate_first: bool, record: bool):
        self.id = f"b{next(_ids)}"
        self.controllers = controllers
        self.games = games
        self.seed = seed
        self.map_id = map_id
        self.alternate_first = alternate_first
        self.record = record

        self.status = "running"        # running | done | stopped | error
        self.error: Optional[str] = None
        self.done = 0
        self.wins = [0, 0]
        self.draws = 0
        self.reasons: Dict[str, int] = {}
        self.turns_sum = 0
        self.rows: List[dict] = []
        self.started = time.time()
        self._stop = threading.Event()

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "controllers": {str(k): v for k, v in self.controllers.items()},
            "games": self.games,
            "done": self.done,
            "wins": list(self.wins),
            "draws": self.draws,
            "end_reasons": dict(self.reasons),
            "avg_turns": round(self.turns_sum / self.done, 1) if self.done else None,
            "rows": list(self.rows),
            "map_id": self.map_id,
            "seed": self.seed,
            "record": self.record,
        }


class BatchRunner:
    """Owns all batch runs; one worker thread per run."""

    def __init__(self, mybots: MyBots, store):
        self.mybots = mybots
        self.store = store
        self._runs: Dict[str, BatchRun] = {}
        self._lock = threading.Lock()

    def start(self, controllers: Dict[int, str], games: int, *,
              seed: Optional[int] = None, map_id: Optional[str] = None,
              alternate_first: bool = True, record: bool = False) -> BatchRun:
        run = BatchRun(controllers, games, seed=seed, map_id=map_id,
                       alternate_first=alternate_first, record=record)
        with self._lock:
            # keep the registry bounded; finished runs older than the last few go
            finished = [r for r in self._runs.values() if r.status != "running"]
            for old in sorted(finished, key=lambda r: r.started)[:-4]:
                self._runs.pop(old.id, None)
            self._runs[run.id] = run
        threading.Thread(target=self._work, args=(run,), daemon=True).start()
        return run

    def get(self, bid: str) -> Optional[BatchRun]:
        with self._lock:
            return self._runs.get(bid)

    def stop(self, bid: str) -> bool:
        run = self.get(bid)
        if run is None:
            return False
        run._stop.set()
        return True

    # -- worker --------------------------------------------------------------
    def _work(self, run: BatchRun) -> None:
        bots: Dict[int, Bot] = {}
        try:
            base_seed = run.seed
            bots = {
                s: _build_bot(name, None if base_seed is None else base_seed + s,
                              self.mybots)
                for s, name in run.controllers.items()
            }
            for g in range(run.games):
                if run._stop.is_set():
                    run.status = "stopped"
                    break
                gseed = None if base_seed is None else base_seed + 101 * g
                first = (g % 2) if run.alternate_first else 0
                # Bots are injected (not rebuilt) each game: subprocess bots are
                # respawned by reset(), PPO checkpoints load only once.
                session = GameSession(
                    dict(run.controllers), seed=gseed, map_id=run.map_id,
                    first_mover=first, bot_overrides=bots,
                    store=self.store if run.record else None,
                )
                guard = session.config.turn_cap * 50
                steps = 0
                while not session.env.done and steps <= guard:
                    if run._stop.is_set():
                        break
                    session.step_bot()
                    steps += 1
                if not session.env.done:      # stopped mid-game: discard it
                    run.status = "stopped"
                    break
                st = session.env.engine.state
                if st.winner is None:
                    run.draws += 1
                else:
                    run.wins[st.winner] += 1
                run.reasons[st.end_reason] = run.reasons.get(st.end_reason, 0) + 1
                run.turns_sum += st.turn_number
                run.rows.append({
                    "game": g + 1,
                    "seed": gseed,
                    "first": first,
                    "map_id": st.map_id,
                    "winner": st.winner,
                    "end_reason": st.end_reason,
                    "turns": st.turn_number,
                    "strikes": [getattr(b, "strikes", 0) for b in
                                (bots.get(0), bots.get(1))],
                    "rid": session.record_id if run.record else None,
                })
                run.done += 1
            else:
                run.status = "done"
        except Exception as e:  # surface the failure instead of a silent hang
            run.status = "error"
            run.error = f"{type(e).__name__}: {e}"
        finally:
            for b in bots.values():
                if hasattr(b, "close"):
                    b.close()
