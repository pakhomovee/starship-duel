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
        e.state.ships[s].energy = 40
        e.apply_action(Action.overcharge())
        self.assertEqual(e.state.ships[s].energy, 0)
        self.assertEqual(e.state.ships[s].banked_overcharge, 1)


class TestCombat(unittest.TestCase):
    def test_fire_hit_wins(self):
        e = fresh_engine()
        s = e.current_ship
        e.state.ships[1 - s].position = e.state.ships[s].position  # co-locate
        e.apply_action(Action.fire())
        self.assertTrue(e.state.done)
        self.assertEqual(e.state.winner, s)
        self.assertEqual(e.state.end_reason, "fire_hit")

    def test_fire_miss_is_not_terminal(self):
        e = fresh_engine()
        s = e.current_ship
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
