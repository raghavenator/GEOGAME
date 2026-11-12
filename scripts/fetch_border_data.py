"""Fetch and vendor the data + libraries Border Bridge needs, then derive geo metadata.

    python scripts/fetch_border_data.py            # download anything missing, then derive
    python scripts/fetch_border_data.py --refresh  # re-download everything

Sources (pinned versions, downloaded once and committed):
  * world-atlas 2.0.2  countries-50m.json  Natural Earth 1:50m admin-0 shapes as TopoJSON
  * world-countries 5.1.0  countries.json  the npm release of mledoze/countries (borders, ISO codes)
  * d3 7.9.0 and topojson-client 3.1.0  browser bundles

Derived outputs:
  * app/games/border_bridge/data/mledoze_countries.json  trimmed to the fields we use
  * app/games/border_bridge/static/data/geo_meta.json    per-country mainland polygons + centroid
  * app/shared/data/countries.json                       gains cca3/ccn3 fields (additive)

If the network is unavailable the script stops and lists the files to add by hand.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAME = ROOT / "app" / "games" / "border_bridge"
VENDOR = ROOT / "app" / "shared" / "static" / "vendor"
CDN = "https://cdn.jsdelivr.net/npm"

TOPO_FILE = GAME / "static" / "data" / "countries-50m.json"
RAW_MLEDOZE = GAME / "data" / "raw" / "world-countries.json"
TRIMMED_MLEDOZE = GAME / "data" / "mledoze_countries.json"
GEO_META = GAME / "static" / "data" / "geo_meta.json"
COUNTRIES_JSON = ROOT / "app" / "shared" / "data" / "countries.json"

DOWNLOADS: list[tuple[str, Path]] = [
    (f"{CDN}/world-atlas@2.0.2/countries-50m.json", TOPO_FILE),
    (f"{CDN}/world-atlas@2.0.2/LICENSE", GAME / "static" / "data" / "LICENSE-world-atlas.txt"),
    (f"{CDN}/world-countries@5.1.0/countries.json", RAW_MLEDOZE),
    (f"{CDN}/world-countries@5.1.0/LICENSE", GAME / "data" / "LICENSE-mledoze-countries.txt"),
    (f"{CDN}/d3@7.9.0/dist/d3.min.js", VENDOR / "d3" / "d3.min.js"),
    (f"{CDN}/d3@7.9.0/LICENSE", VENDOR / "d3" / "LICENSE.txt"),
    (f"{CDN}/topojson-client@3.1.0/dist/topojson-client.min.js", VENDOR / "topojson" / "topojson-client.min.js"),
    (f"{CDN}/topojson-client@3.1.0/LICENSE", VENDOR / "topojson" / "LICENSE.txt"),
]

# Mainland heuristic (used only for camera framing; every polygon is still drawn).
# A polygon belongs to the mainland if it is big and close, or small and very close,
# relative to the country's largest polygon.
MAINLAND_RULES = ((0.20, 1000.0), (0.02, 300.0))  # (min area fraction, max gap km)
KM_PER_DEG = 111.32


def download(refresh: bool) -> None:
    missing: list[tuple[str, Path]] = []
    for url, dest in DOWNLOADS:
        if dest.exists() and not refresh:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"  fetching {url}")
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                dest.write_bytes(resp.read())
        except OSError as exc:
            print(f"    failed: {exc}")
            missing.append((url, dest))
    if missing:
        print("\nNetwork unavailable for some files. Download them manually and place them here:")
        for url, dest in missing:
            print(f"  {url}\n    -> {dest.relative_to(ROOT)}")
        sys.exit(1)


# --------------------------------------------------------------------------- TopoJSON
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


def polygons_of(geom: dict) -> list[list[int]]:
    """Exterior ring (arc indices) of each polygon, in the same order topojson.feature emits them."""
    if geom["type"] == "Polygon":
        return [geom["arcs"][0]]
    if geom["type"] == "MultiPolygon":
        return [poly[0] for poly in geom["arcs"]]
    return []


def ring_stats(pts: list[tuple[float, float]]) -> tuple[float, tuple[float, float], tuple[float, float, float, float]]:
    """Approximate area (km²), centroid (lon, lat) and bbox via an equirectangular shoelace."""
    lat0 = sum(p[1] for p in pts) / len(pts)
    k = math.cos(math.radians(lat0))
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        cross = (x0 * k) * y1 - (x1 * k) * y0
        a += cross
        cx += (x0 * k + x1 * k) * cross
        cy += (y0 + y1) * cross
    a /= 2
    if abs(a) < 1e-12:
        lon, lat = pts[0]
    else:
        lon, lat = cx / (6 * a) / k, cy / (6 * a)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return abs(a) * KM_PER_DEG**2, (lon, lat), (min(xs), min(ys), max(xs), max(ys))


def bbox_gap_km(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    lat = math.radians((a[1] + a[3] + b[1] + b[3]) / 4)
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dx = min(dx, 360 - dx) if dx > 180 else dx
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    return math.hypot(dx * math.cos(lat), dy) * KM_PER_DEG


def derive_geo_meta(topo: dict) -> dict[str, dict]:
    arcs = decode_arcs(topo)
    meta: dict[str, dict] = {}
    for index, geom in enumerate(topo["objects"]["countries"]["geometries"]):
        fid = geom.get("id")
        if not fid:
            continue  # unnamed-in-ISO areas (e.g. Kosovo, N. Cyprus) are drawn but never referenced
        stats = [ring_stats(ring_coords(r, arcs)) for r in polygons_of(geom)]
        if not stats:
            continue
        big = max(range(len(stats)), key=lambda i: stats[i][0])
        big_area, _, big_box = stats[big]
        mainland = [
            i for i, (area, _, box) in enumerate(stats)
            if i == big or any(area >= frac * big_area and bbox_gap_km(box, big_box) <= gap
                               for frac, gap in MAINLAND_RULES)
        ]
        total = sum(stats[i][0] for i in mainland)
        ref = stats[big][1][0]
        lon = sum((ref + (stats[i][1][0] - ref + 180) % 360 - 180) * stats[i][0] for i in mainland) / total
        lat = sum(stats[i][1][1] * stats[i][0] for i in mainland) / total
        lon = (lon + 180) % 360 - 180
        if fid in meta and meta[fid]["area_km2"] >= total:
            continue  # duplicate id (Ashmore and Cartier Is. reuse Australia's 036): keep the larger
        meta[fid] = {
            "index": index,
            "name": geom.get("properties", {}).get("name", ""),
            "mainland": mainland,
            "centroid": [round(lon, 3), round(lat, 3)],
            "area_km2": round(total),
        }
    return meta


# --------------------------------------------------------------------------- mledoze
def trim_mledoze(raw: list[dict]) -> list[dict]:
    out = []
    for c in raw:
        out.append({
            "cca2": c["cca2"],
            "cca3": c["cca3"],
            "ccn3": c.get("ccn3", ""),
            "name": c["name"]["common"],
            "independent": c.get("independent"),
            "unMember": c.get("unMember", False),
            "borders": c.get("borders", []),
            "area": c.get("area"),
            "latlng": c.get("latlng", []),
        })
    out.sort(key=lambda c: c["cca3"])
    return out


def extend_countries_json(trimmed: list[dict]) -> int:
    by_cca2 = {c["cca2"].lower(): c for c in trimmed}
    rows = json.loads(COUNTRIES_JSON.read_text(encoding="utf-8"))
    changed = 0
    for row in rows:
        src = by_cca2.get(row["code"])
        if src is None:
            continue
        for key in ("cca3", "ccn3"):
            if row.get(key) != src[key]:
                row[key] = src[key]
                changed += 1
    COUNTRIES_JSON.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-download even if files exist")
    args = parser.parse_args()

    print("Downloading (skips files already vendored)...")
    download(args.refresh)

    print("Deriving data...")
    trimmed = trim_mledoze(json.loads(RAW_MLEDOZE.read_text(encoding="utf-8")))
    TRIMMED_MLEDOZE.write_text(json.dumps(trimmed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"  {TRIMMED_MLEDOZE.relative_to(ROOT)}: {len(trimmed)} entries")

    meta = derive_geo_meta(json.loads(TOPO_FILE.read_text(encoding="utf-8")))
    GEO_META.write_text(json.dumps(meta, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"  {GEO_META.relative_to(ROOT)}: {len(meta)} shapes")

    print(f"  countries.json: {extend_countries_json(trimmed)} cca3/ccn3 values written")
    print("Done. Run scripts/verify_border_data.py for the join report.")


if __name__ == "__main__":
    main()
