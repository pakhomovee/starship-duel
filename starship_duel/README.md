# Starship Duel — Backend

A zero-dependency simulator + gym-like environment for **Starship Duel**: a 1v1,
alternating-turn, zero-sum, imperfect-information duel across a graph of star
systems. Implements the rules in [`../starship_duel_env_spec.md`](../starship_duel_env_spec.md).

The **core** (`game/`, `env.py`, `bots/`, `run.py`) is pure Python stdlib — no
deps needed to simulate, script bots, or run the CLI. The **web UI** and **RL
adapters** are optional layers on top (`pip install -r requirements.txt`).

## Layout

```
starship_duel/
  game/             # ── pure-stdlib simulator ──
    types.py        # enums + ShipState / GameState dataclasses (no logic)
    maps.py         # GameMap (+layout), reference map, MAPS registry, spawns, BFS
    config.py       # GameConfig — every tunable/TBD number lives here
    engine.py       # the simulator: rules, turn lifecycle, combat, caches
    belief.py       # per-observer "could be here" candidate-set tracker
    observation.py  # partial-info Observation built per ship (spec §3)
  env.py            # StarshipDuelEnv — PettingZoo-style AEC wrapper (stdlib)
  bots/             # Bot interface + random / heuristic / hunter / deepseek / human + belief
  run.py            # match orchestration + CLI (name or 'cmd:<program>' bots)
  rl/               # ── RL adapters (numpy/gymnasium/pettingzoo) ──
    action_coding.py  # flat Discrete action space <-> Action + legal mask
    encoders.py       # Observation -> fixed-size float32 vector
    pettingzoo_env.py # AECEnv for masked self-play (passes pettingzoo api_test)
    single_agent_view.py # learner-seat wrapper (opponent-as-environment)
    model.py          # masked actor-critic (MLP torso + policy/value heads)
    ppo/              # from-scratch PPO trainer: GAE, league self-play, eval
  arena/            # ── isolated subprocess bots (competition ladder) ──
    protocol.py       # JSON-line wire format (encode request / parse reply)
    subprocess_bot.py # spawns + referees an external program as a Bot
    sdk/python/       # standalone Python SDK + example bot
    sdk/cpp/          # single-header C++ SDK (nlohmann/json) + example bot
  tournament/       # ── round-robin ladder + Bradley-Terry ranking ──
    store.py          # SQLite queue (atomic claim) + results + standings
    registry.py       # server-side bot allowlist (baselines + arena bots)
    match.py          # play one match (reuses web.session.GameSession)
    schedule.py       # baseline + full round-robin schedulers (idempotent)
    scoring.py        # choix Bradley-Terry + bootstrap CIs
    worker.py, tick.py # match worker loop; 6h standings recompute (cron)
  web/              # ── self-hosted UI (fastapi + uvicorn) ──
    server.py         # REST + WebSocket, serves the static frontend
    session.py        # in-memory games; human input + bot auto-play/stepping
    serialize.py      # game state -> JSON view (player perspective vs. truth)
    static/           # index.html, app.js, styles.css, sprites.svg
tests/                # test_engine.py, test_rl_web.py, test_arena.py, test_tournament.py
```

## Winning: domination, elimination, or collapse

There are **three** ways to win a skirmish (full rules in
[`../starship_duel_env_spec.md`](../starship_duel_env_spec.md)):

- **Domination** — at the start of each of your turns you bank *control points*
  equal to your income from systems you own (single `+1`, binary `+4`; collapsed
  stars pay nothing). First to `domination_target` (40) wins on points. Income
  banks **both** Energy and points from the same stream.
- **Elimination** — a landed **Fire** is a *raid*: it steals `10` domination,
  **captures** the co-located system, and costs the rival **one life**, then
  respawns it hidden. Run the rival out of `lives` (3) and you win by
  `eliminated`. This is the **hunt**, and it makes the locate/stealth kit matter.
