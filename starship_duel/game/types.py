"""Core value types for the Starship Duel simulator.

These are plain, hashable-where-useful dataclasses / enums with no game logic.
The rules live in :mod:`starship_duel.game.engine`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

# A ship is identified by 0 or 1.  ``other(s)`` flips it.
ShipId = int


def other(ship: ShipId) -> ShipId:
    return 1 - ship


System = str


class SystemStatus(Enum):
    STABLE = "STABLE"
    DESTABILIZING = "DESTABILIZING"
    SUPERNOVA = "SUPERNOVA"


class CacheKind(Enum):
    ENERGY = "ENERGY"
    OVERCHARGE = "OVERCHARGE"


class ActionType(Enum):
    JUMP = "JUMP"
    HOLD = "HOLD"
    CLAIM = "CLAIM"
    FIRE = "FIRE"
    SCAN = "SCAN"
    DEEP_CLOAK = "DEEP_CLOAK"
    OVERCHARGE = "OVERCHARGE"
    UNLOCK_PROXIMITY_ALERT = "UNLOCK_PROXIMITY_ALERT"
    UNLOCK_LONG_RANGE_SCANNERS = "UNLOCK_LONG_RANGE_SCANNERS"
    UNLOCK_JAMMING = "UNLOCK_JAMMING"
    # Pseudo-action: voluntarily end the turn early so unspent actions can bank
    # (see spec 5a).  Does not consume an action.
    END_TURN = "END_TURN"


# Energy-spending actions: what the rival sees of these collapses to a generic
# "JAMMED" when the actor has Jamming (spec 3).  The rest of the visibility rules
# live in ``Engine._public_category``.
ENERGY_ACTIONS = {
    ActionType.SCAN,
    ActionType.DEEP_CLOAK,
    ActionType.OVERCHARGE,
    ActionType.UNLOCK_PROXIMITY_ALERT,
    ActionType.UNLOCK_LONG_RANGE_SCANNERS,
    ActionType.UNLOCK_JAMMING,
}
UNLOCK_ACTIONS = {
    ActionType.UNLOCK_PROXIMITY_ALERT: "proximity_alert",
    ActionType.UNLOCK_LONG_RANGE_SCANNERS: "long_range_scanners",
    ActionType.UNLOCK_JAMMING: "jamming",
}


@dataclass(frozen=True)
class Action:
    """A single action submitted by a ship.

    ``dest`` is only meaningful for :attr:`ActionType.JUMP`.
    """

    type: ActionType
    dest: Optional[System] = None

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        if self.type is ActionType.JUMP:
            return f"JUMP->{self.dest}"
        return self.type.value

    # Convenience constructors ------------------------------------------------
    @staticmethod
    def jump(dest: System) -> "Action":
        return Action(ActionType.JUMP, dest)

    @staticmethod
    def hold() -> "Action":
        return Action(ActionType.HOLD)

    @staticmethod
    def claim() -> "Action":
        return Action(ActionType.CLAIM)

    @staticmethod
    def fire() -> "Action":
        return Action(ActionType.FIRE)

    @staticmethod
    def scan() -> "Action":
        return Action(ActionType.SCAN)

    @staticmethod
    def deep_cloak() -> "Action":
        return Action(ActionType.DEEP_CLOAK)

    @staticmethod
    def overcharge() -> "Action":
        return Action(ActionType.OVERCHARGE)

    @staticmethod
    def end_turn() -> "Action":
        return Action(ActionType.END_TURN)


@dataclass
class Cache:
    kind: CacheKind
    value: int  # Energy amount for ENERGY caches; unused (0) for OVERCHARGE
    # Turn number at which this cache next becomes eligible to upgrade.
    next_upgrade_turn: int = 0


@dataclass
class ShipState:
    position: System
    cloaked: bool = True
    # Number of the ship's *own* upcoming turns that Deep Cloak still covers.
    # While > 0 the ship is immune to every exposure trigger AND to the
    # end-of-turn forced fire — i.e. it can sit in enemy territory, or end its
    # turn on top of the rival, completely undetected.  Decrements at the start
    # of the ship's own turn.
    deep_cloak_turns_left: int = 0
    energy: int = 0
    banked_overcharge: int = 0  # extra actions banked for next turn; uncapped
    actions_remaining: int = 0  # this turn: 2 + banked_overcharge, consumed as spent
    unlocked: Dict[str, bool] = field(
        default_factory=lambda: {
            "proximity_alert": False,
            "long_range_scanners": False,
            "jamming": False,
        }
    )
    # Every action of this ship's most recently completed turn, in order, as the
    # *rival* would see it: the real category when the rival could identify it,
    # else "JAMMED" (masked by the actor's Jamming) or "UNKNOWN" (the actor was
    # hidden, so the action left no observable trace).  Empty before the ship has
    # finished a turn.  ``current_turn_actions`` accumulates the turn in progress
    # and is rotated into ``last_turn_actions`` at end-of-turn.
    last_turn_actions: List[str] = field(default_factory=list)
    current_turn_actions: List[str] = field(default_factory=list)

    @property
    def deep_cloak_active(self) -> bool:
        return self.deep_cloak_turns_left > 0

    @property
    def last_public_action(self) -> Optional[str]:
        """The most recent single public category (legacy view of the log)."""
        log = self.current_turn_actions or self.last_turn_actions
        return log[-1] if log else None

    def clone(self) -> "ShipState":
        return ShipState(
            position=self.position,
            cloaked=self.cloaked,
            deep_cloak_turns_left=self.deep_cloak_turns_left,
            energy=self.energy,
            banked_overcharge=self.banked_overcharge,
            actions_remaining=self.actions_remaining,
            unlocked=dict(self.unlocked),
            last_turn_actions=list(self.last_turn_actions),
            current_turn_actions=list(self.current_turn_actions),
        )


@dataclass
class GameState:
    skirmish_number: int
    campaign_score: List[int]  # [wins_ship0, wins_ship1]
    map_id: str
    binary_systems: frozenset  # of System
    system_owner: Dict[System, Optional[ShipId]]
    system_status: Dict[System, SystemStatus]
    system_cache: Dict[System, Optional[Cache]]
    turn_ship: ShipId
    turn_number: int
    turn_clock: float  # counts down from 60s (informational for training)
    ships: List[ShipState]

    # Map-control score per ship, banked at the start of each of their turns.
    domination: List[int] = field(default_factory=lambda: [0, 0])

    # Lives remaining per ship (the hunt / kill dimension).  A ship reduced to 0
    # lives is eliminated.  Initialised to ``GameConfig.lives`` in ``Engine.reset``.
    lives: List[int] = field(default_factory=lambda: [3, 3])

    # Terminal bookkeeping (None while the skirmish is live).
    winner: Optional[ShipId] = None
    done: bool = False
    end_reason: Optional[str] = None

    def ship(self, s: ShipId) -> ShipState:
        return self.ships[s]
