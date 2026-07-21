"""Tests for Phase 2: accounts/sessions, submission validation, the account ->
competitor bridge, and the web auth/upload API.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import shutil

from starship_duel.tournament.accounts import (
    AccountStore,
    build_command,
    hash_password,
    materialize_active,
    smoke_test,
    static_scan,
    verify_password,
)
from starship_duel.tournament.registry import BASELINES, BotRegistry
from starship_duel.tournament.schedule import register_competitors
from starship_duel.tournament.store import TournamentStore

_EXAMPLE_BOT = (Path(__file__).resolve().parent.parent / "starship_duel" / "arena"
                / "sdk" / "python" / "example_bot.py")
_EXAMPLE_CPP = (Path(__file__).resolve().parent.parent / "starship_duel" / "arena"
                / "sdk" / "cpp" / "example_bot.cpp")
_HAVE_CXX = bool(shutil.which("g++") or shutil.which("clang++"))


# --------------------------------------------------------------- unit tests --
class TestPasswords(unittest.TestCase):
    def test_hash_roundtrip(self):
        digest, salt = hash_password("hunter2")
        self.assertTrue(verify_password("hunter2", digest, salt))
        self.assertFalse(verify_password("wrong", digest, salt))


class TestStaticScan(unittest.TestCase):
    def test_accepts_protocol_essentials(self):
        self.assertIsNone(static_scan(b"import sys, json\nprint('ok')\n"))

    def test_rejects_dangerous_imports_and_calls(self):
        self.assertIn("os", static_scan(b"import os\n") or "")
        self.assertIn("subprocess", static_scan(b"from subprocess import run\n") or "")
        self.assertIn("disallowed call", static_scan(b"eval('1+1')\n") or "")

    def test_rejects_syntax_error_and_oversize(self):
        self.assertIn("syntax", static_scan(b"def (:\n") or "")
        self.assertIn("too large", static_scan(b"x" * (256 * 1024 + 1)) or "")


class TestAccountStore(unittest.TestCase):
    def setUp(self):
        self._db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db.close()
        self.store = AccountStore(self._db.name)

    def tearDown(self):
        os.unlink(self._db.name)

    def test_users_sessions_and_login(self):
        uid = self.store.create_user("alice", "pw", is_admin=False)
        self.assertIsNone(self.store.verify_login("alice", "nope"))
        self.assertEqual(self.store.verify_login("alice", "pw")["id"], uid)

        token = self.store.create_session(uid)
        self.assertEqual(self.store.resolve_session(token)["username"], "alice")
        self.store.delete_session(token)
        self.assertIsNone(self.store.resolve_session(token))

    def test_ensure_admin_is_idempotent(self):
        self.store.ensure_admin("root", "pw")
        self.store.ensure_admin("root", "pw2")
        self.assertEqual(self.store.count_users(), 1)
        self.assertTrue(self.store.get_user_by_name("root")["is_admin"])

    def test_only_latest_validated_submission_is_active(self):
        uid = self.store.create_user("bob", "pw")
        s1 = self.store.add_submission(uid, "bob", "v1.py", b"print(1)")
        self.store.set_submission_status(s1, "validated", "ok", make_active=True)
        s2 = self.store.add_submission(uid, "bob", "v2.py", b"print(2)")
        self.store.set_submission_status(s2, "validated", "ok", make_active=True)

        active = self.store.active_submissions()
        self.assertEqual([a["id"] for a in active], [s2])   # only the newest
        self.assertEqual(len(self.store.list_user_submissions(uid)), 2)


class TestSmokeAndBridge(unittest.TestCase):
    """The example SDK bot should validate and then appear as a competitor."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.store = AccountStore(str(Path(self._tmp) / "acc.db"))

    def test_smoke_passes_example_and_rejects_crasher(self):
        ok, msg = smoke_test(_EXAMPLE_BOT.read_bytes(), out_dir=self._tmp)
        self.assertTrue(ok, msg)
        bad = b"import sys\nfor line in sys.stdin:\n    raise SystemExit(1)\n"
        ok2, _ = smoke_test(bad, out_dir=self._tmp)
        self.assertFalse(ok2)

    def test_active_submission_becomes_competitor(self):
        # This checks the bare-subprocess launch contract (absolute argv, no cwd
        # override), so pin that mode explicitly — otherwise the assertions depend
        # on whether a docker daemon happens to be present in the environment.
        _prev = os.environ.get("STARSHIP_SANDBOX")
        self.addCleanup(lambda: os.environ.__setitem__("STARSHIP_SANDBOX", _prev)
                        if _prev is not None else os.environ.pop("STARSHIP_SANDBOX", None))
        os.environ["STARSHIP_SANDBOX"] = "none"

        uid = self.store.create_user("carol", "pw")
        sid = self.store.add_submission(uid, "carol", "bot.py", _EXAMPLE_BOT.read_bytes())
        self.store.set_submission_status(sid, "validated", "ok", make_active=True)

        specs = materialize_active(self.store, self._tmp)
        self.assertIn("carol", specs)
        # Launch path must be ABSOLUTE (a relative path + a cwd override made the
        # interpreter look for submissions/submissions/<bot>.py -> exit 2).
        self.assertTrue(os.path.isabs(specs["carol"]["command"][1]))
        self.assertNotIn("cwd", specs["carol"])

        reg = BotRegistry(account_store=self.store, submissions_dir=self._tmp)
        self.assertIn("carol", reg.bot_ids())
        tstore = TournamentStore(str(Path(self._tmp) / "t.db"))
        register_competitors(tstore, reg)
        self.assertIn("carol", [c["id"] for c in tstore.list_competitors(kind="bot")])
        # And it builds into a real (subprocess) bot.
        bot = reg.build("carol")
        self.assertEqual(bot.name, "carol")
        bot.close()


