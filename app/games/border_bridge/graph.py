"""The land-border graph Border Bridge puzzles are built on. Pure functions, no Flask.

Build rules (see ``build_graph``):

1. Borders come from mledoze/countries (``borders`` lists of cca3 codes). They are
   made symmetric: if only one side lists a border, the reverse edge is added and
   the pair is reported in ``BorderGraph.asymmetries``.
2. Only UN member states (``independent`` and ``unMember`` in the source data) can
   be puzzle nodes. Dependencies and disputed or special territories (Gibraltar,
   Kosovo, Western Sahara, Palestine, Northern Cyprus, Somaliland, French Guiana,
   Hong Kong, ...) are not nodes, and borders that only reach them are dropped.
   They are still drawn on the map as background land.
3. Borders in ``EXCLUDED_BORDERS`` are removed (overseas-only links and data errors).
4. Countries left with no land borders (islands) are not nodes either.

Real oddities such as Spain-Morocco (Ceuta and Melilla) and Russia-Poland/Lithuania
(Kaliningrad) are kept on purpose.
"""
from __future__ import annotations

import json
import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.shared.countries import load_countries
from app.shared.geo import GEO_META_FILE, is_un_state

PACKAGE_DIR = Path(__file__).parent
MLEDOZE_FILE = PACKAGE_DIR / "data" / "mledoze_countries.json"

# Borders that exist only through an overseas territory. These are listed so they
# stay excluded even if a future data refresh attaches the territory to its parent.
OVERSEAS_ONLY_BORDERS: dict[frozenset[str], str] = {
    frozenset({"FRA", "BRA"}): "only via French Guiana",
    frozenset({"FRA", "SUR"}): "only via French Guiana",
    frozenset({"FRA", "NLD"}): "only via Saint Martin / Sint Maarten",
}
# Borders the source data lists that don't work as a land link on our map.
DATA_FIX_BORDERS: dict[frozenset[str], str] = {
    frozenset({"IND", "LKA"}): "no land border (listed only by Sri Lanka in the source data)",
    frozenset({"DJI", "SOM"}): "border runs through Somaliland, which is drawn as a separate shape",
}
EXCLUDED_BORDERS: dict[frozenset[str], str] = {**OVERSEAS_ONLY_BORDERS, **DATA_FIX_BORDERS}

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class Node:
    cca3: str
    ccn3: str          # = TopoJSON feature id
    code: str          # ISO alpha-2 (lower case), the key in shared countries.json
    name: str
    region: str
    centroid: tuple[float, float]  # mainland (lon, lat)


@dataclass
class BorderGraph:
    nodes: dict[str, Node]
    adj: dict[str, frozenset[str]]
    asymmetries: tuple[tuple[str, str], ...] = ()
    dropped: tuple[tuple[str, str, str], ...] = ()
    _dist: dict[str, dict[str, int]] = field(default_factory=dict, repr=False)
    cache: dict[Any, Any] = field(default_factory=dict, repr=False)  # derived data, e.g. pair pools

    def neighbours(self, cca3: str) -> frozenset[str]:
        return self.adj.get(cca3, frozenset())

    def frontier(self, nodes: Iterable[str]) -> set[str]:
        """Countries that border ``nodes`` but aren't in it."""
        inside = set(nodes)
        return {n for c in inside for n in self.neighbours(c)} - inside

    def bfs(self, sources: Iterable[str]) -> dict[str, int]:
        """Hop distance from the nearest source to every reachable node."""
        dist = {s: 0 for s in sources}
        queue = deque(dist)
        while queue:
            cur = queue.popleft()
            for n in self.adj.get(cur, ()):
                if n not in dist:
                    dist[n] = dist[cur] + 1
                    queue.append(n)
        return dist

    def distance(self, a: str, b: str) -> int | None:
        if a not in self._dist:
            self._dist[a] = self.bfs([a])
        return self._dist[a].get(b)

    def shortest_path(self, sources: Iterable[str], targets: Iterable[str], within: set[str] | None = None) -> list[str]:
        """A shortest path (list of nodes) from any source to any target, optionally
        restricted to ``within``. Empty if unreachable. Deterministic (sorted expansion)."""
        sources, targets = set(sources), set(targets)
        prev: dict[str, str | None] = {s: None for s in sorted(sources)}
        queue = deque(sorted(sources))
        while queue:
            cur = queue.popleft()
            if cur in targets:
                path = [cur]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])  # type: ignore[arg-type]
                return path[::-1]
            for n in sorted(self.adj.get(cur, ())):
                if n not in prev and (within is None or n in within):
                    prev[n] = cur
                    queue.append(n)
        return []

    def km(self, a: str, b: str) -> float:
        return great_circle_km(self.nodes[a].centroid, self.nodes[b].centroid)


