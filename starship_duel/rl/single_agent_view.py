"""Single-agent view over the two-player AEC game for PPO self-play.

The learner controls one seat; the *opponent* (a scripted :class:`Bot` or a
frozen policy snapshot) is folded into the environment dynamics.  We step the
raw :class:`~starship_duel.env.StarshipDuelEnv` and record a transition only for
each of the **learner's** decisions -- the opponent's actions merely advance the
world between two consecutive learner decisions.  From the learner's point of
view this is a perfectly ordinary single-agent MDP with a terminal +1/-1/0
reward, which lets the PPO code below stay completely standard.

A *turn* is several actions by the same ship, so the learner typically records a
short run of consecutive transitions (one per action) before the opponent gets
control.  Episodes always terminate (the collapse bounds game length), so we
collect whole episodes and never need mid-episode value bootstrapping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol

import numpy as np

from ..env import StarshipDuelEnv, agent_id, agent_name
from ..game import Action, GameConfig, Observation
from ..game.maps import get_map
from .action_coding import ActionCodec
from .encoders import ObservationEncoder


class Opponent(Protocol):
    """Anything that can play the other seat: a scripted bot or a frozen
    policy wrapped as one.  Mirrors the :class:`~starship_duel.bots.base.Bot`
    surface we actually use here."""

    def reset(self) -> None: ...
    def act(self, obs: Observation) -> Action: ...


@dataclass
class Episode:
    """One full game's worth of the learner's own transitions (numpy arrays)."""

    obs: np.ndarray          # (T, obs_size)  float32
    masks: np.ndarray        # (T, n_actions) int8
    actions: np.ndarray      # (T,)           int64
    logprobs: np.ndarray     # (T,)           float32
    values: np.ndarray       # (T,)           float32
    reward: float            # terminal, from the learner's perspective (+1/-1/0)
    length: int
    win: bool
    draw: bool
    end_reason: Optional[str]
    illegal_samples: int     # sanity counter: policy actions the env rejected


class ActingPolicy(Protocol):
    """The learner side: sample an action given a single encoded obs + mask.

    Returns ``(action_index, log_prob, value)``.  Implemented by
    :class:`~starship_duel.rl.model.MaskedActorCritic` (see ``act_numpy``)."""

    def act_numpy(self, obs: np.ndarray, mask: np.ndarray): ...


class EncodedGame:
    """Fixed map/config binding of codec + encoder + a reusable env instance.

    One instance owns one env, so create one per worker/thread -- it is *not*
    safe to share a single :class:`EncodedGame` across threads.
    """

    def __init__(
        self,
        config: Optional[GameConfig] = None,
        map_id: str = "reference",
        seed: Optional[int] = None,
    ):
        self.config = config or GameConfig()
        self.map_id = map_id
        systems = sorted(get_map(map_id).systems)
        self.codec = ActionCodec(systems)
        self.encoder = ObservationEncoder(systems)
        self._env = StarshipDuelEnv(config=self.config, seed=seed)

    @property
    def obs_size(self) -> int:
        return self.encoder.size

    @property
    def n_actions(self) -> int:
        return self.codec.n_actions

    def collect(
        self,
        policy: ActingPolicy,
        opponent: Opponent,
        *,
        learner_seat: int,
        first_ship: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> Episode:
        """Play one game and return the learner's transitions.

        ``policy`` plays ``learner_seat`` (0 or 1); ``opponent`` plays the other
        seat.  ``first_ship`` picks who moves first (randomize across calls for
        seat fairness); ``seed`` seeds the game RNG.
        """
        env = self._env
        env.reset(seed=seed, map_id=self.map_id, first_ship=first_ship)
        opponent.reset()

        obs_buf: List[np.ndarray] = []
        mask_buf: List[np.ndarray] = []
        act_buf: List[int] = []
        logp_buf: List[float] = []
        val_buf: List[float] = []
        illegal = 0

        learner_agent = agent_name(learner_seat)
        while not env.done:
            agent = env.agent_selection
            sid = agent_id(agent)
            obs = env.observe(agent)

            if sid == learner_seat:
                enc = self.encoder.encode(obs)
                mask = self.codec.mask(obs)
                a_idx, logp, value = policy.act_numpy(enc, mask)
                a_idx = int(a_idx)
                game_action = self.codec.decode(a_idx)
                # The mask is exactly obs.legal_actions, so this should never
                # fire; if it does, training is silently corrupted -- count it
                # loudly and fall back to a legal END_TURN.
                if not env.engine.is_legal(game_action, sid):
                    illegal += 1
                    game_action = Action.end_turn()
                obs_buf.append(enc)
                mask_buf.append(mask)
                act_buf.append(a_idx)
                logp_buf.append(float(logp))
                val_buf.append(float(value))
                env.step(game_action)
            else:
                env.step(opponent.act(obs))

        reward = float(env.rewards[learner_agent])
        st = env.engine.state
        length = len(act_buf)
        return Episode(
            obs=np.asarray(obs_buf, dtype=np.float32).reshape(length, self.obs_size),
            masks=np.asarray(mask_buf, dtype=np.int8).reshape(length, self.n_actions),
            actions=np.asarray(act_buf, dtype=np.int64),
            logprobs=np.asarray(logp_buf, dtype=np.float32),
            values=np.asarray(val_buf, dtype=np.float32),
            reward=reward,
            length=length,
            win=st.winner == learner_seat,
            draw=st.winner is None,
            end_reason=st.end_reason,
            illegal_samples=illegal,
        )
