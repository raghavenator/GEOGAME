"""Currency data for Currency Quest: the review tables and the join that builds
``data/currencies.json``. Pure functions, no Flask; ``scripts/build_currencies.py`` runs
the join (plus the ISO 4217 cross-check) and the app only reads its output.

Sources (see the README):
* currencies: mledoze/countries ``currencies`` (ISO 4217 code -> name, symbol), npm
  world-countries 5.1.0, the same pinned file the other games use
* names, continents, ids: app/shared/data/countries.json; subregions from mledoze
* cross-check: the ISO 4217 active currency list ("list one", published by SIX)

Pool rule: the same independent UN member states as the other games (``is_un_state``),
with at least one currency and exactly one primary currency.

Review these tables when refreshing the data:
* ``CURRENCY_CORRECTIONS`` replaces a country's whole currency list where the source is
  out of date or wrong (checked against ISO 4217).
* ``CURRENCY_INFO`` sets a currency's display name/symbol: new currencies the source
  doesn't know, and names that aren't distinguishable on their own ("lari").
* ``PRIMARY_OVERRIDES`` picks the single primary currency (the one shown as the answer)
  for a country with several. A multi-currency country without one is excluded.
* ``ISO_DIFFERENCES_REVIEWED`` lists countries whose final list still differs from ISO
  4217 on purpose. The build fails on any other difference.
* ``ISO_ENTITY_NAMES`` maps a country to its ISO 4217 entity name where they differ by
  more than a trailing "(THE)".
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.shared.geo import is_un_state

CURRENCIES_FILE = Path(__file__).parent / "data" / "currencies.json"

# code -> (ISO codes, why). Replaces the source's list for that country.
CURRENCY_CORRECTIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "bg": (("EUR",), "Bulgaria adopted the euro on 1 January 2026; ISO 4217 lists EUR (source: BGN)"),
    "sl": (("SLE",), "the leone was redenominated in 2022; ISO 4217 lists SLE, SLL is withdrawn (source: SLL)"),
    "cu": (("CUP",), "the convertible peso (CUC) was withdrawn in 2021; ISO 4217 lists CUP only"),
    "zw": (("ZWG", "USD", "ZAR", "BWP", "GBP", "EUR", "CNY", "JPY", "INR"),
           "Zimbabwe Gold (ZiG, ISO 4217 ZWG) replaced the RTGS dollar in April 2024; the bond notes (ZWB) "
           "are gone; the multi-currency basket stays legal tender, so none of it may be a distractor"),
    "ki": (("AUD",), "Kiribati dollar coins have no ISO 4217 code and circulate at par; ISO lists AUD only"),
    "tv": (("AUD",), "Tuvaluan dollar coins have no ISO 4217 code and circulate at par; ISO lists AUD only"),
    "fm": (("USD",), "the source lists no currency; the US dollar is the official currency (ISO 4217: USD)"),
    "ht": (("HTG", "USD"), "ISO 4217 lists the US dollar alongside the gourde"),
}

# ISO code -> (display name, symbol, why). Applied over the source's name/symbol.
CURRENCY_INFO: dict[str, tuple[str, str, str]] = {
    "SLE": ("Sierra Leonean leone", "Le", "new code after the 2022 redenomination (not in the source)"),
    "ZWG": ("Zimbabwe Gold", "ZiG", "introduced April 2024 (not in the source)"),
    "GEL": ("Georgian lari", "₾", "source name 'lari' doesn't say whose"),
    "GMD": ("Gambian dalasi", "D", "source name 'dalasi' doesn't say whose"),
    "MKD": ("Macedonian denar", "ден", "source name 'denar' doesn't say whose; symbol in Cyrillic like RSD's"),
    "LKR": ("Sri Lankan rupee", "Rs", "source symbol has a stray second script ('Rs  රු')"),
    "CHF": ("Swiss franc", "Fr.", "the source gives 'Fr' and 'Fr.' in different rows"),
    "ZAR": ("South African rand", "R", "the source gives 'R' and 'Rs' in different rows"),
    "VES": ("Venezuelan bolívar", "Bs.", "ISO 4217 VES; 'soberano' dropped for display"),
}

# code -> (ISO code, why). The single primary currency of a multi-currency country.
PRIMARY_OVERRIDES: dict[str, tuple[str, str]] = {
    "pa": ("PAB", "the balboa is Panama's own currency (US dollar notes circulate at par)"),
    "bs": ("BSD", "the Bahamian dollar is the national currency (US dollar accepted at par)"),
    "bn": ("BND", "the Brunei dollar is the national currency (Singapore dollar interchangeable at par)"),
    "bt": ("BTN", "the ngultrum is the national currency (Indian rupee accepted at par)"),
    "kh": ("KHR", "the riel is the national currency (US dollar widely used)"),
    "sz": ("SZL", "the lilangeni is the national currency (rand legal tender at par)"),
    "ls": ("LSL", "the loti is the national currency (rand legal tender at par)"),
    "na": ("NAD", "the Namibian dollar is the national currency (rand legal tender at par)"),
    "ht": ("HTG", "the gourde is the national currency"),
    "zw": ("ZWG", "Zimbabwe Gold (ZiG) is the national currency since April 2024; the US dollar also circulates"),
}

# code -> why the final list may differ from ISO 4217's entry for that country.
ISO_DIFFERENCES_REVIEWED: dict[str, str] = {
    "kh": "keeps USD as a second currency: it is used for most larger payments in Cambodia",
    "sv": "ISO still lists the colón (SVC), which hasn't circulated since 2001; USD is the currency",
    "sz": "keeps the rand as a second currency: legal tender at par in the Common Monetary Area",
    "zw": "keeps the multi-currency basket (USD, ZAR, BWP, GBP, EUR, CNY, JPY, INR) as legal tender",
    "bs": "keeps USD as a second currency: accepted at par everywhere",
    "bn": "keeps SGD as a second currency: legal tender in Brunei under the 1967 interchangeability agreement",
    "ve": "ISO also lists VED (the 'digital' bolívar, 1 VED = 1,000,000 VES); VES is the bolívar in circulation "
          "and VED is never offered as an option",
}

# code -> ISO 4217 entity name, where it isn't just our name (+ " (THE)").
ISO_ENTITY_NAMES: dict[str, str] = {
    "bo": "BOLIVIA (PLURINATIONAL STATE OF)",
    "bn": "BRUNEI DARUSSALAM",
    "cd": "CONGO (THE DEMOCRATIC REPUBLIC OF THE)",
    "cg": "CONGO (THE)",
    "ir": "IRAN (ISLAMIC REPUBLIC OF)",
    "kp": "KOREA (THE DEMOCRATIC PEOPLE’S REPUBLIC OF)",
    "kr": "KOREA (THE REPUBLIC OF)",
    "la": "LAO PEOPLE’S DEMOCRATIC REPUBLIC (THE)",
    "fm": "MICRONESIA (FEDERATED STATES OF)",
    "md": "MOLDOVA (THE REPUBLIC OF)",
    "ru": "RUSSIAN FEDERATION (THE)",
    "sy": "SYRIAN ARAB REPUBLIC",
    "tz": "TANZANIA, UNITED REPUBLIC OF",
    "gb": "UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND (THE)",
    "us": "UNITED STATES OF AMERICA (THE)",
    "va": "HOLY SEE (THE)",
    "ve": "VENEZUELA (BOLIVARIAN REPUBLIC OF)",
}

# Look-alike name families for Hard mode: currencies whose names share one of these words.
# Each tuple is one family (spelling variants and cognates that look alike on a button).
NAME_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("dollar",), ("franc",), ("peso",), ("pound",), ("dinar", "denar"), ("rupee", "rupiah"),
    ("dirham",), ("krone", "krona", "kroner", "koruna"), ("rial", "riyal"), ("shilling",), ("ruble", "rouble"),
    ("lira",), ("leu", "lev"), ("won",), ("kwacha",), ("manat",), ("som", "somoni"), ("leone",), ("dobra",),
)


@dataclass(frozen=True)
class CurrencyInfo:
    code: str          # ISO 4217
    name: str          # full display name, e.g. "Australian dollar"
    symbol: str        # may be ""
    family: str        # look-alike family (first word of its NAME_FAMILIES entry), or its own code
    countries: tuple[str, ...]      # eligible countries listing it (the reverse index)
    primary_for: tuple[str, ...]    # eligible countries whose primary currency it is


@dataclass(frozen=True)
class CountryCurrencies:
    code: str          # ISO alpha-2, the shared countries.json id (and the flag file name)
    cca3: str
    shape: str | None  # ccn3 = TopoJSON feature id, None if there's no 50m shape (Tuvalu)
    name: str
    continent: str     # Africa | Americas | Asia | Europe | Oceania
    subregion: str
    currencies: tuple[str, ...]     # the correct set: every currency it uses
    primary: str       # the one shown as the answer


@dataclass
class BuildReport:
    countries: list[CountryCurrencies]
    currencies: dict[str, CurrencyInfo]
    corrections_applied: list[tuple[str, str, str, str]] = field(default_factory=list)  # code, from, to, why
    primaries: list[tuple[str, str, str, str]] = field(default_factory=list)          # code, name, list, primary
    excluded: list[tuple[str, str, str]] = field(default_factory=list)                # code, name, why
    not_in_pool: list[tuple[str, str]] = field(default_factory=list)                  # code, name
    no_currency: list[tuple[str, str]] = field(default_factory=list)                  # code, name (source)
    missing_symbol: list[str] = field(default_factory=list)                           # ISO codes
    source_inconsistencies: list[tuple[str, list[tuple[str, str]]]] = field(default_factory=list)
    name_collisions: list[tuple[str, list[str]]] = field(default_factory=list)        # name key, codes


def name_key(text: str) -> str:
    """Case- and accent-insensitive key for comparing display names."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w\s]", "", text.casefold()).split())


