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


def world_data_urls() -> dict[str, str]:
    """Browser URLs of the shapes and their metadata (for GeoMaps.loadWorld)."""
    from flask import url_for

    return {"topoUrl": url_for("border_bridge.static", filename="data/countries-50m.json"),
            "metaUrl": url_for("border_bridge.static", filename="data/geo_meta.json")}
