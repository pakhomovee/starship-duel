"""Map-universal RL: one GNN policy that plays *any* map.

Unlike the legacy single-map stack (``rl/encoders.py``, ``rl/action_coding.py``,
``rl/model.py``), which binds the observation/action spaces to one fixed map's
sorted system list, this package describes each system by **features + graph
structure** (never by identity/index), so a single policy transfers across maps
of different topology, binaries, and size -- including unseen maps.

Pieces:
  - :mod:`.graph_encoder`  -- Observation -> (node features, adjacency, globals).
  - :mod:`.graph_action`   -- fixed-width pointer/verb action codec.
  - :mod:`.model`          -- GNN actor-critic with a pointer JUMP head.
  - :mod:`.game`           -- map-sampling rollout collector.
  - :mod:`.buffer`         -- padded graph rollout batch + GAE.
  - :mod:`.league`/:mod:`.eval`/:mod:`.trainer`/:mod:`.train` -- PPO plumbing.
"""

from .graph_encoder import MAX_SYSTEMS, GraphObs, GraphObsEncoder
from .graph_action import UniversalActionCodec

__all__ = ["MAX_SYSTEMS", "GraphObs", "GraphObsEncoder", "UniversalActionCodec"]
