"""Mechanics tests for the Starship Duel simulator (stdlib unittest)."""

import unittest

from starship_duel.bots import make_bot
from starship_duel.game import (
    Action,
    Cache,
    CacheKind,
    Engine,
    GameConfig,
    build_observation,
)
from starship_duel.game.maps import REFERENCE_MAP
from starship_duel.run import play_skirmish


def fresh_engine(**cfg):
    # Disable the shrinking field by default so per-mechanic unit tests aren't
    # perturbed by systems collapsing; the shrink has its own tests below.
    cfg.setdefault("shrink_enabled", False)
    e = Engine(config=GameConfig(**cfg), seed=1)
    e.reset(map_id="reference", first_ship=0)
    return e


class TestMap(unittest.TestCase):
    def test_reference_map_symmetric(self):
        REFERENCE_MAP.validate()  # raises on any asymmetry

    def test_spawn_distance(self):
        for seed in range(30):
            e = Engine(seed=seed)
            e.reset(map_id="reference")
            p0, p1 = e.state.ships[0].position, e.state.ships[1].position
            self.assertGreaterEqual(e.map.hop_distance(p0, p1), 2)


class TestBasicActions(unittest.TestCase):
    def test_jump_moves_and_costs_action(self):
        e = fresh_engine()
        s = e.current_ship
        pos = e.state.ships[s].position
        before = e.state.ships[s].actions_remaining
        dest = e.map.neighbors(pos)[0]
        e.apply_action(Action.jump(dest))
        self.assertEqual(e.state.ships[s].position, dest)
        self.assertEqual(e.state.ships[s].actions_remaining, before - 1)

    def test_claim_sets_owner_exposes_and_pins_belief(self):
        e = fresh_engine()
        s = e.current_ship  # 0
        pos = e.state.ships[s].position
        e.apply_action(Action.claim())
        self.assertEqual(e.state.system_owner[pos], s)
        self.assertFalse(e.state.ships[s].cloaked)          # exposed
        self.assertEqual(e.belief[1 - s].candidates, {pos})  # rival now knows

    def test_income_after_claim(self):
        e = fresh_engine()
        s = e.current_ship
        pos = e.state.ships[s].position
        is_binary = pos in e.state.binary_systems
        e.apply_action(Action.claim())
        e.apply_action(Action.end_turn())        # ship s ends
        e.apply_action(Action.end_turn())        # rival ends -> s starts again
        expected = e.config.income_binary if is_binary else e.config.income_single
        self.assertEqual(e.state.ships[s].energy, expected)


class TestActionBanking(unittest.TestCase):
    def test_rollover_only_beyond_base(self):
        e = fresh_engine()
        s = e.current_ship
        e.state.ships[s].actions_remaining = 4  # pretend we had 4
        e.apply_action(Action.end_turn())
        self.assertEqual(e.state.ships[s].banked_overcharge, 2)  # 4 - base(2)

    def test_two_unspent_are_lost(self):
        e = fresh_engine()
        s = e.current_ship
        self.assertEqual(e.state.ships[s].actions_remaining, 2)
        e.apply_action(Action.end_turn())
        self.assertEqual(e.state.ships[s].banked_overcharge, 0)

    def test_overcharge_banks_and_spends_energy(self):
        e = fresh_engine()
        s = e.current_ship
        e.state.ships[s].energy = e.config.cost_overcharge
        e.apply_action(Action.overcharge())
        self.assertEqual(e.state.ships[s].energy, 0)
        self.assertEqual(e.state.ships[s].banked_overcharge, 1)


