"""Accounts, sessions, and bot submissions for the tournament (Phase 2).

SQLite again (same style as :mod:`store`), holding:
  * **users** -- login + salted-scrypt password hash + admin flag. New users are
    created by an admin only; an initial admin is seeded from the environment.
  * **sessions** -- opaque bearer tokens (a cookie) with an expiry, revocable.
  * **submissions** -- one row per uploaded ``.py`` bot, versioned per user; only
    the latest *validated* one is ``active`` and eligible to compete.

Security notes:
  * Passwords: :func:`hashlib.scrypt` (stdlib), constant-time compare on verify.
  * The static import scan here is *belt-and-suspenders*, not the real sandbox --
    the isolation boundary is the subprocess (and, later, Docker).  It only needs
    ``sys``/``json`` for the stdio protocol, so obviously-dangerous modules are
    rejected while the protocol essentials are allowed.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from ..arena.sandbox import SandboxSpec

_log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    pw_hash  BLOB NOT NULL,
    pw_salt  BLOB NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    active   INTEGER NOT NULL DEFAULT 1,
    created  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token   TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created REAL NOT NULL,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS submissions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   INTEGER NOT NULL,
    username  TEXT NOT NULL,
    filename  TEXT,
    file_hash TEXT NOT NULL,
    code      BLOB NOT NULL,
    status    TEXT NOT NULL DEFAULT 'pending',  -- pending|validated|rejected
    message   TEXT,
    active    INTEGER NOT NULL DEFAULT 0,
    created   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS submissions_user ON submissions (user_id, created DESC);
CREATE INDEX IF NOT EXISTS submissions_active ON submissions (active);
"""

# scrypt work factors (stdlib defaults are fine for a friendly comp).
_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)
_SESSION_TTL = 7 * 24 * 3600  # a week

# Upload limits + the static-scan blocklist (NOT the security boundary).
MAX_SUBMISSION_BYTES = 256 * 1024
_BLOCKED_IMPORTS = {
    "os", "subprocess", "socket", "ctypes", "shutil", "multiprocessing",
    "requests", "urllib", "http", "ftplib", "smtplib", "importlib",
    "pickle", "marshal", "resource", "signal", "threading", "asyncio",
}
_IMPORT_RE = re.compile(r"^\s*(?:import\s+([\w.]+)|from\s+([\w.]+)\s+import)", re.MULTILINE)
_DANGER_CALLS = re.compile(r"\b(?:eval|exec|compile|__import__|open)\s*\(")
# C++ side: block obvious shell-out / fs-tamper calls. Belt-and-suspenders only;
# the subprocess (and, later, Docker) boundary is the real defence.
_CPP_DANGER = re.compile(r"\b(?:system|popen|execl|execlp|execle|execv|execvp|execvpe|fork|remove|unlink|rename)\s*\(")
_CPP_INCLUDE = re.compile(r"#\s*include\s*<\s*(cstdlib|filesystem|fstream|sys/socket\.h|netinet/)")


def default_db_path() -> str:
    return os.environ.get("STARSHIP_ACCOUNTS_DB") or "starship_accounts.db"


def default_submissions_dir() -> str:
    # Default OUTSIDE the project tree: uvicorn --reload watches the cwd for *.py,
    # and materializing bots there caused an endless reload loop. Keep them in a
    # stable per-host temp dir (all processes derive the same path); bots are
    # re-materialized from the DB on demand, so it need not persist.
    return os.environ.get("STARSHIP_SUBMISSIONS_DIR") or str(
        Path(tempfile.gettempdir()) / "starship_duel" / "submissions")


# -- language support: single-file Python or C++ submissions -----------------
_ARENA = Path(__file__).resolve().parent.parent / "arena"
# Python convenience SDK (``from starship_sdk import run``) dropped next to each
# materialized .py bot (its own dir is on sys.path[0], so the import resolves).
_SDK_SRC = _ARENA / "sdk" / "python" / "starship_sdk.py"
# C++ SDK include dir (starship_bot.hpp + vendored nlohmann/json.hpp).
_CPP_SDK_DIR = _ARENA / "sdk" / "cpp"
_PY_EXTS = {".py"}
_CPP_EXTS = {".cpp", ".cc", ".cxx", ".c++"}


class BuildError(Exception):
    """A submission could not be prepared to run (unknown type / compile error)."""


