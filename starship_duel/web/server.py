"""FastAPI backend for the Starship Duel web UI.

    uvicorn starship_duel.web.server:app --reload      # dev
    python -m starship_duel.web.server                 # prod-ish

REST drives human play and manual bot-stepping; a WebSocket streams a
bot-vs-bot game for step-by-step / auto-play watching.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, Optional, Union

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..bots import REGISTRY
from ..game import GameConfig
from ..game.maps import MAPS
from .serialize import serialize
from .session import GameSession

app = FastAPI(title="Starship Duel")

_STATIC = Path(__file__).parent / "static"
SESSIONS: Dict[str, GameSession] = {}


# --------------------------------------------------------------------------- #
# request models                                                              #
# --------------------------------------------------------------------------- #
class CreateGame(BaseModel):
    ship0: str = "human"
    ship1: str = "heuristic"
    seed: Optional[int] = None
    map_id: Optional[str] = None


class HumanAction(BaseModel):
    type: str
    dest: Optional[str] = None


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _get(game_id: str) -> GameSession:
    s = SESSIONS.get(game_id)
    if s is None:
        raise HTTPException(404, f"no game {game_id}")
    return s


def _default_perspective(s: GameSession) -> Union[str, int]:
    if s.mode == "bot_vs_bot":
        return "truth"
    if s.mode == "human_vs_bot":
        return next(i for i, c in s.controllers.items() if c == "human")
    return s.current_ship  # hotseat human-vs-human: whoever is to move


def _view(s: GameSession, perspective: Optional[str] = None) -> dict:
    if perspective in (None, "auto"):
        persp: Union[str, int] = _default_perspective(s)
    elif perspective == "truth":
        persp = "truth"
    else:
        persp = int(perspective)
    return serialize(s, persp)


# --------------------------------------------------------------------------- #
# REST API                                                                    #
# --------------------------------------------------------------------------- #
@app.get("/api/bots")
def list_bots():
    return {"bots": sorted(REGISTRY.keys())}


@app.get("/api/maps")
def list_maps():
    return {"maps": [m.id for m in MAPS]}


@app.post("/api/game")
def create_game(req: CreateGame):
    controllers = {0: req.ship0, 1: req.ship1}
    for c in controllers.values():
        if c != "human" and c not in REGISTRY:
            raise HTTPException(400, f"unknown controller {c!r}")
    s = GameSession(controllers, config=GameConfig(), seed=req.seed, map_id=req.map_id)
    SESSIONS[s.id] = s
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
# WebSocket: step-by-step / auto-play watch                                   #
# --------------------------------------------------------------------------- #
@app.websocket("/ws/watch/{game_id}")
async def watch(ws: WebSocket, game_id: str):
    await ws.accept()
    s = SESSIONS.get(game_id)
    if s is None:
        await ws.send_json({"error": f"no game {game_id}"})
        await ws.close()
        return

    play_task: Optional[asyncio.Task] = None

    async def send_state():
        await ws.send_json(_view(s))

    async def autoplay(delay: float):
        try:
            while not s.env.done:
                with s.lock:
                    s.step_bot()
                await send_state()
                await asyncio.sleep(max(delay, 0.02))
        except asyncio.CancelledError:
            pass

    await send_state()
    try:
        while True:
            msg = await ws.receive_json()
            cmd = msg.get("cmd")
            if cmd == "step":
                if play_task:
                    play_task.cancel(); play_task = None
                with s.lock:
                    s.step_bot()
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
            elif cmd == "state":
                await send_state()
    except WebSocketDisconnect:
        if play_task:
            play_task.cancel()


# --------------------------------------------------------------------------- #
# static frontend                                                             #
# --------------------------------------------------------------------------- #
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/")
def index():
    return FileResponse(str(_STATIC / "index.html"))


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