class TestCombat(unittest.TestCase):
    def test_fire_hit_wins(self):
        # Classic assassination mode: a co-located FIRE is an instant win.
        e = fresh_engine(enable_instakill=True)
        s = e.current_ship
        e.state.ships[1 - s].position = e.state.ships[s].position  # co-locate
        e.apply_action(Action.fire())
        self.assertTrue(e.state.done)
        self.assertEqual(e.state.winner, s)
        self.assertEqual(e.state.end_reason, "fire_hit")

    def test_fire_raid_steals_domination(self):
        # Territory-control (default): FIRE transfers domination and does NOT end
        # the game; a deep-cloaked rival is immune.
        e = fresh_engine(fire_domination_steal=5)
        s = e.current_ship
        rival = 1 - s
        e.state.ships[rival].position = e.state.ships[s].position  # co-locate
        e.state.ships[s].energy = e.config.cost_fire  # raiding now costs energy
        e.state.domination[rival] = 8
        e.state.domination[s] = 3
        e.apply_action(Action.fire())
        self.assertFalse(e.state.done)                 # raid never ends the game
        self.assertEqual(e.state.domination[rival], 3)  # 8 - 5
        self.assertEqual(e.state.domination[s], 8)      # 3 + 5
        self.assertEqual(e.state.ships[s].energy, 0)    # cost_fire was spent
        # A deep-cloaked rival can't be raided.
        e2 = fresh_engine(fire_domination_steal=5)
        s2 = e2.current_ship
        e2.state.ships[1 - s2].position = e2.state.ships[s2].position
        e2.state.ships[1 - s2].deep_cloak_turns_left = 2
        e2.state.ships[s2].energy = e2.config.cost_fire
        e2.state.domination[1 - s2] = 8
        e2.apply_action(Action.fire())
        self.assertEqual(e2.state.domination[1 - s2], 8)  # untouched

    def test_deep_cloaked_claim_is_invisible_to_rival(self):
        # Lever A: a deep-cloaked CLAIM flips true ownership but stays hidden in
        # the rival's fogged view until it patrols there; an uncloaked claim is
        # seen immediately.
        e = fresh_engine()
        s = e.current_ship
        rival = 1 - s
        # Put the rival somewhere, and the claimer on a system the rival can't see
        # (not the rival's own system and not adjacent to it).
        r_pos = e.state.ships[rival].position
        blind = [x for x in e.map.systems
                 if x != r_pos and x not in set(e.map.neighbors(r_pos))]
        target = blind[0]
        e.state.ships[s].position = target
        e.state.ships[s].deep_cloak_turns_left = 2  # cloaked

        e.apply_action(Action.claim())
        self.assertEqual(e.state.system_owner[target], s)          # true owner flipped
        rival_view = build_observation(e, rival).system_owner
        self.assertIsNone(rival_view[target])                       # ...but rival can't see it
        self.assertEqual(build_observation(e, s).system_owner[target], s)  # claimer knows

        # An uncloaked claim on a fresh engine is visible to the rival at once.
        e2 = fresh_engine()
        s2 = e2.current_ship
        r2 = 1 - s2
        r2_pos = e2.state.ships[r2].position
        blind2 = [x for x in e2.map.systems
                  if x != r2_pos and x not in set(e2.map.neighbors(r2_pos))]
        tgt2 = blind2[0]
        e2.state.ships[s2].position = tgt2
        e2.apply_action(Action.claim())                            # not cloaked
        self.assertEqual(build_observation(e2, r2).system_owner[tgt2], s2)

    def test_info_war_kit(self):
        # #3: LRS = 2-hop vision; SCAN un-fogs ownership around the rival; JAMMING
        # hides the jammer's territory from enemy scans.
        e = fresh_engine()
        s = e.current_ship
        rival = 1 - s
        p = e.state.ships[s].position
        one_hop = set(e.map.neighbors(p))
        two_hop = {q for n in one_hop for q in e.map.neighbors(n)} - one_hop - {p}
        self.assertTrue(two_hop, "need a 2-hop system for the test")
        far = next(iter(two_hop))
        e.state.system_owner[far] = rival            # rival secretly owns a far system

        # Without LRS the far system is out of sight.
        e._observe_local(s)
        self.assertNotIn(far, e.owner_known[s])
        # With LRS the second ring is sensed.
        e.state.ships[s].unlocked["long_range_scanners"] = True
        e._observe_local(s)
        self.assertIn(far, e.owner_known[s])
        self.assertEqual(e.state.system_owner[far], rival)

        # A rival-owned system next to the rival but OUT of the scanner's own
        # sight -- so only a SCAN could reveal it.
        def blind_rival_neighbor(eng, scanner, rid):
            rpos = eng.state.ships[rid].position
            vis = {eng.state.ships[scanner].position} | set(
                eng.map.neighbors(eng.state.ships[scanner].position))
            for q in eng.map.neighbors(rpos):
                if q not in vis:
                    return q
            return None

        # SCAN maps ownership around the rival's position.
        e2 = fresh_engine()
        s2 = e2.current_ship
        r2 = 1 - s2
        neigh = blind_rival_neighbor(e2, s2, r2)
        self.assertIsNotNone(neigh)
        e2.state.system_owner[neigh] = r2
        self.assertNotIn(neigh, e2.owner_known[s2])    # unseen before the scan
        e2.apply_action(Action.scan())
        self.assertIn(neigh, e2.owner_known[s2])        # scan revealed it
        self.assertEqual(build_observation(e2, s2).system_owner[neigh], r2)

        # JAMMING hides the jammer's own systems from the scan.
        e3 = fresh_engine()
        s3 = e3.current_ship
        r3 = 1 - s3
        neigh3 = blind_rival_neighbor(e3, s3, r3)
        self.assertIsNotNone(neigh3)
        e3.state.system_owner[neigh3] = r3
        e3.state.ships[r3].unlocked["jamming"] = True
        e3.apply_action(Action.scan())
        self.assertNotIn(neigh3, e3.owner_known[s3])   # jammed territory stays fogged

    def test_komi_gives_second_mover_a_head_start(self):
        e = fresh_engine()  # reference map, first_ship=0 -> second mover is seat 1
        self.assertEqual(e.state.domination[1], REFERENCE_MAP.komi_domination)
        self.assertEqual(e.state.domination[0], 0)

    def test_capture_on_raid_flips_the_system(self):
        e = fresh_engine(fire_domination_steal=5)
        s = e.current_ship
        rival = 1 - s
        pos = e.state.ships[s].position
        e.state.ships[rival].position = pos          # co-locate
        e.state.system_owner[pos] = rival            # rival holds the ground
        e.state.ships[s].energy = e.config.cost_fire
        e.state.domination[rival] = 8
        e.apply_action(Action.fire())
        self.assertEqual(e.state.system_owner[pos], s)   # captured on the raid

    def _prox_setup(self, **cfg):
        """Engine with the defender holding proximity alert and the mover parked
        one hop from a system adjacent to the defender's ship."""
        e = fresh_engine(**cfg)
        s = e.current_ship
        rival = 1 - s
        e.state.ships[rival].unlocked["proximity_alert"] = True
        rpos = e.state.ships[rival].position
        n = next(iter(e.map.neighbors(rpos)))               # adjacent to defender
        approach = next(x for x in e.map.neighbors(n) if x != rpos)
        e.state.ships[s].position = approach
        e.state.ships[s].cloaked = True
        return e, s, rival, n

    def test_proximity_alert_pierces_cloak_but_jamming_blinds_it(self):
        # RPS: PROX beats DEEP_CLOAK (short-range alarm), JAMMING beats PROX.
        # Plain cloaked intruder -> detected.
        e, s, rival, n = self._prox_setup()
        e.apply_action(Action.jump(n))
        self.assertEqual(build_observation(e, rival).rival_position, n)

        # Deep-cloaked intruder -> STILL detected (the alarm pierces cloak).
        e2, s2, r2, n2 = self._prox_setup()
        e2.state.ships[s2].deep_cloak_turns_left = 2
        e2.apply_action(Action.jump(n2))
        self.assertEqual(build_observation(e2, r2).rival_position, n2)

        # Intruder running JAMMING -> undetected (electronic warfare blinds it).
        e3, s3, r3, n3 = self._prox_setup()
        e3.state.ships[s3].deep_cloak_turns_left = 2
        e3.state.ships[s3].unlocked["jamming"] = True
        e3.apply_action(Action.jump(n3))
        self.assertIsNone(build_observation(e3, r3).rival_position)

    def test_lrs_passively_tracks_rival_in_range(self):
        e = fresh_engine()
        s = e.current_ship
        rival = 1 - s
        e.state.ships[s].unlocked["long_range_scanners"] = True
        within = e._systems_within(e.state.ships[s].position, e.config.lrs_range)
        target = next(x for x in within if x != e.state.ships[s].position)
        e.state.ships[rival].position = target
        e.state.ships[rival].cloaked = True
        e.belief[s].unpin()                                  # belief goes fuzzy...
        e._start_turn(s)
        self.assertEqual(build_observation(e, s).rival_position, target)  # ...LRS re-pins it

    def test_lrs_pins_in_range_rival_on_unlock(self):
        # Buying Long-Range Scanners mid-turn must pin an in-range rival THIS turn
        # (not only at the next _start_turn), so its locating value -- and the
        # Fire->snipe it enables -- kicks in the moment it's purchased.
        e = fresh_engine()
        s = e.current_ship
        rival = 1 - s
        e.state.ships[s].energy = e.config.cost_unlock_long_range_scanners
        n = next(iter(e.map.neighbors(e.state.ships[s].position)))  # adjacent
        e.state.ships[rival].position = n
        e.state.ships[rival].cloaked = True
        e.belief[s].reset(set(e.map.systems))                # belief fully fuzzy
        from starship_duel.game.types import ActionType
        e.apply_action(Action(ActionType.UNLOCK_LONG_RANGE_SCANNERS))
        self.assertEqual(build_observation(e, s).rival_position, n)  # pinned now

    def test_lrs_enables_ranged_raid(self):
        e = fresh_engine(fire_domination_steal=5)
        s = e.current_ship
        rival = 1 - s
        e.state.ships[s].unlocked["long_range_scanners"] = True
        e.state.ships[s].energy = e.config.cost_fire
        n = next(iter(e.map.neighbors(e.state.ships[s].position)))  # adjacent, not co-located
        e.state.ships[rival].position = n
        e.state.system_owner[n] = rival
        e.state.domination[rival] = 8
        e.apply_action(Action.fire())
        self.assertEqual(e.state.system_owner[n], s)     # sniped and captured at range
        self.assertEqual(e.state.domination[s], 5)

    def test_proximity_alert_shields_territory_but_jamming_punches_through(self):
        # PROX blocks the capture (points still stolen); a JAMMING raider ignores it.
        e = fresh_engine(fire_domination_steal=5)
        s = e.current_ship
        rival = 1 - s
        pos = e.state.ships[s].position
        e.state.ships[rival].position = pos
        e.state.system_owner[pos] = rival
        e.state.ships[rival].unlocked["proximity_alert"] = True
        e.state.ships[s].energy = e.config.cost_fire
        e.state.domination[rival] = 8
        e.apply_action(Action.fire())
        self.assertEqual(e.state.system_owner[pos], rival)   # shield held the ground
        self.assertEqual(e.state.domination[s], 5)           # points still raided

        e2 = fresh_engine(fire_domination_steal=5)
        s2 = e2.current_ship
        r2 = 1 - s2
        p2 = e2.state.ships[s2].position
        e2.state.ships[r2].position = p2
        e2.state.system_owner[p2] = r2
        e2.state.ships[r2].unlocked["proximity_alert"] = True
        e2.state.ships[s2].unlocked["jamming"] = True
        e2.state.ships[s2].energy = e2.config.cost_fire
        e2.state.domination[r2] = 8
        e2.apply_action(Action.fire())
        self.assertEqual(e2.state.system_owner[p2], s2)      # jammed through the shield

    def test_scan_recon_sweeps_whole_map_even_vs_deep_cloak(self):
        e = fresh_engine()
        s = e.current_ship
        rival = 1 - s
        near = e._systems_within(e.state.ships[s].position, 1)
        far = next(x for x in e.map.systems if x not in near)
        e.state.system_owner[far] = rival
        e.state.ships[rival].deep_cloak_turns_left = 2     # deep-cloaked
        e.apply_action(Action.scan())
        self.assertIn(far, e.owner_known[s])                # ownership still swept
        self.assertEqual(build_observation(e, s).system_owner[far], rival)

    def test_jamming_makes_claims_permanently_silent(self):
        e = fresh_engine()
        s = e.current_ship
        rival = 1 - s
        rpos = e.state.ships[rival].position
        blind = [x for x in e.map.systems
                 if x != rpos and x not in set(e.map.neighbors(rpos))]
        target = blind[0]
        e.state.ships[s].position = target
        e.state.ships[s].unlocked["jamming"] = True
        e.state.ships[s].cloaked = False                    # uncloaked, yet silent
        e.apply_action(Action.claim())
        self.assertEqual(e.state.system_owner[target], s)   # really taken
        self.assertNotIn(target, e.owner_known[rival])      # rival never learns of it

    def test_fire_costs_a_life_and_respawns_the_victim(self):
        e = fresh_engine(fire_domination_steal=5)
        s = e.current_ship
        rival = 1 - s
        pos = e.state.ships[s].position
        e.state.ships[rival].position = pos              # co-located
        e.state.ships[s].energy = e.config.cost_fire
        e.state.domination[rival] = 8
        e.apply_action(Action.fire())
        self.assertFalse(e.state.done)                   # 2 lives left -> game continues
        self.assertEqual(e.state.lives[rival], 2)
        self.assertNotEqual(e.state.ships[rival].position, pos)   # respawned away, hidden
        self.assertTrue(e.state.ships[rival].cloaked)

    def test_running_rival_out_of_lives_eliminates(self):
        e = fresh_engine(lives=1)
        s = e.current_ship
        rival = 1 - s
        e.state.ships[rival].position = e.state.ships[s].position
        e.state.ships[s].energy = e.config.cost_fire
        e.apply_action(Action.fire())
        self.assertTrue(e.state.done)
        self.assertEqual(e.state.winner, s)
        self.assertEqual(e.state.end_reason, "eliminated")

    def test_deep_cloak_protects_lives(self):
        e = fresh_engine()
        s = e.current_ship
        rival = 1 - s
        e.state.ships[rival].position = e.state.ships[s].position
        e.state.ships[rival].deep_cloak_turns_left = 2   # immune to the hit
        e.state.ships[s].energy = e.config.cost_fire
        e.apply_action(Action.fire())
        self.assertEqual(e.state.lives[rival], 3)        # life untouched
        self.assertFalse(e.state.done)

    def test_fire_miss_is_not_terminal(self):
        e = fresh_engine()
        s = e.current_ship
        e.state.ships[s].energy = e.config.cost_fire  # afford the shot
        # ensure not co-located
        others = [x for x in e.map.systems if x != e.state.ships[s].position]
        e.state.ships[1 - s].position = others[0]
        e.apply_action(Action.fire())
        self.assertFalse(e.state.done)

    def test_ending_turn_colocated_is_safe_by_default(self):
        # Auto-fire is off by default: ending co-located is no longer fatal.
        e = fresh_engine()
        s = e.current_ship
        rival = 1 - s
        e.state.ships[rival].position = e.state.ships[s].position
        e.apply_action(Action.end_turn())
        self.assertFalse(e.state.done)

    def test_forced_fire_when_enabled(self):
        e = fresh_engine(enable_forced_fire=True)
        s = e.current_ship
        rival = 1 - s
        e.state.ships[rival].position = e.state.ships[s].position
        e.apply_action(Action.end_turn())
        self.assertTrue(e.state.done)
        self.assertEqual(e.state.winner, rival)
        self.assertEqual(e.state.end_reason, "forced_fire")


