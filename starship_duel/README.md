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
  bots/             # Bot interface + random / heuristic / human + registry
  run.py            # match orchestration + CLI (the three play modes)
  rl/               # ── RL adapters (numpy/gymnasium/pettingzoo) ──
    action_coding.py  # flat Discrete action space <-> Action + legal mask
    encoders.py       # Observation -> fixed-size float32 vector
    pettingzoo_env.py # AECEnv for masked self-play (passes pettingzoo api_test)
  web/              # ── self-hosted UI (fastapi + uvicorn) ──
    server.py         # REST + WebSocket, serves the static frontend
    session.py        # in-memory games; human input + bot auto-play/stepping
    serialize.py      # game state -> JSON view (player perspective vs. truth)
    static/           # index.html, app.js, styles.css, sprites.svg
tests/                # test_engine.py (core) + test_rl_web.py (rl/web smoke)
```

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

Pick controllers for P1/P2 (any registered bot, or `human`) and hit **New Game**.
- **Human-vs-bot / hotseat:** the board shows *your* partial-information view —
  the rival appears only as candidate-system halos until legitimately revealed;
  click a glowing system (or an action button) to move.
- **Bot-vs-bot:** a spectator ("truth") view of both ships plus each side's
  belief overlay; **Step** advances one action, **Auto** streams the game over a
  WebSocket at an adjustable speed.

API surface: `POST /api/game`, `GET /api/game/{id}`, `POST …/action`,
`POST …/step`, `POST …/reset`, and `WS /ws/watch/{id}` (`step`/`play`/`pause`).

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

res = play_skirmish(make_bot("heuristic"), make_bot("random", seed=0), seed=0)
# {'winner': 0, 'end_reason': 'fire_hit', 'turns': 3, ...}
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
        # Fire if we're certain we're on the rival:
        if obs.candidate_systems == [obs.position]:
            return Action.fire()
        # otherwise fall back to anything legal:
        return obs.legal_actions[0]

register("mybot", lambda seed=None: MyBot(seed=seed))   # now usable via --bot0 mybot
```

Key `Observation` fields (see [`game/observation.py`](game/observation.py)):
`position`, `cloaked`, `energy`, `banked_overcharge`, `actions_remaining`,
`unlocked` (self-private); `system_owner`, `system_status`, `system_cache`,
`adjacency`, `binary_systems`, `rival_unlocked`, `rival_last_action` (public);
and **`candidate_systems`** — the rival's "could be here" set (a singleton when
the rival's exact location is currently known). The rival's true position,
energy and cloak are never exposed.

Return `Action.end_turn()` to pass early and **bank** unspent actions
(`rollover = max(0, actions_remaining - 2)`, spec §5a).

## RL / self-play

`env.py` exposes an AEC-style API: `reset()`, `agent_selection`, `observe(agent)`,
`step(action)`, `last()`, `rewards/terminations/truncations/infos`, and
`legal_actions()`. One skirmish = one episode; reward is sparse and zero-sum
(**+1 / −1 / 0**, spec §6). A turn is many `step`s by the same agent — the agent
stays selected until the engine flips `turn_ship` at end of turn.

## Belief tracking (the hidden-info layer)

`BeliefTracker` maintains each observer's candidate set for the rival, fed **only
public signals** (never ground truth). Because action *categories* are public
(Jump/Hold/Claim/Fire are always distinguishable, spec §3), the number of JUMPs
is known, so only JUMPs expand the set — tighter than a raw BFS-by-budget. The
core invariant, enforced by a regression test over hundreds of games: **the
rival's true system is always in the candidate set** (the belief may
over-approximate but never wrongly rules out the truth).

## Resolved ambiguities & tunables

The spec flags several items as fuzzy/TBD. Each is a **`GameConfig` field** with
a documented default (`game/config.py`):

- **Long-Range Scanners vs. co-location reveal.** Default
  (`reveal_both_on_colocation_entry=False`): jumping onto the rival exposes only
  the *mover* (you trip the defender's alarm); LRS is what additionally reveals
  the *defender to the mover*. This keeps hidden info meaningful — you can jump
  onto a cloaked rival unknowingly and lose to the end-of-turn forced fire
  (spec §5). Set `True` for the literal "both revealed" reading.
- **Supernova timing** — unspecified in the spec; `enable_supernova=False` by
  default for a clean, testable core. When enabled, systems destabilize →
  supernova probabilistically (`destabilize_prob`, `supernova_prob`); a ship
  caught on a collapsing star at end of turn is destroyed.
- **Cache economy** — the spec's pseudocode spawns a cache in *every* empty
  system every tick, which floods the map with energy and kills all scarcity.
  Instead caches are a small **contested pool**: at most `max_active_caches`
  (default 3) exist at once, a new one appears every `cache_spawn_period` turns
  at a random eligible system, and uncollected caches **escalate** in value
  (`cache_upgrade_period`, `cache_overcharge_transform_prob`) — turning them
  into juicy-but-exposing objectives worth fighting over. Binaries are excluded
  by default (`cache_spawn_in_binaries`) since they already pay via ownership.
- **Spawn constraints** — placeholder `min_hop_distance >= 2` plus a rough
  binary-distance balance (`maps.spawn_positions`), matching spec §7.5.
- **Turn-cap timeout** — `turn_cap=200`, `timeout_resolution="draw"` (or
  `"systems"` to tiebreak on systems owned, then energy).

Everything else (income 1/4, action costs, base cache values, banking rule) uses
the exact numbers from the spec.
```
