"""Tunable simulator parameters.

Every number the spec left as "TBD / tunable" lives here so experiments can
sweep them without touching game logic.  Defaults follow the spec's stated
values where given, and conservative choices where the spec is open.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GameConfig:
    # -- economy (spec 5) ----------------------------------------------------
    income_single: int = 1
    income_binary: int = 4
    base_actions: int = 2

    # -- domination victory: reward controlling the map ----------------------
    # Besides the instant-kill FIRE, a ship also wins by out-holding the map:
    # at the start of each of its turns it banks "domination" points equal to
    # the income from the systems it currently owns (single -> income_single,
    # binary -> income_binary; collapsed stars count for nothing).  First to
    # ``domination_target`` wins.  This is the engine of dynamic play -- to
    # score you must CLAIM (which exposes you), then defend that territory and
    # contest the rival's, so scouting, abilities and attacks all earn their
    # keep instead of hiding until the field collapses.
    domination_enabled: bool = True
    # Territory-control mode (enable_instakill=False, below): FIRE only *raids*
    # domination points, so games run longer and are decided purely on the
    # control race -- raised 25 -> 40 to give that race room to breathe now that
    # a knockout can't end things early.  Also lifts the effective energy budget
    # (energy and domination share the income stream), making abilities more
    # affordable.  Tune from results.
    domination_target: int = 80

    # -- action costs (spec 4) ----------------------------------------------
    # Priced against the actual energy budget: a ship earns ~16 Energy over a
    # typical game (income + caches) and rarely holds more than ~12-25 at once,
    # so the old 25-40 costs made most abilities unreachable.  These are tiered
    # so every ability is a real option: cheap tactical tools you can afford most
    # games, mid unlocks that need a few turns of territory, and power unlocks
    # that reward a richer economy.
    # Rebalance iteration 1 (with domination on): energy and domination share the
    # same income stream, so total spendable energy over a game ~= domination_target
    # (~25) and the winning line -- claim, accrue, win on points -- needs no
    # spending.  Only DEEP_CLOAK earned its keep (it defends a control lead).  So
    # OVERCHARGE and the power unlocks are cut hard, and caches (below) are boosted
    # to create spendable surplus that does NOT advance domination.
    # Rebalance iteration 2 (territory-control): a FREE-and-unlimited FIRE was a
    # "scan-and-raid" -- the policy sprayed it every turn, so paying energy to
    # locate (SCAN) was strictly dominated and SCAN/LRS/JAMMING/DEEP_CLOAK all
    # went unused.  Fix: FIRE now costs energy (charged on every attempt, hit or
    # miss), so blind spam self-punishes and a raid becomes a committed strike you
    # take only when protected (DEEP_CLOAK), able to escape (OVERCHARGE), or when
    # the rival is too energy-poor to raid back.  SCAN is made FREE so locating is
    # pure tempo -- the read you want before committing an energy-costed raid.
    cost_scan: int = 0                       # FREE: locating is tempo-only, no energy
    cost_fire: int = 3                       # raiding costs energy (territory mode only)
    cost_deep_cloak: int = 14                # mid: claim / sit in enemy space safely
    cost_overcharge: int = 6                 # was 18: +1 action must be cheap to snowball claims
    cost_unlock_proximity_alert: int = 6     # cheap permanent defense
    cost_unlock_long_range_scanners: int = 10  # was 20: make jump-onto-rival kills a real threat
    cost_unlock_jamming: int = 8             # was 14: affordable once abilities are worth hiding

    # Deep Cloak: how many of the ship's own turns it stays undetectable for
    # (immune to exposure + end-of-turn forced fire).  2 => it covers this turn,
    # the rival's turn, and the ship's next turn.
    deep_cloak_duration: int = 2

    # -- caches (spec 5b) ----------------------------------------------------
    # The spec's pseudocode spawns a cache in *every* empty system every tick,
    # which floods the map with energy and removes all scarcity.  Instead we
    # keep a small, contested pool that escalates in value: at most
    # ``max_active_caches`` exist at once, a new one appears every
    # ``cache_spawn_period`` turns at a random eligible system, and uncollected
    # caches grow -- turning them into juicy-but-exposing objectives worth
    # fighting over.  All values are tunable.
    max_active_caches: int = 4          # was 3: more spendable surplus (caches do NOT advance domination)
    cache_spawn_period: int = 2         # was 3: more contested energy on the map (grabbing exposes you)
    cache_base_single: int = 10
    cache_base_binary: int = 20
    cache_upgrade_period: int = 4       # turns between upgrade ticks
    cache_upgrade_energy_step: int = 10  # +Energy on a non-transform upgrade
    cache_overcharge_transform_prob: float = 0.2  # rarer outcome (TBD in spec)
    cache_spawn_in_binaries: bool = False  # binaries already pay via ownership

    # -- the collapse: a shrinking field (bounds game length) ---------------
    # Systems collapse into supernovae on a deterministic schedule, from the
    # outside in toward a random surviving "eye", forcing the two ships together
    # so every game resolves in ~100 plies (~50 turns each) instead of dragging
    # on.  A system first DESTABILIZES (a warning to evacuate) then goes
    # SUPERNOVA; a ship caught on a supernova system is destroyed.
    shrink_enabled: bool = True
    shrink_start_turn: int = 24     # ply at which the first (outermost) system goes
    shrink_interval: int = 6        # plies between successive system collapses
    # Plies a system spends DESTABILIZING (visibly warning) before it goes
    # supernova.  6 plies = 3 of EACH player's own turns of advance notice.
    shrink_warning: int = 6

    # -- FIRE semantics: assassination vs territory-control ------------------
    # enable_instakill=True: the classic duel -- FIRE while co-located with the
    #   rival is an instant win (end_reason "fire_hit").
    # enable_instakill=False (default): territory-control -- FIRE is a "raid"
    #   that transfers ``fire_domination_steal`` domination points from the rival
    #   to the shooter (capped at what the rival has) and exposes the raider,
    #   instead of killing.  The only win paths become domination and death by
    #   collapse, so combat becomes a comeback/anti-leader tool rather than a
    #   coin-flip assassination -- and locating the rival (SCAN / Long-Range
    #   Scanners) finally earns its keep.  A deep-cloaked rival can't be raided.
    enable_instakill: bool = False
    fire_domination_steal: int = 10

    # -- lives / the hunt ----------------------------------------------------
    # A kill dimension on top of territory control: a landed FIRE (raid) also
    # costs the rival a LIFE and respawns it hidden at a random safe system, so
    # the victim must be re-located after every hit -- which makes *hunting* a
    # real win path and gives the locate tools (SCAN / LRS / tracking) a job.
    # Run the rival out of ``lives`` to win by "eliminated".  Deep cloak prevents
    # the hit outright, so it defends lives as well as territory.
    lives_enabled: bool = True
    lives: int = 3
    # Capture-on-raid: a landed FIRE also flips the co-located system to the
    # shooter (if the rival owned it).  This is what makes combat a *threat you
    # must answer* -- to raid you hunt the enemy ship into its own territory and
    # take the ground under it, so the leader must defend (needs PROXIMITY_ALERT
    # to see it coming) and the attacker must locate/track (SCAN / LRS) and punch
    # through radar (DEEP_CLOAK).  Without this, combat is an ignorable point-skim
    # and the game collapses to a first-mover claim race.
    fire_captures_system: bool = True

    # -- turn-order fairness (komi) -----------------------------------------
    # The mover-one tempo edge compounds in a pure claim race (measured mirror
    # first-mover win rate ~76%).  Give the SECOND mover a starting handicap to
    # pull that toward ~55%.  Tune ``komi_domination`` up if first-mover stays
    # high, down if it overshoots; ``komi_energy`` gives a little turn-one agency.
    # A flat domination head-start alone dilutes against the compounding tempo
    # race, so komi is tempo-aware: ``komi_energy`` = one OVERCHARGE (cost 6) lets
    # the second mover buy an extra action on turn one to match the first mover's
    # claim, and ``komi_domination`` tops up the points race.  These are a first
    # guess -- calibrate both from the retrained mirror first-mover win rate.
    komi_domination: int = 8
    komi_energy: int = 6

    # -- radar unlocks: information tools with a job (see _do_jump/_start_turn) --
    # PROXIMITY_ALERT (defensive): you detect the rival when it moves onto or next
    #   to a system you own or your ship -- an early warning of an incoming raid.
    # LONG_RANGE_SCANNERS (offensive): you passively track the rival's exact
    #   system each turn while it is within ``lrs_range`` hops of you, and you see
    #   ownership two hops out.  Lets you line up capture-raids without spending a
    #   SCAN every turn.
    # Both are defeated by DEEP_CLOAK (immune to every exposure trigger), so cloak
    # is the way to punch a raid through a radar-defended opponent.
    lrs_range: int = 2

    # End-of-turn forced fire (spec 5): if a ship ends its turn co-located with
    # the rival without having FIRE-d, the rival auto-fires and the mover loses.
    # Disabled by default -- a kill now requires actively choosing FIRE.
    enable_forced_fire: bool = False

    # -- episode bounds (spec 6) --------------------------------------------
    turn_cap: int = 200
    # How a turn-cap timeout resolves: "draw" | "systems" (most systems owned,
    # then most energy, else draw).
    timeout_resolution: str = "draw"
    turn_clock_start: float = 60.0

    # -- reveal semantics (documented ambiguity, see README) ----------------
    # False (default): jumping onto the rival exposes only the *mover*; the
    #   Long-Range-Scanners unlock is what reveals the *defender* to the mover.
    # True: literal spec text -- entering the rival's system reveals *both*.
    reveal_both_on_colocation_entry: bool = False

    # Seed each side's belief with the rival's *exact* starting system (both
    # initial positions are revealed at skirmish start, then re-hidden as ships
    # move under cloak).  Matches the spec's "last confirmed position" model and
    # is much more playable.  Set False for maximal initial uncertainty (belief
    # seeded to the whole spawn-consistent set instead).
    reveal_initial_positions: bool = True

    # -- belief tracking -----------------------------------------------------
    # Remove observer-owned systems from a cloaked rival's candidate set.  This
    # was sound before Deep Cloak (entering an owned system exposes you), but a
    # deep-cloaked ship can now sit in your territory undetected, so the prune
    # can wrongly rule out the rival's true system.  Default OFF to preserve the
    # belief-soundness invariant (true position always in the candidate set).
    belief_prune_owned: bool = False