class TestCaches(unittest.TestCase):
    def test_energy_cache_collected_on_turn_start(self):
        e = fresh_engine()
        s = e.current_ship          # 0
        rival = 1 - s
        rpos = e.state.ships[rival].position
        e.state.system_cache[rpos] = Cache(CacheKind.ENERGY, value=13)
        e.apply_action(Action.end_turn())  # s ends -> rival start_turn collects
        self.assertEqual(e.current_ship, rival)
        self.assertGreaterEqual(e.state.ships[rival].energy, 13)
        self.assertIsNone(e.state.system_cache[rpos])
        self.assertFalse(e.state.ships[rival].cloaked)  # collection exposes

    def test_overcharge_cache_grants_banked_action(self):
        e = fresh_engine()
        s = e.current_ship
        rival = 1 - s
        rpos = e.state.ships[rival].position
        e.state.system_cache[rpos] = Cache(CacheKind.OVERCHARGE, value=0)
        e.apply_action(Action.end_turn())
        # Per spec 5b, a collected OVERCHARGE cache banks +1 for *next* turn,
        # so this turn keeps the base action count and the bank shows 1.
        self.assertEqual(e.state.ships[rival].actions_remaining, e.config.base_actions)
        self.assertEqual(e.state.ships[rival].banked_overcharge, 1)

    def test_active_caches_respect_cap(self):
        """Caches are a scarce contested pool, not a flood over every system."""
        cap = 3
        e = fresh_engine(max_active_caches=cap, cache_spawn_period=2)
        s = e.current_ship
        for _ in range(60):
            if e.state.done:
                break
            active = sum(1 for c in e.state.system_cache.values() if c is not None)
            self.assertLessEqual(active, cap)
            e.apply_action(Action.end_turn())
        # And the pool actually fills up to the cap rather than staying empty.
        active = sum(1 for c in e.state.system_cache.values() if c is not None)
        self.assertEqual(active, cap)


