"""Currency Quest rules: options (one right answer only), rounds, scoring and the play clock."""
from __future__ import annotations

import random

import pytest

from app.games.currency_quest import logic as L
from app.games.currency_quest.currency_data import load_currency_data, name_key
from app.shared.settings import SettingsError, validate_settings

DATA = load_currency_data()
BY_CODE = DATA.by_code
INFO = DATA.currencies
DEFAULTS = {"questions": 5, "seconds": 20, "options": "8", "difficulty": "normal", "locator": "on", "region": "all"}
DIFFICULTIES = ("easy", "normal", "hard")


# --------------------------------------------------------------------------- options
@pytest.mark.parametrize("n", [4, 6, 8])
@pytest.mark.parametrize("difficulty", DIFFICULTIES)
def test_count_distinct_one_correct_no_other_correct_currency(n: int, difficulty: str) -> None:
    rng = random.Random(n)
    for target in DATA.countries:
        opts = L.generate_options(target, DATA, n, difficulty, rng)
        assert len(opts) == n and len(set(opts)) == n                    # distinct by ISO code
        assert opts.count(target.primary) == 1                           # exactly one correct
        assert not (set(opts) - {target.primary}) & set(target.currencies)   # no second right answer
        names = [name_key(INFO[c].name) for c in opts]
        assert len(set(names)) == n                                      # no two options read the same


@pytest.mark.parametrize("code,others", [("pa", {"USD"}), ("ls", {"ZAR"}), ("na", {"ZAR"}), ("sz", {"ZAR"}),
                                         ("bt", {"INR"}), ("kh", {"USD"}), ("bn", {"SGD"}), ("bs", {"USD"}),
                                         ("zw", {"USD", "ZAR", "BWP", "GBP", "EUR", "CNY", "JPY", "INR"})])
def test_multi_currency_countries_never_offer_their_other_currency(code: str, others: set[str]) -> None:
    target = BY_CODE[code]
    for seed in range(60):
        for difficulty in DIFFICULTIES:
            opts = L.generate_options(target, DATA, 8, difficulty, random.Random(seed))
            assert target.primary in opts
            assert not others & set(opts), (code, seed, difficulty)


@pytest.mark.parametrize("code", ["de", "fr", "fi", "hr", "bg"])
def test_euro_appears_exactly_once(code: str) -> None:
    for seed in range(30):
        opts = L.generate_options(BY_CODE[code], DATA, 8, "hard", random.Random(seed))
        assert opts.count("EUR") == 1


def test_shared_currency_is_never_a_distractor_for_its_users() -> None:
    # The US dollar is right for Ecuador; it must not be a distractor for El Salvador's
    # question either way (both use it), nor for Panama (it's in Panama's correct set).
    for code in ("ec", "sv", "pa", "tl"):
        for seed in range(20):
            opts = L.generate_options(BY_CODE[code], DATA, 8, "hard", random.Random(seed))
            assert opts.count("USD") == (1 if BY_CODE[code].primary == "USD" else 0)


def test_normal_mixes_regional_and_random() -> None:
    for code in ("fr", "jp", "ke", "br", "ng", "pe"):
        t = BY_CODE[code]
        regional = {e.primary for e in DATA.countries if e.continent == t.continent}
        counts = []
        for seed in range(20):
            opts = [c for c in L.generate_options(t, DATA, 8, "normal", random.Random(seed)) if c != t.primary]
            counts.append(sum(c in regional for c in opts))
            assert sum(c in regional for c in opts) >= 4, code     # at least half (7 -> 4) from the region
        assert min(counts) < 7 or code == "fr"                      # and not all of them, most of the time


def test_easy_is_not_regional() -> None:
    t = BY_CODE["fr"]
    regional = {e.primary for e in DATA.countries if e.continent == t.continent}
    shares = [sum(c in regional for c in L.generate_options(t, DATA, 8, "easy", random.Random(s)) if c != t.primary)
              for s in range(40)]
    assert sum(shares) / len(shares) < 3.5      # Europe has ~20 of ~140 currencies; easy draws from anywhere


@pytest.mark.parametrize("code,family", [("au", "dollar"), ("ch", "franc"), ("mx", "peso"), ("gb", "pound"),
                                         ("in", "rupee"), ("jo", "dinar"), ("se", "krone"), ("sa", "rial")])
def test_hard_has_look_alikes(code: str, family: str) -> None:
    t = BY_CODE[code]
    ans = INFO[t.primary]
    assert ans.family == family

    def alike(c: str) -> bool:      # same name family, or the same symbol (what the player sees)
        return INFO[c].family == family or (bool(ans.symbol) and INFO[c].symbol == ans.symbol)

    for seed in range(10):
        opts = [c for c in L.generate_options(t, DATA, 8, "hard", random.Random(seed)) if c != t.primary]
        looks = [c for c in opts if alike(c)]
        available = {e.primary for e in DATA.countries if alike(e.primary)} - set(t.currencies)
        # About half are look-alikes; the regional fill may add more (pesos in Latin America).
        assert len(looks) >= min(4, len(available)), (code, seed, opts)


