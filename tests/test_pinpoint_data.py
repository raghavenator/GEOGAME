"""Pinpoint data: the vendored locations.json, the override table and the join."""
from __future__ import annotations

import json
import math

from app.games.border_bridge.graph import MLEDOZE_FILE
from app.games.pinpoint.location_data import (
    LOCATION_OVERRIDES, build_locations, load_locations, point_in_polygons,
)
from app.shared.countries import REGIONS, get_index
from app.shared.geo import is_un_state

POOL = load_locations()
BY_CODE = {e.code: e for e in POOL}


def test_every_eligible_country_has_a_valid_point_and_area() -> None:
    assert len(POOL) == len(BY_CODE) == 194
    for e in POOL:
        assert -90 <= e.lat <= 90 and -180 <= e.lng <= 180 and math.isfinite(e.lat + e.lng)
        assert e.area_km2 > 0
        assert e.region in REGIONS and e.subregion and e.name


def test_pool_is_the_shared_un_member_pool() -> None:
    index = get_index()
    rows = {r["cca2"].lower(): r for r in json.loads(MLEDOZE_FILE.read_text(encoding="utf-8"))}
    eligible = {code for code, r in rows.items() if is_un_state(r) and code in index.by_code}
    assert set(BY_CODE) == eligible
    for e in POOL:
        assert index.by_code[e.code].name == e.name
    assert "ps" not in BY_CODE and "tv" in BY_CODE   # Tuvalu: playable without a 50m shape


def test_overrides_are_applied() -> None:
    assert LOCATION_OVERRIDES, "the review table should not be empty"
    for code, (lat, lng, why) in LOCATION_OVERRIDES.items():
        assert (BY_CODE[code].lat, BY_CODE[code].lng) == (round(lat, 4), round(lng, 4)) and why
    assert BY_CODE["kw"].lng > 46.5     # inside Kuwait, not Iraq


def test_point_in_polygon_handles_holes_and_the_antimeridian() -> None:
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    hole = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]
    assert point_in_polygons(2, 2, [[square, hole]])
    assert not point_in_polygons(5, 5, [[square, hole]])
    across = [(170.0, -20.0), (190.0, -20.0), (190.0, -10.0), (170.0, -10.0)]   # unwrapped past 180
    assert point_in_polygons(-15, -175, [[across]]) and point_in_polygons(-15, 175, [[across]])
    assert not point_in_polygons(-15, 160, [[across]])


def test_build_rules() -> None:
    rows = [
        {"cca2": "AA", "independent": True, "unMember": True, "latlng": [5, 5], "area": 100, "subregion": "S"},
        {"cca2": "BB", "independent": True, "unMember": True, "latlng": [], "area": 100, "subregion": "S"},
        {"cca2": "CC", "independent": False, "unMember": False, "latlng": [1, 1], "area": 1, "subregion": "S"},
        {"cca2": "DD", "independent": True, "unMember": True, "latlng": [50, 50], "area": 9, "subregion": "S"},
    ]
    countries = [{"code": r["cca2"].lower(), "name": r["cca2"], "region": "Europe", "cca3": r["cca2"] + "X",
                  "ccn3": str(i)} for i, r in enumerate(rows)]
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    report = build_locations(rows, countries, {"0": [[square]], "3": [[square]]},
                             overrides={"dd": (6.0, 6.0, "fix")})
    assert [e.code for e in report.entries] == ["aa", "dd"]
    assert report.missing == [("bb", "BB", "latlng")]
    assert ("cc", "CC") in report.not_in_pool
    assert report.overrides_applied[0][3] == (6.0, 6.0) and report.outside == []
    assert (report.entries[1].lat, report.entries[1].lng) == (6.0, 6.0)