class TestBelief(unittest.TestCase):
    def test_cloaked_jump_expands_candidate_set(self):
        e = fresh_engine()
        s = e.current_ship  # 0; belief[1] tracks ship 0
        before = e.belief[1].candidates
        pos = e.state.ships[s].position
        expected = set()
        for c in before:
            expected.update(e.map.neighbors(c))
        e.apply_action(Action.jump(e.map.neighbors(pos)[0]))
        self.assertEqual(e.belief[1].candidates, expected)

    def test_initial_positions_revealed(self):
        # Default config reveals both spawns -> rival's exact system is given.
        e = fresh_engine()
        obs = build_observation(e, 0)
        self.assertEqual(obs.rival_position, e.state.ships[1].position)

    def test_uncertain_start_when_disabled(self):
        # No initial reveal -> observation gives no exact position, but the
        # engine's (UI-only) belief still soundly contains the truth.
        e = fresh_engine(reveal_initial_positions=False)
        obs = build_observation(e, 0)
        self.assertIsNone(obs.rival_position)
        self.assertGreater(len(e.belief[0].candidates), 1)
        self.assertIn(e.state.ships[1].position, e.belief[0].candidates)

    def test_observation_omits_fuzzy_candidate_set(self):
        # The "could be here" set must not be handed to bots anymore.
        e = fresh_engine()
        obs = build_observation(e, 0)
        self.assertFalse(hasattr(obs, "candidate_systems"))


