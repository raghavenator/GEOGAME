from __future__ import annotations

import re

import pytest

from app.shared.countries import REGIONS, edit_distance, get_index, normalize
from app.shared.icons import ICON_DIR
from app.games.flag_reveal.routes import FLAG_DIR


def code_of(text: str) -> str | None:
    c = get_index().match(text)
    return c.code if c else None


# ------------------------------------------------------------------ data integrity
def test_dataset_covers_un_members_plus_observers() -> None:
    idx = get_index()
    assert len(idx.countries) == 195
    assert {c.region for c in idx.countries} == set(REGIONS)
    assert all(re.fullmatch(r"[a-z]{2}", c.code) for c in idx.countries)


def test_every_country_has_a_sanitised_flag() -> None:
    for c in get_index().countries:
        svg = (FLAG_DIR / f"{c.code}.svg").read_text(encoding="utf-8")
        assert "flag-icons" not in svg
        assert not re.search(rf'["#( ]{c.code}-', svg), c.code


def test_icons_used_by_registry_exist() -> None:
    from app.games.registry import GAMES

    for g in GAMES:
        assert (ICON_DIR / f"{g.icon}.svg").exists(), g.icon


# ------------------------------------------------------------------ normalisation
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Côte d'Ivoire ", "cote divoire"),
        ("SÃO TOMÉ & PRÍNCIPE", "sao tome and principe"),
        ("St. Lucia", "saint lucia"),
        ("The Gambia", "gambia"),
        ("Guinea-Bissau", "guinea bissau"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


# ------------------------------------------------------------------ matching
@pytest.mark.parametrize(
    ("guess", "code"),
    [
        ("France", "fr"), ("FRANCE", "fr"), ("  france ", "fr"),
        ("USA", "us"), ("United States", "us"), ("America", "us"), ("u.s.a.", "us"),
        ("UK", "gb"), ("Great Britain", "gb"),
        ("Holland", "nl"), ("the netherlands", "nl"),
        ("Ivory Coast", "ci"), ("Cote d'Ivoire", "ci"), ("côte d’ivoire", "ci"),
        ("Turkey", "tr"), ("Türkiye", "tr"), ("Burma", "mm"), ("Swaziland", "sz"),
        ("Sao Tome", "st"), ("St Kitts and Nevis", "kn"), ("DRC", "cd"), ("Congo", "cg"),
        ("Korea", "kr"), ("North Korea", "kp"), ("Czech Republic", "cz"), ("Macedonia", "mk"),
    ],
)
def test_exact_and_alias_matching(guess: str, code: str) -> None:
    assert code_of(guess) == code


@pytest.mark.parametrize(
    ("guess", "code"),
    [
        ("Frnace", "fr"),        # transposition
        ("Germnay", "de"),
        ("Argentinia", "ar"),    # one extra letter
        ("Philipines", "ph"),
        ("Kazakstan", "kz"),
        ("Liechtenstien", "li"),
    ],
)
def test_light_fuzzy_matching(guess: str, code: str) -> None:
    assert code_of(guess) == code


@pytest.mark.parametrize(
    "guess",
    [
        "",                # empty
        "xyz",             # nonsense
        "Narnia",
        "Irax",            # short words get no typo tolerance
        "Austrlia",        # equally close to Austria and Australia -> ambiguous
    ],
)
def test_no_match_for_unknown_or_ambiguous(guess: str) -> None:
    assert code_of(guess) is None


def test_similar_real_names_are_not_confused() -> None:
    assert code_of("Niger") == "ne"
    assert code_of("Nigeria") == "ng"
    assert code_of("Guinea") == "gn"
    assert code_of("Guyana") == "gy"
    assert code_of("Iran") == "ir"
    assert code_of("Iraq") == "iq"
    assert code_of("Dominica") == "dm"
    assert code_of("Dominican Republic") == "do"


def test_edit_distance() -> None:
    assert edit_distance("abc", "abc", 2) == 0
    assert edit_distance("abc", "acb", 2) == 1      # adjacent swap
    assert edit_distance("kitten", "sitting", 5) == 3
    assert edit_distance("a", "abcdef", 2) == 3     # early exit returns limit + 1


def test_name_list_has_no_codes() -> None:
    payload = get_index().name_list()
    assert all(set(item) == {"name", "aliases"} for item in payload)
