"""Isolation-policy tests for the arena docker sandbox.

These validate the *decision logic* and the generated ``docker run`` argv without
needing a real docker daemon (CI/dev have none): the argv is data, so we can
assert the hardening flags are present. The end-to-end containerized run is
covered by a manual/opt-in check when docker is installed.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from starship_duel.arena.sandbox import SandboxError, SandboxSpec
from starship_duel.tournament.accounts import build_command


class TestSandboxPolicy(unittest.TestCase):
    def test_mode_none_disables(self):
        self.assertFalse(SandboxSpec(mode="none").enabled)

    def test_mode_docker_forces_enabled(self):
        # Enabled even without docker present -> require_docker() then fails closed.
        spec = SandboxSpec(mode="docker")
        self.assertTrue(spec.enabled)

    def test_from_env_reads_overrides(self):
        import os
        keys = {
            "STARSHIP_SANDBOX": "docker",
            "STARSHIP_SANDBOX_MEMORY_MB": "512",
            "STARSHIP_SANDBOX_CPUS": "2",
            "STARSHIP_SANDBOX_PIDS": "42",
            "STARSHIP_SANDBOX_IMAGE": "my-image",
        }
        old = {k: os.environ.get(k) for k in keys}
        try:
            os.environ.update(keys)
            spec = SandboxSpec.from_env()
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(spec.mode, "docker")
        self.assertEqual(spec.memory_mb, 512)
        self.assertEqual(spec.cpus, 2.0)
        self.assertEqual(spec.pids, 42)
        self.assertEqual(spec.image, "my-image")


class TestRunArgv(unittest.TestCase):
    def setUp(self):
        self.spec = SandboxSpec(mode="docker", image="img", memory_mb=128, pids=7)
        self.argv = self.spec.run_argv(["python3", "bot.py"], Path("/srv/bots/alice"))
        self.s = " ".join(self.argv)

    def test_has_hardening_flags(self):
        for needle in (
            "docker run --rm -i --init",
            "--network none",
            "--read-only",
            "--cap-drop ALL",
            "--security-opt no-new-privileges",
            "--security-opt apparmor=unconfined",
            "--user 65534:65534",
            "--memory 128m --memory-swap 128m",
            "--pids-limit 7",
            "/srv/bots/alice:/bot:ro",
            "-w /bot",
        ):
            self.assertIn(needle, self.s, needle)

    def test_image_and_inner_command_last(self):
        self.assertEqual(self.argv[-3:], ["img", "python3", "bot.py"])

    def test_no_network_ever(self):
        self.assertIn("none", self.argv[self.argv.index("--network") + 1])


class TestBuildCommandSandboxed(unittest.TestCase):
    def test_python_bot_wrapped_in_docker(self):
        spec = SandboxSpec(mode="docker")  # forced on; python path builds argv only
        tmp = Path(tempfile.mkdtemp()) / "alice"
        cmd = build_command(b"print('hi')\n", "bot.py", tmp, "alice", sandbox=spec)
        self.assertEqual(cmd[0], "docker")
        self.assertIn("--network", cmd)
        # bot source + SDK materialized into the private, to-be-mounted dir
        self.assertTrue((tmp / "alice.py").exists())
        self.assertEqual(cmd[-2:], ["python3", "alice.py"])

    def test_sandbox_files_readable_by_container_under_restrictive_umask(self):
        # Regression: the worker runs with UMask=0077, but the sandbox runs the bot
        # as --user 65534 against a read-only mount, so the dir must be traversable
        # and the files readable by 'other' or the container can't open the bot
        # (exit-2 crash). See accounts._relax_perms_for_container.
        import os, stat
        old = os.umask(0o077)
        try:
            spec = SandboxSpec(mode="docker")
            tmp = Path(tempfile.mkdtemp()) / "carol"
            build_command(b"print('hi')\n", "bot.py", tmp, "carol", sandbox=spec)
            dir_mode = stat.S_IMODE(tmp.stat().st_mode)
            py_mode = stat.S_IMODE((tmp / "carol.py").stat().st_mode)
            sdk_mode = stat.S_IMODE((tmp / "starship_sdk.py").stat().st_mode)
            self.assertTrue(dir_mode & 0o001, f"dir not o+x: {oct(dir_mode)}")
            self.assertTrue(py_mode & 0o004, f"bot not o+r: {oct(py_mode)}")
            self.assertTrue(sdk_mode & 0o004, f"sdk not o+r: {oct(sdk_mode)}")
        finally:
            os.umask(old)

    def test_disabled_sandbox_is_plain_argv(self):
        spec = SandboxSpec(mode="none")
        tmp = Path(tempfile.mkdtemp()) / "bob"
        cmd = build_command(b"print('hi')\n", "bot.py", tmp, "bob", sandbox=spec)
        self.assertNotEqual(cmd[0], "docker")
        self.assertTrue(cmd[-1].endswith("bob.py"))

    def test_require_docker_fails_closed(self):
        # In this env docker is absent; mode=docker must refuse rather than run raw.
        from starship_duel.arena.sandbox import docker_available
        if docker_available():
            self.skipTest("docker is installed here; fail-closed path not exercised")
        with self.assertRaises(SandboxError):
            SandboxSpec(mode="docker").require_docker()


if __name__ == "__main__":
    unittest.main()