class TestPublicActionLog(unittest.TestCase):
    """What the rival can read off a turn (obs.rival_last_turn_actions)."""

    def _rival_log(self, e, actor):
        return build_observation(e, 1 - actor).rival_last_turn_actions

    def test_cloaked_jump_is_indistinguishable_from_hold(self):
        # The bug this guards: a cloaked ship's JUMP used to be reported by name,
        # handing the rival its every move through the fog.
        e = fresh_engine()
        s = e.current_ship
        self.assertTrue(e.state.ships[s].cloaked)
        e.apply_action(Action.jump(e.map.neighbors(e.state.ships[s].position)[0]))
        e.apply_action(Action.hold())  # second action ends the turn
        self.assertEqual(self._rival_log(e, s), ["UNKNOWN", "UNKNOWN"])

    def test_exposed_moves_are_named(self):
        e = fresh_engine()
        s = e.current_ship
        e.apply_action(Action.claim())  # exposes the ship
        e.apply_action(Action.jump(e.map.neighbors(e.state.ships[s].position)[0]))
        self.assertEqual(self._rival_log(e, s), ["CLAIM", "JUMP"])

    def test_whole_turn_is_logged_not_just_the_last_action(self):
        e = fresh_engine()
        s = e.current_ship
        e.state.ships[s].energy = 100
        e.state.ships[s].actions_remaining = 3
        e.apply_action(Action.scan())
        e.apply_action(Action.claim())          # exposes the ship
        e.apply_action(Action.hold())           # re-cloaks it -> the rival loses it
        obs = build_observation(e, 1 - s)
        self.assertEqual(obs.rival_last_turn_actions, ["SCAN", "CLAIM", "UNKNOWN"])
        self.assertEqual(obs.rival_last_action, "UNKNOWN")  # alias = last entry

    def test_hold_that_recloaks_is_unknown(self):
        # Vanishing is not the same as being seen to sit still: once the ship is
        # cloaked again the rival cannot tell the HOLD from a JUMP either.
        e = fresh_engine()
        s = e.current_ship
        e.apply_action(Action.claim())  # exposed
        e.apply_action(Action.hold())   # re-cloaks
        self.assertEqual(self._rival_log(e, s), ["CLAIM", "UNKNOWN"])

    def test_log_rotates_per_turn(self):
        e = fresh_engine()
        s = e.current_ship
        e.apply_action(Action.claim())
        e.apply_action(Action.end_turn())  # END_TURN itself is not logged
        self.assertEqual(self._rival_log(e, s), ["CLAIM"])
        e.apply_action(Action.end_turn())  # rival's turn, s starts again
        e.apply_action(Action.hold())
        e.apply_action(Action.hold())
        self.assertEqual(self._rival_log(e, s), ["UNKNOWN", "UNKNOWN"])

    def test_jamming_masks_energy_actions_and_claims(self):
        e = fresh_engine()
        s = e.current_ship
        e.state.ships[s].unlocked["jamming"] = True
        e.state.ships[s].energy = 100
        e.apply_action(Action.scan())
        e.apply_action(Action.claim())  # silent expansion
        self.assertEqual(self._rival_log(e, s), ["JAMMED", "JAMMED"])

    def test_deep_cloaked_claim_is_unknown(self):
        e = fresh_engine()
        s = e.current_ship
        e.state.ships[s].energy = 100
        e.apply_action(Action.deep_cloak())
        e.apply_action(Action.claim())  # invisible expansion
        self.assertEqual(self._rival_log(e, s), ["DEEP_CLOAK", "UNKNOWN"])

    def test_fire_is_always_public(self):
        e = fresh_engine(enable_instakill=False)
        s = e.current_ship
        e.state.ships[s].energy = 100
        e.apply_action(Action.fire())  # misses; the shot is still heard
        e.apply_action(Action.hold())
        self.assertEqual(self._rival_log(e, s)[0], "FIRE")


