"""Capital Quest data: the vendored capitals.json, the review tables and the join."""
from __future__ import annotations

import json
from collections import Counter

from app.games.capital_quest.capital_data import (
    CAPITAL_OVERRIDES, CONFIRMED_SINGLE_CAPITALS, EXCLUDED_CAPITALS, build_capitals, load_capitals, name_key,
)
from app.games.border_bridge.graph import MLEDOZE_FILE
from app.shared.countries import REGIONS, get_index
from app.shared.geo import is_un_state, load_geo_meta

POOL = load_capitals()
BY_CODE = {e.code: e for e in POOL}


def test_every_eligible_country_has_exactly_one_non_empty_capital() -> None:
    codes = [e.code for e in POOL]
    assert len(codes) == len(set(codes))
    for e in POOL:
        assert isinstance(e.capital, str) and e.capital.strip() == e.capital and e.capital
        assert "," not in e.capital or e.code == "us"     # one city, not a list
        assert e.subregion and e.continent in REGIONS


def test_pool_matches_the_shared_ids_shapes_and_un_rule() -> None:
    index = get_index()
    meta = load_geo_meta()
    rows = {r["cca2"].lower(): r for r in json.loads(MLEDOZE_FILE.read_text(encoding="utf-8"))}
    for e in POOL:
        c = index.by_code[e.code]
        assert c.name == e.name and c.region == e.continent
        assert e.shape in meta, f"{e.name} has no shape"
        assert is_un_state(rows[e.code])
    assert "ps" not in BY_CODE  # Palestine: not a UN member state
    assert 180 <= len(POOL) <= 195


def test_overrides_are_applied() -> None:
    for code, (capital, _why) in CAPITAL_OVERRIDES.items():
        assert BY_CODE[code].capital == capital
    assert BY_CODE["za"].capital == "Pretoria"
    assert BY_CODE["sz"].capital == "Mbabane"
    assert BY_CODE["nl"].capital == "Amsterdam"


def test_excluded_countries_are_absent() -> None:
    for code in EXCLUDED_CAPITALS:
        assert code not in BY_CODE
    assert "tv" not in BY_CODE  # Tuvalu has no 50m shape
    assert not set(EXCLUDED_CAPITALS) & set(CAPITAL_OVERRIDES)
    assert not set(EXCLUDED_CAPITALS) & set(CONFIRMED_SINGLE_CAPITALS)


def test_capital_names_are_unique_and_keep_diacritics() -> None:
    keys = Counter(name_key(e.capital) for e in POOL)
    assert [k for k, n in keys.items() if n > 1] == []
    for code, capital in (("co", "Bogotá"), ("py", "Asunción"), ("br", "Brasília"), ("is", "Reykjavík")):
        assert BY_CODE[code].capital == capital


ROWS = [
    {"cca2": "AA", "independent": True, "unMember": True, "capital": ["Alpha"], "subregion": "S1"},
    {"cca2": "BB", "independent": True, "unMember": True, "capital": ["Beta", "Bis"], "subregion": "S1"},
    {"cca2": "CC", "independent": True, "unMember": True, "capital": [], "subregion": "S2"},
    {"cca2": "DD", "independent": False, "unMember": False, "capital": ["Delta"], "subregion": "S2"},
    {"cca2": "EE", "independent": True, "unMember": True, "capital": ["Epsilon"], "subregion": "S2"},
    {"cca2": "FF", "independent": True, "unMember": True, "capital": ["alpha"], "subregion": "S2"},
    {"cca2": "GG", "independent": True, "unMember": True, "capital": ["Gamma"], "subregion": "S3"},
]
COUNTRIES = [{"code": r["cca2"].lower(), "name": r["cca2"], "region": "Europe", "cca3": r["cca2"] + "X",
              "ccn3": str(i)} for i, r in enumerate(ROWS)]


def test_build_rules() -> None:
    report = build_capitals(ROWS, COUNTRIES, shape_ids=["0", "1", "2", "3", "4", "5"],
                            overrides={"bb": ("Beta", "pick one")}, confirmed={}, excluded={"ee": "disputed"})
    assert [e.code for e in report.entries] == ["aa", "bb", "ff"]
    assert report.overrides_applied == [("bb", "Beta, Bis", "Beta", "pick one")]
    assert {c for c, _, _ in report.excluded} == {"cc", "ee", "gg"}  # empty, table, no shape
    assert ("dd", "DD") in report.not_in_pool
    assert [c for c, _, _ in report.missing_or_empty] == ["bb", "cc"]
    assert report.duplicates == [("alpha", ["aa", "ff"])]