class TestCppSubmissions(unittest.TestCase):
    def test_cpp_static_scan(self):
        clean = b'#include "starship_bot.hpp"\nint main(){ return 0; }\n'
        self.assertIsNone(static_scan(clean, "bot.cpp"))
        self.assertIn("disallowed", static_scan(b'int main(){ system("ls"); }', "bot.cpp") or "")
        self.assertIn("disallowed", static_scan(b'#include <fstream>\nint main(){}', "bot.cpp") or "")

    def test_unknown_extension_rejected(self):
        self.assertIn("unsupported", static_scan(b"data", "bot.rs") or "")

    @unittest.skipUnless(_HAVE_CXX, "no C++ compiler")
    def test_cpp_compiles_and_smokes(self):
        tmp = tempfile.mkdtemp()
        code = _EXAMPLE_CPP.read_bytes()
        # build_command returns a single native-binary argv, cached on rebuild.
        cmd = build_command(code, "bot.cpp", Path(tmp), "alice")
        self.assertEqual(len(cmd), 1)
        self.assertTrue(os.path.isabs(cmd[0]) and os.access(cmd[0], os.X_OK))
        ok, msg = smoke_test(code, "bot.cpp", out_dir=tmp)
        self.assertTrue(ok, msg)

    @unittest.skipUnless(_HAVE_CXX, "no C++ compiler")
    def test_cpp_compile_error_is_reported(self):
        ok, msg = smoke_test(b"int main(){ this is not c++ }", "bad.cpp", out_dir=tempfile.mkdtemp())
        self.assertFalse(ok)
        self.assertIn("compile failed", msg)


class TestRegisterDeleteScripts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.acc = AccountStore(str(Path(self._tmp) / "acc.db"))
        self.ts = TournamentStore(str(Path(self._tmp) / "t.db"))

    def test_bulk_register_skips_existing(self):
        from starship_duel.tournament.register import register_participants, _split_pair

        self.assertEqual(_split_pair("alice:pw"), ("alice", "pw"))
        self.assertEqual(_split_pair("bob,pw2"), ("bob", "pw2"))
        created, skipped = register_participants(
            self.acc, [("alice", "pw"), ("bob", "pw2")])
        self.assertEqual(len(created), 2)
        self.assertIsNotNone(self.acc.verify_login("alice", "pw"))
        # Re-running skips the ones that now exist.
        created2, skipped2 = register_participants(self.acc, [("alice", "x"), ("carol", "pw3")])
        self.assertEqual([c[0] for c in created2], ["carol"])
        self.assertEqual(skipped2, ["alice"])

    def test_delete_removes_account_competitor_and_file(self):
        from starship_duel.tournament.delete import delete_participants

        uid = self.acc.create_user("dave", "pw")
        sid = self.acc.add_submission(uid, "dave", "b.py", b"print(1)")
        self.acc.set_submission_status(sid, "validated", "ok", make_active=True)
        self.ts.add_competitor("dave", "bot")
        self.ts.add_matches([("dave", "random", 0, 1)])  # a pending match
        bot_file = Path(self._tmp) / "dave.py"
        bot_file.write_text("print(1)")

        removed = delete_participants(["dave", "ghost"], accounts=self.acc,
                                      tourney=self.ts, submissions_dir=self._tmp)
        self.assertEqual(removed, ["dave"])
        self.assertIsNone(self.acc.get_user_by_name("dave"))
        self.assertEqual(self.acc.list_user_submissions(uid), [])
        self.assertEqual([c["id"] for c in self.ts.list_competitors()], [])
        self.assertEqual(self.ts.status_counts().get("pending", 0), 0)  # pending match dropped
        self.assertFalse(bot_file.exists())