- **Collapse** — outlast a rival caught on a dying star (see below).

Both scores and both ships' `lives` are public (`obs.domination`, `obs.lives`,
`obs.rival_lives`). Different maps favour different paths — some are hunting
grounds, some are territory races. A turn-cap timeout is a draw.

**Fog of war:** the ownership map is *per-observer* — you only see systems you've
sensed (your neighbourhood, a Scan sweep, or an uncloaked rival claim), so a
**deep-cloaked Claim expands invisibly**. **Komi:** the second mover gets a
per-map starting handicap so each map is ~50/50 on turn order.

The ability kit forms a rock-paper-scissors — **Deep Cloak** beats Scan/LRS,
**Proximity Alert** beats Deep Cloak (short-range alarm that pierces cloak +
shields your territory from capture), **Jamming** beats Proximity — and each also
has standalone value (LRS = ranged raid, Scan = full-map recon, etc.).

The bundled **`heuristic`** bot claims and steals binaries, hunts and pounces on a
fix, buys Deep Cloak/LRS, and evacuates stars before they go supernova. The
**`hunter`** bot leans all-in on the kill path: locate → close → fire → re-locate.

## The collapse (shrinking field)

To keep games bounded, the star field **collapses over time**: starting at
`shrink_start_turn` (24), one system every `shrink_interval` (6) plies goes
supernova, from the outside in toward a random surviving "eye" — a ship caught on
a supernova system is destroyed. This squeezes the two ships together so **every
game ends in ≤~96 plies (~48 turns each)**: competitive bots resolve by combat
much sooner; passive ones get forced together or perish in the collapse.

- **Advance warning.** A system spends `shrink_warning` (6) plies DESTABILIZING
  before it goes SUPERNOVA — **3 turns of notice** for each player. The exact
  countdown is surfaced as `system_collapse_in` (plies to supernova) in every
  observation and the arena protocol, and as a **`⚠ N` badge** in the web UI.
- **Connectivity preserved.** Collapsing strictly farthest-from-eye first keeps
  the surviving field connected at every step (the survivors are always a ball
  around the eye), so a ship can always reach the shrinking core — never
  stranded by disconnection. (Proven; regression-tested.)

All tunable in `GameConfig` (`shrink_enabled`, `shrink_start_turn`,
`shrink_interval`, `shrink_warning`).

