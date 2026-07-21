# Starship Duel — Environment Specification (v0.3)

Two ships duel across a network of star systems. It is a **1v1, alternating-turn,
zero-sum, imperfect-information** game: a ship's exact position is hidden from its
rival unless a specific trigger reveals it, and — since v0.3 — so is most of the
**ownership map** (fog of war).

The game has evolved from a pure assassination duel into a **territory-control +
hunt** game. There are three ways to win:

1. **Domination** — bank enough map-control points (from systems you own).
2. **Elimination** — strip the rival of all their **lives** by hunting them down.
3. **Collapse** — outlast a rival who gets caught on a dying star.

> This document reflects the current simulator (`starship_duel/game/`). The
> canonical numbers live in [`game/config.py`](starship_duel/game/config.py) and
> per-map overrides in [`game/maps.py`](starship_duel/game/maps.py); values quoted
> here are the current defaults and are all tunable.

---

## 1. Star System Map

A map is a graph of star systems; **four maps** ship today (`map1`–`map4`, the
first aliased `reference`) and one is sampled per skirmish. **Binary systems**
(two stars) pay far more income than single-star systems and are the contested
hubs. Each map defines its adjacency, its binary set, and a **per-map komi**
(second-mover handicap, see §7).

Spawns are randomized but fair: `min_hop_distance(p0, p1) >= 2` and both initial
positions are revealed at skirmish start (then re-hidden as ships move under
cloak).

---

## 2. True State (simulator-internal, full information)

```
GameState:
    skirmish_number, campaign_score
    map_id, binary_systems
    system_owner:  {system -> ShipId | None}          # None = unclaimed
    system_status: {system -> STABLE | DESTABILIZING | SUPERNOVA}
    system_cache:  {system -> Cache(ENERGY|OVERCHARGE, value) | None}
    turn_ship, turn_number, turn_clock
    domination: [int, int]        # map-control points per ship (win at target)
    lives:      [int, int]        # lives remaining per ship (0 = eliminated)
    ships: [ShipState, ShipState]

ShipState:
    position, cloaked, deep_cloak_turns_left
    energy, banked_overcharge, actions_remaining
    unlocked: {proximity_alert, long_range_scanners, jamming}
```

The engine also keeps, **per observer**, a fog layer that is *not* part of the
shared state: `observed_owner[o]` (ownership as `o` has actually sensed it) and
`owner_known[o]` (which systems `o` has ever sensed), plus a belief tracker for
the rival's position.

---

## 3. Ship Observation (partial information — what the policy conditions on)

Public to both ships:
- `adjacency`, `binary_systems` (static), `system_status`, `system_cache`
- **`domination` (both ships) and `domination_target`** — the control race is open
- **`lives` and `rival_lives`** — the hunt is open
- `campaign_score`, `turn_ship`, `turn_number`, `turn_clock`
- rival's `unlocked` flags, and `rival_last_turn_actions` — the *category* of
  every action of the rival's last completed turn, in order.  Categories the
  observer cannot identify are not dropped, they collapse to a placeholder, so
  the length always tells you how many actions the rival spent:
  - `"JAMMED"` — deliberately masked by the actor's Jamming (its Energy
    actions, and its silent claims)
  - `"UNKNOWN"` — the actor ended the action cloaked, so it left no trace.
    **`JUMP` and `HOLD` are only named while the actor is exposed afterwards**:
    a cloaked jump and a cloaked hold look identical, and so does the `HOLD`
    that re-cloaks an exposed ship (all you see is a ship that is gone).  A
    deep-cloaked `CLAIM` is invisible too.  `FIRE` is always public (a discharge
    is loud), and so is any action that exposed the actor.

Fogged (per observer):
- **`system_owner`** — only what this ship has sensed. A ship senses the true
  owner of its own system and its neighbours each turn (**two hops** with
  Long-Range Scanners), or by a Scan (full-map sweep), or by witnessing an
  uncloaked rival claim. `owner_known` marks which systems are actually known;
  everything else is *unknown*, not *proven-unowned*.

Private to self only:
- exact `position`, `cloaked`, `energy`, `banked_overcharge`

Inferred (belief): the rival's exact system is given only when currently known
for certain (they are exposed, or a Scan/LRS reveal is still valid); otherwise the
observation gives the last-confirmed system and a move budget so a bot can rebuild
the "could be here" reachability set itself. See §3a.

### 3a. Belief tracking

Between reveals, the rival could be anywhere reachable from where you last saw it
within its action budget — refine that reachability set with whatever the public
log rules out. A **deep-cloaked** ship defeats every position reveal (Scan, LRS,
entry detection), and a **respawn** (after a life loss) teleports the rival to a
random hidden system, re-opening belief to the whole map. Invariant: the rival's
true system is always inside the candidate set (see the belief-soundness test).

---

## 4. Action Space

One "action" = one of the following; each turn grants `2 + banked_overcharge`
actions. Costs are current defaults (Energy).

