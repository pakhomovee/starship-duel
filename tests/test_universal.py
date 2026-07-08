"""Tests for the map-universal (GNN) RL stack: graph encoding, pointer/verb
masking, cross-map transfer of one network, and an end-to-end training smoke
test that trains across all maps, checkpoints, and reloads as a Bot that plays
every map.
"""

from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from starship_duel.game.maps import MAPS, get_map
from starship_duel.rl.universal.graph_encoder import MAX_SYSTEMS, GraphObsEncoder
from starship_duel.rl.universal.graph_action import UniversalActionCodec
from starship_duel.rl.universal.model import GraphActorCritic
from starship_duel.rl.universal.game import UniversalGame


class _RandomGraphPolicy:
    def __init__(self, seed=0):
        self.rng = np.random.default_rng(seed)

    def act_numpy(self, gobs, mask):
        legal = np.flatnonzero(mask)
        return int(self.rng.choice(legal)), float(-np.log(len(legal))), 0.0


class _RandomBotOpponent:
    def __init__(self, seed=1):
        self.rng = np.random.default_rng(seed)

    def reset(self):
        pass

    def act(self, obs):
        return obs.legal_actions[int(self.rng.integers(len(obs.legal_actions)))]


# --------------------------------------------------------------- encoding ----
def test_graph_encoder_shapes_and_padding():
    for m in MAPS:
        systems = sorted(m.systems)
        enc = GraphObsEncoder(systems)
        game = UniversalGame(train_maps=[m.id])
        # build one observation via a fresh env
        from starship_duel.env import StarshipDuelEnv
        env = StarshipDuelEnv()
        env.reset(map_id=m.id, first_ship=0)
        obs = env.observe(env.agent_selection)
        g = enc.encode(obs)
        assert g.node_features.shape == (MAX_SYSTEMS, enc.node_dim)
        assert g.adjacency.shape == (MAX_SYSTEMS, MAX_SYSTEMS)
        assert g.node_mask.sum() == len(systems)          # only real nodes active
        # padded rows are all zero
        assert np.all(g.node_features[len(systems):] == 0)
        # adjacency symmetric on the real block
        n = len(systems)
        assert np.allclose(g.adjacency[:n, :n], g.adjacency[:n, :n].T)


def test_encoder_surfaces_domination_and_belief():
    """The enriched state space must actually carry the map-control race, the
    fuzzy rival-belief set, and per-node income -- the info the win condition
    depends on."""
    from starship_duel.env import StarshipDuelEnv

    enc = GraphObsEncoder(sorted(get_map("map1").systems))
    env = StarshipDuelEnv()
    env.reset(map_id="map1", first_ship=0)
    # Advance a few plies so ownership/domination diverge from the initial zero.
    for _ in range(6):
        if env.done:
            break
        a = env.observe(env.agent_selection).legal_actions[0]
        env.step(a)
    obs = env.observe(env.agent_selection)

    g = enc.encode(obs)
    # New node columns exist and income weight is one of {0, 0.25, 1.0}.
    assert g.node_features.shape[1] == enc.node_dim >= 22
    inc_col = g.node_features[: enc.n, 21]
    assert set(np.unique(inc_col)).issubset({0.0, 0.25, 1.0})
    # At least one node flagged as a rival candidate (belief set is non-empty
    # right after spawn, when initial positions are revealed).
    assert g.node_features[: enc.n, 20].sum() >= 1.0
    # Globals are finite, bounded, and include the 7 new race/immunity signals.
    assert g.global_features.shape[0] == enc.global_dim
    assert np.all(np.isfinite(g.global_features))
    assert np.all(g.global_features[-7:] >= -1.0) and np.all(g.global_features[-7:] <= 1.0)


def test_action_codec_roundtrip_and_verb_layout():
    codec = UniversalActionCodec(sorted(get_map("map3").systems))
    assert codec.n_actions == MAX_SYSTEMS + 10
    from starship_duel.game import Action
    a = Action.jump(codec.systems[2])
    assert codec.decode(codec.encode(a)).dest == codec.systems[2]
    # verbs live at the fixed base regardless of map size
    assert codec.encode(Action.fire()) >= MAX_SYSTEMS


# ------------------------------------------------------------- masking -------
def test_model_masks_illegal_actions_and_batches_mixed_maps():
    game = UniversalGame(train_maps=["map2", "map4"])  # 12- and 16-system maps
    model = GraphActorCritic(game.node_dim, game.global_dim, hidden=32, gnn_layers=2)
    policy = _RandomGraphPolicy()

    # collect from two different-size maps and batch them together
    from starship_duel.rl.universal.buffer import GraphRolloutBatch
    eps = []
    for i, mid in enumerate(["map2", "map4"]):
        eps.append(game.collect(policy, _RandomBotOpponent(i), map_id=mid,
                                learner_seat=i % 2, first_ship=0, seed=i))
    batch = GraphRolloutBatch(eps, gamma=0.99, lam=0.95, device="cpu")
    total = sum(e.length for e in eps)
    assert len(batch) == total

    # forward pass respects the mask: sampled actions are always legal
    for _ in range(20):
        action, logp, ent, val = model.get_action_and_value(batch.minibatch(np.arange(total)))
        legal = batch.action_mask[np.arange(total), action]
        assert torch.all(legal == 1)
    # no illegal actions were ever emitted by a uniform-legal policy
    assert sum(e.illegal_samples for e in eps) == 0


def test_one_network_runs_on_every_map():
    game = UniversalGame(train_maps=[m.id for m in MAPS])
    model = GraphActorCritic(game.node_dim, game.global_dim, hidden=32, gnn_layers=2)

    class _ModelPolicy:
        def act_numpy(self, gobs, mask):
            return model.act_numpy(gobs, mask)

    for m in MAPS:
        ep = game.collect(_ModelPolicy(), _RandomBotOpponent(), map_id=m.id,
                          learner_seat=0, first_ship=0, seed=7)
        assert ep.length > 0
        assert ep.illegal_samples == 0            # same weights, every map, always legal
        assert ep.reward in (-1.0, 0.0, 1.0)


# ------------------------------------------------------ end-to-end smoke -----
def test_universal_training_smoke_and_bot_reload(tmp_path):
    from starship_duel.rl.ppo.config import PPOConfig
    from starship_duel.rl.universal.trainer import GraphPPOTrainer
    from starship_duel.bots.ppo_bot import UniversalPpoBot
    from starship_duel.run import play_skirmish
    from starship_duel.bots import RandomBot

    cfg = PPOConfig(
        total_updates=2, episodes_per_update=6, num_minibatches=2,
        eval_every=100, save_every=100, hidden=32, gnn_layers=2,
        opponent="random", train_maps=["map1", "map2", "map3", "map4"],
        log_dir=str(tmp_path / "run"),
    )
    GraphPPOTrainer(cfg).train()
    ckpt = tmp_path / "run" / "ckpt_final.pt"
    assert ckpt.exists()

    bot = UniversalPpoBot.from_checkpoint(str(ckpt))
    # the one bot must play every map without error
    for mid in ["map1", "map2", "map3", "map4"]:
        res = play_skirmish(bot, RandomBot(seed=0), seed=1, map_id=mid)
        assert res["winner"] in (0, 1, None)