## Install (for the web UI + RL)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt      # fastapi, uvicorn, numpy, torch, gymnasium, pettingzoo
```

## Web interface

A self-hosted UI to **play human-vs-bot** and **watch bot-vs-bot step by step**,
rendered with the `design/` sprite sheet (neon-space art).

```bash
uvicorn starship_duel.web.server:app --reload     # -> http://localhost:8000
# or: python -m starship_duel.web.server
```

Pick controllers for P1/P2 — a built-in bot, `human`, or an **arena bot** (an
external program, listed under "Arena (external)") — and hit **New Game**, so you
can play against your own C++/Python/etc. submission right from the UI.
- **Human-vs-bot / hotseat:** the board shows *your* partial-information view —
  the rival appears as candidate-system halos (both starting positions are
  revealed, then re-hidden as ships move under cloak); click a glowing system
  (or an action button) to move.
- **Bot-vs-bot:** a spectator ("truth") view of both ships plus each side's
  belief overlay.
- **Step / Auto in every mode:** bot turns never auto-resolve on the server —
  **Step** advances one bot action so you can watch it unfold (even in
  human-vs-bot), and **Auto** streams over a WebSocket at an adjustable speed,
  pausing when it's your turn.

**Play against a trained policy.** The bundled map-universal PPO checkpoints show
up as difficulty tiers — **`uppo-easy`**, **`uppo-medium`**, and **`uppo`** (the
hardest) — selectable like any other bot (loaded lazily, so torch is only imported
if you actually pick one). Drop more `*.pt` files in and register tiers in
`bots/__init__.py`. The legacy single-map `ppo-*` checkpoints (trained on the
pre-rebalance game) are no longer offered for selection, but their `.pt` files
remain in `bots/ppo/` and can be loaded ad hoc via the `ppo:<checkpoint>` CLI spec.

There's also a **`deepseek`** controller that picks each move via the DeepSeek
chat API — set `DEEPSEEK_API_KEY` in the environment (optionally `DEEPSEEK_MODEL`
default `deepseek-chat`, `DEEPSEEK_TIMEOUT` default 20s). Without a key it
transparently falls back to the heuristic bot, so matches always run — and it
**logs** each decision (`starship_duel.bots.deepseek`): a loud warning if the key
is missing, otherwise the request, latency, reply, and chosen action, visible in
the server console. Because it spends real API credits, it is **hidden from the
web UI unless you set `STARSHIP_ENABLE_DEEPSEEK=1`** (it's always on the CLI).

New to the game? Hit the **Rules** button in the top bar for an in-app overview
of the goal, actions, economy, hiding/exposure, and the collapse.

**Crossing-free, evenly-spaced maps.** The star systems form a *planar* graph,
so the board is drawn as a straight-line planar embedding — no edges cross
(`web/layout.py`: a Tutte barycentric embedding over the best outer face,
falling back to networkx's `planar_layout`, then a circle if `networkx`/`numpy`
are unavailable). A planarity-preserving force-directed **spread** then opens up
dense clusters — every node move is vetoed if it would create a crossing — so
the field stays planar while the systems breathe apart. The frontend sizes star
sprites and ship orbits to the tightest gap in the layout, so nothing ever
overlaps however densely a map packs. A hand-authored `GameMap.layout` is
honored only when it too is crossing-free.

**Game history & replays.** Every finished skirmish is recorded to a small
SQLite store (`web/history.py`; path `$STARSHIP_GAMES_DB`, default
`./starship_games.db`) as a zlib-compressed list of per-ply truth frames — a few
KB each. Hit **Games** in the top bar to browse past matches and **Watch** any of
them: a scrubber steps/plays/rewinds through the exact game as it happened
(works for every mode, seed, human, or arena bot — no re-simulation).

**Arena bots in the UI.** The bundled `example-py` bot is available out of the
box; add your own by dropping an `arena_bots.json` next to where you launch the
server (or point `$STARSHIP_ARENA_BOTS` at one):

```json
{ "my-cpp-bot": { "command": "./mybot", "timeout": 1.0 },
  "py-hunter":  "python bots/hunter.py" }
```

They appear in the dropdown on next load. The bundled **C++ example** auto-appears
as `example-cpp` once you build it (`cd arena/sdk/cpp && g++ -std=c++17 -O2 -I.
example_bot.cpp -o example_bot`, needs nlohmann/json). **Security:** the web client
only ever selects an arena bot by *name* — the command comes from this server-side
allowlist, never from the request — so the UI can't run arbitrary commands. (A
crashing arena bot forfeits, as in the CLI.)

API surface: `POST /api/game`, `GET /api/game/{id}`, `POST …/action`,
`POST …/step`, `POST …/reset`, and `WS /ws/watch/{id}` (`step`/`play`/`pause`).
Game history: `GET /api/games`, `GET /api/games/{rid}`,
`GET /api/games/{rid}/replay`, `DELETE /api/games/{rid}`.

### Hosting on a VM (open-access testing)

The server binds to **`127.0.0.1` by default**. To reach it from elsewhere, bind
a public interface and (strongly recommended) require a shared token:

```bash
STARSHIP_ACCESS_TOKEN=$(openssl rand -hex 16) \
  python -m starship_duel.web.server --host 0.0.0.0 --port 8000
