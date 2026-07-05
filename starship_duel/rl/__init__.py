"""RL adapters: flat action coding, observation tensor encoding, and a
PettingZoo AEC environment for self-play training.

The pure-Python simulator (``starship_duel.game`` / ``.env``) has no ML
dependencies; everything here depends on numpy / gymnasium / pettingzoo and is
imported lazily so the core stays lightweight.
"""

from .action_coding import ActionCodec
from .encoders import ObservationEncoder

__all__ = ["ActionCodec", "ObservationEncoder"]
