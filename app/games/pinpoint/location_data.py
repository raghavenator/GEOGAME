"""Reference points for Pinpoint: the override table, a point-in-polygon check and the
join that builds ``data/locations.json``. Pure functions, no Flask;
``scripts/build_locations.py`` runs the join and the app only reads its output.

Sources (see the README):
* reference point and size: mledoze/countries ``latlng`` (approximate geographic centre,
  not the capital) and ``area`` in km² (npm world-countries 5.1.0)
* subregion: mledoze/countries ``subregion``
* names, continents, ids: app/shared/data/countries.json (code = ISO alpha-2, ccn3 = shape id)
* shapes for the sanity check: Natural Earth 1:50m via world-atlas

Pool rule: the same as Border Bridge and Capital Quest (independent UN member states,
``is_un_state``). A country doesn't need a 50m shape to be playable (it's only used
to highlight the answer), so Tuvalu is in.

``LOCATION_OVERRIDES`` replaces a source point that is clearly wrong or misleading.
Points that fall outside their own polygon for legitimate reasons (archipelagos,
crescent shapes, coarse 50m outlines of tiny countries) are only reported.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.shared.geo import is_un_state

LOCATIONS_FILE = Path(__file__).parent / "data" / "locations.json"

# code -> (lat, lng, why). Applied on top of the source latlng. Each replacement point is
# inside the country's own 50m polygon; where possible it's the area-weighted centroid of
# the country's mainland from geo_meta.json.
LOCATION_OVERRIDES: dict[str, tuple[float, float, str]] = {
    "kw": (29.334, 47.587, "source (29.5, 45.75) is ~90 km west of Kuwait, in Iraq / Saudi Arabia; "
                           "replaced by the polygon centroid"),
    "sm": (43.942, 12.459, "source (43.767, 12.417) is ~20 km south of San Marino, in Italy; "
                           "replaced by the polygon centroid"),
    "il": (31.0, 34.85, "source (31.47, 35.13) falls in the West Bank; replaced by a central point inside Israel "
                        "(the polygon centroid also falls in the West Bank)"),
    "cu": (21.616, -79.024, "source (21.5, -80.0) is in the sea south of the island; replaced by the polygon centroid"),
}

Ring = Sequence[tuple[float, float]]          # (lon, lat) points; may be unwrapped past ±180
Polygon = Sequence[Ring]                      # exterior ring first, then holes (as app.shared.geo emits them)


@dataclass(frozen=True)
class Location:
    code: str          # ISO alpha-2, the shared countries.json id (and the flag file name)
    cca3: str
    shape: str         # ccn3 = TopoJSON feature id ("" never happens for the pool, but no shape is allowed)
    name: str          # English common name (shared countries.json)
    lat: float
    lng: float
    area_km2: float
    region: str        # Africa | Americas | Asia | Europe | Oceania (shared countries.json)
    subregion: str


@dataclass
class BuildReport:
    entries: list[Location]
    overrides_applied: list[tuple[str, str, tuple[float, float], tuple[float, float], str]] = field(default_factory=list)
    not_in_pool: list[tuple[str, str]] = field(default_factory=list)
    missing: list[tuple[str, str, str]] = field(default_factory=list)        # code, name, what's missing
    no_shape: list[tuple[str, str]] = field(default_factory=list)
    outside: list[tuple[str, str, float, float, float]] = field(default_factory=list)  # code, name, lat, lng, km off


# --------------------------------------------------------------------------- geometry
def _in_ring(lon: float, lat: float, ring: Ring) -> bool:
    """Even-odd ray casting in lon/lat (fine at country scale)."""
    inside = False
    n = len(ring)
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        if (y0 > lat) != (y1 > lat):
            x = x0 + (lat - y0) * (x1 - x0) / (y1 - y0)
            if lon < x:
                inside = not inside
    return inside


def point_in_polygons(lat: float, lng: float, polygons: Iterable[Polygon]) -> bool:
    """Inside any polygon's exterior and outside its holes. Rings crossing the antimeridian
    are unwrapped (longitudes past ±180), so the point is also tried shifted by ±360°."""
    for poly in polygons:
        if not poly:
            continue
        for lon in (lng, lng + 360, lng - 360):
            if _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, hole) for hole in poly[1:]):
                return True
    return False


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    from .logic import haversine_km as _h  # one implementation (logic.py); imported lazily to avoid a cycle
    return _h(lat1, lng1, lat2, lng2)


def km_to_outline(lat: float, lng: float, polygons: Iterable[Polygon]) -> float:
    """Distance to the nearest outline vertex (a good-enough "how far outside" for the report)."""
    best = math.inf
    for poly in polygons:
        for ring in poly:
            for x, y in ring[:: max(1, len(ring) // 400)]:
                best = min(best, haversine_km(lat, lng, y, (x + 540) % 360 - 180))
    return best


# --------------------------------------------------------------------------- the join
def build_locations(
    raw_rows: Iterable[Mapping[str, Any]],
    countries: Iterable[Mapping[str, Any]],
    shapes: Mapping[str, Sequence[Polygon]],
    overrides: Mapping[str, tuple[float, float, str]] = LOCATION_OVERRIDES,
) -> BuildReport:
    """raw_rows: mledoze entries; countries: shared countries.json rows;
    shapes: ccn3 -> polygons (lon/lat rings) from the 50m TopoJSON."""
    by_code = {r["cca2"].lower(): r for r in raw_rows}
    report = BuildReport(entries=[])
    for c in countries:
        code, name = c["code"], c["name"]
        row = by_code.get(code)
        if row is None or not is_un_state(row):
            report.not_in_pool.append((code, name))
            continue
        latlng = row.get("latlng") or []
        area = row.get("area")
        problems = []
        if len(latlng) != 2 or not all(isinstance(v, (int, float)) for v in latlng):
            problems.append("latlng")
        if not isinstance(area, (int, float)) or area <= 0:
            problems.append("area")
        if problems and code not in overrides:
            report.missing.append((code, name, " and ".join(problems)))
            continue
        lat, lng = (float(latlng[0]), float(latlng[1])) if "latlng" not in problems else (math.nan, math.nan)
        if code in overrides:
            o_lat, o_lng, why = overrides[code]
            report.overrides_applied.append((code, name, (lat, lng), (o_lat, o_lng), why))
            lat, lng = o_lat, o_lng
        polygons = shapes.get(c.get("ccn3", ""))
        if not polygons:
            report.no_shape.append((code, name))
        elif not point_in_polygons(lat, lng, polygons):
            report.outside.append((code, name, lat, lng, round(km_to_outline(lat, lng, polygons))))
        report.entries.append(Location(code=code, cca3=c["cca3"], shape=c.get("ccn3", ""), name=name,
                                       lat=round(lat, 4), lng=round(lng, 4), area_km2=float(area),
                                       region=c["region"], subregion=row.get("subregion") or c["region"]))
    report.entries.sort(key=lambda e: e.name)
    report.outside.sort(key=lambda o: -o[4])
    return report


def entries_to_json(entries: Iterable[Location]) -> list[dict[str, Any]]:
    return [asdict(e) for e in entries]


@lru_cache(maxsize=1)
def load_locations(path: Path = LOCATIONS_FILE) -> tuple[Location, ...]:
    """The eligible pool, as written by scripts/build_locations.py."""
    return tuple(Location(**r) for r in json.loads(path.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def locations_by_code() -> dict[str, Location]:
    return {e.code: e for e in load_locations()}
