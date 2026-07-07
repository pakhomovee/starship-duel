"""Crossing-free 2-D layouts for the star-system graphs.

Our maps are *planar* graphs, so they can be drawn on a flat field with no
edge crossings.  This module computes such a straight-line embedding and fits
it to the nominal 1000x720 rendering box the web UI expects.

Strategy (best-effort, each step falls back to the next):

  1. **Tutte barycentric embedding** — pin the largest face on a regular
     polygon and place every other vertex at the average of its neighbours.
     For planar graphs this yields a crossing-free, convex-ish drawing that is
     genuinely pleasant to look at.
  2. networkx's :func:`planar_layout` (Chrobak-Payne) — always crossing-free
     but visually skewed; used only if Tutte degenerates.
  3. a plain circle — the historical fallback, used if networkx is missing or
     the graph somehow isn't planar.

Results are cached per map id.  ``numpy``/``networkx`` are optional: without
them we simply return the circular layout, so the web UI keeps working.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

from ..game.maps import GameMap

Pos = Dict[str, Tuple[float, float]]

# Drawing box.  Fill as much of the 1000x780 board viewBox as we can, leaving a
# margin so node sprites and the labels drawn just below each node stay inside.
_MARGIN_X = 84.0
_MARGIN_TOP = 76.0
_MARGIN_BOT = 104.0
_W = 1000.0
_H = 780.0

_cache: Dict[str, Pos] = {}


def circular_layout(systems: List[str]) -> Pos:
    """Even circle — the dependency-free fallback."""
    n = max(len(systems), 1)
    cx, cy, r = _W / 2, (_MARGIN_TOP + (_H - _MARGIN_BOT)) / 2, 300.0
    return {
        s: (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
        for i, s in enumerate(sorted(systems))
    }


def _fit_to_box(pos: Pos) -> Pos:
    """Scale/translate a unit-ish layout into the drawing box, preserving
    aspect ratio and centring it."""
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    spanx = maxx - minx or 1.0
    spany = maxy - miny or 1.0
    avail_w = _W - 2 * _MARGIN_X
    avail_h = _H - _MARGIN_TOP - _MARGIN_BOT
    scale = min(avail_w / spanx, avail_h / spany)
    # centre the scaled cloud in the available box
    off_x = _MARGIN_X + (avail_w - spanx * scale) / 2
    off_y = _MARGIN_TOP + (avail_h - spany * scale) / 2
    return {
        name: (off_x + (x - minx) * scale, off_y + (y - miny) * scale)
        for name, (x, y) in pos.items()
    }


def _segments_cross(p1, p2, p3, p4) -> bool:
    """Do open segments p1p2 and p3p4 (no shared endpoints) properly cross?"""
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2 = ccw(p3, p4, p1), ccw(p3, p4, p2)
    d3, d4 = ccw(p1, p2, p3), ccw(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def count_crossings(pos: Pos, edges: List[Tuple[str, str]]) -> int:
    n = len(edges)
    total = 0
    for i in range(n):
        a, b = edges[i]
        for j in range(i + 1, n):
            c, d = edges[j]
            if len({a, b, c, d}) < 4:  # adjacent edges never "cross"
                continue
            if _segments_cross(pos[a], pos[b], pos[c], pos[d]):
                total += 1
    return total


def _tutte(G, outer: List[str]):
    """Barycentric (Tutte) embedding: outer face pinned on a regular polygon,
    every interior vertex solved to the centroid of its neighbours."""
    import numpy as np

    outer_set = set(outer)
    pos: Pos = {}
    k = len(outer)
    for i, v in enumerate(outer):
        ang = 2 * math.pi * i / k
        pos[v] = (math.cos(ang), math.sin(ang))

    inner = [v for v in G.nodes() if v not in outer_set]
    if not inner:
        return pos

    ii = {v: i for i, v in enumerate(inner)}
    m = len(inner)
    A = np.zeros((m, m))
    bx = np.zeros(m)
    by = np.zeros(m)
    for v in inner:
        i = ii[v]
        nbrs = list(G[v])
        A[i, i] = len(nbrs)
        for w in nbrs:
            if w in ii:
                A[i, ii[w]] -= 1
            else:
                bx[i] += pos[w][0]
                by[i] += pos[w][1]
    x = np.linalg.solve(A, bx)
    y = np.linalg.solve(A, by)
    for v in inner:
        pos[v] = (float(x[ii[v]]), float(y[ii[v]]))
    return pos


def _min_pair_ratio(pos: Pos) -> float:
    """Smallest pairwise distance as a fraction of the layout's diagonal.
    Guards against degenerate Tutte solutions with overlapping nodes."""
    pts = list(pos.values())
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    diag = math.hypot((max(xs) - min(xs)) or 1.0, (max(ys) - min(ys)) or 1.0)
    dmin = float("inf")
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            dmin = min(dmin, math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1]))
    return dmin / diag if diag else 0.0


def _spread(pos: Pos, edges: List[Tuple[str, str]], iters: int = 160) -> Pos:
    """Even out a crossing-free layout without ever creating a crossing.

    A Fruchterman-Reingold pass (all-pairs repulsion + edge attraction) opens up
    dense clusters, but every node move is vetoed if it would make any of that
    node's incident edges cross another edge — so the drawing stays planar while
    the systems breathe apart into an evenly spaced field.
    """
    import numpy as np

    nodes = list(pos.keys())
    P = {n: np.array(pos[n], dtype=float) for n in nodes}
    incident = {n: [e for e in edges if n in e] for n in nodes}
    lengths = [np.linalg.norm(P[a] - P[b]) for a, b in edges] or [1.0]
    k = float(np.mean(lengths)) or 1.0  # ideal edge length

    def creates_crossing(v, new_p) -> bool:
        old = P[v]
        P[v] = new_p
        try:
            for a, b in incident[v]:
                pa, pb = P[a], P[b]
                lo_x, hi_x = (pa[0], pb[0]) if pa[0] <= pb[0] else (pb[0], pa[0])
                lo_y, hi_y = (pa[1], pb[1]) if pa[1] <= pb[1] else (pb[1], pa[1])
                for c, d in edges:
                    if len({a, b, c, d}) < 4:
                        continue
                    pc, pd = P[c], P[d]
                    # Bounding-box quick reject before the exact orientation test.
                    if min(pc[0], pd[0]) > hi_x or max(pc[0], pd[0]) < lo_x:
                        continue
                    if min(pc[1], pd[1]) > hi_y or max(pc[1], pd[1]) < lo_y:
                        continue
                    if _segments_cross(pa, pb, pc, pd):
                        return True
            return False
        finally:
            P[v] = old

    rng = random.Random(0)
    temp = k * 0.55
    for _ in range(iters):
        disp = {n: np.zeros(2) for n in nodes}
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                u, w = nodes[i], nodes[j]
                delta = P[u] - P[w]
                dist = float(np.linalg.norm(delta)) or 1e-6
                force = (k * k) / dist
                unit = delta / dist
                disp[u] += unit * force
                disp[w] -= unit * force
        for a, b in edges:
            delta = P[a] - P[b]
            dist = float(np.linalg.norm(delta)) or 1e-6
            force = (dist * dist) / k
            unit = delta / dist
            disp[a] -= unit * force
            disp[b] += unit * force

        order = nodes[:]
        rng.shuffle(order)
        for v in order:
            d = disp[v]
            dn = float(np.linalg.norm(d)) or 1e-9
            target = P[v] + d / dn * min(dn, temp)
            if not creates_crossing(v, target):
                P[v] = target
            else:  # binary-search the furthest crossing-free fraction of the move
                lo, hi = 0.0, 1.0
                for _ in range(6):
                    mid = (lo + hi) / 2
                    if creates_crossing(v, P[v] + (target - P[v]) * mid):
                        hi = mid
                    else:
                        lo = mid
                P[v] = P[v] + (target - P[v]) * lo
        temp *= 0.97

    return {n: (float(P[n][0]), float(P[n][1])) for n in nodes}


def _simple_faces(embedding) -> List[List[str]]:
    marked: set = set()
    faces: List[List[str]] = []
    for a, b in embedding.edges():
        if (a, b) in marked:
            continue
        f = embedding.traverse_face(a, b, mark_half_edges=marked)
        if len(set(f)) == len(f):  # simple cycle -> usable as an outer boundary
            faces.append(f)
    faces.sort(key=len, reverse=True)
    return faces


def _planar_pos(gmap: GameMap) -> Optional[Pos]:
    """Best-spaced crossing-free layout, or None if the optional deps are absent.

    We try a Tutte embedding pinned on each of the graph's largest faces, relax
    each with the planarity-preserving spread, and keep whichever leaves the
    systems most evenly separated.  Falls back to networkx ``planar_layout``.
    """
    try:
        import networkx as nx  # noqa: F401
    except Exception:
        return None

    G = nx.Graph()
    G.add_nodes_from(gmap.systems)
    for a, nbrs in gmap.adjacency.items():
        for b in nbrs:
            G.add_edge(a, b)
    edges = [(a, b) for a, b in G.edges()]

    is_planar, embedding = nx.check_planarity(G)
    if not is_planar:
        return None

    # Rank candidate outer faces by their raw Tutte spacing, then spread the top
    # few and keep the best — spreading is the expensive part, so bound it.
    candidates: List[Tuple[float, Pos]] = []
    for face in _simple_faces(embedding):
        try:
            pos = _tutte(G, face)
        except Exception:
            candidates = []
            break
        if count_crossings(pos, edges) == 0:
            candidates.append((_min_pair_ratio(pos), pos))
    candidates.sort(key=lambda c: c[0], reverse=True)

    best: Optional[Tuple[float, Pos]] = None
    for _, pos in candidates[:2]:
        try:
            spread = _spread(pos, edges)
        except Exception:
            spread = pos
        if count_crossings(spread, edges) == 0:
            score = _min_pair_ratio(spread)
            if best is None or score > best[0]:
                best = (score, spread)
    if best is not None:
        return best[1]

    # Fall back to Chrobak-Payne (always crossing-free, just less pretty).
    try:
        p = nx.planar_layout(G)
        pos = {n: (float(xy[0]), float(xy[1])) for n, xy in p.items()}
        spread = _spread(pos, edges)
        return spread if count_crossings(spread, edges) == 0 else pos
    except Exception:
        return None


def _edge_list(gmap: GameMap) -> List[Tuple[str, str]]:
    seen: set = set()
    edges: List[Tuple[str, str]] = []
    for a, nbrs in gmap.adjacency.items():
        for b in nbrs:
            key = tuple(sorted((a, b)))
            if key not in seen:
                seen.add(key)
                edges.append(key)  # type: ignore[arg-type]
    return edges


def compute_layout(gmap: GameMap) -> Pos:
    """Return a crossing-free layout for ``gmap`` fitted to the drawing box.

    A hand-authored ``gmap.layout`` wins *only if it is itself crossing-free*
    (the whole point is a clean planar drawing); otherwise we compute a planar
    embedding (cached), degrading to a circle if the optional deps are absent.
    """
    if gmap.id in _cache:
        return _cache[gmap.id]

    edges = _edge_list(gmap)
    if gmap.layout is not None and count_crossings(gmap.layout, edges) == 0:
        layout = dict(gmap.layout)
    else:
        pos = _planar_pos(gmap)
        layout = _fit_to_box(pos) if pos else circular_layout(gmap.systems)

    _cache[gmap.id] = layout
    return layout
