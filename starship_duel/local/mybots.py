"""Participant-managed registry of local bots ("my bots").

Each entry maps a display name to a subprocess command speaking the arena
JSON-line protocol.  Participants typically add a *file* (their solution, or a
previous version of it); a ``.py`` file is run with the current interpreter so
it works identically on Linux, macOS and Windows.

The registry persists as JSON in the app's data dir and is exposed to the game
session layer under the same ``arena:`` name prefix the web app uses, so
:class:`~starship_duel.web.session.GameSession` needs no changes.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from ..arena import SubprocessBot

#: Controller-name prefix GameSession routes to this registry (shared with web).
PREFIX = "arena:"

_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,39}")

_PKG = Path(__file__).resolve().parent.parent
_EXAMPLE_PY = _PKG / "arena" / "sdk" / "python" / "example_bot.py"
_CPP_SDK_DIR = _PKG / "arena" / "sdk" / "cpp"       # bundles nlohmann/json.hpp

#: Source-file extensions we compile on attach (can't be exec'd directly).
_CPP_SRC_EXTS = {".cpp", ".cc", ".cxx", ".c++", ".c"}


def compile_cpp(src: Path, out: Path) -> None:
    """Compile a C++ (or C) source file to the native executable ``out``.

    Mirrors the tournament server's non-sandboxed build so a bot behaves the
    same locally as on the judge. Raises ``ValueError`` (→ HTTP 400 with an
    actionable message) if no compiler is present or the build fails.
    """
    cxx = os.environ.get("STARSHIP_CXX") or shutil.which("g++") or shutil.which("clang++")
    if not cxx:
        raise ValueError(
            "no C++ compiler found — install one and re-attach: "
            "Linux `sudo apt install g++`, macOS `xcode-select --install`, "
            "Windows `winget install BrechtSanders.WinLibs.POSIX.UCRT` (MinGW-w64). "
            "Already have one elsewhere? point STARSHIP_CXX at it.")
    cmd = [cxx, "-std=c++17", "-O2", "-I", str(_CPP_SDK_DIR), str(src), "-o", str(out)]
    if os.name == "nt":
        # Static-link so the produced .exe carries libstdc++/libwinpthread and
        # launches without "missing DLL" errors on a bare Windows box.
        cmd.append("-static")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise ValueError(f"could not run C++ compiler {cxx!r}")
    except subprocess.TimeoutExpired:
        raise ValueError("C++ compile timed out (120s)")
    if proc.returncode != 0 or not out.exists():
        detail = (proc.stderr or proc.stdout or "unknown error").strip()
        raise ValueError("C++ compile failed:\n" + detail[-1500:])


def default_data_dir() -> Path:
    d = os.environ.get("STARSHIP_LOCAL_DIR")
    return Path(d).expanduser() if d else Path.home() / ".starship_duel"


def resolve_command(entry: str) -> List[str]:
    """Turn a user-entered path-or-command into an argv list.

    A path to a ``.py`` file runs under the current Python (portable across
    platforms — no shebang / file association needed); any other existing file
    is treated as an executable; everything else is split as a command line.
    """
    p = Path(entry.strip()).expanduser()
    if p.is_file():
        if p.suffix.lower() == ".py":
            return [sys.executable, str(p.resolve())]
        if p.suffix.lower() in _CPP_SRC_EXTS:
            raise ValueError(
                f"{p.name} is C++ source, not a program — attach it in My Bots "
                "(it gets compiled for you) or point this at the compiled "
                "executable instead (see LOCAL_APP.md).")
        return [str(p.resolve())]
    # shlex's posix mode mangles Windows backslash paths; split accordingly.
    return shlex.split(entry, posix=(os.name != "nt"))


class MyBots:
    """JSON-backed bot registry with the same duck-type as web ``ArenaBots``
    (``specs`` / ``names()`` / ``make()`` / ``reload()``)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.specs: Dict[str, dict] = {}
        self.reload()

    # -- persistence ---------------------------------------------------------
    def reload(self) -> None:
        specs: Dict[str, dict] = {}
        if _EXAMPLE_PY.exists():  # always have a working external bot to fight
            specs["example-py"] = {
                "command": [sys.executable, str(_EXAMPLE_PY)],
                "entry": str(_EXAMPLE_PY),
                "timeout": 2.0,
                "builtin": True,
            }
        try:
            with open(self.path) as f:
                data = json.load(f)
            for name, spec in data.items():
                if isinstance(spec, dict) and spec.get("command"):
                    spec.setdefault("timeout", 2.0)
                    spec["builtin"] = False
                    specs[name] = spec
        except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
            pass  # a missing/corrupt file must never take the app down
        self.specs = specs

    def _save(self) -> None:
        user = {n: {k: v for k, v in s.items() if k != "builtin"}
                for n, s in self.specs.items() if not s.get("builtin")}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(user, indent=2))
        os.replace(tmp, self.path)  # atomic on POSIX and Windows alike

    # -- editing -------------------------------------------------------------
    def _check_name(self, name: str, timeout: float) -> str:
        name = name.strip()
        if not _NAME_RE.fullmatch(name):
            raise ValueError("name must be 1-40 chars: letters, digits, _ . -")
        if self.specs.get(name, {}).get("builtin"):
            raise ValueError(f"{name!r} is a bundled bot; pick another name")
        if not (0.1 <= timeout <= 60):
            raise ValueError("timeout must be between 0.1 and 60 seconds")
        return name

    def add(self, name: str, entry: str, timeout: float = 2.0,
            cwd: Optional[str] = None, stored: bool = False) -> dict:
        """Register (or replace) a bot; raises ValueError on bad input."""
        name = self._check_name(name, timeout)
        command = resolve_command(entry)
        if not command:
            raise ValueError("empty command")
        # Replacing an uploaded bot drops its old file — but never the file we
        # are registering now (a same-path re-attach reuses it).
        self._delete_stored_file(name, keep=entry.strip())
        self.specs[name] = {
            "command": command,
            "entry": entry.strip(),
            "timeout": float(timeout),
            "cwd": cwd,
            "added": time.time(),
            "stored": stored,
            "builtin": False,
        }
        self._save()
        return self.describe(name)

    def add_stored(self, name: str, filename: str, data: bytes,
                   timeout: float = 2.0) -> dict:
        """Save an uploaded bot file into the data dir and register it.

        The stored copy is what runs, so the participant's original can move or
        change freely until they re-upload.  A C++/C **source** file is compiled
        to a native executable on the way in (source can't be exec'd directly);
        a ``.py`` runs under this Python; anything else is taken as a prebuilt
        executable."""
        name = self._check_name(name, timeout)
        suffix = Path(filename or "").suffix or ".py"
        bots_dir = self.path.parent / "bots"
        bots_dir.mkdir(parents=True, exist_ok=True)

        if suffix.lower() in _CPP_SRC_EXTS:
            # Compile to a scratch binary and swap it in only on a clean build,
            # so a failed recompile leaves the previous working bot intact.
            dest = bots_dir / (name + (".exe" if os.name == "nt" else ""))
            src = bots_dir / (name + ".build" + suffix)
            tmp = bots_dir / (name + ".build.out")
            src.write_bytes(data)
            try:
                compile_cpp(src, tmp)          # ValueError → HTTP 400 in the UI
                os.replace(tmp, dest)
            finally:
                src.unlink(missing_ok=True)
                tmp.unlink(missing_ok=True)
        else:
            dest = bots_dir / (name + suffix)
            dest.write_bytes(data)
        if dest.suffix.lower() != ".py":       # executables must be runnable
            try:
                os.chmod(dest, 0o755)
            except OSError:
                pass
        return self.add(name, str(dest), timeout=timeout, stored=True)

    def _delete_stored_file(self, name: str, keep: Optional[str] = None) -> None:
        """Remove a bot's uploaded/compiled file, if it has one (never user
        files, and never ``keep`` — the path currently being registered)."""
        spec = self.specs.get(name)
        if not spec or not spec.get("stored"):
            return
        stored_dir = (self.path.parent / "bots").resolve()
        p = Path(spec.get("entry", "")).resolve()
        if keep:
            try:
                if p == Path(keep).expanduser().resolve():
                    return
            except OSError:
                pass
        if p.parent == stored_dir:
            try:
                p.unlink()
            except OSError:
                pass

    def remove(self, name: str) -> bool:
        spec = self.specs.get(name)
        if spec is None or spec.get("builtin"):
            return False
        self._delete_stored_file(name)
        del self.specs[name]
        self._save()
        return True

    # -- queries -------------------------------------------------------------
    def names(self) -> List[str]:
        return sorted(self.specs)

    def describe(self, name: str) -> dict:
        s = self.specs[name]
        return {
            "name": name,
            "entry": s.get("entry") or " ".join(s["command"]),
            "command": s["command"],
            "timeout": s.get("timeout", 2.0),
            "builtin": bool(s.get("builtin")),
            "stored": bool(s.get("stored")),
            "added": s.get("added"),
        }

    def make(self, name: str) -> SubprocessBot:
        spec = self.specs[name]
        return SubprocessBot(
            spec["command"],
            name=PREFIX + name,
            timeout=float(spec.get("timeout", 2.0)),
            cwd=spec.get("cwd"),
        )
