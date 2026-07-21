"""Encode a partial-information :class:`Observation` into a fixed-size vector.

The layout is a flat ``float32`` array so it drops straight into an MLP; a
policy that wants graph structure can reshape the per-system block back to
``(n_systems, PER_SYSTEM)`` and attend over it.

    per-system block : n_systems * PER_SYSTEM features
    global block     : GLOBAL features

Everything is normalised to roughly ``[0, 1]`` / ``[-1, 1]``.  Nothing here
reads hidden rival state -- only what the observation already exposes.
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..game import ActionType, Observation

# Per-system one-hot / scalar features (see _encode_systems).
PER_SYSTEM = 16

# Categories a rival's last public action can collapse to (spec 3).  Slot 0
# doubles as the catch-all: it covers both "the rival has not acted yet" and
# "UNKNOWN" (an action we could not identify) -- the width is frozen so the
# shipped checkpoints in ``bots/ppo`` keep loading.
_RIVAL_ACTION_VOCAB = [
    None,
    "JUMP", "HOLD", "CLAIM", "FIRE",
    "JAMMED",
    "SCAN", "DEEP_CLOAK", "OVERCHARGE",
    "UNLOCK_PROXIMITY_ALERT", "UNLOCK_LONG_RANGE_SCANNERS", "UNLOCK_JAMMING",
]


def rival_action_index(label: Optional[str]) -> int:
    """One-hot slot for a public action category (0 = unknown/never acted)."""
    try:
        return _RIVAL_ACTION_VOCAB.index(label)
    except ValueError:
        return 0


class ObservationEncoder:
    def __init__(self, systems: List[str]):
        self.systems = list(systems)
        self.n_systems = len(self.systems)
        self._sys_index = {s: i for i, s in enumerate(self.systems)}
        self.global_size = (
            3        # energy, banked_overcharge, actions_remaining (normalised)
            + 1      # cloaked
            + 3      # my unlocks
            + 3      # rival unlocks
            + 1      # rival_position known (hard reveal)
            + 1      # rival_moves_since_seen (normalised BFS radius)
            + 1      # turn_number (normalised)
            + 1      # campaign score differential
            + len(_RIVAL_ACTION_VOCAB)  # rival last action one-hot
        )
        self.size = self.n_systems * PER_SYSTEM + self.global_size

    # -- public --------------------------------------------------------------
    def encode(self, obs: Observation) -> np.ndarray:
        out = np.zeros(self.size, dtype=np.float32)
        self._encode_systems(obs, out)
        self._encode_globals(obs, out, offset=self.n_systems * PER_SYSTEM)
        return out

    # -- per-system block ----------------------------------------------------
    def _encode_systems(self, obs: Observation, out: np.ndarray) -> None:
        my_pos = obs.position
        my_neighbors = set(obs.adjacency.get(my_pos, []))
        binaries = set(obs.binary_systems)
        status_idx = {"STABLE": 0, "DESTABILIZING": 1, "SUPERNOVA": 2}

        for s, i in self._sys_index.items():
            base = i * PER_SYSTEM
            owner = obs.system_owner.get(s)
            # owner one-hot: none / me / rival
            if owner is None:
                out[base + 0] = 1.0
            elif owner == obs.ship_id:
                out[base + 1] = 1.0
            else:
                out[base + 2] = 1.0
            # status one-hot
            out[base + 3 + status_idx.get(obs.system_status.get(s, "STABLE"), 0)] = 1.0
            # is_binary
            out[base + 6] = 1.0 if s in binaries else 0.0
            # cache one-hot none / energy / overcharge + normalised value
            cache = obs.system_cache.get(s)
            if cache is None:
                out[base + 7] = 1.0
            elif cache["kind"] == "ENERGY":
                out[base + 8] = 1.0
                out[base + 10] = min(cache["value"] / 50.0, 1.0)
            else:  # OVERCHARGE
                out[base + 9] = 1.0
            # positional flags
            out[base + 11] = 1.0 if s == my_pos else 0.0
            out[base + 12] = 1.0 if s in my_neighbors else 0.0
            # rival's exact system, only when known for certain (else all zero)
            out[base + 13] = 1.0 if s == obs.rival_position else 0.0
            out[base + 14] = 1.0 if owner == obs.ship_id else 0.0  # (redundant-safe)
            # the rival's last confirmed system (BFS seed for the belief)
            out[base + 15] = 1.0 if s == obs.rival_last_seen else 0.0

    # -- global block --------------------------------------------------------
    def _encode_globals(self, obs: Observation, out: np.ndarray, offset: int) -> None:
        i = offset
        out[i] = min(obs.energy / 100.0, 1.0); i += 1
        out[i] = min(obs.banked_overcharge / 10.0, 1.0); i += 1
        out[i] = min(obs.actions_remaining / 10.0, 1.0); i += 1
        out[i] = 1.0 if obs.cloaked else 0.0; i += 1
        out[i] = 1.0 if obs.unlocked["proximity_alert"] else 0.0; i += 1
        out[i] = 1.0 if obs.unlocked["long_range_scanners"] else 0.0; i += 1
        out[i] = 1.0 if obs.unlocked["jamming"] else 0.0; i += 1
        out[i] = 1.0 if obs.rival_unlocked["proximity_alert"] else 0.0; i += 1
        out[i] = 1.0 if obs.rival_unlocked["long_range_scanners"] else 0.0; i += 1
        out[i] = 1.0 if obs.rival_unlocked["jamming"] else 0.0; i += 1
        out[i] = 1.0 if obs.rival_position is not None else 0.0; i += 1
        out[i] = min(obs.rival_moves_since_seen / 10.0, 1.0); i += 1
        out[i] = min(obs.turn_number / 200.0, 1.0); i += 1
        me, them = obs.campaign_score[obs.ship_id], obs.campaign_score[1 - obs.ship_id]
        out[i] = float(np.clip((me - them) / 5.0, -1.0, 1.0)); i += 1
        # rival last action one-hot (the final action of its last turn)
        out[i + rival_action_index(obs.rival_last_action)] = 1.0
