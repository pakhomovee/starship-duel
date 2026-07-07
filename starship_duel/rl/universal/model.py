"""GNN actor-critic for the map-universal policy.

Architecture:
  1. Per-node input MLP over the identity-free node features.
  2. ``gnn_layers`` rounds of message passing over the (self-looped, row-
     normalized) adjacency -- this is what lets the net reason about reachability
     on an arbitrary graph.
  3. Masked mean+max pooling over nodes, concatenated with the global features,
     into a graph embedding used by the value head and the verb head.
  4. A **pointer** JUMP head: score each node by a dot-product between a query
     (from the graph embedding) and per-node keys, giving one logit per system.

Full logits are ``[MAX_SYSTEMS jump | 10 verb]`` (see UniversalActionCodec) with
the legal-action mask applied before the softmax.  Everything is padded to
``MAX_SYSTEMS`` and masked, so batching is a plain stack of fixed-shape tensors.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.distributions import Categorical

from ..action_coding import _NON_JUMP_ORDER
from .graph_encoder import MAX_SYSTEMS, GraphObs

_MASK_FILL = -1e8
N_VERBS = len(_NON_JUMP_ORDER)


def _layer_init(layer: nn.Linear, std: float = np.sqrt(2), bias: float = 0.0) -> nn.Linear:
    nn.init.orthogonal_(layer.weight, std)
    nn.init.constant_(layer.bias, bias)
    return layer


class GraphActorCritic(nn.Module):
    def __init__(
        self,
        node_dim: int,
        global_dim: int,
        hidden: int = 256,
        gnn_layers: int = 3,
        max_systems: int = MAX_SYSTEMS,
    ):
        super().__init__()
        self.hidden = hidden
        self.max_systems = max_systems
        self.n_actions = max_systems + N_VERBS

        self.node_in = _layer_init(nn.Linear(node_dim, hidden))
        self.w_self = nn.ModuleList(_layer_init(nn.Linear(hidden, hidden)) for _ in range(gnn_layers))
        self.w_neigh = nn.ModuleList(_layer_init(nn.Linear(hidden, hidden)) for _ in range(gnn_layers))

        self.global_mlp = _layer_init(nn.Linear(2 * hidden + global_dim, hidden))
        self.value_head = _layer_init(nn.Linear(hidden, 1), std=1.0)
        self.verb_head = _layer_init(nn.Linear(hidden, N_VERBS), std=0.01)
        # Pointer JUMP head: query from graph embedding, keys per node.
        self.query = _layer_init(nn.Linear(hidden, hidden), std=0.01)
        self.key = _layer_init(nn.Linear(hidden, hidden))

    # -- core ---------------------------------------------------------------
    def _forward(
        self,
        node_features: torch.Tensor,   # (B, M, F)
        adjacency: torch.Tensor,       # (B, M, M)
        node_mask: torch.Tensor,       # (B, M)
        global_features: torch.Tensor,  # (B, G)
        action_mask: torch.Tensor,     # (B, M + N_VERBS)
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        mask_col = node_mask.unsqueeze(-1)  # (B, M, 1)

        # Self-loops on real nodes, then row-normalize.
        a_sl = adjacency + torch.diag_embed(node_mask)
        deg = a_sl.sum(-1, keepdim=True).clamp(min=1.0)
        norm_a = a_sl / deg

        h = torch.tanh(self.node_in(node_features)) * mask_col
        for gs, gn in zip(self.w_self, self.w_neigh):
            h = torch.tanh(gs(h) + gn(torch.bmm(norm_a, h))) * mask_col

        summed = (h * mask_col).sum(1)
        count = mask_col.sum(1).clamp(min=1.0)
        mean_pool = summed / count
        max_pool = h.masked_fill(mask_col == 0, _MASK_FILL).max(1).values
        g = torch.tanh(self.global_mlp(torch.cat([mean_pool, max_pool, global_features], dim=-1)))

        value = self.value_head(g).squeeze(-1)
        verb_logits = self.verb_head(g)                                  # (B, N_VERBS)
        q = self.query(g).unsqueeze(-1)                                  # (B, hidden, 1)
        jump_logits = torch.bmm(self.key(h), q).squeeze(-1) / (self.hidden ** 0.5)  # (B, M)

        logits = torch.cat([jump_logits, verb_logits], dim=-1)          # (B, M + N_VERBS)
        logits = torch.where(action_mask.bool(), logits, torch.full_like(logits, _MASK_FILL))
        return logits, value

    def get_action_and_value(self, batch, action: Optional[torch.Tensor] = None):
        """``batch`` is a dict of the 5 padded tensors (see GraphRolloutBatch)."""
        logits, value = self._forward(
            batch["node_features"], batch["adjacency"], batch["node_mask"],
            batch["global_features"], batch["action_mask"],
        )
        dist = Categorical(logits=logits)
        if action is None:
            action = dist.sample()
        return action, dist.log_prob(action), dist.entropy(), value

    # -- numpy inference (rollout / bot) ------------------------------------
    @torch.no_grad()
    def act_numpy(self, gobs: GraphObs, mask: np.ndarray, *, deterministic: bool = False):
        device = next(self.parameters()).device
        t = lambda a, d=torch.float32: torch.as_tensor(a, dtype=d, device=device).unsqueeze(0)
        logits, value = self._forward(
            t(gobs.node_features), t(gobs.adjacency), t(gobs.node_mask),
            t(gobs.global_features), t(mask, torch.int8),
        )
        if deterministic:
            action = logits.argmax(dim=-1)
        else:
            action = Categorical(logits=logits).sample()
        logp = Categorical(logits=logits).log_prob(action)
        return int(action.item()), float(logp.item()), float(value.item())
