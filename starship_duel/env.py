"""PettingZoo-style two-agent environment wrapper (spec 3 / 6).

Zero external dependencies: this mirrors PettingZoo's AEC API surface
(``agents``, ``agent_selection``, ``reset``, ``step``, ``observe``, ``rewards``,
``terminations``, ``truncations``, ``infos``, ``last``) without importing the
library, so the simulator runs and tests anywhere.  A thin PettingZoo adapter
can be layered on top if/when that dependency is desired.

A single *turn* is many actions by the same agent, so ``agent_selection`` stays
put until the engine flips ``turn_ship`` at end of turn -- which is exactly what
the AEC model expects for variable-length agent activations.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .game import (
    Action,
    Engine,
    GameConfig,
    Observation,
    ShipId,
    build_observation,
)

AGENTS = ["ship_0", "ship_1"]


def agent_name(ship: ShipId) -> str:
    return f"ship_{ship}"


def agent_id(name: str) -> ShipId:
    return int(name.split("_")[1])


class StarshipDuelEnv:
    """AEC-style env: one action per :meth:`step`, terminal reward is +1/-1/0."""

    metadata = {"name": "starship_duel_v0", "is_parallelizable": False}

    def __init__(self, config: Optional[GameConfig] = None, seed: Optional[int] = None):
        self.engine = Engine(config=config, seed=seed)
        self.possible_agents: List[str] = list(AGENTS)
        self.agents: List[str] = []
        self.rewards: Dict[str, float] = {}
        self._cumulative_rewards: Dict[str, float] = {}
        self.terminations: Dict[str, bool] = {}
        self.truncations: Dict[str, bool] = {}
        self.infos: Dict[str, dict] = {}
        self.agent_selection: str = AGENTS[0]
        self._last_events: List[str] = []

    # ------------------------------------------------------------------ reset
    def reset(
        self,
        *,
        seed: Optional[int] = None,
        skirmish_number: int = 1,
        campaign_score: Optional[List[int]] = None,
        map_id: Optional[str] = None,
        first_ship: Optional[ShipId] = None,
    ) -> None:
        if seed is not None:
            self.engine.rng.seed(seed)
        self.engine.reset(
            skirmish_number=skirmish_number,
            campaign_score=campaign_score,
            map_id=map_id,
            first_ship=first_ship,
        )
        self.agents = list(AGENTS)
        self.rewards = {a: 0.0 for a in self.agents}
        self._cumulative_rewards = {a: 0.0 for a in self.agents}
        self.terminations = {a: False for a in self.agents}
        self.truncations = {a: False for a in self.agents}
        self.infos = {a: {} for a in self.agents}
        self.agent_selection = agent_name(self.engine.current_ship)
        self._last_events = []

    # -------------------------------------------------------------- observe
    def observe(self, agent: str) -> Observation:
        return build_observation(self.engine, agent_id(agent))

    def last(self, observe: bool = True):
        """PettingZoo-style: (obs, reward, termination, truncation, info)."""
        agent = self.agent_selection
        obs = self.observe(agent) if observe else None
        return (
            obs,
            self._cumulative_rewards.get(agent, 0.0),
            self.terminations.get(agent, False),
            self.truncations.get(agent, False),
            self.infos.get(agent, {}),
        )

    def legal_actions(self, agent: Optional[str] = None) -> List[Action]:
        a = agent or self.agent_selection
        return self.engine.legal_actions(agent_id(a))

    # ----------------------------------------------------------------- step
    def step(self, action: Action) -> None:
        if self.engine.is_terminal():
            return

        events = self.engine.apply_action(action)
        self._last_events = events
        self.agent_selection = agent_name(self.engine.current_ship)

        if self.engine.is_terminal():
            self._finish()
        else:
            self.infos[self.agent_selection] = {"events": events}

    def _finish(self) -> None:
        st = self.engine.state
        for a in self.agents:
            self.terminations[a] = True
        # Zero-sum sparse terminal reward (spec 6): +1 win / -1 loss / 0 draw.
        if st.winner is None:  # draw / timeout
            result = {a: 0.0 for a in self.agents}
        else:
            winner = agent_name(st.winner)
            result = {a: (1.0 if a == winner else -1.0) for a in self.agents}
        self.rewards = result
        for a in self.agents:
            self._cumulative_rewards[a] = result[a]
            self.infos[a] = {"events": self._last_events, "end_reason": st.end_reason}

    # ---------------------------------------------------------------- status
    @property
    def winner(self) -> Optional[ShipId]:
        return self.engine.state.winner

    @property
    def done(self) -> bool:
        return self.engine.is_terminal()

    @property
    def last_events(self) -> List[str]:
        return self._last_events
