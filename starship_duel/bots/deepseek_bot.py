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
You are a world-class Starship Duel player. Starship Duel is a 1v1, alternating-turn, \
zero-sum game of HIDDEN INFORMATION on a graph of star systems. You pilot one starship; \
a rival pilots the other. You WIN only by choosing FIRE while your ship is in the SAME \
system as the rival. There is no draw to play for and no reward for surviving — passive \
play loses. Every decision serves one aim: engineer a moment where you know exactly where \
the rival is and you are on top of them, while never letting them do the same to you.

# Turn structure
You act ONE action at a time. You have `actions_remaining` this turn (2, plus any you \
banked). You'll be asked again for each remaining action, so plan combos across a turn \
(e.g. JUMP then FIRE). Unspent actions above 2 bank for next turn.

# The collapse (shrinking field)
The star field is collapsing from the edges inward. Each system shows \
`collapse_in` plies until it goes supernova; when it does, any ship on it is \
DESTROYED. `your_system_collapses_in` is your own countdown (null = safe). If it \
is small, EVACUATE now — JUMP to a system that survives longer (higher \
collapse_in or null), never HOLD on a dying system. The safe zone shrinks toward \
a single surviving "eye", so drift inward over time. This also forces the fight: \
use the shrinking space to corner the rival.

# Economy
Owning systems pays Energy every turn: binary systems pay 4, single-star systems pay 1. \
Binaries are the map's hubs — lucrative but high-traffic (the rival passes through them). \
Energy buys SCAN / DEEP_CLOAK / OVERCHARGE / unlocks. The LEGAL action list already \
excludes anything you cannot afford, so pick freely from it.

# Hidden information (the crux)
Your ship is cloaked (hidden) or exposed (rival sees your exact system). Read these fields:
- `you_are_cloaked` — your status.
- `rival_known_position` — the rival's EXACT system if you know it for certain right now, \
else null.
- `rival_last_seen` + `rival_moves_since_seen` — they were last confirmed at that system \
and could have travelled up to that many hops since.
- `rival_estimated_systems` — your best set of where they might be NOW. Treat these as \
DANGER squares.
You get EXPOSED when you CLAIM, collect a cache (by starting a turn on it), JUMP into a \
system the rival owns or occupies, or FIRE-and-miss while they hold Proximity Alert. \
HOLD re-cloaks you — but does NOTHING if you are already cloaked.
You LEARN the rival's exact spot when they expose themselves, when you SCAN (unless they \
are deep-cloaked), or when you JUMP into their system while owning Long-Range Scanners.

# The only way to kill — and how to set it up
- If `rival_known_position` == `your_position`: FIRE now. You win.
- If `rival_known_position` is ADJACENT and you have >=2 actions left: JUMP onto them, \
then FIRE — a clean kill this turn. NEVER jump onto the rival with only 1 action left: \
you'd land exposed in their system and be shot next turn.
- Manufacture kills: SCAN when you think they're on/next to you (with actions to spare to \
JUMP+FIRE); or unlock LONG-RANGE SCANNERS and JUMP into a high-confidence estimated system \
— that both reveals them and lets you FIRE; or DEEP_CLOAK and walk right onto them \
undetected, then FIRE (deep cloak lets you enter enemy territory and sit on them safely).

# Deadly mistakes — never do these
- Never JUMP into a system in `rival_estimated_systems` unless you are committing to a \
kill. Entering the rival's system exposes YOU there and hands them a free kill next turn. \
When repositioning while hidden, route AROUND the danger squares.
- Never HOLD while already cloaked — it burns the whole turn for nothing.
- Never be predictable. If you always take the shortest path to the obvious objective, the \
rival camps it and kills you. Vary your route and your timing.
- Never idle. Look at `your_recent_actions`: repeating HOLD/END_TURN means you are stuck \
and losing. Force progress — claim, scan, or reposition.
- Don't over-fire on a guess. A miss wastes an action and (vs Proximity Alert) exposes you.