class TestDeepCloak(unittest.TestCase):
    def test_immune_to_exposure_triggers(self):
        e = fresh_engine()
        s = e.current_ship
        e.state.ships[s].energy = 100
        e.apply_action(Action.deep_cloak())
        self.assertTrue(e.state.ships[s].deep_cloak_active)
        e._expose(s, [])  # exposure trigger is a no-op while deep-cloaked
        self.assertTrue(e.state.ships[s].cloaked)

    def test_survives_ending_turn_colocated(self):
        # With forced fire ON, a non-cloaked ship would lose here; deep cloak
        # keeps it undetected so no shot is taken.
        e = fresh_engine(enable_forced_fire=True)
        s = e.current_ship
        rival = 1 - s
        e.state.ships[s].energy = 100
        e.apply_action(Action.deep_cloak())
        e.state.ships[rival].position = e.state.ships[s].position
        e.apply_action(Action.end_turn())
        self.assertFalse(e.state.done)  # undetected -> no forced fire

    def test_expires_after_duration(self):
        e = fresh_engine(deep_cloak_duration=2)
        s = e.current_ship
        e.state.ships[s].energy = 100
        e.apply_action(Action.deep_cloak())
        self.assertEqual(e.state.ships[s].deep_cloak_turns_left, 2)
        e.apply_action(Action.end_turn())   # s ends
        e.apply_action(Action.end_turn())   # rival ends -> s start: 2 -> 1
        self.assertEqual(e.state.ships[s].deep_cloak_turns_left, 1)
        self.assertTrue(e.state.ships[s].deep_cloak_active)
        e.apply_action(Action.end_turn())
        e.apply_action(Action.end_turn())   # s start again: 1 -> 0
        self.assertFalse(e.state.ships[s].deep_cloak_active)