def _language_for(filename: Optional[str]) -> str:
    if filename is None:  # unnamed source defaults to Python (e.g. direct smoke_test)
        return "python"
    ext = Path(filename).suffix.lower()
    if ext in _PY_EXTS:
        return "python"
    if ext in _CPP_EXTS:
        return "cpp"
    raise BuildError(f"unsupported file type {ext or '(none)'!r}; submit a .py or .cpp file")


def _ensure_sdk(dir_: Path) -> None:
    if _SDK_SRC.exists():
        (dir_ / "starship_sdk.py").write_bytes(_SDK_SRC.read_bytes())


def _relax_perms_for_container(work_dir: Path, files=(), execs=()) -> None:
    """Make a bot's dir + mounted files readable by the container's non-owner user.

    The sandbox runs the bot as ``--user 65534`` (nobody) against a read-only bind
    mount, so it must be able to traverse ``/bot`` and read the source/binary — even
    though the worker writes them under ``UMask=0077`` (mode 0600/0700), which a
    non-owner can't read (the symptom is ``python3: can't open file ... Permission
    denied`` and an exit-2 "smoke test" crash). Widen just this bot's own dir; the
    parent ``submissions/`` stays private, and each bot mounts only its own dir, so
    no other host user or bot gains access.
    """
    try:
        os.chmod(work_dir, 0o755)
        for f in files:
            if f.exists():
                os.chmod(f, 0o644)
        for f in execs:
            if f.exists():
                os.chmod(f, 0o755)
    except OSError:
        pass


def source_text(code: bytes, filename: Optional[str]) -> str:
    """Decode a submission for *reading* (the admin source viewer).

    Only the single-file source types the arena accepts are ever readable, so an
    unnamed or unknown-extension blob is refused rather than dumped as text --
    the same allowlist that decides what can run decides what can be viewed.
    Undecodable bytes become U+FFFD instead of raising: a mangled encoding should
    still be inspectable.
    """
    if not filename:
        raise BuildError("submission has no filename; refusing to display it")
    _language_for(filename)  # raises BuildError on anything but .py / .cpp
    if b"\0" in code:
        raise BuildError("submission looks binary, not source; refusing to display it")
    return code.decode("utf-8", errors="replace")


