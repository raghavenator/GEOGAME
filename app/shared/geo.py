"""Shared world-map data: where the vendored Natural Earth shapes live, and the
country pool rule every map game uses.

The files are vendored once by ``scripts/fetch_border_data.py`` and stay in Border
Bridge's static folder (their public URLs are part of that game's contract). Other
games reach them through this module and :func:`world_data_urls` rather than by path.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

GEO_DIR = Path(__file__).resolve().parents[1] / "games" / "border_bridge" / "static" / "data"
TOPO_FILE = GEO_DIR / "countries-50m.json"       # Natural Earth 1:50m admin-0 (world-atlas 2.0.2)
GEO_META_FILE = GEO_DIR / "geo_meta.json"         # TopoJSON feature id -> mainland polygons, centroid, area


def is_un_state(row: Mapping[str, Any]) -> bool:
    """The pool rule for map games: independent UN member states (mledoze flags)."""
    return bool(row.get("independent")) and bool(row.get("unMember"))


@lru_cache(maxsize=1)
def load_geo_meta() -> dict[str, dict[str, Any]]:
    return json.loads(GEO_META_FILE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- TopoJSON
Ring = list[tuple[float, float]]          # (lon, lat); longitudes unwrapped past ±180 where a ring crosses it
Polygon = list[Ring]                      # exterior ring first, then holes


def decode_arcs(topo: dict) -> list[list[tuple[float, float]]]:
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    return arcs


def ring_coords(ring: list[int], arcs: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for i in ring:
        pts = arcs[i] if i >= 0 else arcs[~i][::-1]
        out.extend(pts if not out else pts[1:])
    # Rings crossing the antimeridian (Russia, Fiji) jump between ±180: unwrap so
    # longitudes are continuous, otherwise area and centroid are nonsense.
    unwrapped: list[tuple[float, float]] = []
    shift = 0.0
    for k, (x, y) in enumerate(out):
        if k:
            dx = x + shift - unwrapped[-1][0]
            if dx > 180:
                shift -= 360
            elif dx < -180:
                shift += 360
        unwrapped.append((x + shift, y))
    return unwrapped


@lru_cache(maxsize=1)
def load_country_polygons() -> dict[str, list[Polygon]]:
    """TopoJSON feature id (ccn3) -> polygons with their holes, as lon/lat rings.

    Uses the feature geo_meta.json picked for each id, so a duplicate id (Ashmore and
    Cartier Is. reuse Australia's 036) resolves exactly as it does on the map."""
    topo = json.loads(TOPO_FILE.read_text(encoding="utf-8"))
    arcs = decode_arcs(topo)
    geoms = topo["objects"]["countries"]["geometries"]
    out: dict[str, list[Polygon]] = {}
    for fid, meta in load_geo_meta().items():
        geom = geoms[meta["index"]]
        polys = [geom["arcs"]] if geom["type"] == "Polygon" else geom["arcs"] if geom["type"] == "MultiPolygon" else []
        out[fid] = [[ring_coords(r, arcs) for r in poly] for poly in polys]
    return out


def world_data_urls() -> dict[str, str]:
    """Browser URLs of the shapes and their metadata (for GeoMaps.loadWorld)."""
    from flask import url_for

    return {"topoUrl": url_for("border_bridge.static", filename="data/countries-50m.json"),
            "metaUrl": url_for("border_bridge.static", filename="data/geo_meta.json")}