def test_hard_without_look_alikes_fills_from_region() -> None:
    t = BY_CODE["de"]          # the euro has no name family
    regional = {e.primary for e in DATA.countries if e.continent == t.continent}
    for seed in range(10):
        opts = [c for c in L.generate_options(t, DATA, 8, "hard", random.Random(seed)) if c != t.primary]
        assert all(c in regional for c in opts)


def test_options_are_shuffled() -> None:
    t = BY_CODE["jp"]
    positions = {L.generate_options(t, DATA, 8, "normal", random.Random(s)).index("JPY") for s in range(40)}
    assert len(positions) >= 6


def test_bad_option_count_or_difficulty() -> None:
    with pytest.raises(ValueError):
        L.generate_options(BY_CODE["fr"], DATA, 5, "normal", random.Random(0))
    with pytest.raises(ValueError):
        L.generate_options(BY_CODE["fr"], DATA, 8, "brutal", random.Random(0))


def test_labels_are_code_and_symbol_only() -> None:
    # Names give the answer away ("Yemeni rial"), so options carry only the code and the symbol.
    labels = L.option_labels(["YER", "AUD", "EUR"], DATA)
    assert labels == [{"code": "YER", "symbol": INFO["YER"].symbol}, {"code": "AUD", "symbol": "$"},
                      {"code": "EUR", "symbol": "€"}]


def test_hard_offers_same_symbol_look_alikes() -> None:
    t = BY_CODE["is"]            # Icelandic króna "kr": the other "kr" currencies are look-alikes
    for seed in range(10):
        opts = L.generate_options(t, DATA, 8, "hard", random.Random(seed))
        assert {"DKK", "NOK", "SEK"} <= set(opts)


def test_also_used_in() -> None:
    names, more = L.also_used_in("EUR", "de", DATA)
    assert len(names) == 6 and more == len(INFO["EUR"].countries) - 1 - 6
    assert "Germany" not in names
    names, more = L.also_used_in("JPY", "jp", DATA)          # Zimbabwe lists the yen as legal tender
    assert names == ["Zimbabwe"] and more == 0
    names, more = L.also_used_in("PAB", "pa", DATA)
    assert names == [] and more == 0
    names, _ = L.also_used_in("USD", "us", DATA)
    assert names[0] == "Ecuador"                             # countries whose primary it is come first


# --------------------------------------------------------------------------- rounds
def make_round(seed: int = 0, pool=None, **settings: object) -> L.Round:
    return L.new_round("r", {**DEFAULTS, **settings}, DATA, random.Random(seed), pool=pool)


def test_round_shape() -> None:
    rnd = make_round(questions=7, options="6")
    assert len(rnd.questions) == 7 and all(len(q.options) == 6 for q in rnd.questions)
    assert rnd.max_score == 140 and rnd.score == 0 and rnd.limit == 20
    for q in rnd.questions:
        assert BY_CODE[q.extra["country"]].primary == q.code


def test_countries_and_currencies_never_repeat() -> None:
    for seed in range(40):
        rnd = make_round(seed, questions=30)
        countries = [q.extra["country"] for q in rnd.questions]
        assert len(set(countries)) == 30
        assert len({q.code for q in rnd.questions}) == 30


def test_currency_unique_in_a_euro_heavy_pool() -> None:
    euro = [e for e in DATA.countries if e.primary == "EUR"]
    others = [BY_CODE[c] for c in ("ch", "gb", "se", "pl")]
    pool = euro + others          # 26 euro countries and 4 others: at most 5 different currencies
    for seed in range(50):
        rnd = make_round(seed, pool=pool, questions=5)
        codes = [q.code for q in rnd.questions]
        assert codes.count("EUR") == 1 and len(set(codes)) == 5
    with pytest.raises(ValueError, match="Only 5 different currencies"):
        make_round(0, pool=pool, questions=6)


def test_targets_are_uniform_over_countries() -> None:
    # The first question's country is a uniform draw: a euro country comes up about 26/194 of the time.
    hits = sum(make_round(s, questions=1).questions[0].code == "EUR" for s in range(1500))
    assert 0.08 < hits / 1500 < 0.20


def test_region_filters_targets() -> None:
    rnd = make_round(3, region="oceania", questions=8)
    assert all(BY_CODE[q.extra["country"]].continent == "Oceania" for q in rnd.questions)
    with pytest.raises(ValueError):
        make_round(3, region="oceania", questions=30)


