"""From-scratch PPO for Starship Duel self-play.

Modules:
  - :mod:`.config`   -- hyperparameters.
  - :mod:`.buffer`   -- rollout storage + GAE.
  - :mod:`.league`   -- opponent pool (frozen snapshots + scripted anchors).
  - :mod:`.eval`     -- win-rate benchmarks vs fixed opponents.
  - :mod:`.trainer`  -- the PPO update loop.
  - :mod:`.train`    -- CLI entrypoint.
"""

from .config import PPOConfig

__all__ = ["PPOConfig"]