def family_of(name: str, code: str) -> str:
    words = set(name_key(name).split())
    for fam in NAME_FAMILIES:
        if words & set(fam):
            return fam[0]
    return code


def build_currencies(
    raw_rows: Iterable[Mapping[str, Any]],
    countries: Iterable[Mapping[str, Any]],
    shape_ids: Iterable[str],
    corrections: Mapping[str, tuple[tuple[str, ...], str]] = CURRENCY_CORRECTIONS,
    info: Mapping[str, tuple[str, str, str]] = CURRENCY_INFO,
    primary_overrides: Mapping[str, tuple[str, str]] = PRIMARY_OVERRIDES,
) -> BuildReport:
    """Join mledoze rows to the shared countries.json and apply the tables.

    raw_rows: mledoze entries (cca2, independent, unMember, currencies, subregion).
    countries: shared countries.json rows (code, name, region, cca3, ccn3).
    shape_ids: TopoJSON feature ids that have a 50m shape (for the map locator).
    """
    by_code = {r["cca2"].lower(): r for r in raw_rows}
    shapes = set(shape_ids)
    report = BuildReport(countries=[], currencies={})
    seen_info: dict[str, list[tuple[str, str, str]]] = {}   # ISO -> [(name, symbol, country)]

    for c in countries:
        code, name = c["code"], c["name"]
        row = by_code.get(code)
        if row is None or not is_un_state(row):
            report.not_in_pool.append((code, name))
            continue
        source: dict[str, Mapping[str, Any]] = dict(row.get("currencies") or {})
        for iso, cur in source.items():
            seen_info.setdefault(iso, []).append((cur.get("name") or "", cur.get("symbol") or "", code))
        if not source:
            report.no_currency.append((code, name))
        listed = list(source)
        if code in corrections:
            fixed, why = corrections[code]
            report.corrections_applied.append((code, ", ".join(listed) or "(none)", ", ".join(fixed), why))
            listed = list(fixed)
        if not listed:
            report.excluded.append((code, name, "no currency in the source and no correction"))
            continue
        if len(listed) == 1:
            primary = listed[0]
        elif code in primary_overrides:
            primary = primary_overrides[code][0]
            if primary not in listed:
                raise ValueError(f"{code}: primary override {primary} isn't one of its currencies {listed}")
            report.primaries.append((code, name, ", ".join(listed), primary))
        else:
            report.excluded.append((code, name, f"{len(listed)} currencies ({', '.join(listed)}) and no primary override"))
            continue
        report.countries.append(CountryCurrencies(
            code=code, cca3=c["cca3"], shape=c.get("ccn3") if c.get("ccn3") in shapes else None, name=name,
            continent=c["region"], subregion=row.get("subregion") or c["region"],
            currencies=tuple(listed), primary=primary))

    # The source's own inconsistencies (one ISO code, different names or symbols per row).
    for iso, rows in sorted(seen_info.items()):
        variants = sorted({(n, s) for n, s, _ in rows})
        if len(variants) > 1:
            report.source_inconsistencies.append((iso, variants))

    # One canonical record per currency: the table, else the row of a country whose primary it is.
    report.countries.sort(key=lambda e: e.name)
    users: dict[str, list[str]] = {}
    primary_for: dict[str, list[str]] = {}
    for e in report.countries:
        for iso in e.currencies:
            users.setdefault(iso, []).append(e.code)
        primary_for.setdefault(e.primary, []).append(e.code)
    for iso in sorted(users):
        if iso in info:
            cname, symbol, _ = info[iso]
        else:
            rows = seen_info.get(iso)
            if not rows:
                raise ValueError(f"{iso} is used by {users[iso]} but has no name: add it to CURRENCY_INFO")
            preferred = [r for r in rows if r[2] in primary_for.get(iso, [])] or rows
            cname, symbol = preferred[0][0], preferred[0][1]
        cname = cname[:1].upper() + cname[1:]
        report.currencies[iso] = CurrencyInfo(code=iso, name=cname, symbol=symbol.strip(),
                                              family=family_of(cname, iso), countries=tuple(users[iso]),
                                              primary_for=tuple(primary_for.get(iso, ())))
    report.missing_symbol = [iso for iso, cur in report.currencies.items() if not cur.symbol]
    names: dict[str, list[str]] = {}
    for cur in report.currencies.values():
        names.setdefault(name_key(cur.name), []).append(cur.code)
    report.name_collisions = [(k, v) for k, v in sorted(names.items()) if len(v) > 1]
    return report