def test_settings_validation() -> None:
    assert validate_settings(L.SETTINGS_SCHEMA, {}) == DEFAULTS
    for bad in ({"questions": 0}, {"questions": 31}, {"seconds": 9}, {"seconds": 61}, {"options": "5"},
                {"difficulty": "brutal"}, {"codes": "on"}, {"locator": True}, {"region": "mars"}, {"nope": 1}):
        with pytest.raises(SettingsError):
            validate_settings(L.SETTINGS_SCHEMA, bad)
    ok = validate_settings(L.SETTINGS_SCHEMA, {"questions": 30, "seconds": 60, "options": "4", "difficulty": "hard",
                                               "locator": "off", "region": "europe"})
    assert ok["questions"] == 30 and ok["difficulty"] == "hard"
    assert "locator" not in L.scored_settings(ok)


# --------------------------------------------------------------------------- scoring (Capital Quest's rules)
def test_linear_decay_from_20() -> None:
    assert [L.available_points(t, 20) for t in (0, 5, 10, 20, 25)] == [20, 15, 10, 0, 0]
    assert [L.correct_points(t, 0, 20) for t in (0, 1, 4, 10, 16)] == [20, 19, 16, 10, 4]
    assert L.correct_points(15, 0, 60) == 15


def test_penalty_minimum_whole_numbers() -> None:
    assert L.correct_points(0, 1, 20) == 17
    assert L.correct_points(10, 2, 20) == 4
    assert L.correct_points(10, 3, 20) == 2       # 10 - 9 = 1 -> floored at 2
    assert L.correct_points(19.9, 0, 20) == 2
    assert L.correct_points(0.5, 0, 20) == 20     # 19.5 rounds half up
    assert isinstance(L.correct_points(3.3, 1, 20), int)
    assert L.potential_points(20, 0, 20) == 0 and L.potential_points(19, 0, 20) == 2


def test_same_scoring_as_capital_quest() -> None:
    from app.games.capital_quest import logic as C
    for t in (0, 0.5, 3.3, 7.25, 12, 19.9, 25):
        for w in range(5):
            assert L.correct_points(t, w, 20) == C.correct_points(t, w, 20)
            assert L.potential_points(t, w, 20) == C.potential_points(t, w, 20)


# --------------------------------------------------------------------------- play
def wrong_id(q: L.Question) -> int:
    return next(i for i in q.live_options() if i != q.answer_option)


def test_flow_correct_wrong_timeout_and_no_double_answers() -> None:
    rnd = make_round(1, questions=3)
    L.begin(rnd, 100.0)
    q = rnd.current
    w = wrong_id(q)
    out = L.apply_pick(q, w, 101.0, rnd.limit)
    assert out.kind == "wrong" and w not in q.live_options() and out.potential == 16   # 19 - 3
    with pytest.raises(L.OptionUnavailable):
        L.apply_pick(q, w, 101.5, rnd.limit)                # an eliminated option can't come back
    out = L.apply_pick(q, q.answer_option, 105.0, rnd.limit)
    assert out.kind == "correct" and out.points == 12      # 15 - 3
    with pytest.raises(L.QuestionOver):
        L.apply_pick(q, q.answer_option, 105.1, rnd.limit)  # no double answers
    L.advance(rnd, 0, 106.0)
    q2 = rnd.current
    with pytest.raises(L.TimeoutTooEarly):
        L.apply_timeout(q2, 110.0, rnd.limit)
    assert L.apply_timeout(q2, 126.0, rnd.limit) and q2.points == 0
    assert not L.apply_timeout(q2, 126.1, rnd.limit)        # idempotent
    with pytest.raises(L.QuestionOver):
        L.apply_pick(q2, q2.answer_option, 126.2, rnd.limit)
    L.advance(rnd, 1, 127.0)
    with pytest.raises(L.QuestionActive):
        L.advance(rnd, 2, 127.5)
    L.apply_pick(rnd.current, rnd.current.answer_option, 127.0, rnd.limit)
    L.advance(rnd, 2, 128.0)
    assert rnd.finished and rnd.score == 12 + 0 + 20


def test_paused_time_is_excluded() -> None:
    rnd = make_round(2, questions=1)
    L.begin(rnd, 0.0)
    L.pause(rnd, 4.0)
    L.resume(rnd, 64.0)                                     # a minute off screen
    out = L.apply_pick(rnd.current, rnd.current.answer_option, 66.0, rnd.limit)
    assert out.points == 14                                 # 6 s of play: 20 - 6


def test_late_pick_is_a_timeout() -> None:
    rnd = make_round(4, questions=1)
    L.begin(rnd, 0.0)
    assert L.apply_pick(rnd.current, rnd.current.answer_option, 20.3, rnd.limit).points == 2   # within grace
    rnd = make_round(4, questions=1)
    L.begin(rnd, 0.0)
    out = L.apply_pick(rnd.current, rnd.current.answer_option, 21.0, rnd.limit)
    assert out.kind == "timeout" and out.points == 0