# Decision policy — pick the first that applies
1. Rival known in YOUR system -> FIRE.
2. Rival known ADJACENT and >=2 actions -> JUMP onto them (FIRE next).
3. You are EXPOSED with no immediate kill -> HOLD to vanish.
4. Standing on an unclaimed BINARY with no known rival adjacent -> CLAIM it (income funds \
scans/unlocks; you'll re-cloak next turn).
5. Rival's location unknown/stale and you can afford it -> SCAN to pin them, then strike.
6. Rich enough for a decisive unlock (esp. Long-Range Scanners) that enables kills -> buy it.
7. Otherwise REPOSITION unpredictably toward unclaimed binaries or to intercept the rival's \
likely path, but AVOID every `rival_estimated_systems` square. A safe, purposeful move beats \
sitting still.

Balance three things at once: threaten a FIRE kill, stay cloaked and out of ambush, and keep \
your Energy economy ahead. Choose the single action that best does all three.

# Output
You'll get the game state (JSON) and a numbered list of LEGAL actions, each annotated with \
its effect. Reply with ONLY the integer index of your chosen action — just the digits, \
nothing else. No words, no explanation, no punctuation. Example valid replies: "0" or "4"."""


class DeepSeekBot(Bot):
    name = "deepseek"

    def __init__(self, name: Optional[str] = None, seed: Optional[int] = None,
                 model: Optional[str] = None, timeout: Optional[float] = None):
        super().__init__(name=name, seed=seed)
        self.api_key = os.environ.get("DEEPSEEK_API_KEY")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        self.timeout = timeout if timeout is not None else float(os.environ.get("DEEPSEEK_TIMEOUT", "20"))
        # A little randomness avoids deterministic loops (temperature 0 + an
        # unchanging state made the model pick HOLD forever).
        self.temperature = float(os.environ.get("DEEPSEEK_TEMPERATURE", "0.6"))
        self._fallback = HeuristicBot(seed=seed)
        self.belief = BotBelief()  # we infer the rival's whereabouts ourselves
        self.history: List[str] = []  # our own recent moves, fed back to the model
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
        self.history = []
        self.last_error = None

    def _remember(self, action: Action) -> None:
        self.history.append(str(action))
        self.history = self.history[-6:]

    # -- main entry point ----------------------------------------------------
    def act(self, obs: Observation) -> Action:
        legal = obs.legal_actions
        self.belief.observe(obs)
        if not legal:
            return Action.end_turn()

        if not self.api_key:
            # Logged loudly once in __init__; note the fallback here too so a
            # glance at the logs makes the cause obvious.
            logger.warning("no DEEPSEEK_API_KEY -> playing this move as the heuristic bot")
            action = self._fallback.act(obs)
            self._remember(action)
            return action

        try:
            idx, raw = self._query(obs, legal)
            if idx is not None and 0 <= idx < len(legal):
                self.last_pick = idx
                self.last_error = None
                action = legal[idx]
                logger.info("DeepSeek chose [%d] %s", idx, action)
                self._remember(action)
                return action
            self.last_error = f"unparseable/out-of-range reply {raw!r} (idx={idx}, {len(legal)} legal)"
            logger.warning("%s -> heuristic fallback", self.last_error)
        except Exception as e:  # network/timeout/parse — degrade gracefully
            self.last_error = f"{type(e).__name__}: {e}"
            logger.warning("DeepSeek call failed: %s -> heuristic fallback", self.last_error)
        action = self._fallback.act(obs)
        self._remember(action)
        return action

    # -- API plumbing --------------------------------------------------------
    def _query(self, obs: Observation, legal: List[Action]):
        """Returns ``(index, raw_content)``.  ``index`` is None if unparseable."""
        user_prompt = _build_user_prompt(obs, legal, self.belief, self.history)
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": 24,
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
        msg = payload["choices"][0]["message"]
        content = msg.get("content") or ""
        logger.info("DeepSeek replied in %.1fs: %r", dt, content)
        return _pick_index(content, len(legal)), content


def _pick_index(text: str, n: int) -> Optional[int]:
    """Extract a valid action index from the model's reply.

    Prefers the LAST in-range integer (models often reason first, answer last);
    falls back to the last integer of any value."""
    ints = [int(m) for m in re.findall(r"-?\d+", text or "")]
    if not ints:
        return None
    in_range = [v for v in ints if 0 <= v < n]
    return in_range[-1] if in_range else ints[-1]


# Short effect hints appended to each legal action in the prompt.
_ACTION_EFFECT = {
    "JUMP": "move to {dest}",
    "HOLD": "stay put and re-cloak (DOES NOTHING if you are already cloaked)",
    "CLAIM": "claim this system for income; exposes you",
    "FIRE": "attack this system; wins if the rival is here, else a public miss",
    "SCAN": "reveal the rival's exact location (costs Energy)",
    "DEEP_CLOAK": "become undetectable for 2 turns (costs Energy)",
    "OVERCHARGE": "bank +1 action for next turn (costs Energy)",
    "UNLOCK_PROXIMITY_ALERT": "permanent: rival revealed when their Fire misses",
    "UNLOCK_LONG_RANGE_SCANNERS": "permanent: reveal rival when you jump into their system",
    "UNLOCK_JAMMING": "permanent: hide your Energy-action types from the rival",
    "END_TURN": "end the turn now (banks unspent actions beyond 2)",
}


def _build_user_prompt(obs: Observation, legal: List[Action], belief: "BotBelief",
                       history: Optional[List[str]] = None) -> str:
    my_unlocks = [k for k, v in obs.unlocked.items() if v]
    rival_unlocks = [k for k, v in obs.rival_unlocked.items() if v]
    owned = [s for s, o in obs.system_owner.items() if o == obs.ship_id]
    rival_owned = [s for s, o in obs.system_owner.items() if o not in (None, obs.ship_id)]
    caches = {s: f"{c['kind']}:{c['value']}" for s, c in obs.system_cache.items() if c}
    # The shrinking field: systems already gone, and how many plies until the
    # rest collapse (so the model evacuates in time).
    dead = [s for s, st in obs.system_status.items() if st == "SUPERNOVA"]
    collapsing = {s: n for s, n in obs.system_collapse_in.items()
                  if n is not None and obs.system_status.get(s) != "SUPERNOVA"}
    here_collapse = obs.system_collapse_in.get(obs.position)

    state = {
        "your_position": obs.position,
        "you_are_cloaked": obs.cloaked,
        "energy": obs.energy,
        "actions_remaining": obs.actions_remaining,
        "banked_overcharge": obs.banked_overcharge,
        "your_unlocks": my_unlocks,
        "your_recent_actions": list(history or []),
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
        # THE COLLAPSE: your system collapses in `your_system_collapses_in` plies
        # (null = safe). Evacuate before it hits 0 or you are destroyed.
        "your_system_collapses_in": here_collapse,
        "systems_collapsing_in_plies": collapsing,
        "destroyed_systems": dead,
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
        effect = _ACTION_EFFECT.get(a.type.name, "")
        if a.dest:
            effect = effect.format(dest=a.dest)
        label = a.type.name + (f"({a.dest})" if a.dest else "")
        lines.append(f"  {i}: {label} — {effect}")
    lines.append("")
    lines.append("Your choice (index only):")
    return "\n".join(lines)