# --------------------------------------------------------------------------- ISO 4217 cross-check
def iso_entity_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s*\(THE\)\s*$", "", text.upper())
    return re.sub(r"[^A-Z]", "", text)


def iso_check(report: BuildReport, iso_entries: Mapping[str, set[str]],
              entity_names: Mapping[str, str] = ISO_ENTITY_NAMES,
              reviewed: Mapping[str, str] = ISO_DIFFERENCES_REVIEWED) -> dict[str, Any]:
    """Compare each country's final list with ISO 4217 ("list one": entity -> active codes, funds excluded).

    Returns {"unmatched": [codes], "reviewed": [(code, ours, iso, why)], "unreviewed": [(code, ours, iso)],
    "not_iso": [ISO codes we use that aren't active ISO 4217 codes]}."""
    by_key = {iso_entity_key(n): codes for n, codes in iso_entries.items()}
    active = set().union(*iso_entries.values()) if iso_entries else set()
    out: dict[str, Any] = {"unmatched": [], "reviewed": [], "unreviewed": [], "not_iso": []}
    for e in report.countries:
        key = iso_entity_key(entity_names.get(e.code, e.name))
        codes = by_key.get(key)
        if codes is None:
            out["unmatched"].append(e.code)
            continue
        ours = set(e.currencies)
        if ours != codes:
            row = (e.code, sorted(ours), sorted(codes))
            if e.code in reviewed:
                out["reviewed"].append((*row, reviewed[e.code]))
            else:
                out["unreviewed"].append(row)
    out["not_iso"] = sorted(c for c in report.currencies if c not in active)
    return out


