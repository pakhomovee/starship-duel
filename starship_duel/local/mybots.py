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
    def add(self, name: str, entry: str, timeout: float = 2.0,
            cwd: Optional[str] = None) -> dict:
        """Register (or replace) a bot; raises ValueError on bad input."""
        name = name.strip()
        if not _NAME_RE.fullmatch(name):
            raise ValueError("name must be 1-40 chars: letters, digits, _ . -")
        if self.specs.get(name, {}).get("builtin"):
            raise ValueError(f"{name!r} is a bundled bot; pick another name")
        command = resolve_command(entry)
        if not command:
            raise ValueError("empty command")
        if not (0.1 <= timeout <= 60):
            raise ValueError("timeout must be between 0.1 and 60 seconds")
        self.specs[name] = {
            "command": command,
            "entry": entry.strip(),
            "timeout": float(timeout),
            "cwd": cwd,
            "added": time.time(),
            "builtin": False,
        }
        self._save()
        return self.describe(name)

    def remove(self, name: str) -> bool:
        spec = self.specs.get(name)
        if spec is None or spec.get("builtin"):
            return False
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
