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

    # -- action costs (spec 4) ----------------------------------------------
    cost_scan: int = 10
    cost_deep_cloak: int = 25
    cost_overcharge: int = 40
    cost_unlock_proximity_alert: int = 10
    cost_unlock_long_range_scanners: int = 40
    cost_unlock_jamming: int = 25

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
    max_active_caches: int = 3
    cache_spawn_period: int = 3         # turns between spawn attempts (0 = never)
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