def parse_iso_list(xml_text: str) -> tuple[str, dict[str, set[str]]]:
    """ISO 4217 list one (XML) -> (published date, {entity name: active currency codes}). Funds are skipped."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(xml_text)
    out: dict[str, set[str]] = {}
    for e in root.iter("CcyNtry"):
        ccy, nm = e.find("Ccy"), e.find("CcyNm")
        if ccy is None or ccy.text is None or (nm is not None and nm.get("IsFund") == "true"):
            continue
        out.setdefault(e.findtext("CtryNm") or "", set()).add(ccy.text)
    return root.get("Pblshd", ""), out


# --------------------------------------------------------------------------- the built file
def report_to_json(report: BuildReport, meta: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "meta": dict(meta),
        "countries": [{**asdict(e), "currencies": list(e.currencies)} for e in report.countries],
        "currencies": {iso: {**asdict(c), "countries": list(c.countries), "primary_for": list(c.primary_for)}
                       for iso, c in report.currencies.items()},
    }


@dataclass(frozen=True)
class CurrencyData:
    countries: tuple[CountryCurrencies, ...]
    currencies: dict[str, CurrencyInfo]
    meta: dict[str, Any]

    @property
    def by_code(self) -> dict[str, CountryCurrencies]:
        return {e.code: e for e in self.countries}


@lru_cache(maxsize=1)
def load_currency_data(path: Path = CURRENCIES_FILE) -> CurrencyData:
    """The eligible pool and the currency index, as written by scripts/build_currencies.py."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    countries = tuple(CountryCurrencies(**{**r, "currencies": tuple(r["currencies"])}) for r in raw["countries"])
    currencies = {iso: CurrencyInfo(**{**c, "countries": tuple(c["countries"]), "primary_for": tuple(c["primary_for"])})
                  for iso, c in raw["currencies"].items()}
    return CurrencyData(countries=countries, currencies=currencies, meta=raw.get("meta", {}))


@lru_cache(maxsize=1)
def countries_by_code() -> dict[str, CountryCurrencies]:
    return load_currency_data().by_code
