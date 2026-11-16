"""Build Currency Quest's currencies.json from mledoze/countries, cross-check it against
ISO 4217 and print a review report.

    python scripts/build_currencies.py            # use the downloads if present
    python scripts/build_currencies.py --refresh  # re-download both sources first
    python scripts/build_currencies.py --no-iso   # skip the ISO 4217 cross-check (offline)

Inputs:
* mledoze/countries (npm world-countries 5.1.0, ODbL): the pinned file the other games use,
  app/games/border_bridge/data/raw/world-countries.json (git-ignored)
* ISO 4217 list one (the active currency list, published by SIX for the ISO 4217
  maintenance agency): app/games/currency_quest/data/raw/iso4217-list-one.xml (git-ignored)
* the shared countries.json and the 50m shape metadata

Output: app/games/currency_quest/data/currencies.json with
* "countries": one row per eligible country: code, cca3, shape, name, continent, subregion,
  currencies (the correct set) and primary (the answer shown)
* "currencies": ISO code -> name, symbol, look-alike family, countries using it (the
  reverse index) and the countries whose primary currency it is

The correction and override tables live in app/games/currency_quest/currency_data.py.
Exits non-zero if the ISO check finds a difference that isn't in ISO_DIFFERENCES_REVIEWED,
or a country it can't match.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.games.currency_quest.currency_data import (  # noqa: E402
    CURRENCIES_FILE, build_currencies, iso_check, parse_iso_list, report_to_json,
)
from app.shared.geo import load_geo_meta  # noqa: E402
from mledoze_source import RAW_URL, fetch, load_countries, load_raw  # noqa: E402

ISO_URL = ("https://www.six-group.com/dam/download/financial-information/data-center/"
           "iso-currrency/lists/list-one.xml")
ISO_FILE = ROOT / "app" / "games" / "currency_quest" / "data" / "raw" / "iso4217-list-one.xml"


def fetch_iso(refresh: bool) -> str | None:
    if not ISO_FILE.exists() or refresh:
        ISO_FILE.parent.mkdir(parents=True, exist_ok=True)
        print(f"fetching {ISO_URL}")
        try:
            with urllib.request.urlopen(ISO_URL, timeout=60) as resp:
                ISO_FILE.write_bytes(resp.read())
        except OSError as exc:
            print(f"  ISO 4217 download failed ({exc}); save it by hand as {ISO_FILE.relative_to(ROOT)}")
            return None
    return ISO_FILE.read_text(encoding="utf-8")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="re-download the source data")
    parser.add_argument("--no-iso", action="store_true", help="skip the ISO 4217 cross-check")
    args = parser.parse_args()
    fetch(args.refresh)

    report = build_currencies(load_raw(), load_countries(), load_geo_meta().keys())
    iso_text = None if args.no_iso else fetch_iso(args.refresh)
    iso_published = ""
    check = None
    if iso_text:
        iso_published, entries = parse_iso_list(iso_text)
        check = iso_check(report, entries)

    meta = {"source": RAW_URL, "iso4217_list_published": iso_published or None,
            "iso4217_checked": check is not None}
    CURRENCIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURRENCIES_FILE.write_text(json.dumps(report_to_json(report, meta), indent=1, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    print(f"Wrote {CURRENCIES_FILE.relative_to(ROOT)}: {len(report.countries)} eligible countries, "
          f"{len(report.currencies)} currencies "
          f"({sum(1 for c in report.currencies.values() if c.primary_for)} of them a primary)\n")

    print(f"Corrections applied ({len(report.corrections_applied)}):")
    for code, before, after, why in report.corrections_applied:
        print(f"  {code}  {before} -> {after}  ({why})")
    print(f"\nCountries with several currencies, and the primary chosen ({len(report.primaries)}):")
    for code, name, listed, primary in report.primaries:
        print(f"  {code}  {name}: {listed} -> {primary}")
    print(f"\nExcluded ({len(report.excluded)}):")
    for code, name, why in report.excluded:
        print(f"  {code}  {name}: {why}")
    print("  none" if not report.excluded else "", end="")
    print(f"\nNot in the pool (not an independent UN member state): "
          f"{', '.join(f'{n} ({c})' for c, n in report.not_in_pool) or 'none'}")
    print(f"No currency in the source: {', '.join(f'{n} ({c})' for c, n in report.no_currency) or 'none'}")
    print(f"Currencies with no symbol: {', '.join(report.missing_symbol) or 'none'}")
    print(f"Display-name collisions: {', '.join(f'{k} {v}' for k, v in report.name_collisions) or 'none'}")
    print("Source rows that disagree on a currency's name/symbol (resolved via CURRENCY_INFO or the "
          "primary user's row):")
    for iso, variants in report.source_inconsistencies:
        print(f"  {iso}: {variants}")
    shared = sorted(report.currencies.values(), key=lambda c: (-len(c.countries), c.code))[:10]
    print("Most shared currencies (countries listing it / as primary):")
    for c in shared:
        print(f"  {c.code} {c.name}: {len(c.countries)} / {len(c.primary_for)}")
    families: dict[str, list[str]] = {}
    for c in report.currencies.values():
        if c.primary_for and c.family != c.code:
            families.setdefault(c.family, []).append(c.code)
    print("Look-alike families (primary currencies): "
          + "; ".join(f"{f} {len(v)}" for f, v in sorted(families.items(), key=lambda kv: -len(kv[1]))))
    by_continent: dict[str, int] = {}
    for e in report.countries:
        by_continent[e.continent] = by_continent.get(e.continent, 0) + 1
    print("Per continent: " + ", ".join(f"{k} {v}" for k, v in sorted(by_continent.items())))
    print(f"No 50m shape (no map locator): {', '.join(e.name for e in report.countries if not e.shape) or 'none'}")

    if check is None:
        print("\nISO 4217 cross-check: SKIPPED. Every country's currencies are unverified against ISO 4217.")
        return 0
    print(f"\nISO 4217 cross-check (list one published {iso_published}):")
    print(f"  matching exactly: {len(report.countries) - len(check['unmatched']) - len(check['reviewed']) - len(check['unreviewed'])}"
          f" of {len(report.countries)}")
    for code, ours, iso, why in check["reviewed"]:
        print(f"  reviewed  {code}: ours {ours}, ISO {iso}  ({why})")
    for code, ours, iso in check["unreviewed"]:
        print(f"  DIFFERS   {code}: ours {ours}, ISO {iso}  <- fix CURRENCY_CORRECTIONS or review it")
    if check["unmatched"]:
        print(f"  NO ISO ENTRY FOUND for: {', '.join(check['unmatched'])}  <- add to ISO_ENTITY_NAMES")
    print(f"  codes we use that aren't active ISO 4217 codes: {', '.join(check['not_iso']) or 'none'}")
    return 1 if check["unreviewed"] or check["unmatched"] or check["not_iso"] else 0


if __name__ == "__main__":
    sys.exit(main())