class TestBeliefSoundness(unittest.TestCase):
    def test_true_position_always_in_candidate_set(self):
        """The rival's real system must never be wrongly ruled out (regression
        for the Scan-then-move stale-lock bug)."""
        from starship_duel.env import StarshipDuelEnv, agent_id
        for seed in range(120):
            env = StarshipDuelEnv(config=GameConfig(), seed=seed)
            env.reset()
            bots = {0: make_bot("heuristic"), 1: make_bot("random")}
            st = env.engine.state
            steps = 0
            while not env.done and steps < 5000:
                a = env.agent_selection
                # Soundness is a property of the engine's belief tracker (kept
                # for the UI); the true rival system is never ruled out.
                for o in (0, 1):
                    self.assertIn(st.ships[1 - o].position, env.engine.belief[o].candidates)
                env.step(bots[agent_id(a)].act(env.observe(a)))
                steps += 1


class TestShrinkingField(unittest.TestCase):
    def test_schedule_is_ordered_outside_in(self):
        e = Engine(config=GameConfig(), seed=3)
        e.reset(map_id="reference")
        center = e._shrink_center
        # The eye (center) collapses last; farther systems collapse earlier.
        last = max(e._supernova_turn, key=lambda s: e._supernova_turn[s])
        self.assertEqual(last, center)
        far = max(e.map.systems, key=lambda s: e.map.hop_distance(center, s))
        self.assertLessEqual(e._supernova_turn[far], e._supernova_turn[center])

    def test_ship_dies_on_supernova(self):
        e = Engine(config=GameConfig(), seed=1)
        e.reset(map_id="reference", first_ship=0)
        s = e.current_ship
        # Make this ship's system collapse at the very next turn-start, and
        # keep the rival's system safe so only ``s`` is caught.
        pos = e.state.ships[s].position
        for name in e.map.systems:
            e._supernova_turn[name] = 999
        e._supernova_turn[pos] = e.state.turn_number + 1
        e.apply_action(Action.end_turn())          # s ends -> rival start collapses pos
        self.assertTrue(e.state.done)
        self.assertEqual(e.state.end_reason, "supernova")
        self.assertEqual(e.state.winner, 1 - s)

    def test_all_games_end_within_the_cap(self):
        # With the collapse on, every game must resolve well before the turn cap.
        longest = 0
        for seed in range(60):
            res = play_skirmish(make_bot("random"), make_bot("random"), seed=seed)
            self.assertTrue(res["end_reason"])
            longest = max(longest, res["turns"])
        self.assertLess(longest, 120)  # collapse forces resolution ~turn 96

    def test_survivors_stay_connected(self):
        # The non-collapsed field must remain connected at every step, so a ship
        # can always reach the shrinking core (never stranded by disconnection).
        for seed in range(20):
            e = Engine(config=GameConfig(), seed=seed)
            e.reset(map_id="reference")
            adj = e.map.adjacency
            last = max(e._supernova_turn.values())
            for turn in range(last + 1):
                survivors = {s for s in e.map.systems if e._supernova_turn[s] > turn}
                if len(survivors) <= 1:
                    continue
                start = next(iter(survivors))
                seen, stack = {start}, [start]
                while stack:
                    for m in adj[stack.pop()]:
                        if m in survivors and m not in seen:
                            seen.add(m); stack.append(m)
                self.assertEqual(seen, survivors, f"disconnected @turn {turn} seed {seed}")

    def test_warns_before_collapsing(self):
        # A system spends the warning window DESTABILIZING (with a countdown)
        # before it goes supernova -- 6 plies = 3 turns of notice per player.
        e = Engine(config=GameConfig(), seed=1)
        e.reset(map_id="reference")
        first = min(e._supernova_turn, key=lambda s: e._supernova_turn[s])
        T = e._supernova_turn[first]
        self.assertEqual(e.config.shrink_warning, 6)
        self.assertEqual(e._destabilize_turn[first], T - 6)
        for turn in range(T - e.config.shrink_warning, T + 1):
            e.state.turn_number = turn
            e._advance_system_status()
            if turn < T:
                self.assertEqual(e.state.system_status[first].value, "DESTABILIZING")
                self.assertEqual(e.collapse_in(first), T - turn)
            else:
                self.assertEqual(e.state.system_status[first].value, "SUPERNOVA")