def build_command(code: bytes, filename: Optional[str], work_dir: Path, name: str,
                  sandbox: "Optional[SandboxSpec]" = None) -> List[str]:
    """Materialize a submission and return the argv that runs it.

    Python bots run directly; C++ bots are compiled against the bundled SDK
    (``-I`` the vendored nlohmann/json).  Compilation is cached by content hash so
    unchanged bots aren't rebuilt on every server reload / worker start.  Raises
    :class:`BuildError` on an unsupported type or a compile failure.

    When ``sandbox`` is enabled (see :class:`starship_duel.arena.sandbox.SandboxSpec`),
    the returned argv runs the bot inside a locked-down ``docker`` container and
    C++ is compiled *in-container* too, so no untrusted code -- source or binary --
    ever touches the host directly.  ``work_dir`` is the bot's own directory and
    becomes the container's read-only ``/bot`` mount, so callers must give each
    bot a private dir (no cross-bot source leakage).
    """
    lang = _language_for(filename)
    work_dir = work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    use_sandbox = sandbox is not None and sandbox.enabled

    if lang == "python":
        _ensure_sdk(work_dir)
        path = (work_dir / f"{name}.py").resolve()
        path.write_bytes(code)
        if use_sandbox:
            _relax_perms_for_container(work_dir, files=[path, work_dir / "starship_sdk.py"])
            return sandbox.run_argv(["python3", f"{name}.py"], work_dir)
        return [sys.executable, str(path)]

    # C++: compile to a native binary (cached on the source hash).
    src = (work_dir / f"{name}.cpp").resolve()
    binp = (work_dir / name).resolve()
    hashp = work_dir / f"{name}.sha256"
    digest = hashlib.sha256(code).hexdigest()
    run_argv = (lambda: sandbox.run_argv([f"./{name}"], work_dir)) if use_sandbox \
        else (lambda: [str(binp)])
    if binp.exists() and hashp.exists() and hashp.read_text().strip() == digest:
        if use_sandbox:
            _relax_perms_for_container(work_dir, execs=[binp])
        return run_argv()
    src.write_bytes(code)

    if use_sandbox:
        # Compile inside the sandbox (untrusted C++ + the compiler itself are
        # isolated: no network, capped, ephemeral).
        from ..arena.sandbox import SandboxError
        try:
            proc = sandbox.compile_cpp(f"{name}.cpp", name, work_dir, _CPP_SDK_DIR)
        except SandboxError as e:
            raise BuildError(str(e))
    else:
        cxx = os.environ.get("STARSHIP_CXX") or ("g++" if shutil.which("g++") else "clang++")
        includes = ["-I", str(_CPP_SDK_DIR)]
        extra = os.environ.get("STARSHIP_CPP_INCLUDE")
        if extra:
            includes += ["-I", extra]
        cmd = [cxx, "-std=c++17", "-O2", *includes, str(src), "-o", str(binp)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            raise BuildError(f"no C++ compiler found ({cxx}); install g++/clang++ on the host")
        except subprocess.TimeoutExpired:
            raise BuildError("compile timed out (120s)")
    if proc.returncode != 0:
        raise BuildError("compile failed:\n" + (proc.stderr.strip()[-1200:] or "unknown error"))
    hashp.write_text(digest)
    if use_sandbox:
        _relax_perms_for_container(work_dir, files=[src], execs=[binp])
    return run_argv()


# --------------------------------------------------------------- password ----
def hash_password(password: str, salt: Optional[bytes] = None) -> tuple:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return digest, salt


def verify_password(password: str, digest: bytes, salt: bytes) -> bool:
    cand = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return hmac.compare_digest(cand, digest)


# ---------------------------------------------------------- static scanning ---
def static_scan(code: bytes, filename: Optional[str] = None) -> Optional[str]:
    """Return a rejection reason, or ``None`` if the source passes the cheap
    pre-checks.  Deliberately conservative; the subprocess/Docker boundary is the
    real defence.  The checks differ by language (``.py`` vs ``.cpp``)."""
    if len(code) > MAX_SUBMISSION_BYTES:
        return f"file too large ({len(code)} bytes > {MAX_SUBMISSION_BYTES})"
    try:
        lang = _language_for(filename) if filename is not None else "python"
    except BuildError as e:
        return str(e)
    try:
        text = code.decode("utf-8")
    except UnicodeDecodeError:
        return "file is not valid UTF-8 text"

    if lang == "cpp":
        if _CPP_DANGER.search(text):
            return "disallowed call (system/popen/exec*/fork/remove/unlink/rename)"
        if _CPP_INCLUDE.search(text):
            return "disallowed include (cstdlib/filesystem/fstream/sockets)"
        return None

    # python
    try:
        compile(text, "<submission>", "exec")
    except SyntaxError as e:
        return f"syntax error: {e}"
    for m in _IMPORT_RE.finditer(text):
        mod = (m.group(1) or m.group(2) or "").split(".")[0]
        if mod in _BLOCKED_IMPORTS:
            return f"disallowed import: {mod!r}"
    if _DANGER_CALLS.search(text):
        return "disallowed call (eval/exec/compile/__import__/open)"
    return None


class AccountStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or default_db_path()
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=8.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        return conn

    # -- users ---------------------------------------------------------------
    def create_user(self, username: str, password: str, *, is_admin: bool = False) -> int:
        digest, salt = hash_password(password)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (username, pw_hash, pw_salt, is_admin, created) "
                "VALUES (?,?,?,?,?)",
                (username, digest, salt, int(is_admin), time.time()),
            )
            return cur.lastrowid

    def get_user(self, user_id: int) -> Optional[dict]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(r) if r else None

    def get_user_by_name(self, username: str) -> Optional[dict]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return dict(r) if r else None

    def list_users(self) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, username, is_admin, active, created FROM users ORDER BY username"
            ).fetchall()
        return [dict(r) for r in rows]

    def count_users(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def verify_login(self, username: str, password: str) -> Optional[dict]:
        u = self.get_user_by_name(username)
        if u is None or not u["active"]:
            return None
        if not verify_password(password, u["pw_hash"], u["pw_salt"]):
            return None
        return u

    def ensure_admin(self, username: str, password: str) -> None:
        """Seed an initial admin if it doesn't exist (idempotent)."""
        if self.get_user_by_name(username) is None:
            self.create_user(username, password, is_admin=True)

    def delete_user(self, username: str) -> bool:
        """Remove a user and everything owned by them (sessions + submissions).
        Returns False if no such user."""
        with self._lock, self._connect() as conn:
            r = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if r is None:
                return False
            uid = r["id"]
            conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM submissions WHERE user_id=?", (uid,))
            conn.execute("DELETE FROM users WHERE id=?", (uid,))
            return True

    # -- sessions ------------------------------------------------------------
    def create_session(self, user_id: int, ttl: float = _SESSION_TTL) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions (token, user_id, created, expires) VALUES (?,?,?,?)",
                (token, user_id, now, now + ttl),
            )
        return token

    def resolve_session(self, token: Optional[str]) -> Optional[dict]:
        if not token:
            return None
        with self._connect() as conn:
            r = conn.execute("SELECT user_id, expires FROM sessions WHERE token=?", (token,)).fetchone()
            if r is None or r["expires"] < time.time():
                return None
            u = conn.execute("SELECT * FROM users WHERE id=?", (r["user_id"],)).fetchone()
        return dict(u) if u and u["active"] else None

    def delete_session(self, token: Optional[str]) -> None:
        if not token:
            return
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))

    # -- submissions ---------------------------------------------------------
    def recent_submission_count(self, user_id: int, since_seconds: float) -> int:
        cutoff = time.time() - since_seconds
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM submissions WHERE user_id=? AND created>=?",
                (user_id, cutoff),
            ).fetchone()[0]

    def add_submission(self, user_id: int, username: str, filename: Optional[str],
                       code: bytes) -> int:
        file_hash = hashlib.sha256(code).hexdigest()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO submissions (user_id, username, filename, file_hash, code, "
                "status, created) VALUES (?,?,?,?,?, 'pending', ?)",
                (user_id, username, filename, file_hash, code, time.time()),
            )
            return cur.lastrowid

    def set_submission_status(self, sub_id: int, status: str, message: Optional[str],
                              *, make_active: bool = False) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE submissions SET status=?, message=? WHERE id=?",
                (status, message, sub_id),
            )
            if make_active:
                r = conn.execute("SELECT user_id FROM submissions WHERE id=?", (sub_id,)).fetchone()
                if r is not None:
                    conn.execute("UPDATE submissions SET active=0 WHERE user_id=?", (r["user_id"],))
                    conn.execute("UPDATE submissions SET active=1 WHERE id=?", (sub_id,))

    def get_submission(self, sub_id: int) -> Optional[dict]:
        with self._connect() as conn:
            r = conn.execute("SELECT * FROM submissions WHERE id=?", (sub_id,)).fetchone()
        return dict(r) if r else None

    def _summary(self, r: sqlite3.Row) -> dict:
        return {
            "id": r["id"], "user_id": r["user_id"], "username": r["username"],
            "filename": r["filename"], "file_hash": r["file_hash"],
            "status": r["status"], "message": r["message"],
            "active": bool(r["active"]), "created": r["created"],
        }

    def list_user_submissions(self, user_id: int) -> List[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,user_id,username,filename,file_hash,status,message,active,created "
                "FROM submissions WHERE user_id=? ORDER BY created DESC",
                (user_id,),
            ).fetchall()
        return [self._summary(r) for r in rows]

    def list_all_submissions(self, limit: Optional[int] = None,
                             offset: int = 0) -> List[dict]:
        """Newest first.  ``limit=None`` returns every row (legacy callers);
        pass a limit to page through with :meth:`count_all_submissions`."""
        sql = ("SELECT id,user_id,username,filename,file_hash,status,message,active,created "
               "FROM submissions ORDER BY created DESC")
        args: tuple = ()
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args = (int(limit), max(0, int(offset)))
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._summary(r) for r in rows]

    def count_all_submissions(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0])

    def active_submissions(self) -> List[dict]:
        """Active validated bots, with their source, for the match registry.

        Read-only and lazy: if the DB doesn't exist yet (no user/submission has
        been created), return nothing rather than creating the file -- so merely
        importing the server has no filesystem side effect."""
        if not os.path.exists(self.path):
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, username, filename, code FROM submissions "
                "WHERE active=1 AND status='validated'"
            ).fetchall()
        return [{"id": r["id"], "username": r["username"], "filename": r["filename"],
                 "code": bytes(r["code"])} for r in rows]


