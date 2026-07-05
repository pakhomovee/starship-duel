"""A bot that picks each action via the DeepSeek chat API.

DeepSeek exposes an OpenAI-compatible endpoint, so we POST a small JSON prompt
(the current partial-information observation + the numbered legal actions) and
parse back the chosen index.  Uses only the stdlib (`urllib`) so it adds no
dependency to the package.

Configuration (environment):
    DEEPSEEK_API_KEY    required — your API key
    DEEPSEEK_MODEL      optional — default "deepseek-chat"
    DEEPSEEK_BASE_URL   optional — default "https://api.deepseek.com/v1"

Robustness: if the key is missing, the request fails, times out, or the reply
can't be parsed into a legal action, the bot silently falls back to
:class:`HeuristicBot` for that move (and remembers the last error).  This keeps
matches running even without connectivity.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from typing import List, Optional

from ..game import Action, Observation
from .base import Bot

logger = logging.getLogger("starship_duel.bots.deepseek")
from .belief import BotBelief
from .heuristic_bot import HeuristicBot

_SYSTEM_PROMPT = """\
You are an expert Starship Duel player. Your goal is to win the skirmish by FIRE-ing while in the rival’s exact system. You control one starship, hunting a hidden rival while staying hidden yourself.

Key mechanics:
- You get 2 (+banked) actions per turn. END_TURN passes; unspent actions beyond 2 bank for next turn.
- The rival’s exact location is usually hidden. "rival_known_position" is their exact system when known for certain (else null); "rival_estimated_systems" is your inferred set of where they might be. Reason from the action log and the map to narrow this set.
- JUMP moves to an adjacent system. CLAIM takes the system you're in for income but exposes you. HOLD re-cloaks you. SCAN reveals the rival (costs 10 Energy). DEEP_CLOAK makes you undetectable for two turns (costs 25 Energy). OVERCHARGE banks an extra action (costs 40 Energy). Unlocks (Proximity Alert, Long Range Scanners, Jamming) are permanent upgrades with Energy costs.
- Owning systems gives Energy each turn (binary systems: 4, single-star: 1). Caches give Energy or Overcharge when you START a turn on them; collection exposes you.
- Energy is limited and managing it is critical. You must NEVER choose an action that costs more Energy than you currently have, even if it mistakenly appears in the legal list. Always check your current Energy against the action’s cost before selecting. Only select actions you can afford.
- You cannot use items/abilities (unlocks) if you lack the required Energy.

Winning strategy:
- Locate the rival: use SCANs at key moments, and infer position from the candidate-system estimate and the rival’s last actions. Narrow the set using public info (e.g., rival cannot be where you are unless a reveal occurred).
- Close in safely: move through systems you control or are empty; avoid unnecessary exposure.
- Fire only when you are certain the rival is in your exact system. A missed Fire may reveal your position (if rival has Proximity Alert) or simply waste an action. Firing while exposed is risky.
- Re-cloak (HOLD) after becoming exposed unless you have a good reason to stay visible.
- Spend Energy primarily on economy (CLAIM), occasional SCANs to update the rival’s location, and unlocks that fit your strategy. Deep Cloak or Overcharge should be used sparingly and only when you have surplus Energy and a plan.

