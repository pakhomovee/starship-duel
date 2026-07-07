"""Encode a partial-information :class:`Observation` as a *graph*, not a flat
vector -- the key to map-universality.

Each system becomes a node with **identity-free** features (owner, status,
binary, cache, whether it's my position / a neighbour / the rival's known or
last-seen system, collapse timer, degree, and BFS hop-distances to me and to the
rival's last-seen system).  The map's edges become the adjacency matrix.  Nothing
here depends on a system's *name* or a fixed index, so the same encoder works on
any map.

Everything is padded to a fixed ``MAX_SYSTEMS`` so batching is a plain stack; a
``node_mask`` marks the real nodes.  Node ``i`` corresponds to the ``i``-th entry
of the map's *sorted* system list, matching :class:`UniversalActionCodec` so the
policy's per-node JUMP logits line up with the action indices.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Dict, List

import numpy as np

from ...game import Observation
from ..encoders import _RIVAL_ACTION_VOCAB  # reuse the rival-last-action vocab

# Hard cap on systems per map (pad target).  The current maps are 11-16 systems;
# 32 leaves generous headroom.  Adding a larger map means bumping this and
# retraining (the action space width changes).
MAX_SYSTEMS = 32

_STATUS_IDX = {"STABLE": 0, "DESTABILIZING": 1, "SUPERNOVA": 2}

# Per-node feature width (see encode()).
NODE_DIM = 20


@dataclass
class GraphObs:
    node_features: np.ndarray  # (MAX_SYSTEMS, NODE_DIM) float32
    adjacency: np.ndarray      # (MAX_SYSTEMS, MAX_SYSTEMS) float32, 0/1
    node_mask: np.ndarray      # (MAX_SYSTEMS,) float32, 1 for real nodes
    global_features: np.ndarray  # (global_dim,) float32


def _bfs(adjacency: Dict[str, List[str]], src) -> Dict[str, int]:
    """Hop-distances from ``src`` (empty dict if ``src`` is None)."""
    if src is None:
        return {}
    dist = {src: 0}
    q = deque([src])
    while q:
        n = q.popleft()
        for nb in adjacency.get(n, []):
            if nb not in dist:
                dist[nb] = dist[n] + 1
                q.append(nb)
    return dist


class GraphObsEncoder:
    def __init__(self, systems: List[str], max_systems: int = MAX_SYSTEMS):
        self.systems = list(systems)  # sorted; node order
        self.n = len(self.systems)
        if self.n > max_systems:
            raise ValueError(f"map has {self.n} systems > MAX_SYSTEMS={max_systems}")
        self.max_systems = max_systems
        self._idx = {s: i for i, s in enumerate(self.systems)}
        self.node_dim = NODE_DIM
        self.global_dim = (
            3      # energy, banked_overcharge, actions_remaining
            + 1    # cloaked
            + 3    # my unlocks
            + 3    # rival unlocks
            + 1    # rival position known
            + 1    # rival moves_since_seen
            + 1    # turn number
            + 1    # campaign score diff
            + len(_RIVAL_ACTION_VOCAB)
            + 1    # n_systems (map-size hint)
            + 1    # n_binaries
        )

    # -- public --------------------------------------------------------------
    def encode(self, obs: Observation) -> GraphObs:
        M = self.max_systems
        nf = np.zeros((M, self.node_dim), dtype=np.float32)
        A = np.zeros((M, M), dtype=np.float32)
        node_mask = np.zeros(M, dtype=np.float32)
        node_mask[: self.n] = 1.0

        my_pos = obs.position
        neighbors = set(obs.adjacency.get(my_pos, []))
        binaries = set(obs.binary_systems)
        dist_me = _bfs(obs.adjacency, my_pos)
        dist_seen = _bfs(obs.adjacency, obs.rival_last_seen)

        for s, i in self._idx.items():
            b = nf[i]
            owner = obs.system_owner.get(s)
            if owner is None:
                b[0] = 1.0
            elif owner == obs.ship_id:
                b[1] = 1.0
            else:
                b[2] = 1.0
            b[3 + _STATUS_IDX.get(obs.system_status.get(s, "STABLE"), 0)] = 1.0
            b[6] = 1.0 if s in binaries else 0.0
            cache = obs.system_cache.get(s)
            if cache is None:
                b[7] = 1.0
            elif cache["kind"] == "ENERGY":
                b[8] = 1.0
                b[10] = min(cache["value"] / 50.0, 1.0)
            else:  # OVERCHARGE
                b[9] = 1.0
            b[11] = 1.0 if s == my_pos else 0.0
            b[12] = 1.0 if s in neighbors else 0.0
            b[13] = 1.0 if s == obs.rival_position else 0.0
            b[14] = 1.0 if s == obs.rival_last_seen else 0.0
            ci = obs.system_collapse_in.get(s)
            if ci is not None:
                b[15] = 1.0                          # collapse scheduled
                b[16] = 1.0 - min(ci / 12.0, 1.0)    # urgency (1 == collapsing now)
            b[17] = min(len(obs.adjacency.get(s, [])) / 8.0, 1.0)
            b[18] = min(dist_me.get(s, 10) / 10.0, 1.0)
            b[19] = min(dist_seen.get(s, 10) / 10.0, 1.0) if dist_seen else 1.0

            for nb in obs.adjacency.get(s, []):
                j = self._idx.get(nb)
                if j is not None:
                    A[i, j] = 1.0

        return GraphObs(nf, A, node_mask, self._encode_globals(obs))

    # -- global (graph-level) features --------------------------------------
    def _encode_globals(self, obs: Observation) -> np.ndarray:
        g = np.zeros(self.global_dim, dtype=np.float32)
        i = 0
        g[i] = min(obs.energy / 100.0, 1.0); i += 1
        g[i] = min(obs.banked_overcharge / 10.0, 1.0); i += 1
        g[i] = min(obs.actions_remaining / 10.0, 1.0); i += 1
        g[i] = 1.0 if obs.cloaked else 0.0; i += 1
        g[i] = 1.0 if obs.unlocked["proximity_alert"] else 0.0; i += 1
        g[i] = 1.0 if obs.unlocked["long_range_scanners"] else 0.0; i += 1
        g[i] = 1.0 if obs.unlocked["jamming"] else 0.0; i += 1
        g[i] = 1.0 if obs.rival_unlocked["proximity_alert"] else 0.0; i += 1
        g[i] = 1.0 if obs.rival_unlocked["long_range_scanners"] else 0.0; i += 1
        g[i] = 1.0 if obs.rival_unlocked["jamming"] else 0.0; i += 1
        g[i] = 1.0 if obs.rival_position is not None else 0.0; i += 1
        g[i] = min(obs.rival_moves_since_seen / 10.0, 1.0); i += 1
        g[i] = min(obs.turn_number / 200.0, 1.0); i += 1
        me, them = obs.campaign_score[obs.ship_id], obs.campaign_score[1 - obs.ship_id]
        g[i] = float(np.clip((me - them) / 5.0, -1.0, 1.0)); i += 1
        try:
            k = _RIVAL_ACTION_VOCAB.index(obs.rival_last_action)
        except ValueError:
            k = 0
        g[i + k] = 1.0; i += len(_RIVAL_ACTION_VOCAB)
        g[i] = min(self.n / float(self.max_systems), 1.0); i += 1
        g[i] = min(len(obs.binary_systems) / 5.0, 1.0); i += 1
        return g
