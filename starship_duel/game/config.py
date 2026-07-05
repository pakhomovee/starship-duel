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

    # -- system status / supernova (spec 5) ---------------------------------
    # Timing unspecified in the spec; default OFF for a clean, testable core.
    enable_supernova: bool = False
    destabilize_prob: float = 0.02      # per stable, unoccupied system per tick
    supernova_prob: float = 0.5         # destabilizing -> supernova per tick

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

    # -- belief tracking -----------------------------------------------------
    # Remove observer-owned systems from a cloaked rival's candidate set (a
    # cloaked rival cannot sit in a system you own without having exposed
    # itself on entry).  Approximate -- ignores the Deep-Cloak exception.
    belief_prune_owned: bool = True
