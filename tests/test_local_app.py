"""Tests for the participant-facing local test app (starship_duel.local).

Skipped cleanly when fastapi isn't installed, like the other web tests."""

import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

try:
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient
    _HAVE_WEB = True
except Exception:
    _HAVE_WEB = False


def _drive_to_completion(client, view, cap=20000):
    """Step a bot-vs-bot game to the end via the REST surface."""
    gid = view["game_id"]
    steps = 0
    while not view["done"] and steps < cap:
        view = client.post(f"/api/game/{gid}/step").json()
        steps += 1
    return view


@unittest.skipUnless(_HAVE_WEB, "fastapi not installed")
class TestLocalApp(unittest.TestCase):
    def setUp(self):
        import starship_duel.local.app as local
        self._tmp = tempfile.TemporaryDirectory()
        local.configure(self._tmp.name)
        for gid in list(local.SESSIONS):
            local.SESSIONS.pop(gid).close()
        self.local = local
        self.client = TestClient(local.app)

    def tearDown(self):
        for gid in list(self.local.SESSIONS):
            self.local.SESSIONS.pop(gid).close()
        self._tmp.cleanup()

    # -- meta ----------------------------------------------------------------
    def test_bots_and_maps(self):
        data = self.client.get("/api/bots").json()
        for b in ("random", "heuristic", "hunter", "human"):
            self.assertIn(b, data["bots"])
        self.assertNotIn("deepseek", data["bots"])
        self.assertEqual(data["arena_label"], "My bots")
        self.assertIn("arena:example-py", data["arena"])  # bundled SDK example
        maps = self.client.get("/api/maps").json()["maps"]
        self.assertTrue(maps)

    def test_info(self):
        info = self.client.get("/api/info").json()
        self.assertEqual(Path(info["data_dir"]), Path(self._tmp.name))
        self.assertIn("version", info)

    # -- games + history -----------------------------------------------------
    def test_game_lifecycle_and_history(self):
        view = self.client.post("/api/game", json={
            "ship0": "random", "ship1": "random", "seed": 7}).json()
        self.assertEqual(view["mode"], "bot_vs_bot")
        view = _drive_to_completion(self.client, view)
        self.assertTrue(view["done"])

        games = self.client.get("/api/games").json()["games"]
        self.assertEqual(len(games), 1)
        rid = games[0]["rid"]
        replay = self.client.get(f"/api/games/{rid}/replay").json()
        self.assertGreater(len(replay["frames"]), 1)
        # local app: deletion needs no admin gate
        self.assertEqual(self.client.delete(f"/api/games/{rid}").status_code, 200)
        self.assertEqual(self.client.get("/api/games").json()["games"], [])

    def test_create_game_rejects_bad_input(self):
        r = self.client.post("/api/game", json={"ship0": "nope", "ship1": "random"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/game", json={
            "ship0": "random", "ship1": "random", "map_id": "no-such-map"})
        self.assertEqual(r.status_code, 400)

    def test_first_ship_respected(self):
        for first in (0, 1):
            view = self.client.post("/api/game", json={
                "ship0": "random", "ship1": "random", "seed": 3,
                "first_ship": first}).json()
            self.assertEqual(view["turn_ship"], first)

    # -- my bots -------------------------------------------------------------
    def _write_bot(self, body: str) -> str:
        path = Path(self._tmp.name) / "my_bot.py"
        path.write_text(textwrap.dedent(body))
        return str(path)

    def test_mybots_add_check_remove(self):
        # a minimal always-END_TURN bot speaking the wire protocol
        path = self._write_bot("""\
            import json, sys
            for line in sys.stdin:
                json.loads(line)
                print(json.dumps({"action": "END_TURN"}), flush=True)
        """)
        r = self.client.post("/api/mybots", json={"name": "mybot-v1", "entry": path})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["command"][0], sys.executable)

        bots = self.client.get("/api/bots").json()
        self.assertIn("arena:mybot-v1", bots["arena"])

        chk = self.client.post("/api/mybots/mybot-v1/check").json()
        self.assertTrue(chk["ok"], chk)
        self.assertEqual(chk["strikes"], 0)

        self.assertEqual(self.client.delete("/api/mybots/mybot-v1").status_code, 200)
        self.assertNotIn("arena:mybot-v1",
                         self.client.get("/api/bots").json()["arena"])

    def test_mybots_check_flags_crashing_bot(self):
        path = self._write_bot("import sys; sys.exit(1)\n")
        self.client.post("/api/mybots", json={"name": "crasher", "entry": path})
        chk = self.client.post("/api/mybots/crasher/check").json()
        self.assertFalse(chk["ok"], chk)

    def test_mybots_upload(self):
        import base64
        code = textwrap.dedent("""\
            import json, sys
            for line in sys.stdin:
                json.loads(line)
                print(json.dumps({"action": "END_TURN"}), flush=True)
        """).encode()
        r = self.client.post("/api/mybots/upload", json={
            "name": "uploaded-v1", "filename": "solution.py",
            "content_b64": base64.b64encode(code).decode()})
        self.assertEqual(r.status_code, 200, r.text)
        desc = r.json()
        self.assertTrue(desc["stored"])
        stored = Path(self._tmp.name) / "bots" / "uploaded-v1.py"
        self.assertTrue(stored.exists())
        self.assertEqual(desc["command"][0], sys.executable)
        self.assertEqual(Path(desc["command"][1]).resolve(), stored.resolve())

        chk = self.client.post("/api/mybots/uploaded-v1/check").json()
        self.assertTrue(chk["ok"], chk)

        # removing an uploaded bot also removes its stored file
        self.assertEqual(self.client.delete("/api/mybots/uploaded-v1").status_code, 200)
        self.assertFalse(stored.exists())

    def test_mybots_upload_rejects_bad_input(self):
        r = self.client.post("/api/mybots/upload", json={
            "name": "x", "filename": "a.py", "content_b64": "!!!not-base64!!!"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/mybots/upload", json={
            "name": "x", "filename": "a.py", "content_b64": ""})
        self.assertEqual(r.status_code, 400)

    def test_mybots_validation(self):
        r = self.client.post("/api/mybots", json={"name": "bad name!", "entry": "x"})
        self.assertEqual(r.status_code, 400)
        r = self.client.delete("/api/mybots/example-py")  # bundled: not removable
        self.assertEqual(r.status_code, 404)

    def test_play_vs_my_bot(self):
        path = self._write_bot("""\
            import json, sys
            for line in sys.stdin:
                json.loads(line)
                print(json.dumps({"action": "END_TURN"}), flush=True)
        """)
        self.client.post("/api/mybots", json={"name": "passer", "entry": path})
        view = self.client.post("/api/game", json={
            "ship0": "arena:passer", "ship1": "random", "seed": 1}).json()
        view = _drive_to_completion(self.client, view)
        self.assertTrue(view["done"])

    # -- batch runs ----------------------------------------------------------
    def test_batch_run(self):
        run = self.client.post("/api/batch", json={
            "ship0": "random", "ship1": "random", "games": 4, "seed": 11,
            "record": True}).json()
        bid = run["id"]
        for _ in range(300):
            st = self.client.get(f"/api/batch/{bid}").json()
            if st["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(st["status"], "done", st)
        self.assertEqual(st["done"], 4)
        self.assertEqual(st["wins"][0] + st["wins"][1] + st["draws"], 4)
        self.assertEqual(len(st["rows"]), 4)
        # alternate first mover by default
        self.assertEqual([r["first"] for r in st["rows"]], [0, 1, 0, 1])
        # record=True -> every game is a watchable replay in history
        games = self.client.get("/api/games").json()["games"]
        self.assertEqual(len(games), 4)
        self.assertTrue(all(r["rid"] for r in st["rows"]))

    def test_batch_rejects_human(self):
        r = self.client.post("/api/batch", json={
            "ship0": "human", "ship1": "random", "games": 2})
        self.assertEqual(r.status_code, 400)

    # -- frontend shell ------------------------------------------------------
    def test_pages_served(self):
        self.assertIn("Local", self.client.get("/").text)
        self.assertEqual(self.client.get("/local/local.js").status_code, 200)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/rules").status_code, 200)


if __name__ == "__main__":
    unittest.main()
