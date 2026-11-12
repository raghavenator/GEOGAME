"""Border Bridge land-border graph: build rules, exclusions, known adjacencies, data joins."""
from __future__ import annotations

import json

import pytest

from app.games.border_bridge.graph import (
    EXCLUDED_BORDERS,
    GEO_META_FILE,
    build_graph,
    data_report,
    get_graph,
)


def _row(cca3: str, borders: list[str], state: bool = True) -> dict:
    return {"cca2": cca3[:2], "cca3": cca3, "ccn3": cca3, "independent": state, "unMember": state,
            "borders": borders}


def _tiny(rows: list[dict], excluded: dict | None = None):
    meta = {r["cca3"]: {"centroid": [0, 0]} for r in rows}
    names = {r["cca2"].lower(): (r["cca3"], "Europe") for r in rows}
    return build_graph(rows, meta, names, excluded if excluded is not None else {})


# --------------------------------------------------------------------------- build rules
def test_borders_made_symmetric_and_reported() -> None:
    g = _tiny([_row("AAA", ["BBB"]), _row("BBB", [])])
    assert g.neighbours("BBB") == {"AAA"} and g.neighbours("AAA") == {"BBB"}
    assert g.asymmetries == (("AAA", "BBB"),)


def test_territories_are_not_nodes_and_their_borders_drop() -> None:
    g = _tiny([_row("AAA", ["TTT", "BBB"]), _row("BBB", ["AAA"]), _row("TTT", ["AAA"], state=False)])
    assert "TTT" not in g.nodes
    assert g.neighbours("AAA") == {"BBB"}
    assert any(set(d[:2]) == {"AAA", "TTT"} for d in g.dropped)


def test_excluded_borders_and_isolated_countries() -> None:
    rows = [_row("AAA", ["BBB"]), _row("BBB", ["AAA", "CCC"]), _row("CCC", ["BBB"]), _row("III", [])]
    g = _tiny(rows, {frozenset({"BBB", "CCC"}): "test"})
    assert "CCC" not in g.nodes  # its only border was excluded
    assert "III" not in g.nodes  # island
    assert ("BBB", "CCC", "test") in g.dropped


def test_shortest_path_respects_within() -> None:
    rows = [_row("AAA", ["BBB", "DDD"]), _row("BBB", ["AAA", "CCC"]), _row("CCC", ["BBB", "DDD"]),
            _row("DDD", ["AAA", "CCC"])]
    g = _tiny(rows)
    assert len(g.shortest_path(["AAA"], ["CCC"])) == 3
    assert g.shortest_path(["AAA"], ["CCC"], within={"AAA", "DDD", "CCC"}) == ["AAA", "DDD", "CCC"]
    assert g.shortest_path(["AAA"], ["CCC"], within={"AAA", "CCC"}) == []


# --------------------------------------------------------------------------- real data
@pytest.fixture(scope="module")
def graph():
    return get_graph()


def test_real_graph_is_symmetric(graph) -> None:
    for a, ns in graph.adj.items():
        assert ns, f"{a} is a node without borders"
        for b in ns:
            assert a in graph.neighbours(b), f"{a}-{b} is one-way"


@pytest.mark.parametrize("a,b", [("ESP", "FRA"), ("FRA", "DEU"), ("POL", "DEU"), ("ESP", "MAR"),
                                 ("RUS", "POL"), ("RUS", "LTU"), ("ITA", "SMR"), ("CHN", "IND")])
def test_known_adjacencies(graph, a: str, b: str) -> None:
    assert b in graph.neighbours(a)


def test_portugal_has_exactly_one_neighbour(graph) -> None:
    assert graph.neighbours("PRT") == {"ESP"}


@pytest.mark.parametrize("a,b", [("FRA", "BRA"), ("FRA", "SUR"), ("FRA", "NLD"), ("IND", "LKA"), ("DJI", "SOM")])
def test_excluded_borders_absent(graph, a: str, b: str) -> None:
    assert frozenset({a, b}) in EXCLUDED_BORDERS
    assert b not in graph.neighbours(a)


@pytest.mark.parametrize("code", ["GIB", "UNK", "ESH", "PSE", "GUF", "HKG", "ISL", "JPN", "AUS", "LKA"])
def test_not_puzzle_nodes(graph, code: str) -> None:
    assert code not in graph.nodes


def test_data_joins_are_clean() -> None:
    report = data_report()
    assert report["nodes_without_shape"] == []
    assert report["nodes_without_name"] == []
    assert report["asymmetries"] == [("LKA", "IND")]
    reasons = {r for _, _, r in report["shapes_without_node"]}
    assert reasons <= {"not a UN member state (background only)", "no land borders", "only border(s) excluded"}


def test_geo_meta_mainlands() -> None:
    meta = json.loads(GEO_META_FILE.read_text(encoding="utf-8"))
    for fid, m in meta.items():
        assert m["mainland"] and isinstance(m["index"], int), fid
    def inside(fid: str, lon: tuple[float, float], lat: tuple[float, float]) -> bool:
        x, y = meta[fid]["centroid"]
        return lon[0] <= x <= lon[1] and lat[0] <= y <= lat[1]
    assert inside("250", (-5, 9), (42, 51))       # France without French Guiana / Réunion
    assert inside("840", (-125, -67), (25, 49))   # USA: contiguous states, not Alaska/Hawaii
    assert inside("643", (40, 140), (50, 70))     # Russia across the antimeridian
    assert inside("578", (4, 31), (57, 71))       # Norway without Svalbard
    assert inside("724", (-10, 4), (36, 44))      # Spain without the Canaries
    assert meta["036"]["name"] == "Australia"     # duplicate id 036 keeps the larger shape


def test_graph_nodes_have_names_and_regions(graph) -> None:
    for n in graph.nodes.values():
        assert n.name and n.region in {"Africa", "Americas", "Asia", "Europe", "Oceania"}