class TestDomination(unittest.TestCase):
    def test_income_and_domination_skip_supernova_systems(self):
        from starship_duel.game.types import SystemStatus
        e = fresh_engine()
        s = 0
        b = next(iter(e.state.binary_systems))
        e.state.system_owner[b] = s
        self.assertEqual(e._controlled_income(s), e.config.income_binary)
        e.state.system_status[b] = SystemStatus.SUPERNOVA
        self.assertEqual(e._controlled_income(s), 0)  # a dead star pays nothing

    def test_domination_wins_on_points(self):
        # Isolate the points mechanic: neutralize the per-map komi head-start
        # (which would otherwise hand the second mover the low test target).
        e = fresh_engine(domination_target=8)
        e.state.domination = [0, 0]
        s = 0  # fresh_engine starts ship 0
        b = next(iter(e.state.binary_systems))
        e.state.system_owner[b] = s
        guard = 0
        while not e.state.done and guard < 40:
            e.apply_action(Action.end_turn())  # cycle turns; income banks at each start
            guard += 1
        self.assertTrue(e.state.done)
        self.assertEqual(e.state.end_reason, "domination")
        self.assertEqual(e.state.winner, s)
        self.assertGreaterEqual(e.state.domination[s], 8)

    def test_domination_can_be_disabled(self):
        e = fresh_engine(domination_target=4, domination_enabled=False)
        e.state.system_owner[next(iter(e.state.binary_systems))] = 0
        for _ in range(30):
            if e.state.done:
                break
            e.apply_action(Action.end_turn())
        self.assertNotEqual(e.state.end_reason, "domination")

    def test_timeout_resolved_by_domination(self):
        e = fresh_engine(turn_cap=1, timeout_resolution="draw")
        e.state.domination = [7, 3]
        e.apply_action(Action.end_turn())  # crosses the turn cap
        self.assertTrue(e.state.done)
        self.assertEqual(e.state.end_reason, "timeout")
        self.assertEqual(e.state.winner, 0)  # decided on the control race, not a draw


class TestSmoke(unittest.TestCase):
    def test_random_games_terminate(self):
        for seed in range(40):
            res = play_skirmish(make_bot("random"), make_bot("random"), seed=seed)
            self.assertTrue(res["end_reason"])
            self.assertIn(res["winner"], (0, 1, None))

    def test_heuristic_vs_random_runs(self):
        res = play_skirmish(make_bot("heuristic"), make_bot("random"), seed=7)
        self.assertTrue(res["end_reason"])


if __name__ == "__main__":
    unittest.main()
