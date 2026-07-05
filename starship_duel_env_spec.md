# Starship Duel — RL Environment Specification (Draft v0.2)

Two ships duel across a network of star systems, each trying to hunt
down the other while staying hidden themselves. 1v1, alternating-turn,
zero-sum, imperfect information — a ship's exact location is hidden
from its rival unless revealed by a specific trigger.

---

## 1. Star System Map

A map is a graph of star systems. Multiple maps exist and rotate per
skirmish (first turn randomized each skirmish). Reference map
(verified from a clean screenshot of the source layout):

```python
adjacency = {
    "Solantis":       ["Veyra", "Kestrel Binary", "Drakar Reach"],
    "Drakar Reach":   ["Solantis", "Halcyon Binary", "Tessek"],
    "Veyra":          ["Solantis", "Kestrel Binary", "Ondrix"],
    "Kestrel Binary":       ["Solantis", "Veyra", "Ondrix", "Halcyon Binary",
                       "Corvane", "Aurelia Binary", "Isolde Reach"],
    "Halcyon Binary":       ["Drakar Reach", "Kestrel Binary", "Zarath", "Tessek"],
    "Tessek":         ["Drakar Reach", "Zarath", "Halcyon Binary"],
    "Ondrix":         ["Veyra", "Kestrel Binary", "Lumeth"],
    "Lumeth":         ["Ondrix", "Aurelia Binary"],
    "Aurelia Binary":       ["Kestrel Binary", "Lumeth", "Pallor Minor", "Isolde Reach"],
    "Corvane":        ["Kestrel Binary", "Isolde Reach", "Zarath"],
    "Zarath":         ["Halcyon Binary", "Tessek", "Corvane"],
    "Pallor Minor":   ["Aurelia Binary", "Isolde Reach"],
    "Isolde Reach":   ["Kestrel Binary", "Aurelia Binary", "Pallor Minor", "Corvane"],
}
binary_systems = ["Kestrel Binary", "Halcyon Binary", "Aurelia Binary"]
```

Binary systems (two stars) generate far more Energy than single-star
systems — Kestrel Binary is the map's hub at degree 7.

Each map should also define binary systems and a spawn function.
Starting positions are randomized but constrained — apparent rules of
thumb are "not adjacent to each other" and "roughly similar distance to
binary systems" for fairness, though the exact constraint set isn't
nailed down yet (treat as `min_hop_distance(p0, p1) >= 2` plus a rough
binary-system-distance balance until verified). Additional maps get
added to a `MAPS` registry the environment samples from per skirmish.

---

## 2. True State (simulator-internal, full information)

```
GameState:
    skirmish_number: int
    campaign_score: (int, int)        # skirmishes won, per ship
    map_id: str
    graph: Dict[system, List[system]]
    binary_systems: Set[system]
    system_owner: Dict[system, Optional[ShipId]]   # None = unclaimed
    system_status: Dict[system, Enum(STABLE, DESTABILIZING, SUPERNOVA)]
    system_cache: Dict[system, Optional[Cache]]     # Cache(kind: ENERGY|OVERCHARGE, value: int)
    turn_ship: ShipId
    turn_number: int
    turn_clock: float                  # counts down from 60s

    ships: [ShipState, ShipState]

ShipState:
    position: system
    cloaked: bool
    deep_cloak_active: bool            # true until start of this ship's next turn
    energy: int
    banked_overcharge: int             # from Overcharge / Overcharge Caches; uncapped
    actions_remaining: int             # this turn: 2 + banked_overcharge, consumed as spent
    unlocked: {proximity_alert: bool, long_range_scanners: bool, jamming: bool}
```

---

## 3. Ship Observation (partial information — this is what the policy conditions on)

Public to both ships:
- `graph`, `binary_systems` (static)
- `system_owner`, `system_status`, `system_cache`
- `campaign_score`, `skirmish_number`, `turn_ship`, `turn_clock`
- rival's `unlocked` flags (unlocking notifies the rival)
- rival's last action *category* — Jump/Hold/Claim/Fire are
  distinguishable; Scan/Deep Cloak/Overcharge/unlocks collapse to a
  generic "spent Energy on an operation" if the rival has Jamming
  active, otherwise the specific action is shown

Private to self only:
- exact `position`, `cloaked`, `energy`, `banked_overcharge`

Inferred (not stored in true state — computed per-observer):
- `candidate_systems: Set[system]` — the "could be here" set for the
  rival ship. This is the crux of the whole hidden-info layer.
  Recommended approach: maintain a principled belief set per observer,
  updated by:
  - collapse to a single system on any reveal event (Claim, detected on
    entry, successful Scan, mutual co-location, Long-Range Scanners
    trigger)
  - otherwise, expand via BFS over `graph` bounded by the rival's known
    action budget since last reveal, intersected with any public
    constraints (e.g. a system it's known not to be in, because a
    cache there wasn't collected this turn, or because it's known to
    have avoided a Scan result)
  - this is a principled approximation, not an extracted ground-truth
    formula for the exact original UI heuristic

