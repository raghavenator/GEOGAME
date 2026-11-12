"""Country data loader and forgiving answer matcher, shared by all games."""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "countries.json"
REGIONS: tuple[str, ...] = ("Africa", "Americas", "Asia", "Europe", "Oceania")


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    region: str
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


_PUNCT = re.compile(r"[^\w\s]")
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fold case, accents, punctuation and common abbreviations.

    >>> normalize("  Côte d'Ivoire ")
    'cote divoire'
    >>> normalize("St. Lucia")
    'saint lucia'
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("&", " and ")
    # Drop apostrophes/quotes and dots outright: d'Ivoire -> divoire, U.S.A. -> usa
    text = "".join(ch for ch in text if ch not in "'." and unicodedata.category(ch) not in ("Pi", "Pf"))
    text = _PUNCT.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    if text.startswith("the "):
        text = text[4:]
    if text.startswith("st "):
        text = "saint " + text[3:]
    return text


def edit_distance(a: str, b: str, limit: int) -> int:
    """Optimal-string-alignment distance (Levenshtein + adjacent swaps).

    Returns ``limit + 1`` early once the distance is known to exceed ``limit``.
    """
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def fuzzy_tolerance(length: int) -> int:
    """How many typos we forgive for a guess of this length."""
    if length <= 4:
        return 0
    if length <= 8:
        return 1
    return 2


class CountryIndex:
    """All countries plus a normalised lookup table of names and aliases."""

    def __init__(self, countries: list[Country]) -> None:
        self.countries: tuple[Country, ...] = tuple(countries)
        self.by_code: dict[str, Country] = {c.code: c for c in countries}
        self._lookup: dict[str, str] = {}
        for c in countries:
            for name in c.all_names:
                key = normalize(name)
                existing = self._lookup.get(key)
                if existing and existing != c.code:
                    raise ValueError(f"Ambiguous name {name!r}: {existing} and {c.code}")
                self._lookup[key] = c.code

    def match(self, guess: str) -> Country | None:
        """Resolve free text to a country, or None if unknown or ambiguous."""
        key = normalize(guess)
        if not key:
            return None
        code = self._lookup.get(key)
        if code:
            return self.by_code[code]
        limit = fuzzy_tolerance(len(key))
        if limit == 0:
            return None
        best_dist = limit + 1
        best_codes: set[str] = set()
        for name, code in self._lookup.items():
            d = edit_distance(key, name, limit)
            if d < best_dist:
                best_dist, best_codes = d, {code}
            elif d == best_dist and d <= limit:
                best_codes.add(code)
        if best_dist <= limit and len(best_codes) == 1:
            return self.by_code[best_codes.pop()]
        return None

    def in_regions(self, region: str) -> list[Country]:
        if region == "all":
            return list(self.countries)
        return [c for c in self.countries if c.region == region]

    def name_list(self) -> list[dict[str, object]]:
        """Public autocomplete payload: names and aliases only (no codes)."""
        return [{"name": c.name, "aliases": list(c.aliases)} for c in self.countries]


def load_countries(path: Path = DATA_FILE) -> list[Country]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Country(code=r["code"], name=r["name"], region=r["region"], aliases=tuple(r.get("aliases", ())))
        for r in raw
    ]


@lru_cache(maxsize=1)
def get_index() -> CountryIndex:
    return CountryIndex(load_countries())
