"""PettingZoo AEC wrapper for self-play training (spec 8, step 3).

Bound to a single fixed map so the observation/action spaces are stable. Emits
masked, encoded observations (``{"observation", "action_mask"}``) and a sparse
zero-sum terminal reward (+1 / -1 / 0).

Usage::

    from starship_duel.rl.pettingzoo_env import raw_env
    env = raw_env()
    env.reset(seed=0)
    for agent in env.agent_iter():
        obs, reward, term, trunc, info = env.last()
        if term or trunc:
            action = None
        else:
            mask = obs["action_mask"]
            action = int(np.random.choice(np.flatnonzero(mask)))
        env.step(action)
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from gymnasium import spaces
from pettingzoo import AECEnv
from pettingzoo.utils import wrappers

from ..env import StarshipDuelEnv, agent_id, agent_name
from ..game import GameConfig, build_observation
from ..game.maps import get_map
from .action_coding import ActionCodec
from .encoders import ObservationEncoder


def raw_env(**kwargs) -> "StarshipDuelAEC":
    return StarshipDuelAEC(**kwargs)


def env(**kwargs):
    """Wrapped env with PettingZoo's standard safety wrappers."""
    e = raw_env(**kwargs)
    e = wrappers.AssertOutOfBoundsWrapper(e)
    e = wrappers.OrderEnforcingWrapper(e)
    return e


class StarshipDuelAEC(AECEnv):
    metadata = {"render_modes": ["human"], "name": "starship_duel_v0", "is_parallelizable": False}

    def __init__(
        self,
        config: Optional[GameConfig] = None,
        map_id: str = "reference",
        seed: Optional[int] = None,
        render_mode: Optional[str] = None,
    ):
        super().__init__()
        self.map_id = map_id
        self.render_mode = render_mode
        self._config = config or GameConfig()
        self._env = StarshipDuelEnv(config=self._config, seed=seed)

        gmap = get_map(map_id)
        systems = sorted(gmap.systems)
        self.codec = ActionCodec(systems)
        self.encoder = ObservationEncoder(systems)

        self.possible_agents = ["ship_0", "ship_1"]
        obs_space = spaces.Dict({
            "observation": spaces.Box(
                low=-1.0, high=1.0, shape=(self.encoder.size,), dtype=np.float32
            ),
            "action_mask": spaces.MultiBinary(self.codec.n_actions),
        })
        self._observation_spaces = {a: obs_space for a in self.possible_agents}
        self._action_spaces = {a: spaces.Discrete(self.codec.n_actions) for a in self.possible_agents}

    # -- spaces --------------------------------------------------------------
    def observation_space(self, agent: str):
        return self._observation_spaces[agent]

    def action_space(self, agent: str):
        return self._action_spaces[agent]

    # -- lifecycle -----------------------------------------------------------
    def reset(self, seed: Optional[int] = None, options: Optional[dict] = None):
        first_ship = None if not options else options.get("first_ship")
        self._env.reset(seed=seed, map_id=self.map_id, first_ship=first_ship)

        self.agents = list(self.possible_agents)
        self.rewards = {a: 0.0 for a in self.agents}
        self._cumulative_rewards = {a: 0.0 for a in self.agents}
        self.terminations = {a: False for a in self.agents}
        self.truncations = {a: False for a in self.agents}
        self.infos = {a: {} for a in self.agents}
        self.agent_selection = agent_name(self._env.engine.current_ship)

    def observe(self, agent: str) -> dict:
        obs = build_observation(self._env.engine, agent_id(agent))
        return {
            "observation": self.encoder.encode(obs),
            "action_mask": self.codec.mask(obs)
            if obs.legal_actions
            else np.zeros(self.codec.n_actions, dtype=np.int8),
        }

    def step(self, action: Optional[int]) -> None:
        agent = self.agent_selection
        if self.terminations[agent] or self.truncations[agent]:
            self._was_dead_step(action)
            return

        # Decode; fall back to END_TURN if a masked/illegal index is submitted.
        game_action = self.codec.decode(int(action))
        if not self._env.engine.is_legal(game_action, agent_id(agent)):
            from ..game import Action
            game_action = Action.end_turn()

        self._env.step(game_action)

        # Reward accrues only at episode end (sparse, zero-sum).
        self._cumulative_rewards[agent] = 0.0
        self.agent_selection = agent_name(self._env.engine.current_ship)

        if self._env.done:
            st = self._env.engine.state
            for a in self.agents:
                if st.winner is None:  # timeout / draw
                    self.truncations[a] = True
                else:
                    self.terminations[a] = True
                self.rewards[a] = self._env.rewards[a]
                self.infos[a] = {"end_reason": st.end_reason}
            self._accumulate_rewards()

    def render(self):
        if self.render_mode == "human":
            st = self._env.engine.state
            print(f"turn {st.turn_number} | ship {st.turn_ship} to move | "
                  f"score {st.campaign_score} | done={st.done}")

    def close(self):
        pass