### 3a. Worked example — what "candidate_systems" actually means

Say you last confirmed your rival was in Aurelia Binary — maybe
because they just Claimed it, which always reveals exact position.
It's now their turn, they take their 2 actions, and end up cloaked
again (no reveal happened). You don't know exactly where they are, but
you're not totally in the dark either — you know:

- they started in Aurelia Binary
- they had (at most) 2 actions available
- each action could be Jump-to-a-neighbor, Hold, Claim, or a
  non-moving action (Scan/Deep Cloak/Overcharge/an unlock)

So the set of systems they **could physically be in right now** is
exactly: everywhere reachable from Aurelia Binary in ≤2 hops on the
graph. From the reference map: 1 hop = {Kestrel Binary, Lumeth, Pallor
Minor, Isolde Reach}; 2 hops adds everywhere those connect to (Kestrel
Binary alone reaches Solantis, Veyra, Ondrix, Halcyon Binary, Corvane).
That reachable set (plus Aurelia Binary itself, if they Held twice) is
your `candidate_systems` — the same thing the in-game "could be here"
tags show.

You then **narrow** that set using anything else you've observed
publicly: e.g. if you control Kestrel Binary yourself, the rival can't
be sitting there right now without having exposed themselves on entry
(unless they bought Deep Cloak, which itself pings you as "rival spent
Energy" — so even that folds back into the belief update). A cache
sitting uncollected in a candidate system is a soft signal at best, not
a hard rule-out (caches are only collected on turn-start, not merely by
passing through).

Bottom line: it's graph reachability under a known action budget,
refined by whatever the public action log rules out.

---

## 4. Action Space

One "action" = one of the following. Each turn grants
`2 + banked_overcharge` actions.

| Action | Cost | Preconditions | Effect |
|---|---|---|---|
| `JUMP(dest)` | – | `dest` adjacent to current position | move; entering a rival-claimed system exposes you; entering the rival's actual system → both revealed, see §5 |
| `HOLD` | – | current system not rival-claimed | stay in place; regain cloak if not already exposed |
| `CLAIM` | – | must be in the system | take/reclaim system; **exposes you** |
| `FIRE` | – | – | wins skirmish if rival is in same system now; else a public "miss" (or, if rival has Proximity Alert, exposes you) |
| `SCAN` | 10 Energy | – | reveal rival's exact system unless they have Deep Cloak active |
| `DEEP_CLOAK` | 25 Energy | – | immune to exposure triggers until start of own next turn |
| `OVERCHARGE` | 40 Energy | – | banks +1 extra action for next turn; stackable, uncapped, repeatable same turn |
| `UNLOCK_PROXIMITY_ALERT` | 10 Energy | not already unlocked | permanent for the skirmish: rival's failed Fire reveals them |
| `UNLOCK_LONG_RANGE_SCANNERS` | 40 Energy | not already unlocked | permanent: jumping into rival's system reveals them (unless Deep Cloak) |
| `UNLOCK_JAMMING` | 25 Energy | not already unlocked | permanent: your Energy-spending actions show only as generic "spent Energy" to rival |

All Energy-spending actions notify the rival that Energy was spent
(specific action shown, unless the *actor* has Jamming).

---

## 5. Turn Algorithm (pseudocode)

```
def start_turn(state, s):
    income = sum(4 if sys in binary_systems else 1
                 for sys in state.system_owner if state.system_owner[sys] == s)
    s.energy += income
    s.actions_remaining = 2 + s.banked_overcharge
    s.banked_overcharge = 0
    s.deep_cloak_active = False   # expires at start of own turn

    if state.system_cache[s.position] is not None:
        collect(s, state.system_cache[s.position])
        state.system_cache[s.position] = None
        expose(s)                 # landing on a cache always exposes you

    advance_system_status(state)
    # if s.position just became SUPERNOVA, s must leave this turn (mask HOLD/CLAIM there)

def apply_action(state, s, action):
    # dispatch per Action Space table; decrement s.actions_remaining by 1

def end_turn(state, s):
    rival = other(s)
    s.banked_overcharge += max(0, s.actions_remaining - 2)
    s.actions_remaining = 0
    if s.position == rival.position:
        # co-location at end of turn: whoever didn't use their last
        # action to Fire is the one who gets fired upon
        resolve_forced_fire(state, mover=s, defender=rival)
    else:
        state.turn_ship = rival
```

### 5a. Action-banking across turns — confirmed

Only actions beyond the base 2 survive into next turn. Concretely, at
end of turn: `rollover = max(0, actions_remaining - 2)`. Ending a turn
with 1 or 2 actions unspent loses them; ending with 3+ unspent carries
the excess over 2. Overcharge purchases add their own +1 each
independently, combining additively with any rollover for next turn's
bank (already reflected in the pseudocode above).

### 5b. Cache spawn/upgrade — confirmed

- Base spawn: `ENERGY, value=10` in single-star systems, `ENERGY,
  value=20` in binary systems.
- Over time, an unclaimed cache either bumps `+10` Energy, or
  transforms into `OVERCHARGE` (grants +1 banked action on collection
  instead of Energy) — `OVERCHARGE` is the rarer outcome of the two.
- A cache can never spawn or upgrade in a system a ship currently
  occupies (avoids incidental reveals from spawn/upgrade events).
- Collection: still triggers only if a ship **starts** its turn in the
  cache's system (not merely passing through) — consumes the cache,
  grants its effect (`ENERGY` → `+value` to `s.energy`; `OVERCHARGE` →
  `+1` to `s.banked_overcharge`, for use *next* turn like Overcharge),
  and exposes the collector, per §5.

```
def advance_system_status(state):
    for sys in state.graph:
        if is_occupied(state, sys):
            continue  # placement restriction — skip this tick entirely
        if sys not in state.system_cache or state.system_cache[sys] is None:
            spawn(state, sys, base_value(sys))  # 10 single-star / 20 binary
        elif due_for_upgrade(state, sys):         # exact tick period: TBD
            upgrade_or_transform(state, sys)       # +10 Energy, or -> OVERCHARGE (rare)
```

**Remaining unknown:** the exact tick period between upgrades and the
Energy-vs-Overcharge transform probability aren't specified — treat
both as tunable simulator parameters for now (e.g. upgrade every N
turns) and adjust later if it turns out to matter for strategy; the
qualitative shape of the mechanic (escalating value, rare overcharge
upgrade, no reveal via spawn) is what's load-bearing for ship behavior,
not the precise timing.

---

## 6. Reward & Episode Definition

- Recommend **one skirmish = one training episode** (bounded,
  single-map, ends on a Fire/forced-reveal resolution).
- Recommend **sparse terminal reward**: +1 win / −1 loss / 0 otherwise,
  trained via self-play. Avoid shaping rewards (e.g. per-system-
  controlled bonuses) initially — this is a deception-heavy game and
  shaped rewards are easy to game in ways that don't transfer to real
  skill.
- Add a defensive **turn cap** for the simulator (e.g. 200 turns) with
  a defined draw/timeout resolution, even though the real game may have
  no such cap — needed to bound training episodes.
- Campaign-level structure (best-of-5, elo/rank) is a meta-layer on top
  of single skirmishes; not needed for the core agent, only if you
  eventually want to model skirmish-to-skirmish adaptation (e.g. rival
  modeling across a campaign).
- **Confirmed:** Energy resets to 0 at the start of each new skirmish.
  Treating system control, cloak status, banked overcharge, and
  unlocked abilities as resetting the same way by inference (fresh map
  each skirmish) — flag this if it turns out any of those actually
  persist.

---

## 7. Open Items — Status

**Confirmed:**
1. Rival's exact Energy total is fully hidden.
2. Action-banking: at end of turn, `rollover = max(0, actions_remaining - 2)` — unspent actions beyond the base 2 carry forward, 1 or 2 leftover are simply lost. Overcharge purchases add their own +1 independently. See §5a.
3. Cache spawn/upgrade mechanics — base values, upgrade path, and the no-reveal placement restriction. See §5b.
4. Energy does **not** carry over between skirmishes — resets to 0 at the start of each new skirmish/map. Assuming (not yet separately confirmed, but strongly implied) that system control, cloak status, banked overcharge, and unlocked abilities all reset the same way, since each skirmish is a fresh map.
5. Starting positions are randomized, but not uniformly — constrained by something like "not adjacent to each other" and "similar distance to binary systems" for fairness. Exact constraint set is still fuzzy; treat as a placeholder spawn function until verified empirically (e.g. log spawn pairs across ~20 skirmish-starts and check what's actually enforced vs. coincidental).
6. "Could be here" belief tracking — explained with a worked example (§3a).

**Still open:**
- Exact cache upgrade tick period and Energy-vs-Overcharge transform probability (§5b) — treat as tunable parameters, not blocking implementation.

---

## 8. Suggested Next Steps

1. Lock in the remaining §7 item where feasible (quick, targeted test games).
2. Implement `GameState`/`ShipState` + `apply_action` as the core simulator (no RL yet) — validate against logged real games or manual playthroughs.
3. Wrap as a `PettingZoo`-style two-agent environment (better fit than single-agent Gym for adversarial self-play) exposing the observation in §3 per ship.
4. Start self-play (PPO or NFSP) once the simulator round-trips correctly against a few hand-played reference games.
