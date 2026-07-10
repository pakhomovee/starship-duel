"""Docker isolation for untrusted arena bots.

Submitted bots are *arbitrary code* (Python or C++) from strangers on the
internet.  The static scan in :mod:`starship_duel.tournament.accounts` is only
belt-and-suspenders; the real security boundary is this module: every untrusted
program -- both when it is *compiled* and when it is *run* -- executes inside a
locked-down ``docker`` container with

  * **no network** (``--network none``) -- a bot cannot phone home or attack the
    host's neighbours;
  * a **read-only root filesystem**, dropped Linux capabilities, and
    ``no-new-privileges`` -- it cannot tamper with the image or escalate;
  * a **non-root** user and a small writable ``tmpfs`` at ``/tmp`` only;
  * hard **memory / CPU / PID** caps -- a fork bomb or memory hog is contained;
  * a per-bot mount of **only that bot's own directory** (source + the SDK) --
    one competitor can never read another's code.

The container talks the exact same one-JSON-line-per-turn protocol over
stdin/stdout, so :class:`~starship_duel.arena.subprocess_bot.SubprocessBot` is
unchanged: ``docker run -i`` is transparent to the wire protocol.  A bot's
container is spawned **once per game** (persistent, so per-turn latency excludes
container start-up) and reaped by ``--rm`` when the match ends.

Mode is chosen by ``$STARSHIP_SANDBOX``:

  * ``auto`` (default) -- sandbox if the ``docker`` CLI is available, else fall
    back to a bare subprocess **with a loud warning** (keeps local dev / CI, which
    have no docker, working).
  * ``docker`` -- always sandbox; **fail closed** if docker is missing (use this
    in production so a misconfigured host refuses to run untrusted code raw).
  * ``none`` -- never sandbox (bare subprocess).  Only for a fully trusted bot
    set on a trusted host.

Build the image once with :func:`build_image` (or ``python -m
starship_duel.arena.sandbox build``); see ``arena/Dockerfile``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

logger = logging.getLogger("starship_duel.arena.sandbox")

_IMAGE_DEFAULT = "starship-arena-sandbox"
_DOCKERFILE_DIR = Path(__file__).resolve().parent  # arena/ (holds Dockerfile)
_WARNED_NO_DOCKER = False


def docker_available() -> bool:
    """True if a usable ``docker`` CLI is on PATH (result cached per process)."""
    global _DOCKER_OK
    try:
        return _DOCKER_OK  # type: ignore[name-defined]
    except NameError:
        pass
    _DOCKER_OK = shutil.which("docker") is not None
    return _DOCKER_OK


@dataclass(frozen=True)
class SandboxSpec:
    """Resource + policy knobs for the isolation container.

    Instances are cheap and immutable; build one with :meth:`from_env` (reads
    ``$STARSHIP_SANDBOX*``) at the top of each entry point and thread it through.
    """

    mode: str = "auto"                 # auto | docker | none
    image: str = _IMAGE_DEFAULT
    memory_mb: int = 256
    #: Memory cap for the C++ *compile* container. Deliberately much larger than
    #: memory_mb: cc1plus building the vendored nlohmann/json is OOM-killed at
    #: 256 MB, which would fail every C++ submission. Compiling is a trusted,
    #: short-lived step, so a fat ceiling here is fine.
    compile_memory_mb: int = 1024
    cpus: float = 1.0
    pids: int = 128
    tmpfs_mb: int = 64
    #: Container UID:GID for *running* a bot. 65534 = nobody:nogroup on Debian.
    run_user: str = "65534:65534"
    build_timeout: int = 180           # seconds for the in-container C++ compile

    @classmethod
    def from_env(cls) -> "SandboxSpec":
        def _f(name, cast, default):
            v = os.environ.get(name)
            if v is None or v == "":
                return default
            try:
                return cast(v)
            except (TypeError, ValueError):
                return default

        return cls(
            mode=(os.environ.get("STARSHIP_SANDBOX") or "auto").strip().lower(),
            image=os.environ.get("STARSHIP_SANDBOX_IMAGE") or _IMAGE_DEFAULT,
            memory_mb=_f("STARSHIP_SANDBOX_MEMORY_MB", int, 256),
            compile_memory_mb=_f("STARSHIP_SANDBOX_COMPILE_MEMORY_MB", int, 1024),
            cpus=_f("STARSHIP_SANDBOX_CPUS", float, 1.0),
            pids=_f("STARSHIP_SANDBOX_PIDS", int, 128),
            tmpfs_mb=_f("STARSHIP_SANDBOX_TMPFS_MB", int, 64),
            build_timeout=_f("STARSHIP_SANDBOX_BUILD_TIMEOUT", int, 180),
        )

    # -- policy --------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        """Whether bots should actually be containerized.

        ``docker`` -> always (fail closed later if the CLI is missing); ``none``
        -> never; ``auto`` -> only if docker is present.
        """
        if self.mode == "none":
            return False
        if self.mode == "docker":
            return True
        # auto
        if docker_available():
            return True
        global _WARNED_NO_DOCKER
        if not _WARNED_NO_DOCKER:
            _WARNED_NO_DOCKER = True
            logger.warning(
                "STARSHIP_SANDBOX=auto but the docker CLI was not found: running "
                "untrusted bots as BARE SUBPROCESSES (no isolation). Set "
                "STARSHIP_SANDBOX=docker to require the sandbox, or install docker.")
        return False

    def require_docker(self) -> None:
        """Fail closed: raise if isolation is demanded but docker is unusable."""
        if self.enabled and not docker_available():
            raise SandboxError(
                "STARSHIP_SANDBOX requires isolation but the 'docker' CLI is not "
                "available on PATH; refusing to run untrusted code unsandboxed.")

    # -- argv construction ---------------------------------------------------
    def _limits(self, memory_mb: Optional[int] = None) -> List[str]:
        # memory == memory-swap disables swap (no swap escape hatch for a hog).
        mem = memory_mb or self.memory_mb
        return [
            "--memory", f"{mem}m",
            "--memory-swap", f"{mem}m",
            "--cpus", str(self.cpus),
            "--pids-limit", str(self.pids),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            # Rootless Docker on AppArmor hosts (e.g. Ubuntu 24.04) can't read
            # /sys/kernel/security/apparmor/profiles to load the docker-default
            # profile, so it refuses every container start. Unconfine it: the
            # isolation here comes from the user namespace, --cap-drop ALL,
            # --network none, read-only rootfs and the default seccomp profile
            # (still applied), not from docker-default AppArmor.
            "--security-opt", "apparmor=unconfined",
        ]

    def run_argv(self, inner: Sequence[str], mount_dir: Path) -> List[str]:
        """``docker run`` argv to *run* a bot whose files live in ``mount_dir``.

        ``inner`` is the command to run *inside* the container, relative to
        ``/bot`` (the read-only mount of ``mount_dir``), e.g. ``["python3",
        "bot.py"]`` or ``["./bot"]``.
        """
        return [
            "docker", "run", "--rm", "-i", "--init",
            "--network", "none",
            *self._limits(),
            "--user", self.run_user,
            "--read-only",
            "--tmpfs", f"/tmp:rw,size={self.tmpfs_mb}m,noexec,nosuid,nodev",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "HOME=/tmp",
            "-v", f"{mount_dir}:/bot:ro",
            "-w", "/bot",
            self.image,
            *inner,
        ]

    def compile_cpp(self, src_name: str, out_name: str, mount_dir: Path,
                    include_dir: Path) -> subprocess.CompletedProcess:
        """Compile ``mount_dir/src_name`` -> ``mount_dir/out_name`` inside a
        throwaway container (no network, resource-capped, ephemeral).

        The compiler writes to the mounted dir, so it runs as the image's default
        user with ``/bot`` mounted read-write; the SDK headers are mounted
        read-only at ``/sdk``.  Returns the finished ``CompletedProcess`` (check
        ``returncode`` / ``stderr``); raises :class:`SandboxError` on a missing
        docker or a build timeout.
        """
        self.require_docker()
        inner = [
            "g++", "-std=c++17", "-O2", "-I", "/sdk",
            f"/bot/{src_name}", "-o", f"/bot/{out_name}",
        ]
        argv = [
            "docker", "run", "--rm", "--init",
            "--network", "none",
            # Compiling needs far more memory than running the bot (cc1plus +
            # nlohmann/json), so use the dedicated compile ceiling here.
            *self._limits(self.compile_memory_mb),
            "--read-only",
            "--tmpfs", f"/tmp:rw,size={max(self.tmpfs_mb, 256)}m,nosuid,nodev",
            "-v", f"{mount_dir}:/bot:rw",
            "-v", f"{include_dir}:/sdk:ro",
            "-w", "/bot",
            self.image,
            *inner,
        ]
        try:
            return subprocess.run(argv, capture_output=True, text=True,
                                  timeout=self.build_timeout)
        except FileNotFoundError:
            raise SandboxError("docker CLI not found while trying to compile a bot")
        except subprocess.TimeoutExpired:
            raise SandboxError(f"sandboxed compile timed out ({self.build_timeout}s)")


class SandboxError(Exception):
    """Isolation could not be established (docker missing / build failure)."""


def image_present(spec: Optional[SandboxSpec] = None) -> bool:
    """True if the sandbox image already exists in the daemon's local store."""
    spec = spec or SandboxSpec.from_env()
    if not docker_available():
        return False
    return subprocess.run(
        ["docker", "image", "inspect", spec.image],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def build_image(spec: Optional[SandboxSpec] = None, if_missing: bool = False) -> int:
    """Build the sandbox image from ``arena/Dockerfile``. Returns the exit code.

    With ``if_missing=True`` the build is skipped when the image already exists.
    Some hosts forbid networked rootless containers, so ``docker build``'s
    network-dependent ``RUN`` steps can't run there; on those hosts the image is
    produced elsewhere and brought in via :func:`pull_image` or ``docker load``,
    and ``build --if-missing`` lets validation pass without rebuilding. See
    ``deploy/DEPLOY.md``.
    """
    spec = spec or SandboxSpec.from_env()
    if not docker_available():
        raise SandboxError("cannot build image: docker CLI not found on PATH")
    if if_missing and image_present(spec):
        logger.info("sandbox image %s already present; skipping build", spec.image)
        return 0
    argv = ["docker", "build", "-t", spec.image, "-f",
            str(_DOCKERFILE_DIR / "Dockerfile"), str(_DOCKERFILE_DIR)]
    logger.info("building sandbox image: %s", " ".join(argv))
    return subprocess.run(argv).returncode


def pull_image(reference: str, spec: Optional[SandboxSpec] = None) -> int:
    """Pull a prebuilt sandbox image from a registry and tag it as ``spec.image``.

    For hosts where ``docker build`` cannot run (kernel forbids networked rootless
    containers) but the daemon can still ``docker pull``. Returns the exit code.
    """
    spec = spec or SandboxSpec.from_env()
    if not docker_available():
        raise SandboxError("cannot pull image: docker CLI not found on PATH")
    rc = subprocess.run(["docker", "pull", reference]).returncode
    if rc != 0 or reference == spec.image:
        return rc
    return subprocess.run(["docker", "tag", reference, spec.image]).returncode


def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Starship arena docker sandbox")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build the sandbox docker image")
    b.add_argument("--if-missing", action="store_true",
                   help="skip the build when the image already exists (for hosts "
                        "where docker build can't run and the image is preloaded)")
    p = sub.add_parser("pull", help="pull a prebuilt sandbox image and tag it locally")
    p.add_argument("reference",
                   help="registry reference, e.g. ghcr.io/you/starship-arena-sandbox:latest")
    sub.add_parser("status", help="report sandbox mode and docker availability")
    args = ap.parse_args(argv)
    spec = SandboxSpec.from_env()
    if args.cmd == "build":
        return build_image(spec, if_missing=args.if_missing)
    if args.cmd == "pull":
        return pull_image(args.reference, spec)
    print(f"mode={spec.mode}  docker_available={docker_available()}  "
          f"enabled={spec.enabled}  image={spec.image}  present={image_present(spec)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
