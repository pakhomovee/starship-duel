"""FastAPI backend for the local test app (single user, no auth, no docker).

    python -m starship_duel.local                # start + open browser
    python -m starship_duel.local --port 9000 --no-browser

Reuses the web UI's session / serializer / history-store machinery and its
static assets; adds a "my bots" registry and a batch test runner.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Union

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import __version__
from ..bots import REGISTRY
from ..game import GameConfig
from ..game.maps import MAPS
from ..web.history import GameStore
from ..web.serialize import serialize
from ..web.session import GameSession
from .batch import BatchRunner
from .mybots import PREFIX, MyBots, default_data_dir

log = logging.getLogger("starship_duel.local")

app = FastAPI(title="Starship Duel — Local Test App")

_WEB_STATIC = Path(__file__).parent.parent / "web" / "static"
_LOCAL_STATIC = Path(__file__).parent / "static"

# The DeepSeek bot spends real API credits — keep it off the local surface
# unless explicitly enabled (same env gate as the web app).
DEEPSEEK_ENABLED = bool(os.environ.get("STARSHIP_ENABLE_DEEPSEEK"))
_TORCH_OK = importlib.util.find_spec("torch") is not None

# Mutable app state, (re)built by configure() so tests / --data-dir can retarget
# it before the server starts serving.
DATA_DIR: Path
STORE: GameStore
MYBOTS: MyBots
BATCH: BatchRunner


def configure(data_dir: Optional[Union[str, Path]] = None) -> None:
    """Point all persistent state at ``data_dir`` (default ~/.starship_duel)."""
    global DATA_DIR, STORE, MYBOTS, BATCH
    DATA_DIR = Path(data_dir) if data_dir else default_data_dir()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STORE = GameStore(str(DATA_DIR / "local_games.db"))
    MYBOTS = MyBots(DATA_DIR / "local_bots.json")
    BATCH = BatchRunner(MYBOTS, STORE)


configure(os.environ.get("STARSHIP_LOCAL_DIR"))

# Live sessions. Single user, so a small LRU-bounded pool is plenty; each
# session may own subprocess bots, so evicted sessions are closed.
SESSIONS: Dict[str, GameSession] = {}
_SESSIONS_LOCK = threading.Lock()
MAX_LIVE_SESSIONS = 8


# --------------------------------------------------------------------------- #
# request models                                                              #
# --------------------------------------------------------------------------- #
class CreateGame(BaseModel):
    ship0: str = "human"
    ship1: str = "heuristic"
    seed: Optional[int] = None
    map_id: Optional[str] = None
    first_ship: Optional[int] = None   # lets batch rows be re-run exactly


class HumanAction(BaseModel):
    type: str
    dest: Optional[str] = None


class AddBot(BaseModel):
    name: str
    entry: str                          # file path or command line
    timeout: float = 2.0


class CreateBatch(BaseModel):
    ship0: str = "heuristic"
    ship1: str = "random"
    games: int = 20
    seed: Optional[int] = None
    map_id: Optional[str] = None
    alternate_first: bool = True
    record: bool = False


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _builtin_bots() -> list:
    names = []
    for b in sorted(REGISTRY):
        if b == "deepseek" and not DEEPSEEK_ENABLED:
            continue
        # PPO tiers need torch, an optional heavyweight install.
        if b.startswith(("ppo", "uppo")) and not _TORCH_OK:
            continue
        names.append(b)
    return names


def _valid_controller(c: str, allow_human: bool = True) -> bool:
    if c == "human":
        return allow_human
    if c in REGISTRY:
        if c == "deepseek" and not DEEPSEEK_ENABLED:
            return False
        if c.startswith(("ppo", "uppo")) and not _TORCH_OK:
            return False
        return True
    return c.startswith(PREFIX) and c[len(PREFIX):] in MYBOTS.specs


def _check_map(map_id: Optional[str]) -> None:
    if map_id is not None and map_id not in {m.id for m in MAPS}:
        raise HTTPException(400, f"unknown map {map_id!r}")


def _register_session(s: GameSession) -> None:
    with _SESSIONS_LOCK:
        s.last_access = time.time()
        while len(SESSIONS) >= MAX_LIVE_SESSIONS:
            oldest = min(SESSIONS, key=lambda g: getattr(SESSIONS[g], "last_access", 0.0))
            SESSIONS.pop(oldest).close()
        SESSIONS[s.id] = s


def _get(game_id: str) -> GameSession:
    with _SESSIONS_LOCK:
        s = SESSIONS.get(game_id)
        if s is None:
            raise HTTPException(404, f"no game {game_id}")
        s.last_access = time.time()
        return s


def _default_perspective(s: GameSession) -> Union[str, int]:
    if s.mode == "bot_vs_bot":
        return "truth"
    if s.mode == "human_vs_bot":
        return next(i for i, c in s.controllers.items() if c == "human")
    return s.current_ship


def _view(s: GameSession, perspective: Optional[str] = None) -> dict:
    if perspective in (None, "auto"):
        persp: Union[str, int] = _default_perspective(s)
    elif perspective == "truth":
        persp = "truth"
    else:
        persp = int(perspective)
    return serialize(s, persp)


# --------------------------------------------------------------------------- #
# meta                                                                        #
# --------------------------------------------------------------------------- #
@app.get("/api/bots")
def list_bots():
    MYBOTS.reload()  # pick up hand-edits to local_bots.json without a restart
    return {
        "bots": _builtin_bots(),
        "arena": [PREFIX + n for n in MYBOTS.names()],
        "arena_label": "My bots",
    }


@app.get("/api/maps")
def list_maps():
    return {"maps": [m.id for m in MAPS]}


@app.get("/api/info")
def info():
    import platform
    return {
        "version": __version__,
        "data_dir": str(DATA_DIR),
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "torch": _TORCH_OK,
        "recorded_games": STORE.count(),
    }


# --------------------------------------------------------------------------- #
# live games (same surface app.js already speaks)                             #
# --------------------------------------------------------------------------- #
@app.post("/api/game")
def create_game(req: CreateGame):
    controllers = {0: req.ship0, 1: req.ship1}
    for c in controllers.values():
        if not _valid_controller(c):
            raise HTTPException(400, f"unknown controller {c!r}")
    _check_map(req.map_id)
    if req.first_ship not in (None, 0, 1):
        raise HTTPException(400, "first_ship must be 0 or 1")
    s = GameSession(controllers, config=GameConfig(), seed=req.seed,
                    map_id=req.map_id, first_mover=req.first_ship,
                    arena=MYBOTS, store=STORE)
    _register_session(s)
    return _view(s)


@app.get("/api/game/{game_id}")
def get_game(game_id: str, perspective: Optional[str] = None):
    return _view(_get(game_id), perspective)


@app.post("/api/game/{game_id}/action")
def human_action(game_id: str, action: HumanAction, perspective: Optional[str] = None):
    s = _get(game_id)
    with s.lock:
        try:
            s.apply_human_action(action.type, action.dest)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return _view(s, perspective)


@app.post("/api/game/{game_id}/step")
def step_game(game_id: str, perspective: Optional[str] = None):
    s = _get(game_id)
    with s.lock:
        s.step_bot()
    return _view(s, perspective)


@app.post("/api/game/{game_id}/reset")
def reset_game(game_id: str, perspective: Optional[str] = None):
    s = _get(game_id)
    with s.lock:
        s.reset()
    return _view(s, perspective)


# --------------------------------------------------------------------------- #
# history + replays                                                           #
# --------------------------------------------------------------------------- #
@app.get("/api/games")
def list_games(limit: int = 200):
    return {"games": STORE.list_games(limit=limit)}


@app.get("/api/games/{rid}")
def game_summary(rid: str):
    meta = STORE.get_meta(rid)
    if meta is None:
        raise HTTPException(404, f"no recorded game {rid}")
    return meta


@app.get("/api/games/{rid}/replay")
def game_replay(rid: str):
    replay = STORE.get_replay(rid)
    if replay is None:
        raise HTTPException(404, f"no recorded game {rid}")
    return replay


@app.delete("/api/games/{rid}")
def delete_game(rid: str):
    # your machine, your history — no admin gate here
    if not STORE.delete(rid):
        raise HTTPException(404, f"no recorded game {rid}")
    return {"deleted": rid}


# --------------------------------------------------------------------------- #
# my bots                                                                     #
# --------------------------------------------------------------------------- #
@app.get("/api/mybots")
def my_bots():
    MYBOTS.reload()
    return {"bots": [MYBOTS.describe(n) for n in MYBOTS.names()]}


@app.post("/api/mybots")
def add_my_bot(req: AddBot):
    try:
        return MYBOTS.add(req.name, req.entry, timeout=req.timeout)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/mybots/{name}")
def remove_my_bot(name: str):
    if not MYBOTS.remove(name):
        raise HTTPException(404, f"no removable bot {name!r}")
    return {"deleted": name}


@app.post("/api/mybots/{name}/check")
def check_my_bot(name: str):
    """Smoke-test a bot: one quick headless game vs random. Returns what
    happened so authors get instant feedback on protocol/crash problems."""
    if name not in MYBOTS.specs:
        raise HTTPException(404, f"no bot {name!r}")
    bot = MYBOTS.make(name)
    session = GameSession({0: PREFIX + name, 1: "random"}, seed=0,
                          bot_overrides={0: bot, 1: REGISTRY["random"](seed=1)})
    try:
        guard = session.config.turn_cap * 50
        steps = 0
        while not session.env.done and steps <= guard:
            session.step_bot()
            steps += 1
        st = session.env.engine.state
        crashed = st.end_reason == "crash" and st.winner == 1
        return {
            "ok": not crashed,
            "winner": st.winner,
            "end_reason": st.end_reason,
            "turns": st.turn_number,
            "strikes": getattr(bot, "strikes", 0),
            "message": ("bot crashed — check it reads one JSON line per request "
                        "and writes one reply line" if crashed else
                        f"played a full game vs random "
                        f"({'won' if st.winner == 0 else 'lost' if st.winner == 1 else 'draw'}, "
                        f"{getattr(bot, 'strikes', 0)} strikes)"),
        }
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# batch test runs                                                             #
# --------------------------------------------------------------------------- #
@app.post("/api/batch")
def create_batch(req: CreateBatch):
    for c in (req.ship0, req.ship1):
        if not _valid_controller(c, allow_human=False):
            raise HTTPException(400, f"unknown controller {c!r}")
    _check_map(req.map_id)
    if not (1 <= req.games <= 1000):
        raise HTTPException(400, "games must be 1..1000")
    run = BATCH.start({0: req.ship0, 1: req.ship1}, req.games,
                      seed=req.seed, map_id=req.map_id,
                      alternate_first=req.alternate_first, record=req.record)
    return run.snapshot()


@app.get("/api/batch/{bid}")
def batch_status(bid: str):
    run = BATCH.get(bid)
    if run is None:
        raise HTTPException(404, f"no batch {bid}")
    return run.snapshot()


@app.post("/api/batch/{bid}/stop")
def batch_stop(bid: str):
    if not BATCH.stop(bid):
        raise HTTPException(404, f"no batch {bid}")
    return {"stopping": bid}


# --------------------------------------------------------------------------- #
# websocket: step / auto-play watching (same protocol as the web app)         #
# --------------------------------------------------------------------------- #
@app.websocket("/ws/watch/{game_id}")
async def watch(ws: WebSocket, game_id: str):
    import asyncio

    await ws.accept()
    s = SESSIONS.get(game_id)
    if s is None:
        await ws.send_json({"error": f"no game {game_id}"})
        await ws.close()
        return

    play_task: Optional[asyncio.Task] = None
    persp = {"value": None}

    async def send_state():
        await ws.send_json(_view(s, persp["value"]))

    async def step_once() -> bool:
        def locked_step():
            with s.lock:
                if not s.can_step_bot():
                    return False
                s.step_bot()
                return True
        return await asyncio.to_thread(locked_step)

    async def autoplay(delay: float):
        try:
            while s.can_step_bot():
                s.last_access = time.time()
                await step_once()
                await send_state()
                await asyncio.sleep(max(delay, 0.02))
        except asyncio.CancelledError:
            pass

    await send_state()
    try:
        while True:
            msg = await ws.receive_json()
            s.last_access = time.time()
            cmd = msg.get("cmd")
            if cmd == "step":
                if play_task:
                    play_task.cancel(); play_task = None
                await step_once()
                await send_state()
            elif cmd == "play":
                if not play_task or play_task.done():
                    play_task = asyncio.create_task(autoplay(float(msg.get("delay", 0.5))))
            elif cmd == "pause":
                if play_task:
                    play_task.cancel(); play_task = None
            elif cmd == "reset":
                if play_task:
                    play_task.cancel(); play_task = None
                with s.lock:
                    s.reset()
                await send_state()
            elif cmd == "perspective":
                persp["value"] = msg.get("value")
                await send_state()
            elif cmd == "state":
                await send_state()
    except WebSocketDisconnect:
        if play_task:
            play_task.cancel()


# --------------------------------------------------------------------------- #
# static frontend (web assets + local shell)                                  #
# --------------------------------------------------------------------------- #
app.mount("/static", StaticFiles(directory=str(_WEB_STATIC)), name="static")
app.mount("/local", StaticFiles(directory=str(_LOCAL_STATIC)), name="local")


@app.get("/")
def index():
    return FileResponse(str(_LOCAL_STATIC / "index.html"))


@app.get("/rules")
def rules_page():
    return FileResponse(str(_WEB_STATIC / "rules.html"))


def main(argv=None) -> None:
    import argparse
    import webbrowser

    import uvicorn

    ap = argparse.ArgumentParser(
        prog="starship-duel-local",
        description="Local Starship Duel test app for participants.")
    ap.add_argument("--host", default="127.0.0.1",
                    help="interface to bind (default 127.0.0.1 — local only)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", default=None,
                    help="where bots/history live (default ~/.starship_duel)")
    ap.add_argument("--no-browser", action="store_true",
                    help="don't open the UI in a browser automatically")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s [%(name)s] %(message)s")
    if args.data_dir:
        configure(args.data_dir)

    url = f"http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}/"
    log.info("Starship Duel local app -> %s  (data in %s)", url, DATA_DIR)
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