# share the URL as  http://<vm-host>:8000/?token=<that token>
```

With `STARSHIP_ACCESS_TOKEN` set, every `/api` and `/ws` call must carry the token
(`?token=` or `X-Access-Token`); the static page still loads so the UI can attach
it. `--host`/`--port` also read `STARSHIP_HOST`/`STARSHIP_PORT`.

This is fine for a **short-lived test with people you trust**, not a public
service. Known limitations: it keeps **one global game** (a new game tears down the
previous one, so concurrent visitors interrupt each other), there's no rate
limiting, and arena/PPO bots consume CPU per game. Put it behind a firewall or
reverse proxy, keep the token secret, and don't set `DEEPSEEK_API_KEY` /
`STARSHIP_ENABLE_DEEPSEEK` on a shared host unless you mean to pay for it.

## RL / self-play (PettingZoo)

```python
from starship_duel.rl.pettingzoo_env import raw_env
import numpy as np

env = raw_env(map_id="reference", seed=0)   # spaces are fixed per map
env.reset(seed=0)
for agent in env.agent_iter():
    obs, reward, term, trunc, info = env.last()
    action = None if (term or trunc) else int(
        np.random.choice(np.flatnonzero(obs["action_mask"])))
    env.step(action)
```

Each observation is `{"observation": float32[…], "action_mask": int8[n_actions]}`.
The env passes PettingZoo's `api_test`; reward is sparse zero-sum (+1/−1/0). The
flat action space encodes JUMP by **absolute destination index** (stable across
positions/maps). Swap in your own policy; mask the logits with `action_mask`.

`ActionCodec` / `ObservationEncoder` (in `rl/`) are usable standalone if you
prefer a different training harness (e.g. a custom PPO or the raw `env.py`).

### PPO training (`rl/ppo/`)

A from-scratch, masked PPO with self-play lives in `rl/ppo/`. It trains one seat
via a single-agent view (`rl/single_agent_view.py`) that folds the opponent
(scripted anchor or frozen policy snapshot) into the environment, so the update
loop is standard clipped-surrogate PPO with GAE over whole episodes.

```bash
# Phase 1 — validate against a fixed scripted anchor
python -m starship_duel.rl.ppo.train --opponent heuristic --total-updates 300 \
    --num-workers 32 --episodes-per-update 512

# Phase 2 — self-play league (snapshot pool + scripted anchors)
python -m starship_duel.rl.ppo.train --self-play --total-updates 3000 \
    --num-workers 32 --episodes-per-update 512 --log-dir runs/ppo_league