| Action | Cost | Effect |
|---|---|---|
| `JUMP(dest)` | – | Move to an adjacent, non-supernova system. Entering rival-claimed space or the rival's own system exposes you. |
| `HOLD` | – | Stay; regain cloak if not exposed. |
| `CLAIM` | – | Take/flip the current system (works on neutral **and rival-owned** systems). Exposes you — **unless deep-cloaked** (invisible expansion) or you run **Jamming** (all your claims stay silent). |
| `FIRE` | **3** | A **raid** on a hit (rival co-located, or one hop away with LRS, and not deep-cloaked): steal `10` domination, **capture** the rival's system, and cost the rival **one life** (respawning it hidden). At 0 lives the rival is **eliminated** (you win). Charged whether it lands or not. |
| `SCAN` | **0** | **Recon ping**: sweep the whole map's ownership into your view (even vs a deep-cloaked rival), and fix the rival's exact position unless it is deep-cloaked. |
| `DEEP_CLOAK` | **14** | Immune to every exposure/reveal trigger, to raids, and to life loss, until the start of your `deep_cloak_duration` (2)-th upcoming turn. |
| `OVERCHARGE` | **6** | Bank +1 extra action for next turn; repeatable/stackable. |
| `UNLOCK_PROXIMITY_ALERT` | **6** | Permanent. **Capture shield**: the ground under your ship can't be captured (unless the raider jams). **Radar**: a rival moving adjacent to your ship is revealed — **pierces deep cloak**. |
| `UNLOCK_LONG_RANGE_SCANNERS` | **10** | Permanent. **Ranged raid** (fire one hop away), **2-hop** ownership vision, and passive tracking of a rival within `lrs_range` (2) hops. |
| `UNLOCK_JAMMING` | **8** | Permanent. **Silent expansion** (all your claims stay hidden), **blinds** the rival's Proximity Alert (radar + shield), and hides your territory from the rival's Scan; your Energy actions also show only as "JAMMED". |

### Ability rock-paper-scissors

```
DEEP_CLOAK  beats  SCAN / LRS      (evades long-range detection)
PROXIMITY   beats  DEEP_CLOAK      (short-range alarm pierces cloak; shields territory)
JAMMING     beats  PROXIMITY       (blinds the alarm, punches through the shield)
```

Each also has standalone value regardless of the opponent (cloak = safe
expansion/hunt approach; LRS = ranged offense; Scan = full-map recon; Prox =
capture shield; Jamming = permanent stealth), so they don't depend on a specific
counter to be worth buying.

---

## 5. Turn Algorithm

```
start_turn(s):
    advance_system_status()                 # the collapse (see §6)
    if s stranded on a SUPERNOVA: s dies -> rival wins ("collapse")
    sense_local_ownership(s)                 # lift fog on s.pos + neighbours (2 hops w/ LRS)
    if LRS and rival within range and not deep-cloaked: track rival
    income = sum(4 if binary else 1 for systems s owns, non-supernova)
    s.energy += income
    s.domination += income                   # income banks BOTH energy and points
    if s.domination >= domination_target: s wins ("domination")
    s.actions_remaining = 2 + s.banked_overcharge; s.banked_overcharge = 0
    tick down deep_cloak; collect any cache on s.pos (exposes s)

apply_action(s, a):    # per §4; then s.actions_remaining -= 1
end_turn(s):           # bank actions beyond 2; pass to rival
```

Note the coupling: **income feeds both Energy and domination** from the same
stream, so total spendable Energy over a game ≈ the domination target. FIRE's
Energy cost is what makes raiding a committed choice rather than a free probe.

### 5b. Caches

Small contested pool: at most `max_active_caches` (4) exist, a new one appears
every `cache_spawn_period` (2) turns at a random eligible (unoccupied, non-binary)
system, and uncollected caches escalate in value. Collected only by **starting**
your turn on one (exposes you). `ENERGY` → `+value` Energy; `OVERCHARGE` → +1
banked action. Caches are spendable surplus that does **not** advance domination.

---

## 6. The collapse (shrinking field)

Systems collapse from the outside in on a deterministic schedule
(`shrink_start_turn` 24, then every `shrink_interval` 6 plies), each first
`DESTABILIZING` (a `shrink_warning` of 6 plies) then going `SUPERNOVA`. A ship
caught on a supernova system is destroyed (rival wins, `end_reason="collapse"`).
This bounds game length and forces the two ships together for an endgame.

---

## 7. Turn-order fairness (komi)

A pure claim/hunt race favours the first mover (tempo compounds). The **second
mover** gets a per-map handicap — starting `komi_domination` points and
`komi_energy` Energy (≈ one turn-one Overcharge) — tuned so each map's mirror
first-mover win rate lands near 50%. Values are per-map in
[`maps.py`](starship_duel/game/maps.py) and are re-calibrated whenever the meta
shifts (they are meta-dependent, so they change across balance passes).

---

## 8. Reward & Episode

- **One skirmish = one episode.** Sparse terminal reward: **+1 win / −1 loss / 0
  draw**, trained via self-play (masked PPO; see `rl/`).
- Win: domination target reached, rival eliminated (0 lives), or rival dies to the
  collapse. **Draw**: turn cap (200) with `timeout_resolution="draw"` (rare).
- Energy, domination, lives, cloak, control, banked overcharge and unlocks all
  reset each skirmish (fresh map).

---

## 9. Status vs the original draft

Implemented since v0.2: domination victory + income-as-points; FIRE-as-raid with
**capture-on-hit** and **lives/elimination**; **fog of war** on ownership; the
**ability RPS** with standalone-valued unlocks; **per-map komi**; free Scan /
costed Fire; map-universal GNN policy across all four maps. The old
`enable_instakill` assassination mode (FIRE = instant win) still exists behind a
config flag for reference/tests but is off by default.