You will be given the game state and a numbered list of LEGAL actions. Reply with ONLY the single integer index of the action you choose. No other text."""


class DeepSeekBot(Bot):
    name = "deepseek"

    def __init__(self, name: Optional[str] = None, seed: Optional[int] = None,
                 model: Optional[str] = None, timeout: Optional[float] = None):
        super().__init__(name=name, seed=seed)
        self.api_key = os.environ.get("DEEPSEEK_API_KEY")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.timeout = timeout if timeout is not None else float(os.environ.get("DEEPSEEK_TIMEOUT", "20"))
        self._fallback = HeuristicBot(seed=seed)
        self.belief = BotBelief()  # we infer the rival's whereabouts ourselves
        self.last_error: Optional[str] = None
        self.last_pick: Optional[int] = None
        if self.api_key:
            logger.info("DeepSeekBot ready: model=%s base_url=%s timeout=%ss key=***%s",
                        self.model, self.base_url, self.timeout, self.api_key[-4:])
        else:
            logger.warning("DeepSeekBot has NO DEEPSEEK_API_KEY set -- every move will "
                           "fall back to the heuristic bot. Export DEEPSEEK_API_KEY to enable it.")

    def reset(self) -> None:
        self._fallback.reset()
        self.belief.reset()
        self.last_error = None

    # -- main entry point ----------------------------------------------------
    def act(self, obs: Observation) -> Action:
        legal = obs.legal_actions
        logger.debug("starting observation")
        self.belief.observe(obs)
        if not legal:
            return Action.end_turn()
        logger.debug("observed, quering")
        if not self.api_key:
            self.last_error = "DEEPSEEK_API_KEY not set"
            logger.debug("no API key -> heuristic fallback")
            print("no API key -> heuristic fallback")
            return self._fallback.act(obs)
        try:
            idx = self._query(obs, legal)
            print("API OK")
            if idx is not None and 0 <= idx < len(legal):
                self.last_pick = idx
                self.last_error = None
                logger.info("chose [%d] %s", idx, legal[idx])
                return legal[idx]
            self.last_error = f"model returned unusable index {idx!r}"
            logger.warning("%s (legal has %d actions) -> heuristic fallback",
                           self.last_error, len(legal))
            print("error -> heuristic fallback")
        except Exception as e:  # network/timeout/parse — degrade gracefully
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning("API call failed: %s -> heuristic fallback", self.last_error)
            print("API FAILED")
        return self._fallback.act(obs)

    # -- API plumbing --------------------------------------------------------
    def _query(self, obs: Observation, legal: List[Action]) -> Optional[int]:
        user_prompt = _build_user_prompt(obs, legal, self.belief)
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 12,
            "stream": False,
        }).encode("utf-8")

        url = f"{self.base_url}/chat/completions"
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        logger.info("POST %s (model=%s, %d legal actions, timeout=%ss)...",
                    url, self.model, len(legal), self.timeout)
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            logger.error("HTTP %s from DeepSeek after %.1fs: %s", e.code, time.monotonic() - t0, detail)
            raise
        dt = time.monotonic() - t0
        content = payload["choices"][0]["message"]["content"]
        logger.info("DeepSeek replied in %.1fs: %r", dt, content)
        return _first_int(content)


def _first_int(text: str) -> Optional[int]:
    m = re.search(r"-?\d+", text or "")
    return int(m.group()) if m else None


def _build_user_prompt(obs: Observation, legal: List[Action], belief: "BotBelief") -> str:
    my_unlocks = [k for k, v in obs.unlocked.items() if v]
    rival_unlocks = [k for k, v in obs.rival_unlocked.items() if v]
    owned = [s for s, o in obs.system_owner.items() if o == obs.ship_id]
    rival_owned = [s for s, o in obs.system_owner.items() if o not in (None, obs.ship_id)]
    caches = {s: f"{c['kind']}:{c['value']}" for s, c in obs.system_cache.items() if c}

    state = {
        "your_position": obs.position,
        "you_are_cloaked": obs.cloaked,
        "energy": obs.energy,
        "actions_remaining": obs.actions_remaining,
        "banked_overcharge": obs.banked_overcharge,
        "your_unlocks": my_unlocks,
        "rival_unlocks": rival_unlocks,
        "rival_last_action": obs.rival_last_action,
        # Exact system only if known for certain; else our own inferred estimate.
        "rival_known_position": obs.rival_position,
        "rival_last_seen": obs.rival_last_seen,
        "rival_moves_since_seen": obs.rival_moves_since_seen,
        "rival_estimated_systems": sorted(belief.candidates),
        "you_own": owned,
        "rival_owns": rival_owned,
        "caches": caches,
        "adjacent_to_you": obs.adjacency.get(obs.position, []),
        "map_adjacency": obs.adjacency,
    }
    lines = [
        "GAME STATE:",
        json.dumps(state, indent=0),
        "",
        "LEGAL ACTIONS (reply with one index):",
    ]
    for i, a in enumerate(legal):
        desc = a.type.name + (f"({a.dest})" if a.dest else "")
        lines.append(f"  {i}: {desc}")
    lines.append("")
    lines.append("Your choice (index only):")
    return "\n".join(lines)
