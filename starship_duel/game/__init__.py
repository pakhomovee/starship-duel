"""Core simulator for Starship Duel (rules, state, belief, observations)."""

from .belief import BeliefTracker
from .config import GameConfig
from .engine import Engine, IllegalActionError
from .maps import MAPS, REFERENCE_MAP, GameMap, get_map, sample_map, spawn_positions
from .observation import Observation, build_observation
from .types import (
    Action,
    ActionType,
    Cache,
    CacheKind,
    GameState,
    ShipId,
    ShipState,
    System,
    SystemStatus,
    other,
)

__all__ = [
    "BeliefTracker",
    "GameConfig",
    "Engine",
    "IllegalActionError",
    "MAPS",
    "REFERENCE_MAP",
    "GameMap",
    "get_map",
    "sample_map",
    "spawn_positions",
    "Observation",
    "build_observation",
    "Action",
    "ActionType",
    "Cache",
    "CacheKind",
    "GameState",
    "ShipId",
    "ShipState",
    "System",
    "SystemStatus",
    "other",
]
