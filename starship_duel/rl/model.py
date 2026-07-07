"""Masked actor-critic network for PPO.

A shared MLP torso over the flat :class:`~starship_duel.rl.encoders.ObservationEncoder`
vector feeds two heads: categorical policy logits over the flat action space and
a scalar state value.

**Action masking is applied to the logits before the softmax**: illegal actions
get a large finite negative bias (not ``-inf``, which would produce ``NaN`` if a
whole row were masked).  On a ship's own turn ``END_TURN`` is always legal, so a
row is never fully masked -- but the finite fill keeps us safe regardless.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Categorical

# Large finite penalty for illegal actions (softmax -> ~0 probability) that
# still leaves gradients well-defined even for a hypothetically all-masked row.
_MASK_FILL = -1e8


def _layer_init(layer: nn.Linear, std: float = np.sqrt(2), bias: float = 0.0) -> nn.Linear:
    nn.init.orthogonal_(layer.weight, std)
    nn.init.constant_(layer.bias, bias)
    return layer


class MaskedActorCritic(nn.Module):
    def __init__(self, obs_size: int, n_actions: int, hidden: int = 256, depth: int = 2):
        super().__init__()
        self.obs_size = obs_size
        self.n_actions = n_actions

        layers = []
        in_dim = obs_size
        for _ in range(depth):
            layers += [_layer_init(nn.Linear(in_dim, hidden)), nn.Tanh()]
            in_dim = hidden
        self.torso = nn.Sequential(*layers)
        # Small-gain policy head (near-uniform at init); standard critic gain.
        self.policy_head = _layer_init(nn.Linear(hidden, n_actions), std=0.01)
        self.value_head = _layer_init(nn.Linear(hidden, 1), std=1.0)

    # -- core ---------------------------------------------------------------
    def _logits_value(self, obs: torch.Tensor, mask: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        h = self.torso(obs)
        logits = self.policy_head(h)
        logits = torch.where(mask.bool(), logits, torch.full_like(logits, _MASK_FILL))
        value = self.value_head(h).squeeze(-1)
        return logits, value

    def get_value(self, obs: torch.Tensor) -> torch.Tensor:
        return self.value_head(self.torso(obs)).squeeze(-1)

    def get_action_and_value(
        self,
        obs: torch.Tensor,
        mask: torch.Tensor,
        action: Optional[torch.Tensor] = None,
    ) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        """Returns ``(action, log_prob, entropy, value)``.

        If ``action`` is given (the PPO update), its log-prob/entropy are scored
        under the current policy; otherwise an action is sampled (rollout).
        """
        logits, value = self._logits_value(obs, mask)
        dist = Categorical(logits=logits)
        if action is None:
            action = dist.sample()
        return action, dist.log_prob(action), dist.entropy(), value

    # -- numpy inference (rollout / bot inference) --------------------------
    @torch.no_grad()
    def act_numpy(
        self, obs: np.ndarray, mask: np.ndarray, *, deterministic: bool = False
    ) -> Tuple[int, float, float]:
        """Single-observation sampling from numpy inputs.

        Returns ``(action_index, log_prob, value)``.  Used by the rollout
        collector and by the greedy bot wrapper (``deterministic=True``).
        """
        device = next(self.parameters()).device
        o = torch.as_tensor(obs, dtype=torch.float32, device=device).unsqueeze(0)
        m = torch.as_tensor(mask, dtype=torch.int8, device=device).unsqueeze(0)
        logits, value = self._logits_value(o, m)
        if deterministic:
            action = logits.argmax(dim=-1)
        else:
            action = Categorical(logits=logits).sample()
        logp = Categorical(logits=logits).log_prob(action)
        return int(action.item()), float(logp.item()), float(value.item())
