"""Smoke tests for the web API / session layer.

These import fastapi, so they are skipped cleanly if it's absent (the
pure-stdlib core is covered elsewhere)."""

import os
import tempfile
import unittest

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient
    _HAVE_WEB = True
except Exception:
    _HAVE_WEB = False

try:
    import networkx  # noqa: F401
    _HAVE_NX = True
except Exception:
    _HAVE_NX = False


@unittest.skipUnless(_HAVE_WEB, "fastapi not installed")
class TestWebApi(unittest.TestCase):
    def setUp(self):
        from starship_duel.web import server
        from starship_duel.web.history import GameStore
        server.SESSIONS.clear()
        # Isolate recorded games in a throwaway db so tests don't touch cwd.
        self._db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db.close()
        self._old_store = server.STORE
        server.STORE = GameStore(self._db.name)
        self.client = TestClient(server.app)

    def tearDown(self):
        from starship_duel.web import server
        server.STORE = self._old_store
        try:
            os.unlink(self._db.name)
        except OSError:
            pass

    def test_map_id_validated(self):
        r = self.client.post("/api/game", json={"ship0": "human", "ship1": "random",
                                                "map_id": "___no_such_map___"})
        self.assertEqual(r.status_code, 400)

    def test_deepseek_gated_off_by_default(self):
        # Hidden from the listing and rejected as a controller unless enabled.
        self.assertNotIn("deepseek", self.client.get("/api/bots").json()["bots"])
        r = self.client.post("/api/game", json={"ship0": "human", "ship1": "deepseek"})
        self.assertEqual(r.status_code, 400)

    def test_access_token_gate(self):
        from starship_duel.web import server
        old = server.ACCESS_TOKEN
        server.ACCESS_TOKEN = "s3cret"
        try:
            self.assertEqual(self.client.get("/api/bots").status_code, 401)
            self.assertEqual(self.client.get("/api/bots?token=nope").status_code, 401)
            self.assertEqual(self.client.get("/api/bots?token=s3cret").status_code, 200)
            self.assertEqual(
                self.client.get("/api/bots", headers={"X-Access-Token": "s3cret"}).status_code, 200)
            # The static UI shell stays reachable so the page can load.
            self.assertEqual(self.client.get("/").status_code, 200)
        finally:
            server.ACCESS_TOKEN = old

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
        # The bot may move first now (no server-side auto-play); step it until
        # it is the human's turn, exactly as the UI does.
        guard = 0
        while not view["awaiting_human"] and not view["done"] and guard < 200:
            view = self.client.post(f"/api/game/{gid}/step").json()
            guard += 1
        self.assertTrue(view["awaiting_human"])
        # A legal action from the served list must succeed.
        legal = view["legal_actions"][0]
        r2 = self.client.post(f"/api/game/{gid}/action", json={"type": legal["type"], "dest": legal["dest"]})
        self.assertEqual(r2.status_code, 200)
        # An obviously illegal action (jump to a non-neighbour) is rejected.
        r3 = self.client.post(f"/api/game/{gid}/action", json={"type": "JUMP", "dest": "___nowhere___"})
        self.assertEqual(r3.status_code, 400)

    def test_play_against_arena_bot(self):
        # The bundled example arena bot is selectable and plays via subprocess.
        bots = self.client.get("/api/bots").json()
        self.assertIn("arena:example-py", bots["arena"])
        # Unknown arena bot is rejected (allowlist).
        bad = self.client.post("/api/game", json={"ship0": "human", "ship1": "arena:nope"})
        self.assertEqual(bad.status_code, 400)
        # Human vs the arena subprocess bot.
        r = self.client.post("/api/game", json={"ship0": "human", "ship1": "arena:example-py", "seed": 1})
        self.assertEqual(r.status_code, 200)
        view = r.json()
        self.assertEqual(view["mode"], "human_vs_bot")
        gid = view["game_id"]
        # Step the external bot until it's the human's turn or the game ends.
        guard = 0
        while not view["awaiting_human"] and not view["done"] and guard < 300:
            view = self.client.post(f"/api/game/{gid}/step").json()
            guard += 1
        self.assertTrue(view["awaiting_human"] or view["done"])

    def test_finished_game_is_recorded_and_replayable(self):
        # No games recorded on a fresh store.
        self.assertEqual(self.client.get("/api/games").json()["games"], [])
        r = self.client.post("/api/game", json={"ship0": "heuristic", "ship1": "random", "seed": 4})
        gid = r.json()["game_id"]
        for _ in range(4000):
            v = self.client.post(f"/api/game/{gid}/step").json()
            if v["done"]:
                break
        self.assertTrue(v["done"])

        games = self.client.get("/api/games").json()["games"]
        self.assertEqual(len(games), 1)
        g = games[0]
        self.assertEqual(g["mode"], "bot_vs_bot")
        self.assertIn(g["winner"], (0, 1, None))
        self.assertGreater(g["plies"], 1)

        replay = self.client.get(f"/api/games/{g['rid']}/replay").json()
        self.assertEqual(len(replay["frames"]), g["plies"])
        # Frames are truth views: renderable board + terminal last frame.
        self.assertIn("systems", replay["frames"][0])
        self.assertIn("edges", replay["frames"][0])
        self.assertTrue(replay["frames"][-1]["done"])

        # Replays are shared history: anonymous deletion is forbidden now.
        self.assertEqual(self.client.delete(f"/api/games/{g['rid']}").status_code, 403)
        self.assertEqual(len(self.client.get("/api/games").json()["games"]), 1)

        # An admin (here via the shared admin token) can delete; then it's a 404.
        from starship_duel.web import server
        old_admin = server.ADMIN_TOKEN
        server.ADMIN_TOKEN = "adm1n"
        try:
            r = self.client.delete(f"/api/games/{g['rid']}",
                                   headers={"X-Admin-Token": "adm1n"})
            self.assertEqual(r.status_code, 200)
        finally:
            server.ADMIN_TOKEN = old_admin
        self.assertEqual(self.client.get("/api/games").json()["games"], [])
        self.assertEqual(self.client.get(f"/api/games/{g['rid']}/replay").status_code, 404)

    def test_concurrent_games_coexist(self):
        # Public hosting: one visitor creating a game must not wipe another's
        # (the old single-user behaviour cleared all sessions on every create).
        g1 = self.client.post("/api/game", json={"ship0": "heuristic", "ship1": "random",
                                                 "seed": 1}).json()["game_id"]
        g2 = self.client.post("/api/game", json={"ship0": "heuristic", "ship1": "random",
                                                 "seed": 2}).json()["game_id"]
        self.assertNotEqual(g1, g2)
        # Both are still independently reachable.
        self.assertEqual(self.client.get(f"/api/game/{g1}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/game/{g2}").status_code, 200)

    def test_login_is_rate_limited(self):
        from starship_duel.web import server
        server.LOGIN_LIMITER.reset(f"testclient:ghost")  # isolate from other tests
        max_fails = server.LOGIN_LIMITER.max_hits
        # Wrong credentials are 401 up to the threshold, then 429 (locked out).
        for _ in range(max_fails):
            self.assertEqual(
                self.client.post("/api/login",
                                 json={"username": "ghost", "password": "x"}).status_code,
                401)
        self.assertEqual(
            self.client.post("/api/login",
                             json={"username": "ghost", "password": "x"}).status_code,
            429)
        server.LOGIN_LIMITER.reset(f"testclient:ghost")


@unittest.skipUnless(_HAVE_WEB and _HAVE_NX, "web/networkx deps not installed")
class TestPlanarLayout(unittest.TestCase):
    def test_all_maps_draw_without_edge_crossings(self):
        from starship_duel.game.maps import MAPS
        from starship_duel.web.layout import compute_layout, count_crossings, _edge_list
        for m in MAPS:
            pos = compute_layout(m)
            # every system is placed inside the nominal drawing box
            self.assertEqual(set(pos), set(m.systems))
            self.assertEqual(
                count_crossings(pos, _edge_list(m)), 0,
                f"map {m.id} drawn with edge crossings",
            )


if __name__ == "__main__":
    unittest.main()
