"""Build Pinpoint's locations.json from mledoze/countries and print a review report.

    python scripts/build_locations.py            # use the vendored download if present
    python scripts/build_locations.py --refresh  # re-download world-countries 5.1.0 first

Input: mledoze/countries (``latlng``, ``area``, ``subregion``; the pinned file in
scripts/mledoze_source.py), the shared countries.json and the Natural Earth 50m shapes.

Output: app/games/pinpoint/data/locations.json with one row per eligible country:
code, cca3, shape (ccn3), name, lat, lng, area_km2, region, subregion.

The report lists overrides, eligible countries with a missing latlng or area, countries
without a 50m shape, and every reference point that falls outside its own polygon (with
the distance to the outline). The override table is LOCATION_OVERRIDES in
app/games/pinpoint/location_data.py. Exits non-zero if a country is missing data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.games.pinpoint.location_data import LOCATIONS_FILE, build_locations, entries_to_json  # noqa: E402
from app.shared.geo import load_country_polygons  # noqa: E402
from mledoze_source import fetch, load_countries, load_raw  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-download the source data")
    args = parser.parse_args()
    fetch(args.refresh)

    report = build_locations(load_raw(), load_countries(), load_country_polygons())
    LOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOCATIONS_FILE.write_text(json.dumps(entries_to_json(report.entries), indent=1, ensure_ascii=False) + "\n",
                              encoding="utf-8")
    print(f"Wrote {LOCATIONS_FILE.relative_to(ROOT)}: {len(report.entries)} eligible countries\n")

    print(f"Overrides applied ({len(report.overrides_applied)}):")
    for code, name, before, after, why in report.overrides_applied:
        print(f"  {code}  {name}: {before} -> {after}  ({why})")
    print(f"\nMissing latlng or area: {', '.join(f'{n} ({c}: {w})' for c, n, w in report.missing) or 'none'}")
    print(f"No 50m shape (playable, no highlight): {', '.join(f'{n} ({c})' for c, n in report.no_shape) or 'none'}")
    print(f"Not in the pool: {', '.join(f'{n} ({c})' for c, n in report.not_in_pool) or 'none'}")
    print(f"\nReference point outside its own 50m polygon ({len(report.outside)}), km to the nearest outline:")
    for code, name, lat, lng, km in report.outside:
        print(f"  {code}  {name:<32} ({lat:8.3f}, {lng:9.3f})  {km:6.0f} km")
    per_region: dict[str, int] = {}
    for e in report.entries:
        per_region[e.region] = per_region.get(e.region, 0) + 1
    print("\nPer region: " + ", ".join(f"{k} {v}" for k, v in sorted(per_region.items())))
    return 1 if report.missing else 0


if __name__ == "__main__":
    sys.exit(main())