```

Rollouts fan out across `--num-workers` processes (`rl/ppo/parallel.py`): the sim
is GIL-bound, so this is where nearly all the wall-clock speedup comes from. Set
it to roughly the core count; `0/1` runs in-process (best for tests/debug). On
Linux workers start with `fork`, so they share the parent's loaded torch pages
copy-on-write — ~32 workers add only a few GB of RAM and start instantly.

Progress is tracked by **win-rate vs fixed bots** (`eval/*`) — self-play reward
is ~0 by construction. Metrics go to the console, `runs/.../metrics.jsonl`, and
TensorBoard (grouped `charts/ losses/ eval/`); logs are tiny (~1 KB/update):

```bash
tensorboard --logdir runs/          # then open http://localhost:6006
```

A checkpoint plugs back into every existing tool as a bot (~0.5 MB each):
`--bot0 "ppo:runs/ppo/ckpt_final.pt"`.

## Arena — external bots in any language

For a competition ladder, bots run as **isolated subprocesses** over a
language-agnostic **JSON-line protocol** (the Battlesnake / Halite / CodinGame
pattern), kept separate from the raw-throughput RL loop. Each bot is spawned once
per game with persistent stdin/stdout pipes, so it keeps its own memory across
turns; the engine stays authoritative and validates every reply — a timeout,
crash, malformed line, or illegal move never takes the match down (it substitutes
a safe default and logs a *strike*).

```
engine → bot   {"type":"act","turn":12,"you":{...},"rival":{...},"systems":{...},
                "legal_actions":[{"action":"JUMP","target":"Halcyon Binary"},{"action":"HOLD"},...]}
bot   → engine  {"action":"JUMP","target":"Halcyon Binary"}          # or {"index": 0}
```

The engine validates every reply: a **timeout** or **malformed/illegal** move is
a graceful *strike* (safe default substituted), but a **runtime crash** (the bot
process dies) is an **automatic loss** — so a broken submission can't limp
through. Run any program as a bot with the `cmd:` prefix:

```bash
python -m starship_duel.run --bot0 heuristic \
    --bot1 "cmd:python starship_duel/arena/sdk/python/example_bot.py"

# C++ bot (needs nlohmann/json):
g++ -std=c++17 -O2 starship_duel/arena/sdk/cpp/example_bot.cpp -o mybot
python -m starship_duel.run --bot0 heuristic --bot1 "cmd:./mybot"
```

Thin, copy-pasteable SDKs remove the boilerplate — write one `decide(request) ->
action` function: a ~30-line Python helper (`arena/sdk/python/starship_sdk.py`)
and a single-header C++ SDK (`arena/sdk/cpp/starship_bot.hpp`). Any language that
can read a line and parse JSON works. A trained PPO/NFSP policy wrapped behind the
same protocol is just another entry in the same ladder — no special-casing, and a
sterner strength check than self-play mirrors.

## Tournament (round-robin ladder + Bradley-Terry ranking)

`starship_duel/tournament/` runs a competition over arena bots: it schedules
matches, plays them on trusted host workers, stores each as a replayable game, and
ranks competitors with a regularized **Bradley-Terry** model (via `choix`) with
bootstrap 90% confidence intervals. The referee (engine) is trusted and runs on the
host; each bot is only ever the arena subprocess above, so a bad bot can strike or
forfeit but never corrupt a match.

Competitors come from a server-side **allowlist** (never the DB or a web request) —
`$STARSHIP_TOURNEY_BOTS` JSON, same trust model as `arena_bots.json`:

```json
{ "alice": {"command": ["python","bots/alice.py"], "timeout": 1.0} }
```

Plus the **baselines** (`random`, `heuristic`, `hunter`, `uppo-easy`,
`uppo-medium`, `uppo`) that every ladder includes — the during-contest "partial
standings" opponents.

```bash
# schedule participant-vs-baseline matches (partial standings) — admin endpoint,
# or from Python: tournament.schedule.enqueue_baselines(store, n_each=10)
curl -XPOST localhost:8000/api/tournament/schedule/baselines?n_each=10 \
     -H "X-Admin-Token: $STARSHIP_ADMIN_TOKEN"

# run match workers (safe to run several processes / --workers threads at once;
# the SQLite claim is atomic, so no match is ever played twice)
python -m starship_duel.tournament.worker --workers 4

# publish "current standings" — wire to cron every 6h during the contest:
#   0 */6 * * *  cd /srv/starship && python -m starship_duel.tournament.tick
python -m starship_duel.tournament.tick --scope quick

# after the deadline: full all-pairs round robin, then the final ranking
curl -XPOST localhost:8000/api/tournament/schedule/full?n_each=10 \
     -H "X-Admin-Token: $STARSHIP_ADMIN_TOKEN"
python -m starship_duel.tournament.tick --scope full
```

Standings are read-only and public (`GET /api/tournament/standings?scope=quick|full`);
scheduling and `POST /api/tournament/recompute` require the `X-Admin-Token` header
(set `$STARSHIP_ADMIN_TOKEN`, else those write endpoints are disabled). Tournament
games are ordinary recorded replays, so each standings row's `replay_rid` opens in
the existing viewer via `GET /api/games/{rid}/replay`. State lives in SQLite
(`$STARSHIP_TOURNEY_DB`, default `starship_tournament.db`). Bots are **not**
sandboxed yet — v1 runs them as host subprocesses, so only accept trusted
submissions until the planned Docker isolation lands.

## Quick start

```bash
# bot vs bot, one rendered game
python -m starship_duel.run --bot0 heuristic --bot1 random --seed 3 --render