def great_circle_km(p: tuple[float, float], q: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (*p, *q))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def is_state(row: Mapping[str, Any]) -> bool:
    return is_un_state(row)


def build_graph(
    rows: list[dict[str, Any]],
    geo_meta: Mapping[str, Mapping[str, Any]],
    names: Mapping[str, tuple[str, str]],
    excluded: Mapping[frozenset[str], str] = EXCLUDED_BORDERS,
) -> BorderGraph:
    """Build the puzzle graph.

    rows: mledoze entries (cca2, cca3, ccn3, independent, unMember, borders).
    geo_meta: TopoJSON feature id -> {"centroid": [lon, lat], ...}.
    names: lower-case ISO alpha-2 -> (display name, region), from shared countries.json.
    """
    by_cca3 = {r["cca3"]: r for r in rows}
    edges: set[frozenset[str]] = set()
    asym: list[tuple[str, str]] = []
    for r in rows:
        for other in r.get("borders", []):
            if other not in by_cca3:
                continue
            if r["cca3"] not in by_cca3[other].get("borders", []):
                asym.append((r["cca3"], other))
            edges.add(frozenset({r["cca3"], other}))

    dropped: list[tuple[str, str, str]] = []
    kept: set[frozenset[str]] = set()
    for e in edges:
        a, b = sorted(e)
        if e in excluded:
            dropped.append((a, b, excluded[e]))
        elif not (is_state(by_cca3[a]) and is_state(by_cca3[b])):
            dropped.append((a, b, "touches a territory that isn't a UN member state"))
        else:
            kept.add(e)

    nodes: dict[str, Node] = {}
    for r in rows:
        cca3 = r["cca3"]
        if not is_state(r) or not any(cca3 in e for e in kept):
            continue
        meta = geo_meta.get(r.get("ccn3", ""))
        named = names.get(r["cca2"].lower())
        if meta is None or named is None:
            continue  # surfaced by data_report(); never silently playable
        nodes[cca3] = Node(cca3=cca3, ccn3=r["ccn3"], code=r["cca2"].lower(), name=named[0], region=named[1],
                           centroid=(float(meta["centroid"][0]), float(meta["centroid"][1])))

    adj: dict[str, set[str]] = {c: set() for c in nodes}
    for e in kept:
        a, b = sorted(e)
        if a in nodes and b in nodes:
            adj[a].add(b)
            adj[b].add(a)
    return BorderGraph(
        nodes=nodes,
        adj={c: frozenset(n) for c, n in adj.items()},
        asymmetries=tuple(sorted(asym)),
        dropped=tuple(sorted(dropped)),
    )


def load_sources() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, tuple[str, str]]]:
    rows = json.loads(MLEDOZE_FILE.read_text(encoding="utf-8"))
    geo_meta = json.loads(GEO_META_FILE.read_text(encoding="utf-8"))
    names = {c.code: (c.name, c.region) for c in load_countries()}
    return rows, geo_meta, names


@lru_cache(maxsize=1)
def get_graph() -> BorderGraph:
    return build_graph(*load_sources())


def data_report() -> dict[str, list]:
    """Join diagnostics between shapes, the border dataset and countries.json."""
    rows, geo_meta, names = load_sources()
    graph = build_graph(rows, geo_meta, names)
    by_ccn3 = {r["ccn3"]: r for r in rows if r.get("ccn3")}
    node_ccn3 = {n.ccn3 for n in graph.nodes.values()}

    by_cca3 = {r["cca3"]: r for r in rows}

    def borders_a_state(r: Mapping[str, Any]) -> bool:
        return any(o in by_cca3 and is_state(by_cca3[o]) for o in r.get("borders", []))

    shapes_without_node = []
    for fid, meta in sorted(geo_meta.items()):
        if fid in node_ccn3:
            continue
        row = by_ccn3.get(fid)
        if row is None:
            reason = "no entry in border dataset"
        elif not is_state(row):
            reason = "not a UN member state (background only)"
        elif borders_a_state(row):
            reason = "only border(s) excluded"
        else:
            reason = "no land borders"
        shapes_without_node.append((fid, meta.get("name", ""), reason))

    states_with_borders = [r for r in rows if is_state(r) and borders_a_state(r)]
    nodes_without_shape = [r["cca3"] for r in states_with_borders if r.get("ccn3", "") not in geo_meta]
    nodes_without_name = [r["cca3"] for r in states_with_borders if r["cca2"].lower() not in names]
    return {
        "shapes_without_node": shapes_without_node,
        "nodes_without_shape": nodes_without_shape,
        "nodes_without_name": nodes_without_name,
        "asymmetries": list(graph.asymmetries),
        "excluded_borders": [d for d in graph.dropped if d[2] != "touches a territory that isn't a UN member state"],
        "territory_borders_dropped": [d for d in graph.dropped if d[2] == "touches a territory that isn't a UN member state"],
    }