def materialize_active(store: AccountStore, out_dir: Optional[str] = None,
                       sandbox: "Optional[SandboxSpec]" = None) -> dict:
    """Write each active submission into its own ``out_dir/<username>/`` directory
    and return ``{username: {"command": [...], "timeout": ...}}`` launch specs for
    the tournament :class:`BotRegistry`.

    Each bot gets a *private* directory (source + SDK) so that, once the sandbox
    mounts it read-only, one competitor can never read another's code.  Absolute
    paths (build_command resolves them) keep the launch independent of the worker's
    cwd; C++ bots compile with a hash cache, so this is cheap after the first build
    even though it runs on every reload / worker start."""
    subs = store.active_submissions()
    if not subs:
        return {}
    if sandbox is None:
        from ..arena.sandbox import SandboxSpec as _S
        sandbox = _S.from_env()
    out = Path(out_dir or default_submissions_dir()).resolve()
    specs = {}
    skipped: dict = {}
    for sub in subs:
        bot_dir = out / sub["username"]
        try:
            command = build_command(sub["code"], sub["filename"], bot_dir,
                                    sub["username"], sandbox=sandbox)
        except BuildError as e:
            # A validated bot that no longer builds *here* is skipped -- but say
            # so loudly. Skipping silently is how a competitor ends up scheduled
            # (the competitors table still has the row) yet unlaunchable, so every
            # one of its matches dies with "no launch spec" and no stated reason.
            _log.error("submission %s is validated but will not build; it cannot "
                       "compete until this is fixed: %s", sub["username"], e)
            skipped[sub["username"]] = str(e)
            continue
        specs[sub["username"]] = {"command": command, "timeout": 2.0}
    if skipped:
        _log.error("%d validated submission(s) excluded from the registry: %s",
                   len(skipped), ", ".join(sorted(skipped)))
    return specs