# human vs bot (you drive ship 0 at the prompt)
python -m starship_duel.run --bot0 human --bot1 heuristic

# evaluate over many games (alternates who moves first)
python -m starship_duel.run --bot0 heuristic --bot1 random --games 200

# run the tests (core is stdlib-only; rl/web tests need requirements.txt)
python -m unittest tests.test_engine          # core, no deps
python -m pytest tests/                        # everything (in the venv)
```

Programmatically:

```python
from starship_duel.bots import make_bot
from starship_duel.run import play_skirmish

res = play_skirmish(make_bot("hunter"), make_bot("random", seed=0), seed=0)
# {'winner': 0, 'end_reason': 'eliminated', 'turns': 11, ...}
# end_reason is one of: 'domination' | 'eliminated' | 'collapse' | 'timeout'
```

## The three modes

All three are the *same loop* — only the two controllers differ, because every
controller (human or bot) sees the identical partial-information `Observation`
and returns an `Action`:

| Mode         | `--bot0` / `--bot1`        |
|--------------|----------------------------|
| bot vs bot   | `heuristic`, `random`, …   |
| player vs bot| `human` on one side        |
| player vs player | `human` on both        |

## Writing a custom bot

A bot implements one method. `obs.legal_actions` is pre-filtered to exactly what
is allowed right now, so the minimal valid bot is a one-liner:

```python
from starship_duel.bots import Bot, register
from starship_duel.game import Action, ActionType

class MyBot(Bot):
    name = "mybot"
    def reset(self):        # optional: called at each skirmish start
        self.seen = None
    def act(self, obs):
        # Fire if the rival is known to be right here:
        if obs.rival_position == obs.position:
            return Action.fire()
        # otherwise fall back to anything legal:
        return obs.legal_actions[0]

