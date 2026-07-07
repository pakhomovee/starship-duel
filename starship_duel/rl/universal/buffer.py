"""Padded graph rollout batch + GAE for the universal policy.

Every :class:`GraphObs` is already padded to ``MAX_SYSTEMS``, so batching is a
plain concatenation of fixed-shape tensors -- no ragged handling needed even
though episodes come from maps of different sizes.  GAE is the same whole-episode
computation as the legacy path (reused from :mod:`starship_duel.rl.ppo.buffer`).
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import torch

from ..ppo.buffer import compute_gae
from .game import GraphEpisode


class GraphRolloutBatch:
    def __init__(self, episodes: List[GraphEpisode], gamma: float, lam: float, device: str):
        nf, adj, nmask, glob, amask = [], [], [], [], []
        actions, logprobs, values, advantages, returns = [], [], [], [], []
        for ep in episodes:
            if ep.length == 0:
                continue
            adv = compute_gae(ep.values, ep.reward, gamma, lam)
            nf.append(ep.node_features); adj.append(ep.adjacency)
            nmask.append(ep.node_mask); glob.append(ep.global_features)
            amask.append(ep.action_masks)
            actions.append(ep.actions); logprobs.append(ep.logprobs)
            values.append(ep.values); advantages.append(adv)
            returns.append(adv + ep.values)

        dev = torch.device(device)
        f32 = lambda a: torch.as_tensor(np.concatenate(a), dtype=torch.float32, device=dev)
        self.node_features = f32(nf)
        self.adjacency = f32(adj)
        self.node_mask = f32(nmask)
        self.global_features = f32(glob)
        self.action_mask = torch.as_tensor(np.concatenate(amask), dtype=torch.int8, device=dev)
        self.actions = torch.as_tensor(np.concatenate(actions), dtype=torch.int64, device=dev)
        self.logprobs = f32(logprobs)
        self.values = f32(values)
        self.advantages = f32(advantages)
        self.returns = f32(returns)

    def __len__(self) -> int:
        return self.actions.shape[0]

    def minibatch(self, idx) -> Dict[str, torch.Tensor]:
        return {
            "node_features": self.node_features[idx],
            "adjacency": self.adjacency[idx],
            "node_mask": self.node_mask[idx],
            "global_features": self.global_features[idx],
            "action_mask": self.action_mask[idx],
        }
