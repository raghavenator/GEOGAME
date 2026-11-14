"""Build Capital Quest's capitals.json from mledoze/countries and print a review report.

    python scripts/build_capitals.py            # use the vendored download if present
    python scripts/build_capitals.py --refresh  # re-download world-countries 5.1.0 first

Input: the npm release of mledoze/countries (world-countries 5.1.0, ODbL), the same
pinned file scripts/fetch_border_data.py downloads to
app/games/border_bridge/data/raw/world-countries.json (git-ignored), plus the shared
countries.json and the 50m shape metadata.

Output: app/games/capital_quest/data/capitals.json with one row per eligible country:
code, cca3, shape (ccn3), name, capital, subregion, continent.

The override, confirmation and exclusion tables live in
app/games/capital_quest/capital_data.py. Exits non-zero if a capital is empty.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.games.capital_quest.capital_data import (  # noqa: E402
    CAPITALS_FILE, build_capitals, entries_to_json,
)
from app.shared.geo import load_geo_meta  # noqa: E402
from mledoze_source import fetch, load_countries, load_raw  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-download the source data")
    args = parser.parse_args()
    fetch(args.refresh)

    report = build_capitals(load_raw(), load_countries(), load_geo_meta().keys())

    CAPITALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAPITALS_FILE.write_text(json.dumps(entries_to_json(report.entries), indent=1, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    print(f"Wrote {CAPITALS_FILE.relative_to(ROOT)}: {len(report.entries)} eligible countries\n")

    print(f"Overrides applied ({len(report.overrides_applied)}):")
    for code, before, after, why in report.overrides_applied:
        print(f"  {code}  {before} -> {after}  ({why})")
    print(f"\nKept as the single answer although other seats exist ({len(report.confirmed)}):")
    for code, capital, why in report.confirmed:
        print(f"  {code}  {capital}  ({why})")
    print(f"\nExcluded ({len(report.excluded)}):")
    for code, name, why in report.excluded:
        print(f"  {code}  {name}: {why}")
    print(f"\nNot in the pool (not an independent UN member state): "
          f"{', '.join(f'{n} ({c})' for c, n in report.not_in_pool) or 'none'}")
    print(f"Missing or multiple capitals in the source (eligible countries; overridden or excluded above): "
          f"{', '.join(f'{n} {v}' for _, n, v in report.missing_or_empty) or 'none'}")
    print(f"Duplicate capital names: {', '.join(f'{k} {v}' for k, v in report.duplicates) or 'none'}")
    by_continent: dict[str, int] = {}
    for e in report.entries:
        by_continent[e.continent] = by_continent.get(e.continent, 0) + 1
    print("Per continent: " + ", ".join(f"{k} {v}" for k, v in sorted(by_continent.items())))
    return 1 if any(not e.capital.strip() for e in report.entries) else 0


if __name__ == "__main__":
    sys.exit(main())
