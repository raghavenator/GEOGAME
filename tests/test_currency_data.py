"""Currency Quest data: the built currencies.json, the tables behind it, and the join."""
from __future__ import annotations

import json

import pytest

from app.games.currency_quest import currency_data as D

DATA = D.load_currency_data()
BY_CODE = DATA.by_code
COUNTRIES = json.loads(D.Path(D.__file__).parents[2].joinpath("shared", "data", "countries.json").read_text("utf-8"))


def test_pool_size_and_excluded_countries() -> None:
    codes = {e.code for e in DATA.countries}
    assert len(codes) == len(DATA.countries) == 194
    assert "ps" not in codes          # Palestine: not a UN member state, like the other games' pools
    assert {"fm", "tv", "zw", "pa", "bg"} <= codes   # corrected or overridden, and still in


def test_every_country_has_exactly_one_primary_in_its_correct_set() -> None:
    for e in DATA.countries:
        assert e.currencies, e.code
        assert len(set(e.currencies)) == len(e.currencies), e.code
        assert e.primary in e.currencies, e.code
        if len(e.currencies) > 1:
            assert e.code in D.PRIMARY_OVERRIDES, e.code


@pytest.mark.parametrize("code,primary", [(c, p) for c, (p, _) in D.PRIMARY_OVERRIDES.items()])
def test_primary_overrides_applied(code: str, primary: str) -> None:
    assert BY_CODE[code].primary == primary


@pytest.mark.parametrize("code", list(D.CURRENCY_CORRECTIONS))
def test_corrections_applied(code: str) -> None:
    assert BY_CODE[code].currencies == D.CURRENCY_CORRECTIONS[code][0]


def test_known_staleness_fixes() -> None:
    assert BY_CODE["bg"].primary == "EUR"         # euro since 1 January 2026
    assert BY_CODE["hr"].primary == "EUR"         # euro since 2023
    assert BY_CODE["sl"].primary == "SLE"         # redenominated leone
    assert BY_CODE["zw"].primary == "ZWG"         # ZiG since April 2024
    assert BY_CODE["cu"].currencies == ("CUP",)   # CUC withdrawn
    assert BY_CODE["fm"].currencies == ("USD",)   # missing in the source
    assert set(BY_CODE["pa"].currencies) == {"PAB", "USD"} and BY_CODE["pa"].primary == "PAB"
    assert set(BY_CODE["ls"].currencies) == {"LSL", "ZAR"} and BY_CODE["ls"].primary == "LSL"


def test_reverse_index_is_consistent() -> None:
    for e in DATA.countries:
        for iso in e.currencies:
            assert e.code in DATA.currencies[iso].countries, (e.code, iso)
        assert e.code in DATA.currencies[e.primary].primary_for
    for iso, cur in DATA.currencies.items():
        assert cur.code == iso
        for code in cur.countries:
            assert iso in BY_CODE[code].currencies, (iso, code)
        assert set(cur.primary_for) <= set(cur.countries)
        for code in cur.primary_for:
            assert BY_CODE[code].primary == iso
    eur = DATA.currencies["EUR"]
    assert {"de", "fr", "fi", "hr", "bg", "me", "va"} <= set(eur.primary_for)


def test_display_names_are_full_and_unique() -> None:
    keys = [D.name_key(c.name) for c in DATA.currencies.values()]
    assert len(keys) == len(set(keys))
    for cur in DATA.currencies.values():
        assert cur.name[0].isupper(), cur.code
        # "Australian dollar", never just "dollar": a family word alone isn't a name
        assert len(cur.name.split()) >= 2 or cur.code == "EUR", cur.name


def test_only_eligible_countries_are_in_the_pool() -> None:
    raw = {r["code"] for r in COUNTRIES}
    assert {e.code for e in DATA.countries} <= raw


# --------------------------------------------------------------------------- the join, on made-up rows
def _raw(cca2: str, currencies: dict, un: bool = True, sub: str = "Western Europe") -> dict:
    return {"cca2": cca2.upper(), "independent": un, "unMember": un, "currencies": currencies, "subregion": sub}


def _country(code: str, name: str) -> dict:
    return {"code": code, "name": name, "region": "Europe", "cca3": code.upper() * 2, "ccn3": "001"}


def test_join_excludes_multi_currency_without_override_and_non_un() -> None:
    rows = [_raw("aa", {"AAA": {"name": "Aa dollar", "symbol": "$"}, "BBB": {"name": "Bb dollar", "symbol": "$"}}),
            _raw("bb", {"BBB": {"name": "Bb dollar", "symbol": "$"}}),
            _raw("cc", {"CCC": {"name": "Cc franc", "symbol": "Fr"}}, un=False),
            _raw("dd", {})]
    countries = [_country("aa", "Aland"), _country("bb", "Bland"), _country("cc", "Cland"), _country("dd", "Dland")]
    r = D.build_currencies(rows, countries, ["001"], corrections={}, info={}, primary_overrides={})
    assert [e.code for e in r.countries] == ["bb"]
    assert {c for c, _, _ in r.excluded} == {"aa", "dd"}
    assert r.not_in_pool == [("cc", "Cland")]
    assert r.currencies["BBB"].family == "dollar"

    r = D.build_currencies(rows, countries, ["001"], corrections={"dd": (("BBB",), "fix")}, info={},
                           primary_overrides={"aa": ("AAA", "why")})
    assert {e.code: e.primary for e in r.countries} == {"aa": "AAA", "bb": "BBB", "dd": "BBB"}
    assert r.currencies["BBB"].countries == ("aa", "bb", "dd") and r.currencies["BBB"].primary_for == ("bb", "dd")


def test_override_must_name_one_of_the_countrys_currencies() -> None:
    rows = [_raw("aa", {"AAA": {"name": "A", "symbol": ""}, "BBB": {"name": "B", "symbol": ""}})]
    with pytest.raises(ValueError):
        D.build_currencies(rows, [_country("aa", "Aland")], [], corrections={}, info={},
                           primary_overrides={"aa": ("ZZZ", "nope")})


def test_iso_check_flags_unreviewed_differences() -> None:
    rows = [_raw("aa", {"AAA": {"name": "Aa dollar", "symbol": "$"}}),
            _raw("bb", {"BBB": {"name": "Bb dollar", "symbol": "$"}})]
    r = D.build_currencies(rows, [_country("aa", "Aland"), _country("bb", "Bland")], [], corrections={}, info={},
                           primary_overrides={})
    iso = {"ALAND (THE)": {"AAA"}, "BLAND": {"BBB", "CCC"}}
    out = D.iso_check(r, iso, entity_names={}, reviewed={})
    assert out["unreviewed"] == [("bb", ["BBB"], ["BBB", "CCC"])] and not out["unmatched"]
    out = D.iso_check(r, iso, entity_names={}, reviewed={"bb": "ok"})
    assert not out["unreviewed"] and out["reviewed"][0][0] == "bb"


def test_name_families() -> None:
    assert D.family_of("Australian dollar", "AUD") == "dollar"
    assert D.family_of("West African CFA franc", "XOF") == "franc"
    assert D.family_of("Swedish krona", "SEK") == D.family_of("Danish krone", "DKK") == "krone"
    assert D.family_of("Saudi riyal", "SAR") == D.family_of("Omani rial", "OMR") == "rial"
    assert D.family_of("Euro", "EUR") == "EUR"
