"""Tests for the PPO stack: action masking, GAE, the single-agent view, and a
short end-to-end training smoke test that trains, checkpoints, and reloads the
policy as a Bot.
"""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from starship_duel.rl.model import MaskedActorCritic
from starship_duel.rl.single_agent_view import EncodedGame
from starship_duel.rl.ppo.buffer import compute_gae, RolloutBatch
from starship_duel.rl.ppo.config import PPOConfig


class _RandomPolicy:
    """A uniform-over-legal 'policy' for testing the collector without torch."""

    def __init__(self, n_actions, seed=0):
        self.rng = np.random.default_rng(seed)
        self.n_actions = n_actions

    def act_numpy(self, obs, mask):
        legal = np.flatnonzero(mask)
        a = int(self.rng.choice(legal))
        logp = -np.log(len(legal))  # uniform over legal
        return a, logp, 0.0


class _RandomBotOpponent:
    def __init__(self, seed=1):
        self.rng = np.random.default_rng(seed)

    def reset(self):
        pass

    def act(self, obs):
        return obs.legal_actions[int(self.rng.integers(len(obs.legal_actions)))]


# ---------------------------------------------------------------- masking ----
def test_masked_logits_zero_probability_on_illegal():
    torch.manual_seed(0)
    model = MaskedActorCritic(obs_size=32, n_actions=10, hidden=16)
    obs = torch.randn(4, 32)
    mask = torch.zeros(4, 10, dtype=torch.int8)
    for i in range(4):  # a couple legal actions per row
        mask[i, i % 10] = 1
        mask[i, (i + 3) % 10] = 1
    logits, _ = model._logits_value(obs, mask)
    probs = torch.softmax(logits, dim=-1)
    # Illegal actions must carry ~zero probability.
    illegal = mask == 0
    assert torch.all(probs[illegal] < 1e-6)
    # Sampled actions must always be legal.
    for _ in range(50):
        action, _, _, _ = model.get_action_and_value(obs, mask)
        assert torch.all(mask[torch.arange(4), action] == 1)


def test_act_numpy_returns_legal_action():
    torch.manual_seed(0)
    model = MaskedActorCritic(obs_size=20, n_actions=8, hidden=16)
    mask = np.zeros(8, dtype=np.int8)
    mask[2] = mask[5] = 1
    for _ in range(100):
        a, logp, v = model.act_numpy(np.random.randn(20).astype(np.float32), mask)
        assert mask[a] == 1
        assert np.isfinite(logp) and np.isfinite(v)


# ------------------------------------------------------------------- GAE -----
def test_gae_monte_carlo_returns_equal_terminal_reward():
    # With gamma = lambda = 1 and zero intermediate rewards, every step's return
    # is exactly the terminal reward.
    values = np.array([0.5, 0.2, -0.1], dtype=np.float32)
    adv = compute_gae(values, terminal_reward=1.0, gamma=1.0, lam=1.0)
    returns = adv + values
    assert np.allclose(returns, 1.0)


def test_gae_shapes_and_discount():
    values = np.array([0.0, 0.0], dtype=np.float32)
    adv = compute_gae(values, terminal_reward=1.0, gamma=0.9, lam=0.5)
    assert adv.shape == (2,)
    # Last step advantage = terminal reward - value = 1.0.
    assert adv[-1] == pytest.approx(1.0)


# ---------------------------------------------------- single-agent view ------
def test_collect_produces_legal_consistent_episode():
    game = EncodedGame(map_id="reference", seed=0)
    policy = _RandomPolicy(game.n_actions, seed=0)
    ep = game.collect(policy, _RandomBotOpponent(), learner_seat=0, first_ship=0, seed=42)
    assert ep.length > 0
    assert ep.obs.shape == (ep.length, game.obs_size)
    assert ep.masks.shape == (ep.length, game.n_actions)
    assert ep.actions.shape == (ep.length,)
    # Uniform-over-legal policy can never emit an illegal action.
    assert ep.illegal_samples == 0
    assert ep.reward in (-1.0, 0.0, 1.0)
    # Every recorded action was legal under its own mask.
    assert np.all(ep.masks[np.arange(ep.length), ep.actions] == 1)


def test_rollout_batch_concatenates():
    game = EncodedGame(map_id="reference", seed=0)
    policy = _RandomPolicy(game.n_actions, seed=0)
    eps = [game.collect(policy, _RandomBotOpponent(s), learner_seat=s % 2,
                        first_ship=0, seed=s) for s in range(4)]
    batch = RolloutBatch(eps, gamma=0.99, lam=0.95, device="cpu")
    total = sum(e.length for e in eps)
    assert len(batch) == total
    assert batch.obs.shape == (total, game.obs_size)
    assert batch.advantages.shape == (total,)


# ----------------------------------------------------- end-to-end smoke ------
def test_training_smoke_and_bot_reload(tmp_path):
    from starship_duel.rl.ppo.trainer import PPOTrainer
    from starship_duel.bots.ppo_bot import PpoBot
    from starship_duel.run import play_skirmish
    from starship_duel.bots import RandomBot

    cfg = PPOConfig(
        total_updates=2,
        episodes_per_update=4,
        num_minibatches=2,
        eval_every=100,   # skip eval in the smoke test (keep it fast)
        save_every=100,
        hidden=32,
        log_dir=str(tmp_path / "run"),
        opponent="random",
    )
    trainer = PPOTrainer(cfg)
    trainer.train()  # must not raise

    ckpt = tmp_path / "run" / "ckpt_final.pt"
    assert ckpt.exists()

    bot = PpoBot.from_checkpoint(str(ckpt))
    # Single-map checkpoint: pin its training map (it degrades to random off-map).
    res = play_skirmish(bot, RandomBot(seed=0), seed=0, map_id="map1")
    assert res["winner"] in (0, 1, None)


def test_parallel_rollout_smoke(tmp_path):
    # Exercises the multiprocessing collector end-to-end, including snapshot
    # broadcast to workers under self-play.
    from starship_duel.rl.ppo.trainer import PPOTrainer

    cfg = PPOConfig(
        total_updates=3,
        episodes_per_update=6,
        num_workers=2,
        num_minibatches=2,
        self_play=True,
        snapshot_every=1,     # force a snapshot broadcast every update
        eval_every=100,
        save_every=100,
        hidden=32,
        opponent="random",
        log_dir=str(tmp_path / "run"),
    )
    trainer = PPOTrainer(cfg)
    trainer.train()
    assert trainer._pool.n_snapshots > 0
    assert (tmp_path / "run" / "ckpt_final.pt").exists()