# ------------------------------------------------------------------- web API -
try:
    from fastapi.testclient import TestClient  # noqa: F401
    _HAVE_WEB = True
except Exception:  # pragma: no cover
    _HAVE_WEB = False


@unittest.skipUnless(_HAVE_WEB, "fastapi not installed")
class TestAuthApi(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from starship_duel.web import server
        from starship_duel.tournament.registry import BotRegistry

        self._tmp = tempfile.mkdtemp()
        self._old = (server.ACCOUNTS, server.TOURNEY, server.TOURNEY_BOTS)
        server.ACCOUNTS = AccountStore(str(Path(self._tmp) / "acc.db"))
        server.TOURNEY = TournamentStore(str(Path(self._tmp) / "t.db"))
        server.TOURNEY_BOTS = BotRegistry(
            account_store=server.ACCOUNTS, submissions_dir=str(Path(self._tmp) / "subs"))
        server.ACCOUNTS.create_user("admin", "adminpw", is_admin=True)
        self.server = server
        self.client = TestClient(server.app)

    def tearDown(self):
        self.server.ACCOUNTS, self.server.TOURNEY, self.server.TOURNEY_BOTS = self._old

    def _login(self, user, pw):
        return self.client.post("/api/login", json={"username": user, "password": pw})

    def test_login_me_logout(self):
        self.assertEqual(self.client.get("/api/me").json()["authenticated"], False)
        self.assertEqual(self._login("admin", "wrong").status_code, 401)
        self.assertEqual(self._login("admin", "adminpw").status_code, 200)
        me = self.client.get("/api/me").json()
        self.assertTrue(me["authenticated"] and me["is_admin"])
        self.client.post("/api/logout")
        self.assertEqual(self.client.get("/api/me").json()["authenticated"], False)

    def test_admin_creates_user_and_gate(self):
        # Not logged in -> forbidden.
        self.assertEqual(self.client.post("/api/admin/users",
                         json={"username": "x", "password": "p"}).status_code, 403)
        self._login("admin", "adminpw")
        r = self.client.post("/api/admin/users", json={"username": "alice", "password": "pw"})
        self.assertEqual(r.status_code, 200)
        # Non-admin can't create users.
        self.client.post("/api/logout")
        self._login("alice", "pw")
        self.assertEqual(self.client.post("/api/admin/users",
                         json={"username": "y", "password": "p"}).status_code, 403)

    def test_upload_validates_and_registers_competitor(self):
        self._login("admin", "adminpw")
        self.client.post("/api/admin/users", json={"username": "dave", "password": "pw"})
        self.client.post("/api/logout")
        self._login("dave", "pw")

        # Rejected by the static scan (no execution).
        bad = self.client.post("/api/submissions",
                               files={"file": ("bot.py", b"import os\n", "text/x-python")})
        self.assertEqual(bad.json()["status"], "rejected")

        # Valid bot: smoke-tested, activated, and now a competitor.
        good = self.client.post(
            "/api/submissions",
            files={"file": ("bot.py", _EXAMPLE_BOT.read_bytes(), "text/x-python")})
        self.assertEqual(good.json()["status"], "validated", good.json())
        subs = self.client.get("/api/submissions").json()["submissions"]
        self.assertTrue(any(s["active"] and s["status"] == "validated" for s in subs))
        self.assertIn("dave", self.server.TOURNEY_BOTS.bot_ids())

    def _upload_as(self, user):
        self._login("admin", "adminpw")
        self.client.post("/api/admin/users", json={"username": user, "password": "pw"})
        self.client.post("/api/logout")
        self._login(user, "pw")
        return self.client.post(
            "/api/submissions",
            files={"file": ("bot.py", _EXAMPLE_BOT.read_bytes(), "text/x-python")})

    def test_upload_auto_queues_baseline_matches(self):
        r = self._upload_as("erin").json()
        self.assertEqual(r["status"], "validated", r)
        # Queued, not played: the request only appends rows for the workers.
        self.assertEqual(r["queued"], len(BASELINES) * self.server.AUTOEVAL_GAMES)
        counts = self.server.TOURNEY.status_counts()
        self.assertEqual(counts.get("pending"), r["queued"])
        self.assertEqual(counts.get("running", 0) + counts.get("done", 0), 0)
        rows = self.server.TOURNEY.list_matches(limit=500)
        self.assertTrue(all(m["a_id"] == "erin" and m["b_id"] in BASELINES for m in rows))

        # Re-uploading replaces the queue instead of growing it.
        again = self.client.post(
            "/api/submissions",
            files={"file": ("bot.py", _EXAMPLE_BOT.read_bytes(), "text/x-python")}).json()
        self.assertEqual(again["queued"], r["queued"])
        self.assertEqual(self.server.TOURNEY.pending_count(), r["queued"])

    def test_autoeval_refuses_to_queue_a_bot_that_did_not_materialize(self):
        """A bot can pass its smoke test yet be absent from the launch registry
        (materialize_active drops anything that won't build here). Queueing for
        it would just manufacture 'no launch spec' errors."""
        r = self._upload_as("gwen")
        self.assertEqual(r.json()["status"], "validated", r.json())
        self.server.TOURNEY.purge_matches("gwen")

        # Simulate the build failing on reload: the bot leaves the registry.
        self.server.TOURNEY_BOTS.specs = {}
        queued = self.server._autoeval("gwen")
        self.assertEqual(queued, 0)
        self.assertEqual(self.server.TOURNEY.pending_count(), 0)

    def test_autoeval_backs_off_when_queue_is_deep(self):
        self.server.TOURNEY.add_matches([("a", "b", 0, i) for i in range(5)])
        old = self.server.AUTOEVAL_MAX_PENDING
        self.server.AUTOEVAL_MAX_PENDING = 5
        try:
            r = self._upload_as("frank").json()
        finally:
            self.server.AUTOEVAL_MAX_PENDING = old
        # Still a good submission — just nothing added on top of a saturated queue.
        self.assertEqual(r["status"], "validated", r)
        self.assertEqual(r["queued"], 0)
        self.assertEqual(self.server.TOURNEY.pending_count(), 5)

    def test_upload_requires_login(self):
        r = self.client.post("/api/submissions",
                             files={"file": ("bot.py", b"print(1)\n", "text/x-python")})
        self.assertEqual(r.status_code, 401)

    # -- admin submission log: paging + source viewer -----------------------
    def _seed_submissions(self, n):
        """``n`` rejected uploads (rejection is instant -- no smoke game)."""
        self._login("admin", "adminpw")
        self.client.post("/api/admin/users", json={"username": "gale", "password": "pw"})
        self.client.post("/api/logout")
        self._login("gale", "pw")
        for i in range(n):
            self.client.post("/api/submissions", files={
                "file": (f"bot{i}.py", f"import os  # {i}\n".encode(), "text/x-python")})
        self.client.post("/api/logout")
        self._login("admin", "adminpw")

    def test_admin_submissions_are_paged_newest_first(self):
        self._seed_submissions(7)
        p1 = self.client.get("/api/admin/submissions?limit=3&offset=0").json()
        self.assertEqual(p1["total"], 7)
        self.assertEqual(len(p1["submissions"]), 3)
        self.assertEqual([s["filename"] for s in p1["submissions"]],
                         ["bot6.py", "bot5.py", "bot4.py"])  # newest first
        p3 = self.client.get("/api/admin/submissions?limit=3&offset=6").json()
        self.assertEqual([s["filename"] for s in p3["submissions"]], ["bot0.py"])
        # Past the end is empty, not an error.
        self.assertEqual(self.client.get(
            "/api/admin/submissions?limit=3&offset=99").json()["submissions"], [])

    def test_admin_can_read_submission_source(self):
        self._seed_submissions(1)
        sub = self.client.get("/api/admin/submissions").json()["submissions"][0]
        r = self.client.get(f"/api/admin/submissions/{sub['id']}/code")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["code"], "import os  # 0\n")
        self.assertEqual(r.json()["username"], "gale")
        self.assertEqual(self.client.get(
            "/api/admin/submissions/999999/code").status_code, 404)

    def test_submission_source_is_admin_only(self):
        self._seed_submissions(1)
        sub = self.client.get("/api/admin/submissions").json()["submissions"][0]
        self.client.post("/api/logout")
        # Anonymous, and then the author themselves: source is admin-only.
        self.assertEqual(self.client.get(
            f"/api/admin/submissions/{sub['id']}/code").status_code, 403)
        self._login("gale", "pw")
        self.assertEqual(self.client.get(
            f"/api/admin/submissions/{sub['id']}/code").status_code, 403)

    def test_non_source_submission_is_not_dumped(self):
        # Only the single-file source types the arena accepts are viewable; a
        # binary blob is refused rather than served back as text.
        self._login("admin", "adminpw")
        sub_id = self.server.ACCOUNTS.add_submission(
            1, "admin", "bot.exe", b"MZ\x00\x90binary")
        r = self.client.get(f"/api/admin/submissions/{sub_id}/code")
        self.assertEqual(r.status_code, 415)


if __name__ == "__main__":
    unittest.main()
