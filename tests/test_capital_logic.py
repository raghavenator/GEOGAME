"""Capital Quest rules: options, scoring, the play clock and round flow."""
from __future__ import annotations

import random

import pytest

from app.games.capital_quest import logic as L
from app.games.capital_quest.capital_data import load_capitals, name_key
from app.shared.settings import SettingsError, validate_settings

POOL = load_capitals()
BY_CODE = {e.code: e for e in POOL}
DEFAULTS = {"questions": 5, "seconds": 20, "options": "8", "view": "flat", "framing": "region", "region": "all"}


# --------------------------------------------------------------------------- options
@pytest.mark.parametrize("n", [4, 6, 8])
def test_option_count_unique_names_one_correct(n: int) -> None:
    rng = random.Random(1)
    for target in POOL:
        opts = L.generate_options(target, POOL, n, rng)
        assert len(opts) == n and len(set(opts)) == n
        assert opts.count(target.code) == 1
        names = [name_key(BY_CODE[c].capital) for c in opts]
        assert len(set(names)) == n                      # no two options share a name
        assert names.count(name_key(target.capital)) == 1  # no distractor named like the answer


def test_no_distractor_named_like_the_answer() -> None:
    # A made-up pool where another country's capital has the answer's name (accents aside).
    t = BY_CODE["co"]
    clash = L.CapitalEntry("xx", "XXX", "999", "Clashland", "Bogota", t.subregion, t.continent)
    for seed in range(30):
        opts = L.generate_options(t, [*POOL, clash], 8, random.Random(seed))
        assert "xx" not in opts


def test_distractors_mix_near_and_far() -> None:
    rng = random.Random(2)
    for code in ("fr", "jp", "ke", "br", "au", "mt", "sg"):
        t = BY_CODE[code]
        opts = [BY_CODE[c] for c in L.generate_options(t, POOL, 8, rng) if c != code]
        near = [e for e in opts if e.continent == t.continent]
        far = [e for e in opts if e.continent != t.continent]
        assert len(near) == 4 and len(far) == 3, code
        if sum(e.subregion == t.subregion for e in POOL) > 4:
            assert all(e.subregion == t.subregion for e in near), code


def test_options_are_shuffled() -> None:
    t = BY_CODE["fr"]
    positions = {L.generate_options(t, POOL, 8, random.Random(s)).index("fr") for s in range(40)}
    assert len(positions) >= 6  # the answer isn't always in the same slot
    first = [L.generate_options(t, POOL, 8, random.Random(s)) for s in range(10)]
    assert len({tuple(o) for o in first}) == 10


def test_bad_option_count() -> None:
    with pytest.raises(ValueError):
        L.generate_options(BY_CODE["fr"], POOL, 5, random.Random(0))


# --------------------------------------------------------------------------- scoring
def test_linear_decay_from_20() -> None:
    assert L.available_points(0, 20) == 20
    assert L.available_points(5, 20) == 15
    assert L.available_points(10, 20) == 10
    assert L.available_points(20, 20) == 0
    assert L.available_points(25, 20) == 0
    assert L.available_points(15, 60) == 15
    assert [L.correct_points(t, 0, 20) for t in (0, 1, 4, 10, 16)] == [20, 19, 16, 10, 4]


def test_wrong_penalty_and_minimum() -> None:
    assert L.correct_points(0, 1, 20) == 17
    assert L.correct_points(0, 2, 20) == 14
    assert L.correct_points(10, 2, 20) == 4
    assert L.correct_points(10, 3, 20) == 2   # 10 - 9 = 1 -> floored at 2
    assert L.correct_points(19.9, 0, 20) == 2
    assert L.correct_points(19.9, 7, 20) == 2


def test_whole_number_rounding() -> None:
    assert L.correct_points(0.5, 0, 20) == 20     # 19.5 rounds half up
    assert L.correct_points(0.6, 0, 20) == 19     # 19.4
    assert L.correct_points(3.3, 0, 20) == 17     # 16.7
    assert L.correct_points(7.5, 1, 20) == 10     # 12.5 - 3 = 9.5 -> 10
    assert L.round_half_up(2.5) == 3 and L.round_half_up(2.49) == 2
    for t in (0.1, 3.7, 11.11, 19.99):
        assert isinstance(L.correct_points(t, 1, 20), int)


def test_potential_points_is_zero_after_the_limit() -> None:
    assert L.potential_points(20, 0, 20) == 0
    assert L.potential_points(19, 0, 20) == 2
    assert L.potential_points(0, 1, 20) == 17


# --------------------------------------------------------------------------- state machine
def make_round(seed: int = 0, **settings: object) -> L.Round:
    return L.new_round("r", {**DEFAULTS, **settings}, POOL, random.Random(seed))


def wrong_id(q: L.Question, skip: tuple[int, ...] = ()) -> int:
    return next(i for i in q.live_options() if i != q.answer_option and i not in skip)


