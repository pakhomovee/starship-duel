"""Map-sampling single-agent view for the universal policy.

Same "opponent-as-environment" idea as the legacy
:class:`~starship_duel.rl.single_agent_view.EncodedGame`, but a **map is chosen
per game** so one policy trains across all maps at once.  Per-map graph
adapters (encoder + action codec) are built lazily and cached.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from ...env import StarshipDuelEnv, agent_id, agent_name
from ...game import Action, GameConfig, Observation
from ...game.maps import get_map
from .graph_action import UniversalActionCodec
from .graph_encoder import GraphObs, GraphObsEncoder


@dataclass
class GraphEpisode:
    node_features: np.ndarray   # (T, M, F)
    adjacency: np.ndarray       # (T, M, M)
    node_mask: np.ndarray       # (T, M)
    global_features: np.ndarray  # (T, G)
    action_masks: np.ndarray    # (T, n_actions) int8
    actions: np.ndarray         # (T,) int64
    logprobs: np.ndarray        # (T,) float32
    values: np.ndarray          # (T,) float32
    reward: float
    length: int
    win: bool
    draw: bool
    end_reason: Optional[str]
    map_id: str
    illegal_samples: int


class UniversalGame:
    def __init__(
        self,
        config: Optional[GameConfig] = None,
        train_maps: Optional[List[str]] = None,
        seed: Optional[int] = None,
    ):
        self.config = config or GameConfig()
        self.train_maps = list(train_maps or ["map1", "map2", "map3", "map4"])
        self._env = StarshipDuelEnv(config=self.config, seed=seed)
        self._adapters_cache: Dict[str, Tuple[UniversalActionCodec, GraphObsEncoder]] = {}
        probe = self._adapters(self.train_maps[0])[1]
        self.node_dim = probe.node_dim
        self.global_dim = probe.global_dim
        self.n_actions = self._adapters(self.train_maps[0])[0].n_actions

    def _adapters(self, map_id: str) -> Tuple[UniversalActionCodec, GraphObsEncoder]:
        if map_id not in self._adapters_cache:
            systems = sorted(get_map(map_id).systems)
            self._adapters_cache[map_id] = (
                UniversalActionCodec(systems), GraphObsEncoder(systems)
            )
        return self._adapters_cache[map_id]

    def collect(
        self,
        policy,
        opponent,
        *,
        map_id: str,
        learner_seat: int,
        first_ship: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> GraphEpisode:
        codec, encoder = self._adapters(map_id)
        env = self._env
        env.reset(seed=seed, map_id=map_id, first_ship=first_ship)
        opponent.reset()

        nf, adj, nmask, glob, amask = [], [], [], [], []
        acts, logps, vals = [], [], []
        illegal = 0
        learner_agent = agent_name(learner_seat)

        while not env.done:
            agent = env.agent_selection
            sid = agent_id(agent)
            obs = env.observe(agent)
            if sid == learner_seat:
                gobs = encoder.encode(obs)
                mask = codec.mask(obs)
                a_idx, logp, value = policy.act_numpy(gobs, mask)
                a_idx = int(a_idx)
                game_action = codec.decode(a_idx)
                if not env.engine.is_legal(game_action, sid):
                    illegal += 1
                    game_action = Action.end_turn()
                nf.append(gobs.node_features); adj.append(gobs.adjacency)
                nmask.append(gobs.node_mask); glob.append(gobs.global_features)
                amask.append(mask); acts.append(a_idx)
                logps.append(float(logp)); vals.append(float(value))
                env.step(game_action)
            else:
                env.step(opponent.act(obs))

        reward = float(env.rewards[learner_agent])
        st = env.engine.state
        T = len(acts)
        M, F = encoder.max_systems, encoder.node_dim
        return GraphEpisode(
            node_features=np.asarray(nf, dtype=np.float32).reshape(T, M, F),
            adjacency=np.asarray(adj, dtype=np.float32).reshape(T, M, M),
            node_mask=np.asarray(nmask, dtype=np.float32).reshape(T, M),
            global_features=np.asarray(glob, dtype=np.float32).reshape(T, encoder.global_dim),
            action_masks=np.asarray(amask, dtype=np.int8).reshape(T, codec.n_actions),
            actions=np.asarray(acts, dtype=np.int64),
            logprobs=np.asarray(logps, dtype=np.float32),
            values=np.asarray(vals, dtype=np.float32),
            reward=reward, length=T,
            win=st.winner == learner_seat, draw=st.winner is None,
            end_reason=st.end_reason, map_id=map_id, illegal_samples=illegal,
        )