def smoke_test(code: bytes, filename: Optional[str] = None, *, out_dir: Optional[str] = None,
               plies: int = 300, map_id: str = "map1", timeout: float = 2.0,
               sandbox: "Optional[SandboxSpec]" = None) -> tuple:
    """Build the bot (compiling C++ if needed) and play a short game vs ``random``
    to catch build failures, crashes, and protocol errors.

    Runs the candidate through the same isolation path it will compete under (so a
    bot that only misbehaves *inside* the sandbox is caught at upload).  Returns
    ``(ok, message)``.  A compile error, or a dead/timed-out process that
    GameSession would forfeit, fails validation here so the author gets immediate
    feedback.
    """
    from ..arena import SubprocessBot
    from ..bots.base import BotError
    from ..bots.random_bot import RandomBot
    from ..game import Engine, GameConfig, build_observation

    if sandbox is None:
        from ..arena.sandbox import SandboxSpec as _S
        sandbox = _S.from_env()
    # A private per-candidate dir keeps the sandbox mount tight and cleanup simple.
    name = f"_smoke_{secrets.token_hex(4)}"
    tmp = Path(out_dir or default_submissions_dir()).resolve() / name
    try:
        command = build_command(code, filename, tmp, name, sandbox=sandbox)
    except BuildError as e:
        return False, str(e)
    bot = SubprocessBot(command, timeout=timeout, name="candidate")
    opp = RandomBot(seed=0)
    eng = Engine(config=GameConfig(), seed=0)
    eng.reset(map_id=map_id)
    bot.reset()
    opp.reset()
    try:
        n = 0
        while not eng.is_terminal() and n < plies:
            s = eng.current_ship
            obs = build_observation(eng, s)
            action = bot.act(obs) if s == 0 else opp.act(obs)
            eng.apply_action(action)
            n += 1
        strikes = getattr(bot, "strikes", 0)
        if strikes > n // 2:
            return False, f"bot misbehaved on {strikes}/{n} of its moves (timeouts/illegal replies)"
        return True, f"ok ({n} plies, {strikes} strikes)"
    except BotError as e:
        return False, f"bot crashed during smoke test: {e}"
    except Exception as e:  # noqa: BLE001 - surface any harness error to the author
        return False, f"smoke test error: {type(e).__name__}: {e}"
    finally:
        bot.close()
        shutil.rmtree(tmp, ignore_errors=True)  # the whole private candidate dir