def test_questions_never_repeat() -> None:
    for seed in range(20):
        rnd = make_round(seed, questions=30)
        codes = [q.code for q in rnd.questions]
        assert len(codes) == len(set(codes)) == 30
    oceania = make_round(0, questions=13, region="oceania")
    assert {BY_CODE[q.code].continent for q in oceania.questions} == {"Oceania"}
    with pytest.raises(ValueError, match="Only 13"):
        make_round(0, questions=14, region="oceania")


def test_round_shape() -> None:
    rnd = make_round(options="4")
    assert all(len(q.options) == 4 for q in rnd.questions)
    assert rnd.max_score == 100 and rnd.score == 0


def test_clock_starts_at_begin_and_pause_is_excluded() -> None:
    rnd = make_round()
    q = rnd.current
    assert q.started_at is None and q.elapsed(500) == 0
    assert L.begin(rnd, 100.0) and not L.begin(rnd, 101.0)
    assert L.pause(rnd, 104.0) and not L.pause(rnd, 105.0)
    assert L.resume(rnd, 160.0) and not L.resume(rnd, 161.0)  # 56 s paused
    out = L.apply_pick(q, q.answer_option, 166.0, rnd.limit)  # 4 s + 6 s of play = 10 s
    assert out.kind == "correct" and out.points == 10 and q.elapsed(999) == 10


def test_wrong_then_correct() -> None:
    rnd = make_round()
    L.begin(rnd, 0.0)
    q = rnd.current
    w = wrong_id(q)
    out = L.apply_pick(q, w, 2.0, 20)
    assert out.kind == "wrong" and out.points == 0 and out.potential == 15  # 18 - 3
    assert w in q.eliminated and w not in q.live_options()
    with pytest.raises(L.OptionUnavailable):
        L.apply_pick(q, w, 2.5, 20)                       # the wrong option doesn't come back
    with pytest.raises(L.OptionUnavailable):
        L.apply_pick(q, 99, 2.5, 20)
    out = L.apply_pick(q, q.answer_option, 4.0, 20)
    assert out.kind == "correct" and out.points == 13 and q.points == 13   # 16 - 3
    with pytest.raises(L.QuestionOver):                   # no double answers
        L.apply_pick(q, q.answer_option, 4.1, 20)
    assert L.apply_timeout(q, 30.0, 20) is False          # stale timer: no-op
    assert q.points == 13 and q.status == "correct"


def test_timeout() -> None:
    rnd = make_round()
    L.begin(rnd, 0.0)
    q = rnd.current
    with pytest.raises(L.TimeoutTooEarly):
        L.apply_timeout(q, 10.0, 20)
    assert L.apply_timeout(q, 19.5, 20) is True           # within the clock-drift tolerance
    assert q.status == "timeout" and q.points == 0
    with pytest.raises(L.QuestionOver):
        L.apply_pick(q, q.answer_option, 19.6, 20)


def test_late_pick_is_a_timeout_but_grace_counts() -> None:
    rnd = make_round()
    L.begin(rnd, 0.0)
    q = rnd.current
    assert L.apply_pick(q, q.answer_option, 20.4, 20).points == 2   # network grace
    rnd = make_round(1)
    L.begin(rnd, 0.0)
    q = rnd.current
    out = L.apply_pick(q, q.answer_option, 21.0, 20)
    assert out.kind == "timeout" and q.points == 0 and q.elapsed(99) == 20


def test_advance() -> None:
    rnd = make_round(questions=2)
    L.begin(rnd, 0.0)
    with pytest.raises(L.QuestionActive):
        L.advance(rnd, 0, 5.0)
    L.apply_pick(rnd.current, rnd.current.answer_option, 1.0, 20)
    L.advance(rnd, 0, 6.0)
    L.advance(rnd, 0, 6.5)                                 # idempotent
    assert rnd.index == 1 and rnd.current.started_at == 6.0
    L.advance(rnd, 1, 30.0)                                # time ran out on the server: closed as timeout
    assert rnd.finished and rnd.questions[1].status == "timeout"
    assert rnd.score == 19


def test_advance_while_paused_starts_the_next_clock_on_resume() -> None:
    rnd = make_round(questions=2)
    L.begin(rnd, 0.0)
    L.apply_pick(rnd.current, rnd.current.answer_option, 1.0, 20)
    L.pause(rnd, 2.0)
    L.advance(rnd, 0, 3.0)
    L.resume(rnd, 50.0)
    assert rnd.current.elapsed(52.0) == 2.0


def test_settings_validation() -> None:
    assert validate_settings(L.SETTINGS_SCHEMA, {}) == DEFAULTS
    for bad in ({"questions": 0}, {"questions": 31}, {"seconds": 9}, {"seconds": 61}, {"options": "5"},
                {"options": 8}, {"view": "3d"}, {"framing": "zoom"}, {"region": "mars"}, {"extra": 1}):
        with pytest.raises(SettingsError):
            validate_settings(L.SETTINGS_SCHEMA, bad)
    assert L.scored_settings(DEFAULTS) == {k: DEFAULTS[k] for k in ("questions", "seconds", "options", "framing",
                                                                      "region")}
