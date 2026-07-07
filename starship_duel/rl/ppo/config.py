"""PPO hyperparameters.

Defaults are tuned for this long-horizon, sparse, zero-sum turn game: a high
discount (episodes run ~dozens of learner decisions), GAE(0.95), and a modest
entropy bonus to keep the masked policy from collapsing onto a single exploit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PPOConfig:
    # -- environment --------------------------------------------------------
    map_id: str = "reference"
    seed: int = 0

    # -- network ------------------------------------------------------------
    hidden: int = 256
    depth: int = 2
    # Warm-start the policy from an existing checkpoint (e.g. Phase 2 continuing
    # from Phase 1).  Must share architecture/map.  None = fresh init.
    init_from: Optional[str] = None

    # -- rollout ------------------------------------------------------------
    episodes_per_update: int = 64      # full self-play games collected per update
    total_updates: int = 2000
    # Rollout worker processes.  0 or 1 = in-process (best for tests/debug);
    # set to ~#cpu-cores on a training box.  The sim is GIL-bound, so this is
    # where nearly all the wall-clock speedup comes from.
    num_workers: int = 0
    # Multiprocessing start method.  None = auto: "fork" on Linux (workers share
    # the parent's loaded torch pages copy-on-write -> far less RAM and instant
    # startup), "spawn" elsewhere (fork is unsafe on macOS).  Override if needed.
    mp_start_method: Optional[str] = None

    # -- PPO core -----------------------------------------------------------
    gamma: float = 0.997               # long horizon (many learner decisions/game)
    gae_lambda: float = 0.95
    clip_coef: float = 0.2
    clip_vloss: bool = True            # value-function clipping
    ent_coef: float = 0.01
    vf_coef: float = 0.5
    max_grad_norm: float = 0.5
    update_epochs: int = 4
    num_minibatches: int = 8
    norm_adv: bool = True
    target_kl: Optional[float] = 0.03  # early-stop epochs if exceeded (None = off)

    # -- optimizer ----------------------------------------------------------
    lr: float = 3e-4
    anneal_lr: bool = True

    # -- self-play / league -------------------------------------------------
    # Phase 1: train against a fixed scripted opponent to validate PPO.  Phase 2
    # (league) turns on once ``self_play`` is set and snapshots accumulate.
    opponent: str = "heuristic"        # scripted anchor used before/for the league
    self_play: bool = False            # add frozen snapshots of ourselves as opponents
    snapshot_every: int = 25           # updates between adding a policy snapshot
    max_snapshots: int = 10
    # Opponent sampling weights when self_play is on: latest snapshot / older
    # snapshots (uniform) / scripted anchor.  Renormalized internally.
    p_latest: float = 0.4
    p_past: float = 0.3
    p_scripted: float = 0.3
    # Scripted anchors mixed into the league (kept to prevent forgetting).
    scripted_anchors: List[str] = field(default_factory=lambda: ["heuristic", "random"])

    # -- eval / logging -----------------------------------------------------
    eval_every: int = 25
    eval_games: int = 100
    eval_opponents: List[str] = field(default_factory=lambda: ["random", "heuristic"])
    log_dir: str = "runs/ppo"
    save_every: int = 100
    device: str = "cpu"                # sim is CPU-bound; "cuda" rarely helps here
