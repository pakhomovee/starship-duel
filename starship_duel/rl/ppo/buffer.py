"""Rollout storage + GAE over whole self-play episodes.

Because episodes always terminate (the collapse bounds game length) we collect
them in full and never bootstrap a truncated tail: the only non-zero reward is
the terminal +1/-1/0 on the last learner decision, and the value target past the
end is 0.  Advantages are computed per episode, then everything is concatenated
into flat tensors ready for minibatch SGD.
"""

from __future__ import annotations

from typing import List

import numpy as np
import torch

from ..single_agent_view import Episode


def compute_gae(
    values: np.ndarray, terminal_reward: float, gamma: float, lam: float
) -> np.ndarray:
    """GAE(λ) advantages for one episode.

    Reward is 0 on every step except the last (``terminal_reward``); the
    bootstrap value past the terminal step is 0.
    """
    t = len(values)
    adv = np.zeros(t, dtype=np.float32)
    last = 0.0
    for i in reversed(range(t)):
        next_value = 0.0 if i == t - 1 else float(values[i + 1])
        reward = terminal_reward if i == t - 1 else 0.0
        delta = reward + gamma * next_value - float(values[i])
        last = delta + gamma * lam * last
        adv[i] = last
    return adv


class RolloutBatch:
    """Flattened, device-resident tensors for a batch of episodes."""

    def __init__(self, episodes: List[Episode], gamma: float, lam: float, device: str):
        obs, masks, actions, logprobs, values, advantages, returns = [], [], [], [], [], [], []
        for ep in episodes:
            if ep.length == 0:
                continue
            adv = compute_gae(ep.values, ep.reward, gamma, lam)
            obs.append(ep.obs)
            masks.append(ep.masks)
            actions.append(ep.actions)
            logprobs.append(ep.logprobs)
            values.append(ep.values)
            advantages.append(adv)
            returns.append(adv + ep.values)

        dev = torch.device(device)
        self.obs = torch.as_tensor(np.concatenate(obs), dtype=torch.float32, device=dev)
        self.masks = torch.as_tensor(np.concatenate(masks), dtype=torch.int8, device=dev)
        self.actions = torch.as_tensor(np.concatenate(actions), dtype=torch.int64, device=dev)
        self.logprobs = torch.as_tensor(np.concatenate(logprobs), dtype=torch.float32, device=dev)
        self.values = torch.as_tensor(np.concatenate(values), dtype=torch.float32, device=dev)
        self.advantages = torch.as_tensor(np.concatenate(advantages), dtype=torch.float32, device=dev)
        self.returns = torch.as_tensor(np.concatenate(returns), dtype=torch.float32, device=dev)

    def __len__(self) -> int:
        return self.obs.shape[0]
