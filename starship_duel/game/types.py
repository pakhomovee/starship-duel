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


# Which action *categories* a rival can always distinguish, versus the ones
# that collapse to a generic "spent Energy" when the actor has Jamming (spec 3).
MOVEMENT_CATEGORIES = {
    ActionType.JUMP,
    ActionType.HOLD,
    ActionType.CLAIM,
    ActionType.FIRE,
}
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
    deep_cloak_active: bool = False  # immune to exposure until start of own next turn
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
    # The category of this ship's most recent action, as the *rival* would see
    # it (already jam-obfuscated).  ``None`` before the ship has acted.
    last_public_action: Optional[str] = None

    def clone(self) -> "ShipState":
        return ShipState(
            position=self.position,
            cloaked=self.cloaked,
            deep_cloak_active=self.deep_cloak_active,
            energy=self.energy,
            banked_overcharge=self.banked_overcharge,
            actions_remaining=self.actions_remaining,
            unlocked=dict(self.unlocked),
            last_public_action=self.last_public_action,
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

    # Terminal bookkeeping (None while the skirmish is live).
    winner: Optional[ShipId] = None
    done: bool = False
    end_reason: Optional[str] = None

    def ship(self, s: ShipId) -> ShipState:
        return self.ships[s]
