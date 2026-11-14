"""Capital-city data for Capital Quest: the review tables and the join that builds
``data/capitals.json``. Pure functions, no Flask; ``scripts/build_capitals.py`` runs
the join and the app only reads its output.

Sources (see the README):
* capitals, subregions: mledoze/countries ``capital`` / ``subregion`` (npm world-countries 5.1.0)
* names, continents, ids: app/shared/data/countries.json (code = ISO alpha-2, ccn3 = shape id)
* shapes: Natural Earth 1:50m via world-atlas (geo_meta.json)

Pool rule: the same as Border Bridge (independent UN member states, ``is_un_state``),
plus a country must have a 50m shape to highlight and exactly one agreed capital.

Review the three tables below when refreshing the data:
* ``CAPITAL_OVERRIDES`` replaces the source value (a single agreed answer, or spelling).
* ``CONFIRMED_SINGLE_CAPITALS`` are countries with several seats of government where
  the source's single value is kept on purpose. Listing them makes the choice explicit.
* ``EXCLUDED_CAPITALS`` removes countries whose capital is multiple or disputed and has
  no uncontroversial single answer.
A country whose source lists more than one capital, or none, is excluded automatically
unless it has an override.
"""
from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.shared.geo import is_un_state

CAPITALS_FILE = Path(__file__).parent / "data" / "capitals.json"

# code -> (capital, why). Applied on top of the source value.
CAPITAL_OVERRIDES: dict[str, tuple[str, str]] = {
    "za": ("Pretoria", "source lists Pretoria, Bloemfontein and Cape Town; Pretoria is the executive capital "
                       "and the usual single answer"),
    "sz": ("Mbabane", "source gives Lobamba (royal and legislative); Mbabane is the administrative capital "
                      "and the usual single answer"),
    "us": ("Washington, D.C.", "spelling (source: 'Washington D.C.')"),
    "mn": ("Ulaanbaatar", "standard English spelling (source: 'Ulan Bator')"),
    "is": ("Reykjavík", "diacritic (source: 'Reykjavik')"),
    "ag": ("St. John's", "consistent with St. George's (source: 'Saint John's')"),
}

# code -> why the source's single value is kept although other seats exist.
CONFIRMED_SINGLE_CAPITALS: dict[str, str] = {
    "nl": "Amsterdam is the constitutional capital; The Hague is the seat of government, not the capital",
    "ci": "Yamoussoukro is the official capital; Abidjan is the economic centre",
    "bj": "Porto-Novo is the official capital; Cotonou is the seat of government",
    "my": "Kuala Lumpur is the capital; Putrajaya is the administrative centre",
    "tz": "Dodoma is the official capital; Dar es Salaam is the former capital",
    "ch": "Bern is the federal city, the accepted answer",
    "nr": "Nauru has no official capital; Yaren is the de facto seat and the accepted answer",
    "me": "Podgorica is the capital; Cetinje is the historic old royal capital",
    "ye": "Sana'a is the constitutional capital; the recognised government sits in Aden temporarily",
}

# code -> why the country is left out of Capital Quest.
EXCLUDED_CAPITALS: dict[str, str] = {
    "bo": "two capitals: Sucre (constitutional) and La Paz (seat of government) are both widely taught",
    "lk": "two capitals: Sri Jayawardenepura Kotte (official) and Colombo (executive, commercial)",
    "il": "the status of Jerusalem as capital is disputed and not widely recognised",
}


@dataclass(frozen=True)
class CapitalEntry:
    code: str          # ISO alpha-2, the shared countries.json id (and the flag file name)
    cca3: str
    shape: str         # ccn3 = TopoJSON feature id
    name: str          # country display name (shared countries.json)
    capital: str
    subregion: str     # UN subregion as given by mledoze, e.g. "Southern Europe"
    continent: str     # Africa | Americas | Asia | Europe | Oceania (shared countries.json region)


@dataclass
class BuildReport:
    entries: list[CapitalEntry]
    overrides_applied: list[tuple[str, str, str, str]] = field(default_factory=list)  # code, from, to, why
    confirmed: list[tuple[str, str, str]] = field(default_factory=list)              # code, capital, why
    excluded: list[tuple[str, str, str]] = field(default_factory=list)               # code, name, why
    not_in_pool: list[tuple[str, str]] = field(default_factory=list)                 # code, name (non-UN)
    missing_or_empty: list[tuple[str, str, list[str]]] = field(default_factory=list)  # code, name, raw value
    duplicates: list[tuple[str, list[str]]] = field(default_factory=list)            # capital, codes


def name_key(text: str) -> str:
    """Case- and accent-insensitive key for comparing capital names."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().replace(".", " ").replace(",", " ").split())


def build_capitals(
    raw_rows: Iterable[Mapping[str, Any]],
    countries: Iterable[Mapping[str, Any]],
    shape_ids: Iterable[str],
    overrides: Mapping[str, tuple[str, str]] = CAPITAL_OVERRIDES,
    confirmed: Mapping[str, str] = CONFIRMED_SINGLE_CAPITALS,
    excluded: Mapping[str, str] = EXCLUDED_CAPITALS,
) -> BuildReport:
    """Join mledoze rows to shared countries.json and the shapes, apply the tables.

    raw_rows: mledoze entries (cca2, independent, unMember, capital, subregion).
    countries: shared countries.json rows (code, name, region, cca3, ccn3).
    shape_ids: TopoJSON feature ids that have a 50m shape.
    """
    by_code = {r["cca2"].lower(): r for r in raw_rows}
    shapes = set(shape_ids)
    report = BuildReport(entries=[])
    for c in countries:
        code, name = c["code"], c["name"]
        row = by_code.get(code)
        if row is None or not is_un_state(row):
            report.not_in_pool.append((code, name))
            continue
        if code in excluded:
            report.excluded.append((code, name, excluded[code]))
            continue
        if c.get("ccn3") not in shapes:
            report.excluded.append((code, name, "no Natural Earth 50m shape to highlight"))
            continue
        source = [s.strip() for s in row.get("capital") or [] if s and s.strip()]
        if len(source) != 1:
            report.missing_or_empty.append((code, name, list(row.get("capital") or [])))
        if code in overrides:
            capital, why = overrides[code]
            report.overrides_applied.append((code, ", ".join(source) or "(none)", capital, why))
        elif len(source) == 1:
            capital = source[0]
            if code in confirmed:
                report.confirmed.append((code, capital, confirmed[code]))
        else:
            report.excluded.append((code, name, "source lists " + ("no capital" if not source else
                                                                  f"{len(source)} capitals") + " and there's no override"))
            continue
        report.entries.append(CapitalEntry(code=code, cca3=c["cca3"], shape=c["ccn3"], name=name, capital=capital,
                                           subregion=row.get("subregion") or c["region"], continent=c["region"]))

    seen: dict[str, list[str]] = {}
    for e in report.entries:
        seen.setdefault(name_key(e.capital), []).append(e.code)
    report.duplicates = [(k, codes) for k, codes in sorted(seen.items()) if len(codes) > 1]
    report.entries.sort(key=lambda e: e.name)
    return report


def entries_to_json(entries: Iterable[CapitalEntry]) -> list[dict[str, str]]:
    return [asdict(e) for e in entries]


@lru_cache(maxsize=1)
def load_capitals(path: Path = CAPITALS_FILE) -> tuple[CapitalEntry, ...]:
    """The eligible pool, as written by scripts/build_capitals.py."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    return tuple(CapitalEntry(**r) for r in rows)


@lru_cache(maxsize=1)
def capitals_by_code() -> dict[str, CapitalEntry]:
    return {e.code: e for e in load_capitals()}
