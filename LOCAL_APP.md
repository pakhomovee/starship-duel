# Starship Duel — Local Test App

A small desktop companion for competition participants: test your bot on your
own machine against the built-in baselines (random / heuristic / hunter) and
against **previous versions of your own bot**, watch games unfold on the real
board, run headless batches for win rates, and browse replayable game history.

Works on Linux, macOS and Windows — it's a plain Python web server that opens
in your browser. No accounts, no docker, no network access needed.

## Quick start

```bash
git clone <this repo> && cd starship-duel
python -m venv .venv
# Linux/macOS:            source .venv/bin/activate
# Windows (PowerShell):   .venv\Scripts\Activate.ps1
pip install -r requirements-local.txt
python -m starship_duel.local
```

Your browser opens at `http://127.0.0.1:8765/`. Flags: `--port`, `--no-browser`,
`--data-dir` (state lives in `~/.starship_duel` by default; also settable via
`$STARSHIP_LOCAL_DIR`).

## Registering your bot ("My Bots" 🤖)

Your bot is an external program speaking the arena JSON-line protocol — exactly
what the tournament server runs. See `starship_duel/arena/SANDBOX.md` and the
SDKs in `starship_duel/arena/sdk/` (`python/starship_sdk.py` is the fastest way
to start; a working `example_bot.py` comes pre-registered).

In **My Bots**, attach your bot file and give it a name (auto-suggested from
the file name):

* a `.py` file runs with the same Python that runs the app (portable, no
  shebang / file-association issues on Windows);
* any other file runs as an executable (e.g. a compiled C++ bot).

The file is copied into the app's data dir, so the registered version keeps
playing exactly as uploaded even while you edit the original — re-attach under
the same name to update it. For bots that need a runtime command instead
(e.g. `node mybot.js`), use the *Advanced* command field.

Use the **Check ✓** button after adding: it plays one quick game vs `random`
and reports crashes, protocol mistakes (strikes) or timeouts immediately.

Tip for testing progress: register old copies of your solution under versioned
names (`mybot-v1`, `mybot-v2`, …) and batch them against the newest one.

### Building a C++ bot

The app runs your bot as a plain subprocess, so a C++ bot just needs to be
compiled to a native executable first — then attach that executable in **My
Bots** exactly like a `.py` file. The bundled `nlohmann/json.hpp` lives beside
the SDK, so build from `starship_duel/arena/sdk/cpp/` (or add it with `-I`):

```bash
# Linux / macOS
cd starship_duel/arena/sdk/cpp
g++ -std=c++17 -O2 -I. example_bot.cpp -o example_bot
```

```powershell
# Windows (no WSL needed) — install a compiler once:
#   winget install BrechtSanders.WinLibs.POSIX.UCRT      # MinGW-w64 g++
# then build a *self-contained* .exe (static linking avoids "missing
# libstdc++/libwinpthread DLL" errors when the app launches it):
cd starship_duel\arena\sdk\cpp
g++ -std=c++17 -O2 -I. example_bot.cpp -o example_bot.exe -static
```

Attach the resulting `example_bot` / `example_bot.exe` in **My Bots** and hit
**Check ✓**. Because the runtime is just stdin/stdout JSON lines, a bot built
this way behaves identically on Linux, macOS and Windows.

## Testing your bot ("Test Run" ⚔)

Pick two bots, a game count, optionally a fixed map/seed, and run. You get a
live progress bar, the win/draw split, end-reason breakdown, and a per-game
table. With **record replays** on, every batch game lands in history so you can
watch exactly how a loss happened; otherwise each row has a **Re-run ▶** button
that replays that game's seed/map/first-mover as a live watchable game.

`alternate first mover` (default on) swaps who starts each game so the
matchup is fair.

## Watching & history

* **New Game** starts a live game — play as human vs your bot, or watch bot vs
  bot with Step / Auto, at any speed, from the truth view or through either
  ship's fog of war.
* **Games ▤** lists finished games (each finished live game is recorded
  automatically); pick one to scrub through its replay.
* **Rules ?** opens the full field guide.

## Where things are stored

Everything lives in the data dir (default `~/.starship_duel`):

| file              | contents                              |
|-------------------|---------------------------------------|
| `local_bots.json` | your registered bots (editable JSON)  |
| `bots/`           | uploaded copies of your bot files     |
| `local_games.db`  | recorded games / replays (SQLite)     |

Delete either file to start fresh; the app recreates them.