register("mybot", lambda seed=None: MyBot(seed=seed))   # now usable via --bot0 mybot
```

Key `Observation` fields (see [`game/observation.py`](game/observation.py)):
`position`, `cloaked`, `energy`, `banked_overcharge`, `actions_remaining`,
`unlocked` (self-private); `domination` + `domination_target`, **`lives` +
`rival_lives`**, `system_status`, `system_cache`, `adjacency`, `binary_systems`,
`rival_unlocked`, `rival_last_action` (public); **`system_owner` is fogged** —
only systems in **`owner_known`** are actually sensed, the rest are unknown (a
deep-cloaked rival can be claiming territory you can't see). And the hidden-info
signals: **`rival_position`** — the rival's *exact* system,
given only when known for certain (else `None`); **`rival_last_seen`** — the last
system it was confirmed in; **`rival_moves_since_seen`** — an upper bound on hops
travelled since. Those last two are exactly enough to re-run the reachability BFS
yourself (`candidates ≈ systems within rival_moves_since_seen hops of
rival_last_seen`); the fuzzy set itself is **not** handed over.
`starship_duel.bots.BotBelief` does this BFS for you (used by the heuristic and
DeepSeek bots) — feed it each `obs` and read `.candidates` / `.known_position()`.

Return `Action.end_turn()` to pass early and **bank** unspent actions
(`rollover = max(0, actions_remaining - 2)`, spec §5a).

## RL / self-play

`env.py` exposes an AEC-style API: `reset()`, `agent_selection`, `observe(agent)`,
`step(action)`, `last()`, `rewards/terminations/truncations/infos`, and
`legal_actions()`. One skirmish = one episode; reward is sparse and zero-sum
(**+1 / −1 / 0**, spec §6). A turn is many `step`s by the same agent — the agent
stays selected until the engine flips `turn_ship` at end of turn.

## Belief tracking (the hidden-info layer)

Two separate trackers, on purpose:

- **Engine-side `BeliefTracker`** (in `game/belief.py`) maintains each observer's
  candidate set from **all** public signals (it sees every action). It is **not**
  handed to bots — it powers the web UI's "could be here" overlay. Core invariant,
  regression-tested over hundreds of games: **the rival's true system is always in
  the candidate set** (it may over-approximate, never wrongly rules out the truth).
- **Bot-side `bots.BotBelief`** is what a bot uses to infer the rival itself. A bot
  only sees the board on *its own* turns, so this tracker is deliberately
  approximate: it collapses on a hard reveal (`obs.rival_position`), widens by the
  rival's reach between turns, and re-seeds from freshly-claimed systems. Bots that
  track smarter than this get a real edge.

The observation only ever hands a bot the rival's **exact** system when it's known
for certain (`obs.rival_position`); the fuzzy set is the bot's own problem.

## Resolved ambiguities & tunables

The spec flags several items as fuzzy/TBD. Each is a **`GameConfig` field** with
a documented default (`game/config.py`):

- **End-of-turn forced fire** — `enable_forced_fire=False` (removed by default).
  Under the spec (§5) a ship that ends its turn co-located with the rival is
  auto-fired upon and loses; disabled here so a kill requires actively choosing
  **FIRE** while co-located. Flip to `True` to restore the spec behavior.
- **Long-Range Scanners vs. co-location reveal.** Default
  (`reveal_both_on_colocation_entry=False`): jumping onto the rival exposes only
  the *mover* (you trip the defender's alarm); LRS is what additionally reveals
  the *defender to the mover*. Set `True` for the literal "both revealed" reading.
- **The collapse (supernova timing)** — unspecified in the spec, so implemented
  as a **deterministic shrinking field** (`shrink_enabled=True`) that bounds
  game length: systems collapse outside-in on a fixed schedule
  (`shrink_start_turn`, `shrink_interval`, `shrink_warning`) toward a random eye,
  and a ship caught on a supernova system is destroyed. See "The collapse" above.
- **Cache economy** — the spec's pseudocode spawns a cache in *every* empty
  system every tick, which floods the map with energy and kills all scarcity.
  Instead caches are a small **contested pool**: at most `max_active_caches`
  (default 4) exist at once, a new one appears every `cache_spawn_period` turns
  at a random eligible system, and uncollected caches **escalate** in value
  (`cache_upgrade_period`, `cache_overcharge_transform_prob`) — turning them
  into juicy-but-exposing objectives worth fighting over. Binaries are excluded
  by default (`cache_spawn_in_binaries`) since they already pay via ownership.
- **Deep Cloak** — makes a ship *undetectable* (immune to every exposure
  trigger **and** to the end-of-turn forced fire, so it can sit in enemy
  territory or end its turn on the rival unnoticed) for `deep_cloak_duration`
  of its own turns (default **2**), counted down at the start of each of its
  turns.
- **Reveal initial positions** — `reveal_initial_positions=True` seeds each
  side's belief with the rival's exact spawn (spec's "last confirmed position"
  model), then it re-hides as ships move. Set `False` for maximal initial
  uncertainty (belief seeded to the whole spawn-consistent set).
- **Belief owned-system prune** — `belief_prune_owned=False`. Removing your
  owned systems from a cloaked rival's candidate set *was* sound, but a
  deep-cloaked ship can now sit in your territory undetected, so the prune would
  violate the soundness invariant (true position always in the candidate set).
- **Spawn constraints** — placeholder `min_hop_distance >= 2` plus a rough
  binary-distance balance (`maps.spawn_positions`), matching spec §7.5.
- **Turn-cap timeout** — `turn_cap=200`, `timeout_resolution="draw"` (or
  `"systems"` to tiebreak on systems owned, then energy).

Everything else (income 1/4, action costs, base cache values, banking rule) uses
the exact numbers from the spec.
```
