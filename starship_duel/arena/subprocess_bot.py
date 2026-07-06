"""Run an external program as a bot, refereed over the JSON-line protocol.

The process is spawned ONCE per game (persistent pipes, so the bot keeps its own
memory across turns).  Each :meth:`act` writes one request line and waits up to
``timeout`` seconds for one reply line.  A timeout, dead process, malformed line,
or illegal move never crashes the match: the engine substitutes a safe default
action and records a *strike*.
"""

from __future__ import annotations

import json
import logging
import queue
import shlex
import subprocess
import threading
from typing import List, Optional, Sequence, Union

from ..bots.base import Bot, BotError
from ..game import Action, Observation
from .protocol import encode_request, parse_reply

logger = logging.getLogger("starship_duel.arena")


class SubprocessBot(Bot):
    """A :class:`Bot` backed by an external process speaking the wire protocol."""

    def __init__(
        self,
        command: Union[str, Sequence[str]],
        name: Optional[str] = None,
        seed: Optional[int] = None,
        timeout: float = 1.0,
        cwd: Optional[str] = None,
    ):
        super().__init__(name=name or "subprocess", seed=seed)
        self.command: List[str] = shlex.split(command) if isinstance(command, str) else list(command)
        self.timeout = timeout
        self.cwd = cwd
        self.strikes = 0
        self._proc: Optional[subprocess.Popen] = None
        self._q: "queue.Queue[Optional[str]]" = queue.Queue()
        self._reader: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------------
    def reset(self) -> None:
        self.close()
        self.strikes = 0
        self._q = queue.Queue()
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,  # let the bot's stderr flow to the console for debugging
                text=True,
                bufsize=1,
                cwd=self.cwd,
            )
        except OSError as e:
            logger.error("could not launch bot %r: %s", " ".join(self.command), e)
            self._proc = None
            return
        self._reader = threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True)
        self._reader.start()
        logger.info("launched bot %r (pid=%s)", " ".join(self.command), self._proc.pid)

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    def __del__(self):  # best-effort cleanup
        try:
            self.close()
        except Exception:
            pass

    # -- IO ------------------------------------------------------------------
    def _read_loop(self, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:  # blocks; ends on EOF
                self._q.put(line)
        except Exception:
            pass
        finally:
            self._q.put(None)  # sentinel: stream closed

    def _drain(self) -> None:
        """Discard any stale/late lines so requests and replies stay in sync."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    # -- decision ------------------------------------------------------------
    def act(self, obs: Observation) -> Action:
        legal = obs.legal_actions
        default = self._default(legal)

        # A dead process is a runtime crash -> automatic loss (not a strike).
        if self._proc is None or self._proc.poll() is not None:
            raise BotError(f"bot process is not running (exit={self._exit_code()})")

        self._drain()
        request = json.dumps(encode_request(obs), separators=(",", ":"))
        try:
            self._proc.stdin.write(request + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise BotError(f"bot process died while being sent a request: {e}")

        try:
            line = self._q.get(timeout=self.timeout)
        except queue.Empty:
            # Still alive, just slow -> graceful strike + default (not a loss).
            return self._strike(f"timed out after {self.timeout}s", default)
        if line is None:
            # Output stream closed -> the process exited/crashed -> loss.
            raise BotError(f"bot process crashed / closed its output (exit={self._exit_code()})")

        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            return self._strike(f"non-JSON reply {line.strip()!r}", default)

        action = parse_reply(reply, legal)
        if action is None:
            return self._strike(f"illegal/unknown reply {reply!r}", default)
        return action

    def _exit_code(self):
        return self._proc.poll() if self._proc else None

    def _default(self, legal: List[Action]) -> Action:
        from ..game import ActionType
        for a in legal:  # a harmless pass if available
            if a.type is ActionType.END_TURN:
                return a
        return legal[0] if legal else Action.end_turn()

    def _strike(self, reason: str, default: Action) -> Action:
        self.strikes += 1
        logger.warning("bot %r strike #%d (%s) -> default %s",
                       self.name, self.strikes, reason, default)
        return default
