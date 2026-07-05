"""Smoke tests for the RL adapters and the web session layer.

These import numpy/gymnasium/pettingzoo/fastapi, so they are skipped cleanly if
those optional deps are absent (the pure-stdlib core is covered elsewhere)."""

import unittest

try:
    import numpy as np  # noqa: F401
    import gymnasium  # noqa: F401
    import pettingzoo  # noqa: F401
    _HAVE_RL = True
except Exception:
    _HAVE_RL = False

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient
    _HAVE_WEB = True
except Exception:
    _HAVE_WEB = False


@unittest.skipUnless(_HAVE_RL, "RL deps not installed")
class TestRLAdapters(unittest.TestCase):
    def test_codec_roundtrip(self):
        from starship_duel.game.maps import REFERENCE_MAP
        from starship_duel.rl.action_coding import ActionCodec
        codec = ActionCodec.from_map(REFERENCE_MAP)
        for i in range(codec.n_actions):
            self.assertEqual(codec.encode(codec.decode(i)), i)

    def test_encoder_size_and_range(self):
        from starship_duel.env import StarshipDuelEnv
        from starship_duel.game import build_observation
        from starship_duel.game.maps import REFERENCE_MAP
        from starship_duel.rl.encoders import ObservationEncoder
        env = StarshipDuelEnv(seed=0)
        env.reset(map_id="reference")
        enc = ObservationEncoder(sorted(REFERENCE_MAP.systems))
        vec = enc.encode(build_observation(env.engine, env.engine.current_ship))
        self.assertEqual(vec.shape[0], enc.size)
        self.assertTrue(np.all(vec >= -1.0) and np.all(vec <= 1.0))

    def test_pettingzoo_api(self):
        from pettingzoo.test import api_test
        from starship_duel.rl.pettingzoo_env import raw_env
        api_test(raw_env(seed=3), num_cycles=8, verbose_progress=False)

    def test_pettingzoo_reward_zero_sum(self):
        from starship_duel.rl.pettingzoo_env import raw_env
        env = raw_env(seed=5)
        env.reset(seed=5)
        total = {"ship_0": 0.0, "ship_1": 0.0}
        for agent in env.agent_iter(max_iter=5000):
            _, r, term, trunc, _ = env.last()
            total[agent] += r
            if term or trunc:
                env.step(None)
            else:
                mask = env.observe(agent)["action_mask"]
                env.step(int(np.random.choice(np.flatnonzero(mask))))
        self.assertAlmostEqual(sum(total.values()), 0.0)


@unittest.skipUnless(_HAVE_WEB, "fastapi not installed")
class TestWebApi(unittest.TestCase):
    def setUp(self):
        from starship_duel.web.server import app, SESSIONS
        SESSIONS.clear()
        self.client = TestClient(app)

    def test_bot_vs_bot_step_flow(self):
        r = self.client.post("/api/game", json={"ship0": "heuristic", "ship1": "random", "seed": 1})
        self.assertEqual(r.status_code, 200)
        view = r.json()
        self.assertEqual(view["mode"], "bot_vs_bot")
        self.assertEqual(view["perspective"], "truth")
        gid = view["game_id"]
        # Step until the game ends (bounded).
        for _ in range(4000):
            v = self.client.post(f"/api/game/{gid}/step").json()
            if v["done"]:
                break
        self.assertTrue(v["done"])
        self.assertIn(v["winner"], (0, 1, None))

    def test_human_action_and_illegal(self):
        r = self.client.post("/api/game", json={"ship0": "human", "ship1": "heuristic", "seed": 2})
        view = r.json()
        gid = view["game_id"]
        self.assertEqual(view["mode"], "human_vs_bot")
        self.assertTrue(view["awaiting_human"])
        # A legal action from the served list must succeed.
        legal = view["legal_actions"][0]
        r2 = self.client.post(f"/api/game/{gid}/action", json={"type": legal["type"], "dest": legal["dest"]})
        self.assertEqual(r2.status_code, 200)
        # An obviously illegal action (jump to a non-neighbour) is rejected.
        r3 = self.client.post(f"/api/game/{gid}/action", json={"type": "JUMP", "dest": "___nowhere___"})
        self.assertEqual(r3.status_code, 400)


if __name__ == "__main__":
    unittest.main()
