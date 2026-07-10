"""FastAPI backend for the Starship Duel web UI.

    uvicorn starship_duel.web.server:app --reload      # dev
    python -m starship_duel.web.server                 # prod-ish

REST drives human play and manual bot-stepping; a WebSocket streams a
bot-vs-bot game for step-by-step / auto-play watching.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional, Union

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..bots import REGISTRY
from ..game import GameConfig
from ..game.maps import MAPS
from ..tournament import BotRegistry, TournamentStore
from ..tournament.accounts import AccountStore, smoke_test, static_scan
from ..tournament.schedule import (
    enqueue_baselines,
    enqueue_full_round_robin,
    register_competitors,
)
from ..tournament.scoring import compute_bt
from .history import GameStore
from .serialize import serialize
from .session import GameSession

# Surface package logs (e.g. the DeepSeek bot) on the server console even under
# uvicorn's own logging config.
_pkg_log = logging.getLogger("starship_duel")
_pkg_log.setLevel(logging.INFO)
if not _pkg_log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    _pkg_log.addHandler(_h)
    _pkg_log.propagate = False

app = FastAPI(title="Starship Duel")

_STATIC = Path(__file__).parent / "static"

# Live game sessions, keyed by game id. Each session owns its bot subprocesses /
# sandbox containers, so the pool is *bounded* and idle sessions are reaped: a
# public visitor's game no longer wipes everyone else's (the old single-user
# behaviour), but we still can't let sessions — and their containers — pile up
# without limit. Guarded by _SESSIONS_LOCK for the threadpool request workers.
SESSIONS: Dict[str, GameSession] = {}
_SESSIONS_LOCK = threading.Lock()
# Max concurrent live games (each may hold a sandbox container per bot) and how
# long a game may sit untouched before it's reaped. Both tunable via env.
MAX_LIVE_SESSIONS = int(os.environ.get("STARSHIP_MAX_LIVE_SESSIONS", "32"))
SESSION_IDLE_TTL = float(os.environ.get("STARSHIP_SESSION_IDLE_SECONDS", "1800"))

# Persistent record of every finished skirmish (browse + replay). Path comes
# from $STARSHIP_GAMES_DB, defaulting to ./starship_games.db.
STORE = GameStore()

# --------------------------------------------------------------------------- #
# light hardening for VM/open-access test hosting (opt-in via env)            #
# --------------------------------------------------------------------------- #
# If set, every /api and /ws request must present this token (query ?token= or
# X-Access-Token header).  Share the UI as http://host:port/?token=<token>.
ACCESS_TOKEN = os.environ.get("STARSHIP_ACCESS_TOKEN") or None
# Tournament admin actions (schedule, recompute) require this token in an
# X-Admin-Token header; if unset, those write endpoints are disabled entirely.
# Read-only standings stay open (behind the normal ACCESS_TOKEN gate, if any).
ADMIN_TOKEN = os.environ.get("STARSHIP_ADMIN_TOKEN") or None
# Tournament state (queue + results + standings) and the bot allowlist. Workers
# run in separate processes (python -m starship_duel.tournament.worker); the API
# only schedules, reports standings, and triggers recomputes.
TOURNEY = TournamentStore()
# Accounts / sessions / submissions. Active validated submissions are surfaced to
# the match registry as competitors (keyed by username).
ACCOUNTS = AccountStore()
TOURNEY_BOTS = BotRegistry(account_store=ACCOUNTS)
SESSION_COOKIE = "sd_session"
MAX_SUBMISSIONS_PER_DAY = int(os.environ.get("STARSHIP_MAX_SUBMISSIONS_PER_DAY", "20"))

# Seed an initial admin so the tournament is manageable out of the box. Set both
# env vars once; thereafter the admin creates the rest of the accounts.
_admin_user = os.environ.get("STARSHIP_ADMIN_USER")
_admin_pass = os.environ.get("STARSHIP_ADMIN_PASSWORD")
if _admin_user and _admin_pass:
    ACCOUNTS.ensure_admin(_admin_user, _admin_pass)
# The DeepSeek bot spends real API credits, so it's hidden from the web surface
# unless explicitly enabled (it stays available on the CLI regardless).
DEEPSEEK_ENABLED = bool(os.environ.get("STARSHIP_ENABLE_DEEPSEEK"))

# Server-side allowlist of external "arena" bots (bundled example + any from
# arena_bots.json). The client picks these by name; it never sends a command.
from .arena_registry import PREFIX as ARENA_PREFIX
from .arena_registry import ArenaBots
ARENA = ArenaBots()


@app.middleware("http")
async def _require_token(request: Request, call_next):
    """Gate the API behind a shared token when STARSHIP_ACCESS_TOKEN is set.

    Only /api is protected; the static UI shell loads freely so a visitor can
    open the page, but every game action needs the token."""
    if ACCESS_TOKEN and request.url.path.startswith("/api"):
        tok = request.query_params.get("token") or request.headers.get("x-access-token")
        if tok != ACCESS_TOKEN:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


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


class LoginReq(BaseModel):
    username: str
    password: str


class CreateUserReq(BaseModel):
    username: str
    password: str
    is_admin: bool = False


# --------------------------------------------------------------------------- #
# rate limiting (in-process, dependency-free)                                  #
# --------------------------------------------------------------------------- #
class _RateLimiter:
    """A sliding-window limiter: at most ``max_hits`` recorded hits per ``window``
    seconds per key. Used to throttle password guessing on /api/login."""

    def __init__(self, max_hits: int, window: float):
        self.max_hits = max_hits
        self.window = window
        self._hits: Dict[str, list] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> list:
        q = [t for t in self._hits.get(key, ()) if now - t < self.window]
        if q:
            self._hits[key] = q
        else:
            self._hits.pop(key, None)
        return q

    def allowed(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            return len(self._prune(key, now)) < self.max_hits

    def hit(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._prune(key, now)
            self._hits.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


# Lock out after 10 failed logins per (ip, username) within 5 minutes; a success
# clears the counter. Bounds brute-forcing without punishing honest typos.
LOGIN_LIMITER = _RateLimiter(
    max_hits=int(os.environ.get("STARSHIP_LOGIN_MAX_FAILS", "10")),
    window=float(os.environ.get("STARSHIP_LOGIN_WINDOW_SECONDS", "300")),
)


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Behind our own reverse proxy the socket peer is
    localhost, so honour X-Forwarded-For (Caddy/nginx set it); the left-most hop
    is the original client."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _reap_idle_locked(now: float) -> None:
    """Close and drop sessions untouched for longer than SESSION_IDLE_TTL.
    Caller must hold _SESSIONS_LOCK."""
    stale = [gid for gid, s in SESSIONS.items()
             if now - getattr(s, "last_access", now) > SESSION_IDLE_TTL]
    for gid in stale:
        SESSIONS.pop(gid).close()


def _register_session_locked(s: GameSession) -> None:
    """Insert a new session, reaping idle ones and evicting the oldest if we're at
    capacity (its bot containers are torn down). Caller holds _SESSIONS_LOCK."""
    now = time.time()
    s.last_access = now
    _reap_idle_locked(now)
    while len(SESSIONS) >= MAX_LIVE_SESSIONS:
        oldest = min(SESSIONS, key=lambda g: getattr(SESSIONS[g], "last_access", 0.0))
        SESSIONS.pop(oldest).close()
    SESSIONS[s.id] = s


def _get(game_id: str) -> GameSession:
    with _SESSIONS_LOCK:
        s = SESSIONS.get(game_id)
        if s is None:
            raise HTTPException(404, f"no game {game_id}")
        s.last_access = time.time()  # keep active games from being reaped
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
    # Built-in bots plus external arena bots (prefixed so the client can tell
    # them apart and label them). Reload the arena list so a freshly-edited
    # arena_bots.json shows up without restarting the server.
    ARENA.reload()
    names = [b for b in sorted(REGISTRY.keys()) if b != "deepseek" or DEEPSEEK_ENABLED]
    return {
        "bots": names,
        "arena": [ARENA_PREFIX + n for n in ARENA.names()],
    }


@app.get("/api/maps")
def list_maps():
    return {"maps": [m.id for m in MAPS]}


def _valid_controller(c: str) -> bool:
    if c == "deepseek":
        return DEEPSEEK_ENABLED  # hidden from the web surface unless enabled
    if c == "human" or c in REGISTRY:
        return True
    return c.startswith(ARENA_PREFIX) and c[len(ARENA_PREFIX):] in ARENA.specs


@app.post("/api/game")
def create_game(req: CreateGame):
    controllers = {0: req.ship0, 1: req.ship1}
    for c in controllers.values():
        if not _valid_controller(c):
            raise HTTPException(400, f"unknown controller {c!r}")
    if req.map_id is not None and req.map_id not in {m.id for m in MAPS}:
        raise HTTPException(400, f"unknown map {req.map_id!r}")
    # Multi-user hosting: keep each visitor's game alive independently, but bound
    # the pool so bot subprocesses / sandbox containers can't accumulate without
    # limit — idle games are reaped and the oldest is evicted at capacity.
    s = GameSession(controllers, config=GameConfig(), seed=req.seed,
                    map_id=req.map_id, arena=ARENA, store=STORE)
    with _SESSIONS_LOCK:
        _register_session_locked(s)
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
# Game history + replays                                                       #
# --------------------------------------------------------------------------- #
@app.get("/api/games")
def list_games(limit: int = 200):
    """Summaries of past skirmishes, newest first."""
    return {"games": STORE.list_games(limit=limit)}


@app.get("/api/games/{rid}")
def game_summary(rid: str):
    meta = STORE.get_meta(rid)
    if meta is None:
        raise HTTPException(404, f"no recorded game {rid}")
    return meta


@app.get("/api/games/{rid}/replay")
def game_replay(rid: str):
    """Metadata plus the full list of per-ply truth frames for playback."""
    replay = STORE.get_replay(rid)
    if replay is None:
        raise HTTPException(404, f"no recorded game {rid}")
    return replay


@app.delete("/api/games/{rid}")
def delete_game(rid: str, request: Request):
    _require_admin(request)  # replays are shared history: admin-only deletion
    if not STORE.delete(rid):
        raise HTTPException(404, f"no recorded game {rid}")
    return {"deleted": rid}


# --------------------------------------------------------------------------- #
# Tournament: standings (public) + scheduling/recompute (admin)               #
# --------------------------------------------------------------------------- #
def _current_user(request: Request) -> Optional[dict]:
    return ACCOUNTS.resolve_session(request.cookies.get(SESSION_COOKIE))


def _require_user(request: Request) -> dict:
    u = _current_user(request)
    if u is None:
        raise HTTPException(401, "login required")
    return u


def _require_admin(request: Request) -> None:
    """Admin gate: a logged-in admin *user*, or the shared admin token (for cron
    / CLI).  Either is sufficient."""
    u = _current_user(request)
    if u is not None and u["is_admin"]:
        return
    tok = request.headers.get("x-admin-token") or request.query_params.get("admin_token")
    if ADMIN_TOKEN and tok == ADMIN_TOKEN:
        return
    raise HTTPException(403, "admin only")


def _check_scope(scope: str) -> str:
    if scope not in ("quick", "full"):
        raise HTTPException(400, "scope must be 'quick' or 'full'")
    return scope


@app.post("/api/login")
def login(req: LoginReq, request: Request, response: Response):
    key = f"{_client_ip(request)}:{req.username}"
    if not LOGIN_LIMITER.allowed(key):
        raise HTTPException(429, "too many failed login attempts; try again later")
    u = ACCOUNTS.verify_login(req.username, req.password)
    if u is None:
        LOGIN_LIMITER.hit(key)
        raise HTTPException(401, "invalid username or password")
    LOGIN_LIMITER.reset(key)  # clear the counter on a successful login
    token = ACCOUNTS.create_session(u["id"])
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=7 * 24 * 3600)
    return {"username": u["username"], "is_admin": bool(u["is_admin"])}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    ACCOUNTS.delete_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    u = _current_user(request)
    if u is None:
        return {"authenticated": False}
    return {"authenticated": True, "username": u["username"], "is_admin": bool(u["is_admin"])}


@app.post("/api/admin/users")
def admin_create_user(req: CreateUserReq, request: Request):
    _require_admin(request)
    if ACCOUNTS.get_user_by_name(req.username) is not None:
        raise HTTPException(409, f"user {req.username!r} already exists")
    uid = ACCOUNTS.create_user(req.username, req.password, is_admin=req.is_admin)
    return {"id": uid, "username": req.username, "is_admin": req.is_admin}


@app.get("/api/admin/users")
def admin_list_users(request: Request):
    _require_admin(request)
    return {"users": ACCOUNTS.list_users()}


@app.post("/api/submissions")
async def upload_submission(request: Request, file: UploadFile = File(...)):
    """Upload a single-file bot: static-scan, smoke-test vs random, and (on pass)
    make it this user's active competitor. Synchronous so authors get instant
    feedback."""
    u = _require_user(request)
    if ACCOUNTS.recent_submission_count(u["id"], 24 * 3600) >= MAX_SUBMISSIONS_PER_DAY:
        raise HTTPException(429, f"daily submission limit reached ({MAX_SUBMISSIONS_PER_DAY})")
    code = await file.read()
    sub_id = ACCOUNTS.add_submission(u["id"], u["username"], file.filename, code)

    reason = static_scan(code, file.filename)
    if reason:
        ACCOUNTS.set_submission_status(sub_id, "rejected", reason)
        return {"id": sub_id, "status": "rejected", "message": reason, "active": False}

    ok, msg = smoke_test(code, file.filename, out_dir=TOURNEY_BOTS.submissions_dir)
    if not ok:
        ACCOUNTS.set_submission_status(sub_id, "rejected", msg)
        return {"id": sub_id, "status": "rejected", "message": msg, "active": False}

    ACCOUNTS.set_submission_status(sub_id, "validated", msg, make_active=True)
    # Surface the freshly-activated bot to the match registry + competitor list.
    TOURNEY_BOTS.reload()
    register_competitors(TOURNEY, TOURNEY_BOTS)
    return {"id": sub_id, "status": "validated", "message": msg, "active": True}


@app.get("/api/submissions")
def my_submissions(request: Request):
    u = _require_user(request)
    return {"submissions": ACCOUNTS.list_user_submissions(u["id"])}


@app.get("/api/admin/submissions")
def all_submissions(request: Request):
    _require_admin(request)
    return {"submissions": ACCOUNTS.list_all_submissions()}


@app.get("/api/tournament/standings")
def tournament_standings(scope: str = "quick"):
    """Latest cached Bradley-Terry snapshot (populated by tick / recompute)."""
    snap = TOURNEY.get_standings(_check_scope(scope))
    return snap or {"scope": scope, "computed": None, "rows": []}


@app.get("/api/tournament/matches")
def tournament_matches(status: Optional[str] = None, limit: int = 200):
    return {"counts": TOURNEY.status_counts(),
            "matches": TOURNEY.list_matches(status=status, limit=limit)}


@app.post("/api/tournament/recompute")
def tournament_recompute(request: Request, scope: str = "quick"):
    _require_admin(request)
    rows = compute_bt(TOURNEY, _check_scope(scope))
    return {"scope": scope, "rows": rows}


@app.post("/api/tournament/schedule/baselines")
def tournament_schedule_baselines(request: Request, n_each: int = 10):
    _require_admin(request)
    TOURNEY_BOTS.reload()
    register_competitors(TOURNEY, TOURNEY_BOTS)
    added = enqueue_baselines(TOURNEY, n_each=n_each)
    return {"added": added, "counts": TOURNEY.status_counts()}


@app.post("/api/tournament/schedule/full")
def tournament_schedule_full(request: Request, n_each: int = 10):
    _require_admin(request)
    TOURNEY_BOTS.reload()
    register_competitors(TOURNEY, TOURNEY_BOTS)
    added = enqueue_full_round_robin(TOURNEY, n_each=n_each)
    return {"added": added, "counts": TOURNEY.status_counts()}


# --------------------------------------------------------------------------- #
# WebSocket: step-by-step / auto-play watch                                   #
# --------------------------------------------------------------------------- #
@app.websocket("/ws/watch/{game_id}")
async def watch(ws: WebSocket, game_id: str):
    # The http middleware doesn't see websockets, so enforce the token here too.
    if ACCESS_TOKEN and ws.query_params.get("token") != ACCESS_TOKEN:
        await ws.close(code=1008)  # policy violation
        return
    await ws.accept()
    s = SESSIONS.get(game_id)
    if s is None:
        await ws.send_json({"error": f"no game {game_id}"})
        await ws.close()
        return

    play_task: Optional[asyncio.Task] = None
    # Per-connection view perspective: None -> mode default (truth for bot-vs-bot),
    # "truth", or a seat id "0"/"1" to watch through that ship's fog of war.
    persp = {"value": None}

    async def send_state():
        await ws.send_json(_view(s, persp["value"]))

    async def step_once() -> bool:
        """Advance one bot action off the event loop (a DeepSeek step may be
        slow).  Returns True if it actually advanced."""
        def locked_step():
            with s.lock:
                if not s.can_step_bot():
                    return False
                s.step_bot()
                return True
        return await asyncio.to_thread(locked_step)

    async def autoplay(delay: float):
        try:
            # Auto-advance bot actions, stopping when it's a human's turn or the
            # skirmish ends (so human-vs-bot pauses for your input).
            while s.can_step_bot():
                s.last_access = time.time()  # active watch: don't reap under us
                await step_once()
                await send_state()
                await asyncio.sleep(max(delay, 0.02))
        except asyncio.CancelledError:
            pass

    await send_state()
    try:
        while True:
            msg = await ws.receive_json()
            s.last_access = time.time()  # active watch: keep out of the idle reaper
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
                persp["value"] = msg.get("value")  # "truth" | "0" | "1" | None
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


@app.get("/tournament")
def tournament_page():
    return FileResponse(str(_STATIC / "tournament.html"))


def main():
    """Run the server. Defaults to localhost; pass --host 0.0.0.0 (or set
    STARSHIP_HOST) to expose it on a VM — see the README security note first."""
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(prog="starship-duel-web")
    ap.add_argument("--host", default=os.environ.get("STARSHIP_HOST", "127.0.0.1"),
                    help="interface to bind (default 127.0.0.1; use 0.0.0.0 to expose)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("STARSHIP_PORT", "8000")))
    args = ap.parse_args()

    if args.host != "127.0.0.1":
        gate = "token required" if ACCESS_TOKEN else "NO ACCESS TOKEN SET — open to the network"
        _pkg_log.warning("binding %s:%s — %s", args.host, args.port, gate)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
