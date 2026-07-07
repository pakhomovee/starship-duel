"""Star-system maps: graph, binary systems, reachability helpers, spawns.

Additional maps are registered in :data:`MAPS`; the engine samples one per
skirmish (spec 1).
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from .types import System


@dataclass(frozen=True)
class GameMap:
    id: str
    adjacency: Dict[System, Tuple[System, ...]]
    binary_systems: frozenset
    # Optional 2-D positions (in a nominal 1000x720 space) used only for
    # rendering the graph in the web UI.  ``None`` -> the UI falls back to a
    # circular layout.
    layout: Optional[Dict[System, Tuple[float, float]]] = None

    # -- structure -----------------------------------------------------------
    @property
    def systems(self) -> List[System]:
        return list(self.adjacency.keys())

    def neighbors(self, s: System) -> Tuple[System, ...]:
        return self.adjacency[s]

    def degree(self, s: System) -> int:
        return len(self.adjacency[s])

    # -- reachability --------------------------------------------------------
    def hop_distance(self, a: System, b: System) -> int:
        """BFS shortest-path length in hops (``inf`` -> a very large int)."""
        if a == b:
            return 0
        seen = {a}
        frontier = deque([(a, 0)])
        while frontier:
            node, d = frontier.popleft()
            for nxt in self.adjacency[node]:
                if nxt == b:
                    return d + 1
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append((nxt, d + 1))
        return 10**9

    def reachable_within(self, start: System, hops: int) -> Set[System]:
        """All systems reachable from ``start`` using at most ``hops`` moves
        (includes ``start`` itself, i.e. the "held in place" option)."""
        seen = {start}
        frontier = deque([(start, 0)])
        while frontier:
            node, d = frontier.popleft()
            if d == hops:
                continue
            for nxt in self.adjacency[node]:
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append((nxt, d + 1))
        return seen

    def validate(self) -> None:
        """Sanity-check symmetry / referential integrity of the graph."""
        for a, nbrs in self.adjacency.items():
            for b in nbrs:
                if b not in self.adjacency:
                    raise ValueError(f"{a} links to unknown system {b}")
                if a not in self.adjacency[b]:
                    raise ValueError(f"edge {a}-{b} is not symmetric")
        for b in self.binary_systems:
            if b not in self.adjacency:
                raise ValueError(f"binary system {b} not in graph")


# --------------------------------------------------------------------------- #
# Reference map (spec 1)                                                        #
# --------------------------------------------------------------------------- #
_REFERENCE_ADJ = {
    "Solantis": ["Veyra", "Kestrel Binary", "Drakar Reach"],
    "Drakar Reach": ["Solantis", "Halcyon Binary", "Tessek"],
    "Veyra": ["Solantis", "Kestrel Binary", "Ondrix"],
    "Kestrel Binary": ["Solantis", "Veyra", "Ondrix", "Halcyon Binary",
                       "Corvane", "Aurelia Binary", "Isolde Reach"],
    "Halcyon Binary": ["Drakar Reach", "Kestrel Binary", "Zarath", "Tessek"],
    "Tessek": ["Drakar Reach", "Zarath", "Halcyon Binary"],
    "Ondrix": ["Veyra", "Kestrel Binary", "Lumeth"],
    "Lumeth": ["Ondrix", "Aurelia Binary"],
    "Aurelia Binary": ["Kestrel Binary", "Lumeth", "Pallor Minor", "Isolde Reach"],
    "Corvane": ["Kestrel Binary", "Isolde Reach", "Zarath"],
    "Zarath": ["Halcyon Binary", "Tessek", "Corvane"],
    "Pallor Minor": ["Aurelia Binary", "Isolde Reach"],
    "Isolde Reach": ["Kestrel Binary", "Aurelia Binary", "Pallor Minor", "Corvane"],
}

# Hand-tuned positions with Kestrel Binary (the degree-7 hub) near the centre.
_REFERENCE_LAYOUT = {
    "Drakar Reach": (200, 90),
    "Tessek": (470, 70),
    "Halcyon Binary": (710, 130),
    "Solantis": (360, 200),
    "Veyra": (250, 340),
    "Kestrel Binary": (500, 370),
    "Zarath": (785, 305),
    "Corvane": (760, 480),
    "Ondrix": (195, 510),
    "Isolde Reach": (625, 505),
    "Aurelia Binary": (430, 565),
    "Lumeth": (250, 660),
    "Pallor Minor": (475, 690),
}

REFERENCE_MAP = GameMap(
    id="reference",
    adjacency={k: tuple(v) for k, v in _REFERENCE_ADJ.items()},
    binary_systems=frozenset({"Kestrel Binary", "Halcyon Binary", "Aurelia Binary"}),
    layout=_REFERENCE_LAYOUT,
)
REFERENCE_MAP.validate()


# --------------------------------------------------------------------------- #
# Additional maps (each trains its own specialist policy -- the RL spaces are  #
# bound per-map, so a checkpoint is not transferable across maps).             #
# --------------------------------------------------------------------------- #
_MAP2_ADJ = {  # 12 systems, 2 binaries
    "Halvennor Binary": ["Corvessa", "Ashveil", "Solmere", "Zoryne Binary"],
    "Corvessa":         ["Halvennor Binary", "Ashveil", "Drenmark"],
    "Ashveil":          ["Halvennor Binary", "Corvessa", "Solmere", "Thravencourt"],
    "Solmere":          ["Halvennor Binary", "Ashveil", "Thravencourt"],
    "Thravencourt":     ["Solmere", "Ashveil", "Drenmark", "Veskar"],
    "Drenmark":         ["Corvessa", "Thravencourt", "Ostrun", "Vesparil"],
    "Ostrun":           ["Drenmark", "Kelvane", "Zoryne Binary", "Vesparil", "Pallidor"],
    "Kelvane":          ["Ostrun", "Zoryne Binary"],
    "Zoryne Binary":    ["Halvennor Binary", "Ostrun", "Kelvane"],
    "Vesparil":         ["Drenmark", "Ostrun", "Pallidor", "Veskar"],
    "Pallidor":         ["Ostrun", "Vesparil", "Veskar"],
    "Veskar":           ["Thravencourt", "Vesparil", "Pallidor"],
}
MAP2 = GameMap(
    id="map2",
    adjacency={k: tuple(v) for k, v in _MAP2_ADJ.items()},
    binary_systems=frozenset({"Halvennor Binary", "Zoryne Binary"}),
)
MAP2.validate()

_MAP3_ADJ = {  # 11 systems, 3 binaries
    "Thessyn":         ["Meridian Binary", "Ashkarn Binary"],
    "Calderyx":        ["Meridian Binary", "Voxhollow", "Lyranth"],
    "Meridian Binary": ["Thessyn", "Calderyx", "Voxhollow", "Ashkarn Binary"],
    "Ashkarn Binary":  ["Thessyn", "Meridian Binary", "Ostravel Binary", "Kharnos"],
    "Voxhollow":       ["Calderyx", "Meridian Binary", "Lyranth", "Corvath"],
    "Lyranth":         ["Calderyx", "Voxhollow", "Corvath", "Ember Vale"],
    "Corvath":         ["Voxhollow", "Lyranth", "Ember Vale", "Sildrun"],
    "Ember Vale":      ["Lyranth", "Corvath", "Sildrun"],
    "Sildrun":         ["Corvath", "Ember Vale", "Ostravel Binary"],
    "Ostravel Binary": ["Ashkarn Binary", "Kharnos", "Sildrun"],
    "Kharnos":         ["Ashkarn Binary", "Ostravel Binary"],
}
MAP3 = GameMap(
    id="map3",
    adjacency={k: tuple(v) for k, v in _MAP3_ADJ.items()},
    binary_systems=frozenset({"Meridian Binary", "Ashkarn Binary", "Ostravel Binary"}),
)
MAP3.validate()

_MAP4_ADJ = {  # 16 systems, 2 binaries
    "Boreth":          ["Nytheris Binary", "Drakspire", "Vaelor", "Kelvax"],
    "Kelvax":          ["Boreth", "Vaelor", "Ostrune"],
    "Ashmere":         ["Nytheris Binary", "Calvenna", "Rivenna"],
    "Nytheris Binary": ["Boreth", "Ashmere", "Calvenna", "Drakspire"],
    "Drakspire":       ["Boreth", "Nytheris Binary", "Vaelor", "Sundrel"],
    "Vaelor":          ["Boreth", "Kelvax", "Drakspire", "Ostrune", "Sundrel", "Kharvos"],
    "Ostrune":         ["Kelvax", "Vaelor", "Threndal Binary"],
    "Calvenna":        ["Ashmere", "Nytheris Binary", "Rivenna", "Halcrest"],
    "Rivenna":         ["Ashmere", "Calvenna", "Halcrest"],
    "Halcrest":        ["Calvenna", "Rivenna", "Zephyrn", "Ilmara"],
    "Sundrel":         ["Drakspire", "Vaelor", "Kharvos", "Threndal Binary"],
    "Kharvos":         ["Vaelor", "Sundrel"],
    "Ilmara":          ["Halcrest", "Zephyrn", "Cassivar"],
    "Zephyrn":         ["Halcrest", "Ilmara", "Cassivar"],
    "Cassivar":        ["Zephyrn", "Ilmara", "Threndal Binary"],
    "Threndal Binary": ["Cassivar", "Sundrel", "Ostrune"],
}
MAP4 = GameMap(
    id="map4",
    adjacency={k: tuple(v) for k, v in _MAP4_ADJ.items()},
    binary_systems=frozenset({"Nytheris Binary", "Threndal Binary"}),
)
MAP4.validate()


# Registry the environment samples from per skirmish.  Add new maps here.
MAPS: List[GameMap] = [REFERENCE_MAP, MAP2, MAP3, MAP4]

# Human-friendly aliases -> canonical map id (keeps existing "reference" callers
# working while letting training address the same map as "map1").
_MAP_ALIASES = {"map1": "reference"}


def get_map(map_id: str) -> GameMap:
    map_id = _MAP_ALIASES.get(map_id, map_id)
    for m in MAPS:
        if m.id == map_id:
            return m
    raise KeyError(f"no map with id {map_id!r}")


def sample_map(rng: random.Random) -> GameMap:
    return rng.choice(MAPS)


# --------------------------------------------------------------------------- #
# Spawn placement (spec 1 / 7.5)                                               #
# --------------------------------------------------------------------------- #
def spawn_positions(
    gmap: GameMap,
    rng: random.Random,
    min_hop_distance: int = 2,
    binary_balance_tolerance: int = 1,
    max_attempts: int = 2000,
) -> Tuple[System, System]:
    """Pick a fair-ish starting pair.

    Placeholder constraint set (spec flags this as not fully nailed down):
      * ``min_hop_distance(p0, p1) >= min_hop_distance`` (default 2, i.e. not
        adjacent and not co-located), and
      * roughly balanced summed hop-distance to binary systems, within
        ``binary_balance_tolerance``.

    Falls back to the loosest satisfiable constraint rather than looping
    forever on pathological maps.
    """
    systems = gmap.systems
    binaries = list(gmap.binary_systems)

    def binary_dist(s: System) -> int:
        return sum(gmap.hop_distance(s, b) for b in binaries)

    best_fallback: Optional[Tuple[System, System]] = None
    for _ in range(max_attempts):
        p0, p1 = rng.sample(systems, 2)
        if gmap.hop_distance(p0, p1) < min_hop_distance:
            continue
        if best_fallback is None:
            best_fallback = (p0, p1)
        if abs(binary_dist(p0) - binary_dist(p1)) <= binary_balance_tolerance:
            return (p0, p1)

    if best_fallback is not None:
        return best_fallback
    # Extremely small/degenerate map: just take any two distinct systems.
    return tuple(rng.sample(systems, 2))  # type: ignore[return-value]
