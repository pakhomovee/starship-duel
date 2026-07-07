"""Wrap a trained PPO checkpoint as a :class:`Bot`.

This is the bridge back to the existing infra: a ``PpoBot`` plugs straight into
the runner, arena, and web UI like any other bot.  It depends on the RL layer
(torch / numpy), so -- unlike the stdlib-only core bots -- it is imported lazily
and is **not** registered eagerly in :mod:`starship_duel.bots`.  Construct it via
``PpoBot.from_checkpoint(path)`` or the CLI spec ``ppo:<path>`` (see
``starship_duel.run.build_bot``).
"""

from __future__ import annotations

from typing import Optional

from ..game import Action, Observation
from .base import Bot


class PpoBot(Bot):
    name = "ppo"

    def __init__(self, model, codec, encoder, *, name: Optional[str] = None,
                 deterministic: bool = True, seed: Optional[int] = None):
        super().__init__(name=name, seed=seed)
        self.model = model
        self.codec = codec
        self.encoder = encoder
        self.deterministic = deterministic

    @classmethod
    def from_checkpoint(cls, path: str, *, deterministic: bool = True,
                        name: Optional[str] = None, seed: Optional[int] = None) -> "PpoBot":
        import torch

        from ..rl.action_coding import ActionCodec
        from ..rl.encoders import ObservationEncoder
        from ..rl.model import MaskedActorCritic

        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        systems = ckpt["systems"]
        model = MaskedActorCritic(
            ckpt["obs_size"], ckpt["n_actions"],
            hidden=ckpt["hidden"], depth=ckpt["depth"],
        )
        model.load_state_dict(ckpt["model_state"])
        model.eval()
        return cls(
            model, ActionCodec(systems), ObservationEncoder(systems),
            name=name or f"ppo:{path}", deterministic=deterministic, seed=seed,
        )

    def act(self, obs: Observation) -> Action:
        enc = self.encoder.encode(obs)
        mask = self.codec.mask(obs)
        a_idx, _, _ = self.model.act_numpy(enc, mask, deterministic=self.deterministic)
        return self.codec.decode(int(a_idx))
